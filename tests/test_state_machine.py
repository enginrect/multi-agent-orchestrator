"""Tests for the task state machine."""

import pytest

from orchestrator.domain.errors import InvalidTransitionError
from orchestrator.domain.models import TaskState
from orchestrator.domain.state_machine import (
    TRANSITIONS,
    get_allowed_transitions,
    is_terminal,
    validate_transition,
)


class TestTransitions:
    """Verify every declared transition is valid and undeclared ones raise."""

    def test_initialized_to_cursor_implementing(self) -> None:
        validate_transition(TaskState.INITIALIZED, TaskState.CURSOR_IMPLEMENTING)

    def test_cursor_implementing_to_claude_reviewing(self) -> None:
        validate_transition(TaskState.CURSOR_IMPLEMENTING, TaskState.CLAUDE_REVIEWING)

    def test_claude_reviewing_to_codex_reviewing(self) -> None:
        validate_transition(TaskState.CLAUDE_REVIEWING, TaskState.CODEX_REVIEWING)

    def test_claude_reviewing_to_cursor_reworking(self) -> None:
        validate_transition(TaskState.CLAUDE_REVIEWING, TaskState.CURSOR_REWORKING)

    def test_cursor_reworking_to_codex_reviewing(self) -> None:
        validate_transition(TaskState.CURSOR_REWORKING, TaskState.CODEX_REVIEWING)

    def test_cursor_reworking_to_claude_reviewing(self) -> None:
        validate_transition(TaskState.CURSOR_REWORKING, TaskState.CLAUDE_REVIEWING)

    def test_codex_reviewing_to_approved(self) -> None:
        validate_transition(TaskState.CODEX_REVIEWING, TaskState.APPROVED)

    def test_codex_reviewing_to_cursor_reworking(self) -> None:
        validate_transition(TaskState.CODEX_REVIEWING, TaskState.CURSOR_REWORKING)

    def test_codex_reviewing_to_escalated(self) -> None:
        validate_transition(TaskState.CODEX_REVIEWING, TaskState.ESCALATED)

    def test_approved_to_archived(self) -> None:
        validate_transition(TaskState.APPROVED, TaskState.ARCHIVED)


class TestInvalidTransitions:
    """Verify undeclared transitions raise InvalidTransitionError."""

    def test_initialized_to_approved(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(TaskState.INITIALIZED, TaskState.APPROVED)

    def test_archived_to_anything(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(TaskState.ARCHIVED, TaskState.INITIALIZED)

    def test_escalated_to_anything(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(TaskState.ESCALATED, TaskState.CURSOR_IMPLEMENTING)

    def test_cursor_implementing_to_approved(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(TaskState.CURSOR_IMPLEMENTING, TaskState.APPROVED)

    def test_backwards_to_initialized(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(TaskState.CLAUDE_REVIEWING, TaskState.INITIALIZED)


class TestHelpers:
    def test_terminal_states(self) -> None:
        assert is_terminal(TaskState.ARCHIVED)
        assert is_terminal(TaskState.ESCALATED)
        assert not is_terminal(TaskState.INITIALIZED)
        assert not is_terminal(TaskState.CODEX_REVIEWING)

    def test_get_allowed_transitions(self) -> None:
        allowed = get_allowed_transitions(TaskState.CLAUDE_REVIEWING)
        assert TaskState.CODEX_REVIEWING in allowed
        assert TaskState.CURSOR_REWORKING in allowed
        assert len(allowed) == 2

    def test_all_states_have_transition_entry(self) -> None:
        for state in TaskState:
            assert state in TRANSITIONS, f"Missing transition entry for {state}"
