"""Task lifecycle operations.

This is the primary service for creating, advancing, and archiving tasks.
It coordinates the domain model, state machine, file store, and templates.
"""

from __future__ import annotations

from typing import Optional

from ..domain.errors import (
    MaxCyclesExceededError,
    TaskNotFoundError,
)
from ..domain.models import AgentRole, ReviewOutcome, Task, TaskState
from ..domain.state_machine import validate_transition
from ..domain.workflow import NextStep, resolve_next_step
from ..infrastructure.config_loader import OrchestratorConfig
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.template_renderer import TemplateRenderer
from .artifact_service import ArtifactService


class TaskService:
    """Manages the full task lifecycle."""

    def __init__(
        self,
        config: OrchestratorConfig,
        store: FileStateStore,
        renderer: TemplateRenderer,
        artifact_service: ArtifactService,
    ) -> None:
        self.config = config
        self.store = store
        self.renderer = renderer
        self.artifact_service = artifact_service

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def init_task(
        self,
        task_name: str,
        target_repo: str = "",
        description: str = "",
        max_cycles: Optional[int] = None,
    ) -> Task:
        """Create a new task directory with initial state and scope template."""
        self.store.ensure_workspace()

        effective_repo = target_repo or self.config.default_target_repo
        effective_max = max_cycles if max_cycles is not None else self.config.max_cycles

        task = Task(
            name=task_name,
            target_repo=effective_repo,
            state=TaskState.INITIALIZED,
            max_cycles=effective_max,
            description=description,
        )

        self.store.create_task_dir(task_name)
        self.store.save_task(task)

        scope_content = self.renderer.render(
            "00-scope.md",
            {"short name matching the directory name": task_name},
        )
        self.store.write_artifact(task_name, "00-scope.md", scope_content)

        task.record_transition(
            TaskState.CURSOR_IMPLEMENTING,
            artifact="00-scope.md",
            note="Task initialized, scope template created.",
        )
        self.store.save_task(task)

        return task

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_task(self, task_name: str) -> Task:
        return self.store.load_task(task_name)

    def list_tasks(self, include_archived: bool = False) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {"active": self.store.list_active_tasks()}
        if include_archived:
            result["archived"] = self.store.list_archived_tasks()
        return result

    # ------------------------------------------------------------------
    # Advance
    # ------------------------------------------------------------------

    def advance(
        self,
        task_name: str,
        review_outcome: Optional[ReviewOutcome] = None,
    ) -> Task:
        """Advance a task to the next state.

        For review phases (Claude/Codex), the review_outcome determines
        whether the task advances, loops back, or escalates.
        If review_outcome is not provided, attempts to auto-detect from
        the latest artifact.
        """
        task = self.store.load_task(task_name)

        if task.is_terminal:
            return task

        next_step = resolve_next_step(task)
        if next_step is None:
            return task

        if not self.store.artifact_exists(task_name, next_step.artifact):
            return task

        outcome = review_outcome
        if outcome is None:
            outcome = self.artifact_service.read_review_outcome(
                task_name, next_step.artifact
            )

        new_state = self._resolve_transition(task, next_step, outcome)

        validate_transition(task.state, new_state)

        if new_state == TaskState.CURSOR_REWORKING and task.state == TaskState.CODEX_REVIEWING:
            task.cycle += 1

        task.record_transition(
            new_state,
            artifact=next_step.artifact,
            review_outcome=outcome.value if outcome else None,
        )
        self.store.save_task(task)

        if new_state == TaskState.APPROVED:
            approval_artifact = "05-final-approval.md" if task.cycle <= 1 else "09-final-approval.md"
            if not self.store.artifact_exists(task_name, approval_artifact):
                content = self.renderer.render(
                    "05-final-approval.md",
                    {"short name": task_name, "1 or 2": str(task.cycle)},
                )
                self.store.write_artifact(task_name, approval_artifact, content)

        return task

    def _resolve_transition(
        self,
        task: Task,
        next_step: NextStep,
        outcome: Optional[ReviewOutcome],
    ) -> TaskState:
        """Determine the actual target state based on review outcome."""
        state = task.state

        if state in (TaskState.INITIALIZED, TaskState.CURSOR_IMPLEMENTING):
            return next_step.state_after

        if state == TaskState.CURSOR_REWORKING:
            return TaskState.CODEX_REVIEWING

        if state == TaskState.CLAUDE_REVIEWING:
            if outcome == ReviewOutcome.CHANGES_REQUESTED:
                return TaskState.CURSOR_REWORKING
            return TaskState.CODEX_REVIEWING

        if state == TaskState.CODEX_REVIEWING:
            if outcome in (ReviewOutcome.APPROVED, ReviewOutcome.MINOR_FIXES_APPLIED):
                return TaskState.APPROVED
            if outcome == ReviewOutcome.CHANGES_REQUESTED:
                if task.cycle >= task.max_cycles:
                    return TaskState.ESCALATED
                return TaskState.CURSOR_REWORKING

            return TaskState.APPROVED

        return next_step.state_after

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    def archive(self, task_name: str) -> Task:
        """Archive an approved task."""
        task = self.store.load_task(task_name)
        if task.state != TaskState.APPROVED:
            raise ValueError(
                f"Cannot archive task '{task_name}' in state '{task.state.value}'. "
                f"Only approved tasks can be archived."
            )
        validate_transition(task.state, TaskState.ARCHIVED)
        task.record_transition(TaskState.ARCHIVED, note="Task archived.")
        self.store.save_task(task)
        self.store.archive_task(task_name)
        return task

    # ------------------------------------------------------------------
    # Next step
    # ------------------------------------------------------------------

    def get_next_step(self, task_name: str) -> Optional[NextStep]:
        task = self.store.load_task(task_name)
        return resolve_next_step(task)
