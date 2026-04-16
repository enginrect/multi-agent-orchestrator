"""Core domain models for the multi-agent orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class AgentRole(str, Enum):
    """Agents participating in the review workflow."""

    CURSOR = "cursor"
    CLAUDE = "claude"
    CODEX = "codex"


class TaskState(str, Enum):
    """Explicit states a task can be in.

    The state machine enforces valid transitions between these states.
    Terminal states: APPROVED, ESCALATED, ARCHIVED.
    """

    INITIALIZED = "initialized"
    CURSOR_IMPLEMENTING = "cursor_implementing"
    CLAUDE_REVIEWING = "claude_reviewing"
    CURSOR_REWORKING = "cursor_reworking"
    CODEX_REVIEWING = "codex_reviewing"
    APPROVED = "approved"
    ESCALATED = "escalated"
    ARCHIVED = "archived"


class ReviewOutcome(str, Enum):
    """Possible outcomes from a review phase."""

    APPROVED = "approved"
    MINOR_FIXES_APPLIED = "minor-fixes-applied"
    CHANGES_REQUESTED = "changes-requested"


class RunStatus(str, Enum):
    """Execution-level status for the run orchestrator.

    Orthogonal to TaskState: TaskState tracks *where* in the review pipeline
    the task is; RunStatus tracks *what the execution engine is doing*.
    """

    IDLE = "idle"
    RUNNING = "running"
    WAITING_ON_CURSOR = "waiting_on_cursor"
    WAITING_ON_CLAUDE = "waiting_on_claude"
    WAITING_ON_CODEX = "waiting_on_codex"
    COMPLETED = "completed"
    SUSPENDED = "suspended"


class AdapterCapability(str, Enum):
    """What an adapter can do when invoked."""

    MANUAL = "manual"        # Generates instructions; human completes the step
    SEMI_AUTO = "semi_auto"  # Starts execution but may need human follow-up
    AUTOMATIC = "automatic"  # Fully autonomous; completes without intervention


class ExecutionStatus(str, Enum):
    """Outcome of a single adapter invocation."""

    COMPLETED = "completed"  # Artifact written, step done
    WAITING = "waiting"      # Needs external completion (manual adapter)
    FAILED = "failed"        # Step failed, run should suspend


@dataclass
class ExecutionResult:
    """Returned by an adapter after attempting to execute a step."""

    status: ExecutionStatus
    artifact_written: bool = False
    message: str = ""
    review_outcome: Optional[ReviewOutcome] = None


@dataclass
class ArtifactSpec:
    """Specification for a single artifact in the workflow.

    Attributes:
        name: Template name (e.g. "00-scope.md").
        filename_pattern: Actual filename in a task directory. May include
            cycle number placeholder ``{cycle}``.
        author: Which agent produces this artifact.
        required: Whether the artifact must exist before the workflow can advance.
        description: Human-readable purpose.
    """

    name: str
    filename_pattern: str
    author: AgentRole
    required: bool
    description: str

    def filename(self, cycle: int = 1) -> str:
        return self.filename_pattern.format(cycle=cycle)


@dataclass
class StateTransition:
    """Record of a single state change."""

    from_state: TaskState
    to_state: TaskState
    timestamp: str
    artifact: Optional[str] = None
    review_outcome: Optional[str] = None
    note: Optional[str] = None


@dataclass
class Task:
    """A review task managed by the orchestrator.

    This is the aggregate root. All task state is serializable to/from
    ``state.yaml`` in the task directory.
    """

    name: str
    target_repo: str
    state: TaskState = TaskState.INITIALIZED
    run_status: RunStatus = RunStatus.IDLE
    cycle: int = 1
    max_cycles: int = 2
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    description: str = ""
    history: list[StateTransition] = field(default_factory=list)

    def record_transition(
        self,
        new_state: TaskState,
        artifact: Optional[str] = None,
        review_outcome: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        """Append a state transition to history and update current state."""
        transition = StateTransition(
            from_state=self.state,
            to_state=new_state,
            timestamp=_now_iso(),
            artifact=artifact,
            review_outcome=review_outcome,
            note=note,
        )
        self.history.append(transition)
        self.state = new_state
        self.updated_at = _now_iso()

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            TaskState.APPROVED,
            TaskState.ESCALATED,
            TaskState.ARCHIVED,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target_repo": self.target_repo,
            "state": self.state.value,
            "run_status": self.run_status.value,
            "cycle": self.cycle,
            "max_cycles": self.max_cycles,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "history": [
                {
                    "from_state": t.from_state.value,
                    "to_state": t.to_state.value,
                    "timestamp": t.timestamp,
                    "artifact": t.artifact,
                    "review_outcome": t.review_outcome,
                    "note": t.note,
                }
                for t in self.history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        history = [
            StateTransition(
                from_state=TaskState(h["from_state"]),
                to_state=TaskState(h["to_state"]),
                timestamp=h["timestamp"],
                artifact=h.get("artifact"),
                review_outcome=h.get("review_outcome"),
                note=h.get("note"),
            )
            for h in data.get("history", [])
        ]
        return cls(
            name=data["name"],
            target_repo=data["target_repo"],
            state=TaskState(data["state"]),
            run_status=RunStatus(data.get("run_status", "idle")),
            cycle=data.get("cycle", 1),
            max_cycles=data.get("max_cycles", 2),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            description=data.get("description", ""),
            history=history,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
