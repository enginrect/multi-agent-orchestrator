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

import time
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
from ..domain import provenance
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.github_service import GitHubError, GitHubService
from ..infrastructure.run_logger import RunLogger
from ..infrastructure.logger import get_logger
from ..user_hints import hint_resume_github
from .github_task_service import GitHubTaskService

_log = get_logger(__name__)

_MAX_SAME_STEP_REPEATS = 2


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
        local_repo_path: Optional[str] = None,
    ) -> None:
        self.task_service = task_service
        self.github = github
        self.store = store
        self.adapters = adapters or {}
        self.fallback_adapter = fallback_adapter
        self._prompt_content: Optional[str] = None
        self.local_repo_path = local_repo_path
        self.timeout_override: Optional[int] = None
        self._saved_branch: Optional[str] = None

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
        local_path = self.local_repo_path or task.repo
        ctx: dict[str, Any] = {
            "task": task.to_dict(),
            "cycle": task.cycle,
            "target_repo": local_path,
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
        if self.timeout_override is not None:
            ctx["timeout_override"] = self.timeout_override
        return ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_local_repo(self) -> None:
        """Pre-flight check: verify --local-repo matches --repo.

        Prevents cross-repo contamination by inspecting the origin
        remote of the local clone.
        """
        if not self.local_repo_path:
            return
        GitHubService.validate_local_repo(self.local_repo_path, self.github.repo)

    def _save_local_branch(self) -> None:
        """Record the current branch of the local repo before agents modify it."""
        if not self.local_repo_path:
            return
        branch = GitHubService.get_current_branch(self.local_repo_path)
        if branch:
            self._saved_branch = branch
            _log.info("Saved local branch before workflow: %s", branch)

    def _restore_local_branch(self, logger: Optional[RunLogger] = None) -> None:
        """Restore the local repo to the branch it was on before the workflow."""
        if not self.local_repo_path or not self._saved_branch:
            return

        current = GitHubService.get_current_branch(self.local_repo_path)
        if current == self._saved_branch:
            return

        _log.info(
            "Restoring local branch: %s -> %s", current, self._saved_branch,
        )
        ok = GitHubService.checkout_branch(self.local_repo_path, self._saved_branch)
        if ok:
            if logger:
                logger.log(
                    "local_branch_restored",
                    from_branch=current,
                    to_branch=self._saved_branch,
                )
        else:
            msg = (
                f"Could not restore local branch to '{self._saved_branch}' "
                f"(currently on '{current}'). "
                f"Please run: git -C {self.local_repo_path} checkout {self._saved_branch}"
            )
            _log.warning(msg)
            if logger:
                logger.log("local_branch_restore_failed", message=msg)

    def run(
        self,
        issue_number: int,
        work_type: WorkType = WorkType.FEAT,
        on_step: Optional[Callable[[str], None]] = None,
        prompt_content: Optional[str] = None,
    ) -> GitHubRunResult:
        """Claim an issue and drive it through the review pipeline.

        Args:
            prompt_content: If provided, injected as detailed task instructions
                into the adapter context (from a ``--prompt-file``).
        """
        self.validate_local_repo()
        self._save_local_branch()

        self._prompt_content = prompt_content
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

        result = self._execute_loop(task, on_step, logger)
        self._restore_local_branch(logger)
        return result

    def resume(
        self,
        task_name: str,
        on_step: Optional[Callable[[str], None]] = None,
    ) -> GitHubRunResult:
        """Continue a task that was paused waiting for manual completion."""
        self.validate_local_repo()
        self._save_local_branch()

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

        result = self._execute_loop(task, on_step, logger)
        self._restore_local_branch(logger)
        return result

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
        prev_step_key: Optional[str] = None
        same_step_count = 0

        for iteration in range(max_iterations):
            task = self.task_service.get_task(task.name)

            if logger:
                logger.log(
                    "loop_iteration",
                    iteration=iteration,
                    state=task.state.value,
                    pr_number=task.pr_number,
                )

            if task.is_terminal:
                self._update_run_status(task, RunStatus.COMPLETED)
                if logger:
                    logger.log(
                        "github_run_complete",
                        final_state=task.state.value,
                        steps=len(steps),
                    )
                if task.state == GitHubTaskState.APPROVED and task.pr_number:
                    self._post_provenance(
                        task,
                        provenance.comment_approved(task.pr_number, task.cycle),
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
                prev_step_key = None
                same_step_count = 0
                continue

            next_step = resolve_github_next_step(task)
            if next_step is None:
                self._update_run_status(task, RunStatus.COMPLETED)
                if task.state == GitHubTaskState.APPROVED:
                    self._log_self_approval_caveat(task, logger)
                return GitHubRunResult(
                    task_name=task.name,
                    final_state=task.state,
                    run_status=RunStatus.COMPLETED,
                    steps=steps,
                    message="No further steps available.",
                    pr_number=task.pr_number,
                    pr_url=task.pr_url,
                )

            step_key = f"{task.state.value}:{next_step.action}"
            if step_key == prev_step_key:
                same_step_count += 1
            else:
                prev_step_key = step_key
                same_step_count = 1

            if same_step_count > _MAX_SAME_STEP_REPEATS:
                if logger:
                    logger.log(
                        "same_step_exceeded",
                        step_key=step_key,
                        count=same_step_count,
                    )
                self._update_run_status(task, RunStatus.SUSPENDED)
                return GitHubRunResult(
                    task_name=task.name,
                    final_state=task.state,
                    run_status=RunStatus.SUSPENDED,
                    steps=steps,
                    message=(
                        f"Step '{next_step.action}' repeated {same_step_count} times "
                        f"without state change (state: {task.state.value}). "
                        f"Suspended to prevent infinite loop."
                    ),
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
                    state=task.state.value,
                    adapter=adapter.name,
                    capability=adapter.capability.value,
                    same_step_count=same_step_count,
                )

            if on_step:
                on_step(
                    f"[{next_step.agent.value}] Invoking {adapter.name} adapter "
                    f"for {next_step.action}..."
                )

            if next_step.action in ("review_pr", "final_review"):
                self._post_provenance(
                    task,
                    provenance.comment_review_started(
                        next_step.agent,
                        task.pr_number or 0,
                        task.cycle,
                    ),
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

                if next_step.action in ("review_pr", "final_review"):
                    review_outcome = getattr(result, "review_outcome", None)
                    status_str = review_outcome.value if review_outcome else ""
                    has_formal = self._check_formal_review_posted(task, next_step.agent)
                    if has_formal:
                        self._post_provenance(
                            task,
                            provenance.comment_review_completed(
                                next_step.agent,
                                task.pr_number or 0,
                                task.cycle,
                                status=status_str,
                            ),
                        )
                    else:
                        self._relay_review_to_pr(
                            task, next_step.agent, artifact_name, logger,
                        )

                self._post_step_advance(task, next_step, logger=logger)
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
                        f"{hint_resume_github(task.name)}"
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
                self._post_provenance(
                    task,
                    provenance.comment_pr_opened(AgentRole.CURSOR, pr_number, task.cycle),
                )
                self.task_service.advance(task.name)
                return True

        if state in (
            GitHubTaskState.CLAUDE_REVIEWING,
            GitHubTaskState.CODEX_REVIEWING,
        ):
            review_state = self.task_service.detect_review_state(task.name)
            if review_state:
                agent = (
                    AgentRole.CLAUDE
                    if state == GitHubTaskState.CLAUDE_REVIEWING
                    else AgentRole.CODEX
                )
                if on_step:
                    on_step(f"Review state detected: {review_state}")
                steps.append(GitHubStepRecord(
                    agent=agent,
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
        logger: Optional[RunLogger] = None,
    ) -> None:
        """Advance the task after an adapter step completed."""
        if step.action in ("implement", "open_pr"):
            pr_number = self._detect_pr_with_retry(task.name, logger=logger)
            if pr_number:
                self.task_service.advance(task.name)
            else:
                if logger:
                    logger.log(
                        "force_advance_no_pr",
                        action=step.action,
                        state=task.state.value,
                        note="Adapter reported success but PR not detected; force-advancing.",
                    )
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

    def _detect_pr_with_retry(
        self,
        task_name: str,
        max_attempts: int = 4,
        delay: float = 3.0,
        logger: Optional[RunLogger] = None,
    ) -> Optional[int]:
        """Poll for PR detection with bounded retries.

        GitHub API may return stale data immediately after PR creation.
        """
        for attempt in range(1, max_attempts + 1):
            pr_number = self.task_service.detect_pr(task_name)
            if pr_number:
                if logger:
                    logger.log(
                        "pr_detected",
                        pr_number=pr_number,
                        attempt=attempt,
                    )
                return pr_number
            if attempt < max_attempts:
                if logger:
                    logger.log(
                        "pr_detect_retry",
                        attempt=attempt,
                        delay=delay,
                    )
                time.sleep(delay)
        if logger:
            logger.log(
                "pr_detect_exhausted",
                attempts=max_attempts,
            )
        return None

    def _post_provenance(self, task: GitHubTask, body: str) -> None:
        """Post an issue comment for workflow provenance (best-effort)."""
        try:
            self.github.add_issue_comment(task.issue_number, body)
        except GitHubError:
            pass

    def _check_formal_review_posted(
        self,
        task: GitHubTask,
        agent: AgentRole,
    ) -> bool:
        """Check if the agent posted a formal PR review on GitHub.

        Returns True if at least one non-COMMENTED review exists.
        Tolerates API failures by returning True (assume posted).
        """
        if not task.pr_number:
            return False
        try:
            state = self.github.get_latest_review_state(task.pr_number)
            return state is not None
        except GitHubError:
            return True

    def _relay_review_to_pr(
        self,
        task: GitHubTask,
        agent: AgentRole,
        artifact_name: str,
        logger: Optional[RunLogger] = None,
    ) -> None:
        """Relay an agent's review to the PR when the agent could not post directly.

        Reads the step log to extract the agent's review output, then
        posts it as a PR comment via the orchestrator's own ``gh``
        credentials with explicit provenance attribution.
        """
        pr_number = task.pr_number
        if not pr_number:
            return

        review_body = self._extract_review_from_log(task.name, artifact_name)

        if review_body:
            relay_comment = provenance.comment_relayed_review(
                agent, pr_number, task.cycle, review_body,
            )
            try:
                self.github.add_pr_comment(pr_number, relay_comment)
                if logger:
                    logger.log(
                        "review_relayed_to_pr",
                        agent=agent.value,
                        pr_number=pr_number,
                        body_length=len(review_body),
                    )
                self._post_provenance(
                    task,
                    provenance.comment_review_completed(
                        agent, pr_number, task.cycle,
                        status="relayed to PR",
                    ),
                )
                return
            except GitHubError:
                if logger:
                    logger.log(
                        "review_relay_failed",
                        agent=agent.value,
                        pr_number=pr_number,
                    )

        self._post_provenance(
            task,
            provenance.comment_fallback_review(
                agent, pr_number, task.cycle,
            ),
        )

    def _extract_review_from_log(
        self,
        task_name: str,
        artifact_name: str,
    ) -> Optional[str]:
        """Extract meaningful review content from an agent's step log.

        Looks for the step log file, reads it, and attempts to extract
        the review body the agent intended to post.
        """
        log_file = self.store.task_dir(task_name) / f".log-{artifact_name}.txt"
        if not log_file.is_file():
            return None

        try:
            content = log_file.read_text()
        except OSError:
            return None

        if not content.strip():
            return None

        import re
        md_block = re.search(
            r"```(?:md|markdown)?\s*\n(🤖.*?)```",
            content,
            re.DOTALL,
        )
        if md_block:
            return md_block.group(1).strip()

        for marker in ("Summary:", "## Summary", "Findings:", "## Findings", "Verdict:"):
            idx = content.find(marker)
            if idx != -1:
                candidate = content[idx:].strip()
                if len(candidate) > 50:
                    return candidate[:3000]

        return None

    def _log_self_approval_caveat(
        self,
        task: GitHubTask,
        logger: Optional[RunLogger] = None,
    ) -> None:
        """Log a caveat that orchestrator approvals share a single git identity.

        GitHub treats all agent reviews as from the same account, so the
        approval is an orchestrator-internal gate, not an independent
        GitHub review approval.
        """
        msg = (
            "Self-approval caveat: Cursor, Claude, and Codex share a single "
            "git/gh identity. GitHub treats all reviews as from one account. "
            "This approval is an orchestrator-internal gate and does not "
            "count as an independent GitHub review approval. "
            "A human reviewer may still be required by branch protection rules."
        )
        _log.info(msg)
        if logger:
            logger.log(
                "self_approval_caveat",
                pr_number=task.pr_number,
                message=msg,
            )

    def _action_to_artifact(
        self,
        step: GitHubNextStep,
        task: GitHubTask,
    ) -> str:
        """Map a GitHub action to a nominal artifact name for adapter compatibility."""
        action_map = {
            "implement": f"github-implementation-cycle-{task.cycle}.md",
            "open_pr": f"github-pr-cycle-{task.cycle}.md",
            "review_pr": f"github-review-cycle-{task.cycle}.md",
            "final_review": f"github-final-review-cycle-{task.cycle}.md",
            "rework": f"github-rework-cycle-{task.cycle}.md",
        }
        return action_map.get(step.action, f"github-{step.action}.md")
