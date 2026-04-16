"""Run orchestrator — single-command multi-agent execution engine.

Drives a task from creation through the full review pipeline automatically.
For each step:
  1. Resolve next step from task state
  2. If the expected artifact already exists (resume case), advance
  3. Otherwise, invoke the adapter for the responsible agent
  4. If adapter returns COMPLETED, advance and loop
  5. If adapter returns WAITING, record run status and return
  6. If adapter returns FAILED, suspend and return
  7. Loop until terminal state

This is the top-level orchestration layer above the file-based
artifact workflow. It preserves all existing manual-mode semantics
and adds automatic execution on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..adapters.base import AgentAdapter
from ..domain.models import (
    AgentRole,
    ExecutionStatus,
    ReviewOutcome,
    RunStatus,
    TaskState,
    _now_iso,
)
from ..domain.workflow import resolve_next_step
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.run_logger import RunLogger
from ..user_hints import hint_resume_task
from .artifact_service import ArtifactService
from .task_service import TaskService


@dataclass
class StepRecord:
    """Record of one execution step within a run."""

    agent: AgentRole
    artifact: str
    status: ExecutionStatus
    message: str
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class RunResult:
    """Final result of a ``run`` or ``resume`` invocation."""

    task_name: str
    final_state: TaskState
    run_status: RunStatus
    steps: list[StepRecord] = field(default_factory=list)
    waiting_on: Optional[AgentRole] = None
    message: str = ""

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


class RunOrchestrator:
    """Drives a task through the full review pipeline.

    Supports three execution scenarios:

    1. **Full auto** — all adapters are AUTOMATIC. The run completes
       in a single ``run()`` call.
    2. **Mixed** — some adapters are AUTOMATIC, some MANUAL. Auto steps
       execute immediately; manual steps pause the run. ``resume()``
       picks up where it left off.
    3. **Full manual** — all adapters are MANUAL. ``run()`` pauses at
       the first step. ``resume()`` advances one step (if the artifact
       was written externally) and pauses at the next manual step.
    """

    def __init__(
        self,
        task_service: TaskService,
        artifact_service: ArtifactService,
        store: FileStateStore,
        adapters: Optional[dict[AgentRole, AgentAdapter]] = None,
        fallback_adapter: Optional[AgentAdapter] = None,
    ) -> None:
        self.task_service = task_service
        self.artifact_service = artifact_service
        self.store = store
        self.adapters = adapters or {}
        self.fallback_adapter = fallback_adapter

    def _get_adapter(self, agent: AgentRole) -> Optional[AgentAdapter]:
        adapter = self.adapters.get(agent)
        if adapter is not None:
            return adapter
        return self.fallback_adapter

    def _update_run_status(self, task_name: str, status: RunStatus) -> None:
        task = self.store.load_task(task_name)
        task.run_status = status
        task.updated_at = _now_iso()
        self.store.save_task(task)

    def _build_context(self, task: Any) -> dict[str, Any]:
        return {
            "task": task.to_dict(),
            "cycle": task.cycle,
            "target_repo": task.target_repo,
            "agent": "",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        task_name: str,
        target_repo: str = "",
        description: str = "",
        on_step: Optional[Callable[[str], None]] = None,
    ) -> RunResult:
        """Create a task and drive it through the review pipeline.

        Args:
            task_name: Unique task name (becomes directory name).
            target_repo: Path to the repository being reviewed.
            description: Short task description for context.
            on_step: Optional callback invoked with a message before each step.

        Returns:
            RunResult with final state, steps executed, and run status.
        """
        task = self.task_service.init_task(
            task_name=task_name,
            target_repo=target_repo,
            description=description,
        )
        self._update_run_status(task_name, RunStatus.RUNNING)

        logger = RunLogger(self.store.task_dir(task_name))
        logger.log("run_start", task_name=task_name, target_repo=target_repo)

        if on_step:
            on_step(f"Task created: {task_name} (state: {task.state.value})")

        return self._execute_loop(task_name, on_step, logger)

    def resume(
        self,
        task_name: str,
        on_step: Optional[Callable[[str], None]] = None,
    ) -> RunResult:
        """Continue a task that was paused waiting for manual completion.

        Checks whether the expected artifact now exists (written externally),
        advances if so, and re-enters the execution loop.
        """
        task = self.store.load_task(task_name)

        if task.is_terminal:
            return RunResult(
                task_name=task_name,
                final_state=task.state,
                run_status=RunStatus.COMPLETED,
                message=f"Task already in terminal state: {task.state.value}",
            )

        self._update_run_status(task_name, RunStatus.RUNNING)

        logger = RunLogger(self.store.task_dir(task_name))
        logger.log("run_resume", task_name=task_name, state=task.state.value)

        if on_step:
            on_step(f"Resuming task: {task_name} (state: {task.state.value})")

        return self._execute_loop(task_name, on_step, logger)

    # ------------------------------------------------------------------
    # Execution loop
    # ------------------------------------------------------------------

    def _execute_loop(
        self,
        task_name: str,
        on_step: Optional[Callable[[str], None]] = None,
        logger: Optional[RunLogger] = None,
    ) -> RunResult:
        """Core execution loop. Runs until terminal, waiting, or failed."""
        steps: list[StepRecord] = []
        max_iterations = 20  # circuit breaker

        for _ in range(max_iterations):
            # Try to advance first — picks up externally-written artifacts
            task = self.task_service.advance(task_name)

            if task.is_terminal:
                self._update_run_status(task_name, RunStatus.COMPLETED)
                if logger:
                    logger.log("run_complete", final_state=task.state.value, steps=len(steps))
                return RunResult(
                    task_name=task_name,
                    final_state=task.state,
                    run_status=RunStatus.COMPLETED,
                    steps=steps,
                    message=f"Task reached terminal state: {task.state.value}",
                )

            next_step = resolve_next_step(task)
            if next_step is None:
                self._update_run_status(task_name, RunStatus.COMPLETED)
                return RunResult(
                    task_name=task_name,
                    final_state=task.state,
                    run_status=RunStatus.COMPLETED,
                    steps=steps,
                    message="No further steps available.",
                )

            # If artifact already exists but advance didn't move us,
            # it means the artifact content didn't trigger a transition
            # (e.g. missing status field). Skip to avoid infinite loop.
            if self.store.artifact_exists(task_name, next_step.artifact):
                task = self.task_service.advance(task_name)
                if not task.is_terminal:
                    steps.append(StepRecord(
                        agent=next_step.agent,
                        artifact=next_step.artifact,
                        status=ExecutionStatus.COMPLETED,
                        message="Advanced from existing artifact.",
                    ))
                continue

            # Invoke the adapter
            adapter = self._get_adapter(next_step.agent)
            if adapter is None:
                self._update_run_status(task_name, RunStatus.SUSPENDED)
                return RunResult(
                    task_name=task_name,
                    final_state=task.state,
                    run_status=RunStatus.SUSPENDED,
                    steps=steps,
                    message=f"No adapter configured for {next_step.agent.value}.",
                )

            if logger:
                logger.log(
                    "step_start",
                    agent=next_step.agent.value,
                    adapter=adapter.name,
                    artifact=next_step.artifact,
                    capability=adapter.capability.value,
                )

            if on_step:
                on_step(
                    f"[{next_step.agent.value}] Invoking {adapter.name} adapter "
                    f"for {next_step.artifact}..."
                )

            context = self._build_context(task)
            context["agent"] = next_step.agent.value
            result = adapter.execute(
                task_name=task_name,
                artifact=next_step.artifact,
                template=next_step.template,
                instruction=next_step.instruction,
                context=context,
            )

            steps.append(StepRecord(
                agent=next_step.agent,
                artifact=next_step.artifact,
                status=result.status,
                message=result.message,
            ))

            if result.status == ExecutionStatus.COMPLETED:
                if logger:
                    logger.log(
                        "step_completed",
                        agent=next_step.agent.value,
                        artifact=next_step.artifact,
                        review_outcome=result.review_outcome.value if result.review_outcome else None,
                    )
                if on_step:
                    outcome_str = ""
                    if result.review_outcome:
                        outcome_str = f" ({result.review_outcome.value})"
                    on_step(f"[{next_step.agent.value}] Completed: {next_step.artifact}{outcome_str}")

                self.task_service.advance(task_name, result.review_outcome)
                continue

            if result.status == ExecutionStatus.WAITING:
                waiting_status = _agent_to_waiting_status(next_step.agent)
                self._update_run_status(task_name, waiting_status)
                if logger:
                    logger.log("step_waiting", agent=next_step.agent.value, artifact=next_step.artifact)

                if on_step:
                    on_step(
                        f"[{next_step.agent.value}] Waiting for manual completion. "
                        f"{hint_resume_task(task_name)}"
                    )

                return RunResult(
                    task_name=task_name,
                    final_state=task.state,
                    run_status=waiting_status,
                    steps=steps,
                    waiting_on=next_step.agent,
                    message=result.message,
                )

            # FAILED
            self._update_run_status(task_name, RunStatus.SUSPENDED)
            if logger:
                logger.log("step_failed", agent=next_step.agent.value, artifact=next_step.artifact, message=result.message)
            return RunResult(
                task_name=task_name,
                final_state=task.state,
                run_status=RunStatus.SUSPENDED,
                steps=steps,
                message=f"Step failed: {result.message}",
            )

        # Circuit breaker hit
        self._update_run_status(task_name, RunStatus.SUSPENDED)
        return RunResult(
            task_name=task_name,
            final_state=self.store.load_task(task_name).state,
            run_status=RunStatus.SUSPENDED,
            steps=steps,
            message="Execution loop exceeded maximum iterations.",
        )
