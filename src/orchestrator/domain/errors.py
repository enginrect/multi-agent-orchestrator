"""Domain-specific exceptions for the orchestrator."""

from __future__ import annotations

import re
from typing import Optional


class OrchestratorError(Exception):
    """Base exception for all orchestrator errors."""


class InvalidTransitionError(OrchestratorError):
    """Raised when a state transition is not allowed."""

    def __init__(self, current_state: str, target_state: str) -> None:
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Cannot transition from '{current_state}' to '{target_state}'"
        )


class TaskNotFoundError(OrchestratorError):
    """Raised when a task directory or state file does not exist."""

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        super().__init__(f"Task not found: '{task_name}'")


class TaskAlreadyExistsError(OrchestratorError):
    """Raised when trying to create a task that already exists."""

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        super().__init__(f"Task already exists: '{task_name}'")


class ArtifactMissingError(OrchestratorError):
    """Raised when a required artifact file is missing."""

    def __init__(self, artifact_name: str, task_name: str) -> None:
        self.artifact_name = artifact_name
        self.task_name = task_name
        super().__init__(
            f"Required artifact '{artifact_name}' missing in task '{task_name}'"
        )


class MaxCyclesExceededError(OrchestratorError):
    """Raised when the maximum number of review cycles is reached."""

    def __init__(self, task_name: str, max_cycles: int) -> None:
        self.task_name = task_name
        self.max_cycles = max_cycles
        super().__init__(
            f"Task '{task_name}' exceeded maximum {max_cycles} review cycles. "
            f"Escalate to human."
        )


class WorkflowConfigError(OrchestratorError):
    """Raised when workflow configuration is invalid."""


class AgentResourceLimitError(OrchestratorError):
    """Base class for agent resource limit failures (tokens, rate, quota, etc.)."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        retry_after: Optional[int] = None,
    ) -> None:
        self.agent_name = agent_name
        self.message = message
        self.retry_after = retry_after
        detail = f"Agent '{agent_name}': {message}"
        if retry_after is not None:
            detail = f"{detail} (retry after {retry_after}s)"
        super().__init__(detail)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(agent_name={self.agent_name!r}, "
            f"message={self.message!r}, retry_after={self.retry_after!r})"
        )


class AgentTokenLimitError(AgentResourceLimitError):
    """Raised when token or context length limits are exceeded."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(agent_name, message, retry_after)


class AgentRateLimitError(AgentResourceLimitError):
    """Raised when API rate limits are exceeded."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(agent_name, message, retry_after)


class AgentQuotaLimitError(AgentResourceLimitError):
    """Raised when quota or billing limits are exceeded."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(agent_name, message, retry_after)


class AgentProviderRefusalError(AgentResourceLimitError):
    """Raised when the provider refuses the request due to capacity or limits."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(agent_name, message, retry_after)


def _parse_retry_after_seconds(stderr: str) -> Optional[int]:
    """Best-effort extraction of Retry-After style hints from stderr (seconds)."""
    text = stderr.lower()
    # e.g. "retry after 60", "retry-after: 120"
    m = re.search(r"retry[- ]after[:\s]+(\d+)", text)
    if m:
        return int(m.group(1))
    return None


def classify_resource_error(
    agent: str, stderr: str, exit_code: int
) -> Optional[AgentResourceLimitError]:
    """Classify stderr from an agent command as a resource-limit error, if possible.

    Patterns are checked in order: token/context, rate limit, quota/billing,
    provider refusal. HTTP status substrings and exit codes 429 / 503 are used
    as signals. Returns ``None`` when nothing matches.
    """
    lowered = stderr.lower()
    retry_after = _parse_retry_after_seconds(stderr)

    token_patterns = (
        "token limit",
        "context length",
        "max tokens",
    )
    if any(p in lowered for p in token_patterns):
        return AgentTokenLimitError(
            agent, "Token or context limit exceeded (matched stderr pattern)", retry_after
        )

    rate_patterns = (
        "rate limit",
        "too many requests",
        "429",
    )
    if any(p in lowered for p in rate_patterns) or exit_code == 429:
        return AgentRateLimitError(
            agent, "Rate limit exceeded (matched stderr or HTTP 429)", retry_after
        )

    quota_patterns = (
        "quota",
        "billing",
        "insufficient credits",
        "exceeded your",
    )
    if any(p in lowered for p in quota_patterns):
        return AgentQuotaLimitError(
            agent, "Quota or billing limit exceeded (matched stderr pattern)", retry_after
        )

    refusal_patterns = (
        "refused",
        "capacity",
        "overloaded",
        "503",
    )
    if any(p in lowered for p in refusal_patterns) or exit_code == 503:
        return AgentProviderRefusalError(
            agent,
            "Provider refused or overloaded (matched stderr or HTTP 503)",
            retry_after,
        )

    return None
