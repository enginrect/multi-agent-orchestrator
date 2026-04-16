"""Tests for the task service (task lifecycle operations)."""

import tempfile
from pathlib import Path

import pytest

from orchestrator.application.artifact_service import ArtifactService
from orchestrator.application.task_service import TaskService
from orchestrator.domain.errors import TaskAlreadyExistsError, TaskNotFoundError
from orchestrator.domain.models import ReviewOutcome, TaskState
from orchestrator.infrastructure.config_loader import OrchestratorConfig
from orchestrator.infrastructure.file_state_store import FileStateStore
from orchestrator.infrastructure.template_renderer import TemplateRenderer


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def template_dir() -> Path:
    return Path(__file__).parent.parent / "templates" / "artifacts"


@pytest.fixture
def service(workspace: Path, template_dir: Path) -> TaskService:
    config = OrchestratorConfig(
        workspace_dir=str(workspace),
        template_dir=str(template_dir),
        max_cycles=2,
    )
    store = FileStateStore(workspace)
    renderer = TemplateRenderer(template_dir)
    artifact_svc = ArtifactService(store)
    return TaskService(config, store, renderer, artifact_svc)


class TestInitTask:
    def test_creates_task_directory(self, service: TaskService, workspace: Path) -> None:
        task = service.init_task("test-task", target_repo="/tmp/repo")
        assert (workspace / "active" / "test-task" / "state.yaml").is_file()
        assert (workspace / "active" / "test-task" / "00-scope.md").is_file()

    def test_initial_state_is_cursor_implementing(self, service: TaskService) -> None:
        task = service.init_task("test-task", target_repo="/tmp/repo")
        assert task.state == TaskState.CURSOR_IMPLEMENTING

    def test_duplicate_task_raises(self, service: TaskService) -> None:
        service.init_task("test-task", target_repo="/tmp/repo")
        with pytest.raises(TaskAlreadyExistsError):
            service.init_task("test-task", target_repo="/tmp/repo")

    def test_task_name_preserved(self, service: TaskService) -> None:
        task = service.init_task("my-review", target_repo="/tmp/repo")
        assert task.name == "my-review"
        assert task.target_repo == "/tmp/repo"

    def test_history_has_initial_transition(self, service: TaskService) -> None:
        task = service.init_task("test-task", target_repo="/tmp/repo")
        assert len(task.history) == 1
        assert task.history[0].from_state == TaskState.INITIALIZED
        assert task.history[0].to_state == TaskState.CURSOR_IMPLEMENTING


class TestGetTask:
    def test_load_existing_task(self, service: TaskService) -> None:
        service.init_task("test-task", target_repo="/tmp/repo")
        loaded = service.get_task("test-task")
        assert loaded.name == "test-task"
        assert loaded.state == TaskState.CURSOR_IMPLEMENTING

    def test_missing_task_raises(self, service: TaskService) -> None:
        with pytest.raises(TaskNotFoundError):
            service.get_task("nonexistent")


class TestAdvance:
    def test_advance_after_implementation_artifact(
        self, service: TaskService, workspace: Path
    ) -> None:
        task = service.init_task("test-task", target_repo="/tmp/repo")
        # Write the implementation artifact
        impl_path = workspace / "active" / "test-task" / "01-cursor-implementation.md"
        impl_path.write_text(
            "# Implementation\n\n**Task**: test-task\n\nDone.\n"
        )
        task = service.advance("test-task")
        assert task.state == TaskState.CLAUDE_REVIEWING

    def test_advance_with_claude_approved(
        self, service: TaskService, workspace: Path
    ) -> None:
        service.init_task("test-task", target_repo="/tmp/repo")
        task_dir = workspace / "active" / "test-task"

        (task_dir / "01-cursor-implementation.md").write_text("# Impl\n")
        service.advance("test-task")

        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        task = service.advance("test-task")
        assert task.state == TaskState.CODEX_REVIEWING

    def test_advance_with_claude_changes_requested(
        self, service: TaskService, workspace: Path
    ) -> None:
        service.init_task("test-task", target_repo="/tmp/repo")
        task_dir = workspace / "active" / "test-task"

        (task_dir / "01-cursor-implementation.md").write_text("# Impl\n")
        service.advance("test-task")

        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: changes-requested\n"
        )
        task = service.advance("test-task")
        assert task.state == TaskState.CURSOR_REWORKING

    def test_full_happy_path(
        self, service: TaskService, workspace: Path
    ) -> None:
        """Walk through the entire cycle 1 happy path."""
        service.init_task("happy-path", target_repo="/tmp/repo")
        task_dir = workspace / "active" / "happy-path"

        # Cursor implementation
        (task_dir / "01-cursor-implementation.md").write_text("# Impl\n")
        service.advance("happy-path")

        # Claude approves
        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        service.advance("happy-path")

        # Codex approves
        (task_dir / "04-codex-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        task = service.advance("happy-path")
        assert task.state == TaskState.APPROVED

    def test_no_advance_without_artifact(self, service: TaskService) -> None:
        """Advance should be a no-op if the expected artifact doesn't exist."""
        service.init_task("test-task", target_repo="/tmp/repo")
        task = service.advance("test-task")
        # Still in cursor_implementing because 01 doesn't exist
        assert task.state == TaskState.CURSOR_IMPLEMENTING


class TestArchive:
    def test_archive_approved_task(
        self, service: TaskService, workspace: Path
    ) -> None:
        service.init_task("arch-test", target_repo="/tmp/repo")
        task_dir = workspace / "active" / "arch-test"

        (task_dir / "01-cursor-implementation.md").write_text("# Impl\n")
        service.advance("arch-test")
        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        service.advance("arch-test")
        (task_dir / "04-codex-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        service.advance("arch-test")

        task = service.archive("arch-test")
        assert task.state == TaskState.ARCHIVED
        assert (workspace / "archive" / "arch-test" / "state.yaml").is_file()
        assert not (workspace / "active" / "arch-test").exists()

    def test_archive_non_approved_raises(self, service: TaskService) -> None:
        service.init_task("not-ready", target_repo="/tmp/repo")
        with pytest.raises(ValueError, match="Only approved"):
            service.archive("not-ready")


class TestListTasks:
    def test_list_active_tasks(self, service: TaskService) -> None:
        service.init_task("task-a", target_repo="/tmp/repo")
        service.init_task("task-b", target_repo="/tmp/repo")
        tasks = service.list_tasks()
        assert "task-a" in tasks["active"]
        assert "task-b" in tasks["active"]
