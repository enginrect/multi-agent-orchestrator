"""Workflow orchestration engine.

Coordinates adapter invocation, artifact generation, and state advancement.
In manual mode, it generates instructions. In adapter-ready mode, it can
delegate to agent adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..adapters.base import AgentAdapter
from ..domain.models import AgentRole, Task, TaskState
from ..domain.workflow import NextStep, resolve_next_step
from ..user_hints import task_advance_shell
from .artifact_service import ArtifactService
from .task_service import TaskService


@dataclass
class StepResult:
    """Result of executing a workflow step."""

    task_name: str
    agent: AgentRole
    artifact: str
    instruction: str
    completed: bool
    message: str


class WorkflowEngine:
    """Drives the review workflow for a task.

    Supports two modes:
    - Manual: generates instructions for human operators.
    - Automated: delegates to agent adapters.
    """

    def __init__(
        self,
        task_service: TaskService,
        artifact_service: ArtifactService,
        adapters: Optional[dict[AgentRole, AgentAdapter]] = None,
    ) -> None:
        self.task_service = task_service
        self.artifact_service = artifact_service
        self.adapters = adapters or {}

    def run_next_step(self, task_name: str) -> StepResult:
        """Execute the next step in the workflow.

        If an adapter is registered for the responsible agent, delegate to it.
        Otherwise, return instructions for manual execution.
        """
        task = self.task_service.get_task(task_name)

        if task.is_terminal:
            return StepResult(
                task_name=task_name,
                agent=AgentRole.CURSOR,
                artifact="",
                instruction="",
                completed=True,
                message=f"Task is in terminal state: {task.state.value}",
            )

        next_step = resolve_next_step(task)
        if next_step is None:
            return StepResult(
                task_name=task_name,
                agent=AgentRole.CURSOR,
                artifact="",
                instruction="",
                completed=True,
                message="No further steps.",
            )

        adapter = self.adapters.get(next_step.agent)
        if adapter is not None:
            return self._run_with_adapter(task, next_step, adapter)

        return StepResult(
            task_name=task_name,
            agent=next_step.agent,
            artifact=next_step.artifact,
            instruction=next_step.instruction,
            completed=False,
            message=self._format_manual_instruction(task, next_step),
        )

    def _run_with_adapter(
        self, task: Task, step: NextStep, adapter: AgentAdapter
    ) -> StepResult:
        """Delegate step execution to an agent adapter."""
        from ..domain.models import ExecutionStatus

        try:
            result = adapter.execute(
                task_name=task.name,
                artifact=step.artifact,
                template=step.template,
                instruction=step.instruction,
                context={
                    "task": task.to_dict(),
                    "cycle": task.cycle,
                    "target_repo": task.target_repo,
                },
            )
            completed = result.status == ExecutionStatus.COMPLETED
            return StepResult(
                task_name=task.name,
                agent=step.agent,
                artifact=step.artifact,
                instruction=step.instruction,
                completed=completed,
                message=result.message or f"Adapter '{adapter.name}': {result.status.value}",
            )
        except Exception as e:
            return StepResult(
                task_name=task.name,
                agent=step.agent,
                artifact=step.artifact,
                instruction=step.instruction,
                completed=False,
                message=f"Adapter '{adapter.name}' failed: {e}",
            )

    def _format_manual_instruction(self, task: Task, step: NextStep) -> str:
        lines = [
            f"=== NEXT STEP: {task.name} ===",
            f"State:    {task.state.value}",
            f"Cycle:    {task.cycle}",
            f"Agent:    {step.agent.value}",
            f"Artifact: {step.artifact}",
            f"Template: {step.template}",
            "",
            "INSTRUCTION:",
            step.instruction,
            "",
            "After completing this step:",
            f"  1. Write the artifact file: {step.artifact}",
            f"  2. Run: {task_advance_shell(task.name)}",
        ]
        return "\n".join(lines)

    def get_task_summary(self, task_name: str) -> dict:
        """Generate a summary of the current task state."""
        task = self.task_service.get_task(task_name)
        artifacts = self.artifact_service.list_existing(task_name)
        validation = self.artifact_service.validate(task)
        next_step = resolve_next_step(task)

        return {
            "task_name": task.name,
            "target_repo": task.target_repo,
            "state": task.state.value,
            "cycle": task.cycle,
            "max_cycles": task.max_cycles,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "artifacts": artifacts,
            "missing_required": validation.missing_required,
            "next_step": {
                "agent": next_step.agent.value,
                "artifact": next_step.artifact,
                "instruction": next_step.instruction,
            }
            if next_step
            else None,
            "history_count": len(task.history),
        }
