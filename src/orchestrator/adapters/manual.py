"""Manual adapter — generates instructions for human operators.

This adapter does not invoke any agent automatically. Instead, it writes
an instruction file to the task directory and prints guidance to stdout.
A human or external agent reads the instruction, performs the work, and
writes the artifact file. The orchestrator then detects the artifact and
advances the workflow on ``resume``.

Capability: MANUAL — the run orchestrator will pause and return WAITING.
"""

from __future__ import annotations

import io
import sys
from typing import Any

from ..domain.models import (
    AdapterCapability,
    ExecutionResult,
    ExecutionStatus,
)
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.template_renderer import TemplateRenderer
from .base import AgentAdapter


class ManualAdapter(AgentAdapter):
    """Generates instructions for human operators to follow."""

    def __init__(
        self,
        store: FileStateStore,
        renderer: TemplateRenderer,
        output: Any = None,
    ) -> None:
        self._store = store
        self._renderer = renderer
        self._output = output or sys.stdout

    @property
    def name(self) -> str:
        return "manual"

    @property
    def capability(self) -> AdapterCapability:
        return AdapterCapability.MANUAL

    def execute(
        self,
        task_name: str,
        artifact: str,
        template: str,
        instruction: str,
        context: dict[str, Any],
    ) -> ExecutionResult:
        """Write an instruction file, pre-populate artifact, return WAITING."""
        task_dir = self._store.task_dir(task_name)
        variables = {
            "short name matching the directory name": task_name,
            "short name": task_name,
            "YYYY-MM-DD": context.get("date", ""),
            "1 or 2": str(context.get("cycle", 1)),
        }
        try:
            content = self._renderer.render(template, variables)
        except FileNotFoundError:
            content = f"# {artifact}\n\n<!-- Fill in this artifact -->\n"

        artifact_path = task_dir / artifact
        if not artifact_path.exists():
            self._store.write_artifact(task_name, artifact, content)

        instruction_file = task_dir / f".instruction-{artifact}.md"
        instruction_content = (
            f"# Instruction for: {artifact}\n\n"
            f"**Task**: {task_name}\n"
            f"**Agent**: {context.get('agent', 'unknown')}\n"
            f"**Cycle**: {context.get('cycle', 1)}\n\n"
            f"## What to do\n\n{instruction}\n\n"
            f"## Artifact file\n\n"
            f"Edit `{artifact}` in this directory, then run:\n\n"
            f"```bash\norchestrator resume {task_name}\n```\n"
        )
        instruction_file.write_text(instruction_content)

        msg = (
            f"Manual step required for {artifact}. "
            f"Edit the file, then run: orchestrator resume {task_name}"
        )

        if not isinstance(self._output, io.StringIO):
            self._output.write(
                f"\n--- Manual step required ---\n"
                f"Task:     {task_name}\n"
                f"Artifact: {artifact}\n"
                f"Template: {artifact_path}\n\n"
                f"{instruction}\n\n"
                f"After completing, run: orchestrator resume {task_name}\n"
                f"---\n\n"
            )

        return ExecutionResult(
            status=ExecutionStatus.WAITING,
            artifact_written=False,
            message=msg,
        )
