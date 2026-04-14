"""Markdown-prompt driven execution.

Reads a markdown file as the initial task prompt and drives it through
the configured agent pipeline. The first agent receives the markdown
content as its instruction; subsequent agents review the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..adapters.base import AgentAdapter
from ..domain.models import (
    AgentRole,
    ExecutionStatus,
    RunStatus,
    _now_iso,
)
from ..infrastructure.config_loader import AgentsConfig
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.run_logger import RunLogger


@dataclass
class PromptStepRecord:
    agent: str
    role: str
    status: ExecutionStatus
    message: str
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class PromptRunResult:
    prompt_file: str
    task_name: str
    run_status: RunStatus
    steps: list[PromptStepRecord] = field(default_factory=list)
    waiting_on: Optional[str] = None
    message: str = ""

    @property
    def is_complete(self) -> bool:
        return self.run_status == RunStatus.COMPLETED


def _agent_name_to_role(name: str) -> AgentRole:
    return AgentRole(name)


class PromptRunner:
    """Drive a markdown prompt through the configured agent pipeline."""

    def __init__(
        self,
        store: FileStateStore,
        agents_config: AgentsConfig,
        adapters: dict[AgentRole, AgentAdapter],
        fallback_adapter: Optional[AgentAdapter] = None,
    ) -> None:
        self.store = store
        self.agents_config = agents_config
        self.adapters = adapters
        self.fallback_adapter = fallback_adapter

    def _get_adapter(self, agent: AgentRole) -> Optional[AgentAdapter]:
        adapter = self.adapters.get(agent)
        if adapter is not None:
            return adapter
        return self.fallback_adapter

    def run(
        self,
        prompt_path: str | Path,
        task_name: Optional[str] = None,
        target_repo: str = "",
        on_step: Optional[Callable[[str], None]] = None,
    ) -> PromptRunResult:
        """Execute the prompt through the agent pipeline.

        Args:
            prompt_path: Path to the markdown prompt file.
            task_name: Optional override; defaults to the filename stem.
            target_repo: Target repository path for the agents.
            on_step: Progress callback.
        """
        path = Path(prompt_path)
        if not path.is_file():
            return PromptRunResult(
                prompt_file=str(path),
                task_name=task_name or path.stem,
                run_status=RunStatus.SUSPENDED,
                message=f"Prompt file not found: {path}",
            )

        prompt_content = path.read_text()
        if not task_name:
            task_name = f"prompt-{path.stem}"

        self.store.ensure_workspace()
        self.store.create_task_dir(task_name)

        logger = RunLogger(self.store.task_dir(task_name))
        logger.log("prompt_run_start", prompt_file=str(path), task_name=task_name)

        prompt_copy = self.store.task_dir(task_name) / "prompt.md"
        prompt_copy.write_text(prompt_content)

        steps: list[PromptStepRecord] = []
        enabled = self.agents_config.enabled

        for i, agent_name in enumerate(enabled):
            role = _agent_name_to_role(agent_name)
            is_first = i == 0
            phase = "implement" if is_first else "review"

            adapter = self._get_adapter(role)
            if adapter is None:
                if on_step:
                    on_step(f"No adapter for {agent_name} — run suspended")
                return PromptRunResult(
                    prompt_file=str(path),
                    task_name=task_name,
                    run_status=RunStatus.SUSPENDED,
                    steps=steps,
                    message=f"No adapter configured for {agent_name}",
                )

            if is_first:
                instruction = prompt_content
                artifact = f"01-{agent_name}-implementation"
            else:
                instruction = (
                    f"Review the work done by the previous agent(s). "
                    f"The original prompt is in prompt.md. "
                    f"Check quality, correctness, and completeness."
                )
                artifact = f"{i + 1:02d}-{agent_name}-review"

            context: dict[str, Any] = {
                "cycle": 1,
                "target_repo": target_repo,
                "agent": agent_name,
                "agent_order": enabled,
                "agent_index": i,
                "phase": phase,
            }

            if on_step:
                on_step(f"[{agent_name}] Starting {phase}...")

            logger.log(
                "prompt_step_start",
                agent=agent_name,
                phase=phase,
                adapter=adapter.name,
            )

            result = adapter.execute(
                task_name=task_name,
                artifact=artifact,
                template="",
                instruction=instruction,
                context=context,
            )

            steps.append(PromptStepRecord(
                agent=agent_name,
                role=phase,
                status=result.status,
                message=result.message,
            ))

            if result.status == ExecutionStatus.COMPLETED:
                if on_step:
                    on_step(f"[{agent_name}] Completed {phase}")
                logger.log("prompt_step_completed", agent=agent_name, phase=phase)
                continue

            if result.status == ExecutionStatus.WAITING:
                if on_step:
                    on_step(
                        f"[{agent_name}] Waiting for manual completion. "
                        f"Resume: morch resume {task_name}"
                    )
                return PromptRunResult(
                    prompt_file=str(path),
                    task_name=task_name,
                    run_status=RunStatus.SUSPENDED,
                    steps=steps,
                    waiting_on=agent_name,
                    message=result.message,
                )

            if on_step:
                on_step(f"[{agent_name}] Failed: {result.message}")
            return PromptRunResult(
                prompt_file=str(path),
                task_name=task_name,
                run_status=RunStatus.SUSPENDED,
                steps=steps,
                message=f"Agent {agent_name} failed: {result.message}",
            )

        logger.log("prompt_run_complete", steps=len(steps))
        if on_step:
            on_step("All agents completed successfully")

        return PromptRunResult(
            prompt_file=str(path),
            task_name=task_name,
            run_status=RunStatus.COMPLETED,
            steps=steps,
            message="All agents completed the prompt pipeline",
        )
