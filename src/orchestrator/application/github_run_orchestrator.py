"""GitHub-native run orchestrator — single-command issue-to-PR execution.

Drives a GitHub-backed task from issue claim through the full review
pipeline: implementation -> PR -> Claude review -> Codex review -> approval.

For each step:
  1. Resolve next step from task state
  2. Check GitHub for state changes (PR opened, review posted)
  3. If the expected condition is met, advance and loop
  4. Otherwise, invoke the adapter for the responsible agent
  5. If adapter returns COMPLETED, check GitHub and advance
  6. If adapter returns WAITING, record run status and return
  7. If adapter returns FAILED, suspend and return
  8. Loop until terminal state
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..adapters.base import AgentAdapter
from ..domain.github_models import GitHubTask, GitHubTaskState, WorkType
from ..domain.github_workflow import GitHubNextStep, resolve_github_next_step
from ..domain.models import (
    AgentRole,
    ExecutionStatus,
    RunStatus,
    _now_iso,
)
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.github_service import GitHubService
from ..infrastructure.run_logger import RunLogger
from .github_task_service import GitHubTaskService


@dataclass
class GitHubStepRecord:
    """Record of one execution step within a GitHub run."""

    agent: AgentRole
    action: str
    status: ExecutionStatus
    message: str
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class GitHubRunResult:
    """Final result of a ``github-run`` or ``github-resume`` invocation."""

    task_name: str
    final_state: GitHubTaskState
    run_status: RunStatus
    steps: list[GitHubStepRecord] = field(default_factory=list)
    waiting_on: Optional[AgentRole] = None
    message: str = ""
    pr_number: Optional[int] = None
    pr_url: str = ""

    @property
    def is_complete(self) -> bool:
        return self.run_status == RunStatus.COMPLETED

    @property
    def is_waiting(self) -> bool:
        return self.run_status in (
            RunStatus.WAITING_ON_CURSOR,
            RunStatus.WAITING_ON_CLAUDE,
            RunStatus.WAITING_ON_CODEX,
        )


def _agent_to_waiting_status(agent: AgentRole) -> RunStatus:
    return {
        AgentRole.CURSOR: RunStatus.WAITING_ON_CURSOR,
        AgentRole.CLAUDE: RunStatus.WAITING_ON_CLAUDE,
        AgentRole.CODEX: RunStatus.WAITING_ON_CODEX,
    }[agent]


class GitHubRunOrchestrator:
    """Drives a GitHub-backed task through the full review pipeline.

    Coordinates issue claiming, agent invocations, PR detection,
    review state checking, and state transitions.
    """

    def __init__(
        self,
        task_service: GitHubTaskService,
        github: GitHubService,
        store: FileStateStore,
        adapters: Optional[dict[AgentRole, AgentAdapter]] = None,
        fallback_adapter: Optional[AgentAdapter] = None,
    ) -> None:
        self.task_service = task_service
        self.github = github
        self.store = store
        self.adapters = adapters or {}
        self.fallback_adapter = fallback_adapter
        self._prompt_content: Optional[str] = None
        self._local_repo_path: str = ""

    def _get_adapter(self, agent: AgentRole) -> Optional[AgentAdapter]:
        adapter = self.adapters.get(agent)
        if adapter is not None:
            return adapter
        return self.fallback_adapter

    def _update_run_status(self, task: GitHubTask, status: RunStatus) -> None:
        task.run_status = status
        task.updated_at = _now_iso()
        self.task_service._save_github_task(task)

    def _build_context(self, task: GitHubTask) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "task": task.to_dict(),
            "cycle": task.cycle,
            "target_repo": self._local_repo_path or task.repo,
            "github_repo": task.repo,
            "issue_number": task.issue_number,
            "issue_title": task.issue_title,
            "work_type": task.work_type.value,
            "branch_name": task.branch_name,
            "pr_number": task.pr_number,
            "pr_url": task.pr_url,
            "base_branch": self.task_service.base_branch,
            "pr_title_pattern": self.task_service.pr_title_pattern,
            "agent": "",
            "workflow_mode": "github",
        }
        if self._prompt_content:
            ctx["prompt_content"] = self._prompt_content
        return ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        issue_number: int,
        work_type: WorkType = WorkType.FEAT,
        on_step: Optional[Callable[[str], None]] = None,
        prompt_content: Optional[str] = None,
        local_repo_path: str = "",
    ) -> GitHubRunResult:
        """Claim an issue and drive it through the review pipeline.

        Args:
            prompt_content: If provided, injected as detailed task instructions
                into the adapter context (from a ``--prompt-file``).
            local_repo_path: Local filesystem path to the git clone. Used as
                the working directory and workspace path for agent adapters.
                Defaults to CWD if not provided.
        """
        self._prompt_content = prompt_content
        self._local_repo_path = local_repo_path
        task = self.task_service.claim_issue(issue_number, work_type=work_type)

        self._update_run_status(task, RunStatus.RUNNING)

        task_dir = self.store.task_dir(task.name)
        if prompt_content:
            (task_dir / "prompt.md").write_text(prompt_content)

        logger = RunLogger(task_dir)
        logger.log(
            "github_run_start",
            task_name=task.name,
            issue_number=issue_number,
            repo=task.repo,
            has_prompt_file=bool(prompt_content),
        )

        if on_step:
            on_step(
                f"Issue #{issue_number} claimed. "
                f"Branch: {task.branch_name}"
            )

        return self._execute_loop(task, on_step, logger)

    def resume(
        self,
        task_name: str,
        on_step: Optional[Callable[[str], None]] = None,
        local_repo_path: str = "",
    ) -> GitHubRunResult:
        """Continue a task that was paused waiting for manual completion."""
        if local_repo_path:
            self._local_repo_path = local_repo_path
        task = self.task_service.get_task(task_name)

        if task.is_terminal:
            return GitHubRunResult(
                task_name=task_name,
                final_state=task.state,
                run_status=RunStatus.COMPLETED,
                message=f"Task already in terminal state: {task.state.value}",
                pr_number=task.pr_number,
                pr_url=task.pr_url,
            )

        self._update_run_status(task, RunStatus.RUNNING)

        logger = RunLogger(self.store.task_dir(task_name))
        logger.log("github_run_resume", task_name=task_name, state=task.state.value)

        if on_step:
            on_step(f"Resuming task: {task_name} (state: {task.state.value})")

        return self._execute_loop(task, on_step, logger)

    # ------------------------------------------------------------------
    # Execution loop
    # ------------------------------------------------------------------

    def _execute_loop(
        self,
        task: GitHubTask,
        on_step: Optional[Callable[[str], None]] = None,
        logger: Optional[RunLogger] = None,
    ) -> GitHubRunResult:
        steps: list[GitHubStepRecord] = []
        max_iterations = 20

        for _ in range(max_iterations):
            task = self.task_service.get_task(task.name)

            if task.is_terminal:
                self._update_run_status(task, RunStatus.COMPLETED)
                if logger:
                    logger.log(
                        "github_run_complete",
                        final_state=task.state.value,
                        steps=len(steps),
                    )
                return GitHubRunResult(
                    task_name=task.name,
                    final_state=task.state,
                    run_status=RunStatus.COMPLETED,
                    steps=steps,
                    message=f"Task reached terminal state: {task.state.value}",
                    pr_number=task.pr_number,
                    pr_url=task.pr_url,
                )

            advanced = self._try_advance_from_github(task, steps, on_step)
            if advanced:
                continue

            next_step = resolve_github_next_step(task)
            if next_step is None:
                self._update_run_status(task, RunStatus.COMPLETED)
                return GitHubRunResult(
                    task_name=task.name,
                    final_state=task.state,
                    run_status=RunStatus.COMPLETED,
                    steps=steps,
                    message="No further steps available.",
                    pr_number=task.pr_number,
                    pr_url=task.pr_url,
                )

            adapter = self._get_adapter(next_step.agent)
            if adapter is None:
                self._update_run_status(task, RunStatus.SUSPENDED)
                return GitHubRunResult(
                    task_name=task.name,
                    final_state=task.state,
                    run_status=RunStatus.SUSPENDED,
                    steps=steps,
                    message=f"No adapter configured for {next_step.agent.value}.",
                    pr_number=task.pr_number,
                    pr_url=task.pr_url,
                )

            if logger:
                logger.log(
                    "github_step_start",
                    agent=next_step.agent.value,
                    action=next_step.action,
                    adapter=adapter.name,
                    capability=adapter.capability.value,
                )

            if on_step:
                on_step(
                    f"[{next_step.agent.value}] Invoking {adapter.name} adapter "
                    f"for {next_step.action}..."
                )

            context = self._build_context(task)
            context["agent"] = next_step.agent.value

            artifact_name = self._action_to_artifact(next_step, task)

            result = adapter.execute(
                task_name=task.name,
                artifact=artifact_name,
                template="",
                instruction=next_step.instruction,
                context=context,
            )

            steps.append(GitHubStepRecord(
                agent=next_step.agent,
                action=next_step.action,
                status=result.status,
                message=result.message,
            ))

            if result.status == ExecutionStatus.COMPLETED:
                if logger:
                    logger.log(
                        "github_step_completed",
                        agent=next_step.agent.value,
                        action=next_step.action,
                    )
                if on_step:
                    on_step(
                        f"[{next_step.agent.value}] Completed: {next_step.action}"
                    )

                self._post_step_advance(task, next_step)
                continue

            if result.status == ExecutionStatus.WAITING:
                waiting_status = _agent_to_waiting_status(next_step.agent)
                self._update_run_status(task, waiting_status)
                if logger:
                    logger.log(
                        "github_step_waiting",
                        agent=next_step.agent.value,
                        action=next_step.action,
                    )
                if on_step:
                    on_step(
                        f"[{next_step.agent.value}] Waiting for manual completion. "
                        f"Run: orchestrator github-resume {task.name}"
                    )
                return GitHubRunResult(
                    task_name=task.name,
                    final_state=task.state,
                    run_status=waiting_status,
                    steps=steps,
                    waiting_on=next_step.agent,
                    message=result.message,
                    pr_number=task.pr_number,
                    pr_url=task.pr_url,
                )

            # FAILED
            self._update_run_status(task, RunStatus.SUSPENDED)
            if logger:
                logger.log(
                    "github_step_failed",
                    agent=next_step.agent.value,
                    action=next_step.action,
                    message=result.message,
                )
            return GitHubRunResult(
                task_name=task.name,
                final_state=task.state,
                run_status=RunStatus.SUSPENDED,
                steps=steps,
                message=f"Step failed: {result.message}",
                pr_number=task.pr_number,
                pr_url=task.pr_url,
            )

        # Circuit breaker
        self._update_run_status(task, RunStatus.SUSPENDED)
        return GitHubRunResult(
            task_name=task.name,
            final_state=task.state,
            run_status=RunStatus.SUSPENDED,
            steps=steps,
            message="Execution loop exceeded maximum iterations.",
            pr_number=task.pr_number,
            pr_url=task.pr_url,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _try_advance_from_github(
        self,
        task: GitHubTask,
        steps: list[GitHubStepRecord],
        on_step: Optional[Callable[[str], None]],
    ) -> bool:
        """Check GitHub for state changes and advance if possible.

        Returns True if the task was advanced.
        """
        state = task.state

        if state == GitHubTaskState.CURSOR_IMPLEMENTING:
            pr_number = self.task_service.detect_pr(task.name)
            if pr_number:
                if on_step:
                    on_step(f"PR #{pr_number} detected on GitHub.")
                steps.append(GitHubStepRecord(
                    agent=AgentRole.CURSOR,
                    action="pr_detected",
                    status=ExecutionStatus.COMPLETED,
                    message=f"PR #{pr_number} detected.",
                ))
                self.task_service.advance(task.name)
                return True

        if state in (
            GitHubTaskState.CLAUDE_REVIEWING,
            GitHubTaskState.CODEX_REVIEWING,
        ):
            review_state = self.task_service.detect_review_state(task.name)
            if review_state:
                if on_step:
                    on_step(f"Review state detected: {review_state}")
                steps.append(GitHubStepRecord(
                    agent=(
                        AgentRole.CLAUDE
                        if state == GitHubTaskState.CLAUDE_REVIEWING
                        else AgentRole.CODEX
                    ),
                    action="review_detected",
                    status=ExecutionStatus.COMPLETED,
                    message=f"Review state: {review_state}",
                ))
                self.task_service.advance(task.name, review_state=review_state)
                return True

        return False

    def _post_step_advance(
        self,
        task: GitHubTask,
        step: GitHubNextStep,
    ) -> None:
        """Advance the task after an adapter step completed."""
        if step.action == "implement":
            pr_number = self.task_service.detect_pr(task.name)
            if pr_number:
                self.task_service.advance(task.name)
            return

        if step.action == "open_pr":
            pr_number = self.task_service.detect_pr(task.name)
            if pr_number:
                self.task_service.advance(task.name)
            return

        if step.action in ("review_pr", "final_review"):
            review_state = self.task_service.detect_review_state(task.name)
            self.task_service.advance(task.name, review_state=review_state)
            return

        if step.action == "rework":
            self.task_service.advance(task.name)
            return

        self.task_service.advance(task.name)

    def _action_to_artifact(
        self,
        step: GitHubNextStep,
        task: GitHubTask,
    ) -> str:
        """Map a GitHub action to a nominal artifact name for adapter compatibility."""
        action_map = {
            "implement": f"github-implementation-cycle-{task.cycle}",
            "open_pr": f"github-pr-cycle-{task.cycle}",
            "review_pr": f"github-review-cycle-{task.cycle}",
            "final_review": f"github-final-review-cycle-{task.cycle}",
            "rework": f"github-rework-cycle-{task.cycle}",
        }
        return action_map.get(step.action, f"github-{step.action}")
