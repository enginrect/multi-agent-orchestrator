"""Domain-specific exceptions for the orchestrator."""


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
