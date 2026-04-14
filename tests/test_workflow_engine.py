"""Tests for the workflow engine and next-step resolution."""

from pathlib import Path

import pytest

from orchestrator.application.artifact_service import ArtifactService
from orchestrator.application.task_service import TaskService
from orchestrator.application.workflow_engine import WorkflowEngine
from orchestrator.domain.models import AgentRole, ReviewOutcome, Task, TaskState
from orchestrator.domain.workflow import resolve_next_step
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
def engine(workspace: Path, template_dir: Path) -> WorkflowEngine:
    config = OrchestratorConfig(
        workspace_dir=str(workspace),
        template_dir=str(template_dir),
    )
    store = FileStateStore(workspace)
    renderer = TemplateRenderer(template_dir)
    artifact_svc = ArtifactService(store)
    task_svc = TaskService(config, store, renderer, artifact_svc)
    return WorkflowEngine(task_svc, artifact_svc)


class TestResolveNextStep:
    def test_initialized_needs_scope(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.INITIALIZED)
        step = resolve_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CURSOR
        assert step.artifact == "00-scope.md"

    def test_cursor_implementing_needs_impl(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.CURSOR_IMPLEMENTING)
        step = resolve_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CURSOR
        assert step.artifact == "01-cursor-implementation.md"

    def test_claude_reviewing_cycle_1(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.CLAUDE_REVIEWING, cycle=1)
        step = resolve_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CLAUDE
        assert "cycle-1" in step.artifact

    def test_claude_reviewing_cycle_2(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.CLAUDE_REVIEWING, cycle=2)
        step = resolve_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CLAUDE
        assert "cycle-2" in step.artifact

    def test_codex_reviewing_cycle_1(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.CODEX_REVIEWING, cycle=1)
        step = resolve_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CODEX
        assert "04-codex" in step.artifact

    def test_terminal_state_returns_none(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.ARCHIVED)
        assert resolve_next_step(task) is None

    def test_approved_returns_none(self) -> None:
        task = Task(name="t", target_repo="", state=TaskState.APPROVED)
        assert resolve_next_step(task) is None


class TestWorkflowEngine:
    def test_get_task_summary(self, engine: WorkflowEngine, workspace: Path) -> None:
        engine.task_service.init_task("summary-test", target_repo="/tmp/repo")
        summary = engine.get_task_summary("summary-test")
        assert summary["task_name"] == "summary-test"
        assert summary["state"] == "cursor_implementing"
        assert summary["next_step"] is not None
        assert summary["next_step"]["agent"] == "cursor"

    def test_run_next_step_no_adapter(
        self, engine: WorkflowEngine, workspace: Path
    ) -> None:
        engine.task_service.init_task("manual-test", target_repo="/tmp/repo")
        result = engine.run_next_step("manual-test")
        assert not result.completed
        assert "manual-test" in result.message
        assert result.agent == AgentRole.CURSOR

    def test_terminal_task_returns_completed(
        self, engine: WorkflowEngine, workspace: Path
    ) -> None:
        engine.task_service.init_task("terminal-test", target_repo="/tmp/repo")
        task_dir = workspace / "active" / "terminal-test"

        (task_dir / "01-cursor-implementation.md").write_text("# Impl\n")
        engine.task_service.advance("terminal-test")
        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        engine.task_service.advance("terminal-test")
        (task_dir / "04-codex-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        engine.task_service.advance("terminal-test")

        result = engine.run_next_step("terminal-test")
        assert result.completed
        assert "terminal" in result.message.lower() or "approved" in result.message.lower()


class TestEscalation:
    """Verify that exceeding max cycles leads to escalation."""

    def test_cycle_2_changes_requested_escalates(
        self, engine: WorkflowEngine, workspace: Path
    ) -> None:
        svc = engine.task_service
        svc.init_task("esc-test", target_repo="/tmp/repo")
        task_dir = workspace / "active" / "esc-test"

        # Cycle 1: Cursor → Claude → Codex changes-requested
        (task_dir / "01-cursor-implementation.md").write_text("# Impl\n")
        svc.advance("esc-test")
        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        svc.advance("esc-test")
        (task_dir / "04-codex-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: changes-requested\n"
        )
        task = svc.advance("esc-test")
        assert task.state == TaskState.CURSOR_REWORKING
        assert task.cycle == 2

        # Cycle 2: Cursor rework → Codex → changes-requested again
        (task_dir / "07-cursor-response-cycle-2.md").write_text("# Response\n")
        task = svc.advance("esc-test")
        assert task.state == TaskState.CODEX_REVIEWING

        (task_dir / "08-codex-review-cycle-2.md").write_text(
            "# Review\n\n**Status**: changes-requested\n"
        )
        task = svc.advance("esc-test")
        assert task.state == TaskState.ESCALATED
