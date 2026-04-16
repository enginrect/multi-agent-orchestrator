"""Explicit state machine for task lifecycle transitions.

Every valid transition is declared here. Attempting an undeclared transition
raises ``InvalidTransitionError``. The state machine is pure logic — no I/O.
"""

from __future__ import annotations

from .errors import InvalidTransitionError
from .models import TaskState

# Allowed transitions: { from_state: [to_state, ...] }
TRANSITIONS: dict[TaskState, list[TaskState]] = {
    TaskState.INITIALIZED: [
        TaskState.CURSOR_IMPLEMENTING,
    ],
    TaskState.CURSOR_IMPLEMENTING: [
        TaskState.CLAUDE_REVIEWING,
    ],
    TaskState.CLAUDE_REVIEWING: [
        TaskState.CODEX_REVIEWING,   # Claude approved / minor-fixes-applied
        TaskState.CURSOR_REWORKING,  # Claude changes-requested
    ],
    TaskState.CURSOR_REWORKING: [
        TaskState.CODEX_REVIEWING,   # Rework done, advance to Codex
        TaskState.CLAUDE_REVIEWING,  # Cycle 2: back to Claude after rework
    ],
    TaskState.CODEX_REVIEWING: [
        TaskState.APPROVED,          # Codex approved
        TaskState.CURSOR_REWORKING,  # Codex changes-requested, cycle < max
        TaskState.ESCALATED,         # Codex changes-requested, cycle >= max
    ],
    TaskState.APPROVED: [
        TaskState.ARCHIVED,
    ],
    # Terminal states — no outgoing transitions
    TaskState.ESCALATED: [],
    TaskState.ARCHIVED: [],
}


def validate_transition(current: TaskState, target: TaskState) -> None:
    """Raise ``InvalidTransitionError`` if the transition is not allowed."""
    allowed = TRANSITIONS.get(current, [])
    if target not in allowed:
        raise InvalidTransitionError(current.value, target.value)


def get_allowed_transitions(current: TaskState) -> list[TaskState]:
    """Return the list of states reachable from ``current``."""
    return list(TRANSITIONS.get(current, []))


def is_terminal(state: TaskState) -> bool:
    """Return True if the state has no outgoing transitions."""
    return len(TRANSITIONS.get(state, [])) == 0
