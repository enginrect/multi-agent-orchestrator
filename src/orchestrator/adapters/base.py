"""Abstract agent adapter interface.

All agent adapters must implement this interface. The orchestrator calls
adapters through this contract, which allows:

- ``ManualAdapter``: generates instructions, returns WAITING (works now)
- ``StubAdapter``: auto-completes steps for testing, returns COMPLETED
- Future: MCP adapter, API adapter, CLI adapter for Cursor/Claude/Codex

Adapters declare their capability level so the run orchestrator knows
whether to expect synchronous completion or an asynchronous wait.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..domain.models import AdapterCapability, ExecutionResult


class AgentAdapter(ABC):
    """Contract for invoking an agent to complete a workflow step.

    Each adapter encapsulates the mechanics of reaching a specific agent
    (manual prompt, MCP call, API request, etc.). The orchestrator only
    depends on this interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name for logging."""

    @property
    @abstractmethod
    def capability(self) -> AdapterCapability:
        """Declare what this adapter can do.

        MANUAL: generates instructions only — run will pause with WAITING.
        SEMI_AUTO: starts execution but may need human follow-up.
        AUTOMATIC: fully autonomous — run continues without intervention.
        """

    @abstractmethod
    def execute(
        self,
        task_name: str,
        artifact: str,
        template: str,
        instruction: str,
        context: dict[str, Any],
    ) -> ExecutionResult:
        """Execute a workflow step and produce the expected artifact.

        Args:
            task_name: Name of the task being processed.
            artifact: Expected output filename (e.g. "02-claude-review-cycle-1.md").
            template: Template filename to use as a starting point.
            instruction: Human-readable instruction describing the step.
            context: Additional context (task state, cycle, target repo, etc.).

        Returns:
            ExecutionResult indicating COMPLETED, WAITING, or FAILED.
        """

    def health_check(self) -> bool:
        """Verify the adapter can reach its agent. Default: always True."""
        return True
