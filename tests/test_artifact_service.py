"""Tests for artifact validation and review outcome parsing."""

from pathlib import Path

import pytest

from orchestrator.application.artifact_service import ArtifactService
from orchestrator.domain.models import ReviewOutcome, Task, TaskState
from orchestrator.infrastructure.file_state_store import FileStateStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def store(workspace: Path) -> FileStateStore:
    s = FileStateStore(workspace)
    s.ensure_workspace()
    return s


@pytest.fixture
def service(store: FileStateStore) -> ArtifactService:
    return ArtifactService(store)


@pytest.fixture
def task_with_artifacts(store: FileStateStore) -> Task:
    """Create a task in claude_reviewing state with scope and impl artifacts."""
    task = Task(
        name="test-task",
        target_repo="/tmp/repo",
        state=TaskState.CLAUDE_REVIEWING,
        cycle=1,
    )
    store.create_task_dir("test-task")
    store.save_task(task)
    store.write_artifact("test-task", "00-scope.md", "# Scope\n")
    store.write_artifact("test-task", "01-cursor-implementation.md", "# Impl\n")
    return task


class TestValidate:
    def test_missing_required_artifacts(
        self, service: ArtifactService, store: FileStateStore
    ) -> None:
        task = Task(name="empty", target_repo="/tmp", state=TaskState.INITIALIZED)
        store.create_task_dir("empty")
        store.save_task(task)
        result = service.validate(task)
        assert not result.is_valid
        assert "00-scope.md" in result.missing_required

    def test_some_artifacts_present(
        self, service: ArtifactService, task_with_artifacts: Task
    ) -> None:
        result = service.validate(task_with_artifacts)
        assert not result.is_valid
        existing = [a.filename for a in result.artifacts if a.exists]
        assert "00-scope.md" in existing
        assert "01-cursor-implementation.md" in existing

    def test_optional_artifacts_not_in_missing(
        self, service: ArtifactService, task_with_artifacts: Task
    ) -> None:
        result = service.validate(task_with_artifacts)
        assert "03-cursor-response-cycle-1.md" not in result.missing_required
        assert "05-final-approval.md" not in result.missing_required


class TestReadReviewOutcome:
    def test_parse_approved(
        self, service: ArtifactService, store: FileStateStore
    ) -> None:
        store.create_task_dir("outcome-test")
        store.write_artifact(
            "outcome-test",
            "02-claude-review-cycle-1.md",
            "# Review\n\n**Status**: approved\n",
        )
        outcome = service.read_review_outcome(
            "outcome-test", "02-claude-review-cycle-1.md"
        )
        assert outcome == ReviewOutcome.APPROVED

    def test_parse_changes_requested(
        self, service: ArtifactService, store: FileStateStore
    ) -> None:
        store.create_task_dir("outcome-test")
        store.write_artifact(
            "outcome-test",
            "02-claude-review-cycle-1.md",
            "# Review\n\n**Status**: changes-requested\n",
        )
        outcome = service.read_review_outcome(
            "outcome-test", "02-claude-review-cycle-1.md"
        )
        assert outcome == ReviewOutcome.CHANGES_REQUESTED

    def test_parse_minor_fixes(
        self, service: ArtifactService, store: FileStateStore
    ) -> None:
        store.create_task_dir("outcome-test")
        store.write_artifact(
            "outcome-test",
            "02-claude-review-cycle-1.md",
            "# Review\n\n**Status**: minor-fixes-applied\n",
        )
        outcome = service.read_review_outcome(
            "outcome-test", "02-claude-review-cycle-1.md"
        )
        assert outcome == ReviewOutcome.MINOR_FIXES_APPLIED

    def test_no_status_returns_none(
        self, service: ArtifactService, store: FileStateStore
    ) -> None:
        store.create_task_dir("outcome-test")
        store.write_artifact(
            "outcome-test",
            "02-claude-review-cycle-1.md",
            "# Review\n\nNo status here.\n",
        )
        outcome = service.read_review_outcome(
            "outcome-test", "02-claude-review-cycle-1.md"
        )
        assert outcome is None

    def test_missing_artifact_returns_none(
        self, service: ArtifactService, store: FileStateStore
    ) -> None:
        store.create_task_dir("outcome-test")
        outcome = service.read_review_outcome(
            "outcome-test", "nonexistent.md"
        )
        assert outcome is None


class TestListExisting:
    def test_lists_artifact_files(
        self, service: ArtifactService, task_with_artifacts: Task
    ) -> None:
        artifacts = service.list_existing("test-task")
        assert "00-scope.md" in artifacts
        assert "01-cursor-implementation.md" in artifacts
        assert "state.yaml" not in artifacts
