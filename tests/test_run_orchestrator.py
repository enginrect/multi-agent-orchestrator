"""Tests for the run orchestrator — single-command multi-agent execution.

Covers:
- Full happy path (all auto, cycle 1 approval)
- Rework path (Claude requests changes)
- Cycle 2 escalation
- Waiting/resume with manual adapters
- Mixed adapter capabilities
"""

from pathlib import Path

import pytest

from orchestrator.adapters.manual import ManualAdapter
from orchestrator.adapters.stub import StubAdapter
from orchestrator.application.artifact_service import ArtifactService
from orchestrator.application.run_orchestrator import RunOrchestrator, RunResult
from orchestrator.application.task_service import TaskService
from orchestrator.domain.models import (
    AgentRole,
    ExecutionStatus,
    ReviewOutcome,
    RunStatus,
    TaskState,
)
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
def store(workspace: Path) -> FileStateStore:
    s = FileStateStore(workspace)
    s.ensure_workspace()
    return s


@pytest.fixture
def renderer(template_dir: Path) -> TemplateRenderer:
    return TemplateRenderer(template_dir)


@pytest.fixture
def config(workspace: Path, template_dir: Path) -> OrchestratorConfig:
    return OrchestratorConfig(
        workspace_dir=str(workspace),
        template_dir=str(template_dir),
    )


@pytest.fixture
def task_service(
    config: OrchestratorConfig,
    store: FileStateStore,
    renderer: TemplateRenderer,
) -> TaskService:
    artifact_svc = ArtifactService(store)
    return TaskService(config, store, renderer, artifact_svc)


def _make_orchestrator(
    task_service: TaskService,
    store: FileStateStore,
    adapters: dict[AgentRole, StubAdapter | ManualAdapter],
    fallback: StubAdapter | ManualAdapter | None = None,
) -> RunOrchestrator:
    return RunOrchestrator(
        task_service=task_service,
        artifact_service=ArtifactService(store),
        store=store,
        adapters=adapters,
        fallback_adapter=fallback,
    )


# ======================================================================
# Happy path — all automatic, cycle 1 approval
# ======================================================================


class TestFullAutoHappyPath:
    def test_run_completes_in_one_call(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        result = orch.run("happy", target_repo="/tmp/repo")

        assert result.is_complete
        assert result.final_state == TaskState.APPROVED
        assert result.run_status == RunStatus.COMPLETED
        assert len(result.steps) >= 3  # impl, claude review, codex review

    def test_all_artifacts_written(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        orch.run("artifacts", target_repo="/tmp/repo")

        artifacts = store.list_artifacts("artifacts")
        assert "00-scope.md" in artifacts
        assert "01-cursor-implementation.md" in artifacts
        assert "02-claude-review-cycle-1.md" in artifacts
        assert "04-codex-review-cycle-1.md" in artifacts

    def test_task_state_is_approved(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        orch.run("state-check", target_repo="/tmp/repo")

        task = store.load_task("state-check")
        assert task.state == TaskState.APPROVED
        assert task.run_status == RunStatus.COMPLETED

    def test_on_step_callback_invoked(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        messages: list[str] = []
        orch.run("callback", target_repo="/tmp/repo", on_step=messages.append)

        assert len(messages) > 0
        assert any("Task created" in m for m in messages)
        assert any("Completed" in m for m in messages)


# ======================================================================
# Rework path — Claude requests changes
# ======================================================================


class TestReworkPath:
    def test_claude_changes_requested_triggers_rework(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        """Claude says changes-requested → Cursor reworks → Codex approves."""
        stub = StubAdapter(
            store,
            default_outcome=ReviewOutcome.APPROVED,
            outcome_overrides={
                "02-claude-review": ReviewOutcome.CHANGES_REQUESTED,
            },
        )
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        result = orch.run("rework", target_repo="/tmp/repo")

        assert result.is_complete
        assert result.final_state == TaskState.APPROVED

        artifacts = store.list_artifacts("rework")
        assert "03-cursor-response-cycle-1.md" in artifacts
        assert "04-codex-review-cycle-1.md" in artifacts

    def test_rework_step_recorded(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(
            store,
            default_outcome=ReviewOutcome.APPROVED,
            outcome_overrides={
                "02-claude-review": ReviewOutcome.CHANGES_REQUESTED,
            },
        )
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        result = orch.run("rework-steps", target_repo="/tmp/repo")

        agent_sequence = [s.agent for s in result.steps]
        assert AgentRole.CURSOR in agent_sequence
        assert AgentRole.CLAUDE in agent_sequence
        assert AgentRole.CODEX in agent_sequence


# ======================================================================
# Escalation — cycle 2 Codex rejection
# ======================================================================


class TestEscalation:
    def test_cycle_2_codex_rejection_escalates(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        """Codex rejects in cycle 1 and cycle 2 → task escalated."""
        stub = StubAdapter(
            store,
            default_outcome=ReviewOutcome.APPROVED,
            outcome_overrides={
                "04-codex-review": ReviewOutcome.CHANGES_REQUESTED,
                "08-codex-review": ReviewOutcome.CHANGES_REQUESTED,
            },
        )
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        result = orch.run("escalate", target_repo="/tmp/repo")

        assert result.is_complete
        assert result.final_state == TaskState.ESCALATED
        assert result.run_status == RunStatus.COMPLETED

    def test_escalated_task_has_cycle_2(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(
            store,
            default_outcome=ReviewOutcome.APPROVED,
            outcome_overrides={
                "04-codex-review": ReviewOutcome.CHANGES_REQUESTED,
                "08-codex-review": ReviewOutcome.CHANGES_REQUESTED,
            },
        )
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        orch.run("esc-cycle", target_repo="/tmp/repo")

        task = store.load_task("esc-cycle")
        assert task.cycle == 2
        assert task.state == TaskState.ESCALATED


# ======================================================================
# Waiting / Resume with manual adapters
# ======================================================================


class TestWaitingResume:
    def test_manual_adapter_causes_waiting(
        self,
        task_service: TaskService,
        store: FileStateStore,
        renderer: TemplateRenderer,
    ) -> None:
        manual = ManualAdapter(store, renderer, output=__import__("io").StringIO())
        orch = _make_orchestrator(task_service, store, {}, fallback=manual)

        result = orch.run("manual-wait", target_repo="/tmp/repo")

        assert result.is_waiting
        assert result.waiting_on == AgentRole.CURSOR
        assert result.run_status == RunStatus.WAITING_ON_CURSOR

    def test_resume_after_manual_completion(
        self,
        task_service: TaskService,
        store: FileStateStore,
        renderer: TemplateRenderer,
    ) -> None:
        """Manual run → wait → write artifact externally → resume → wait again."""
        manual = ManualAdapter(store, renderer, output=__import__("io").StringIO())
        orch = _make_orchestrator(task_service, store, {}, fallback=manual)

        # First run pauses at cursor step
        result1 = orch.run("resume-test", target_repo="/tmp/repo")
        assert result1.is_waiting
        assert result1.waiting_on == AgentRole.CURSOR

        # Simulate human writing the implementation artifact
        task_dir = store.task_dir("resume-test")
        (task_dir / "01-cursor-implementation.md").write_text(
            "# Impl\n\n**Task**: resume-test\n\nDone.\n"
        )

        # Resume — should advance past cursor, pause at claude
        result2 = orch.resume("resume-test")
        assert result2.is_waiting
        assert result2.waiting_on == AgentRole.CLAUDE

    def test_resume_through_full_manual_flow(
        self,
        task_service: TaskService,
        store: FileStateStore,
        renderer: TemplateRenderer,
    ) -> None:
        """Walk through full manual flow with multiple resume calls."""
        manual = ManualAdapter(store, renderer, output=__import__("io").StringIO())
        orch = _make_orchestrator(task_service, store, {}, fallback=manual)
        task_dir_path = store.active_dir / "full-manual"

        # run → wait on cursor
        orch.run("full-manual", target_repo="/tmp/repo")

        # Write impl
        (task_dir_path / "01-cursor-implementation.md").write_text("# Impl\n")
        r = orch.resume("full-manual")
        assert r.waiting_on == AgentRole.CLAUDE

        # Write claude review (approved)
        (task_dir_path / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        r = orch.resume("full-manual")
        assert r.waiting_on == AgentRole.CODEX

        # Write codex review (approved)
        (task_dir_path / "04-codex-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )
        r = orch.resume("full-manual")
        assert r.is_complete
        assert r.final_state == TaskState.APPROVED

    def test_run_status_persisted_in_state_yaml(
        self,
        task_service: TaskService,
        store: FileStateStore,
        renderer: TemplateRenderer,
    ) -> None:
        manual = ManualAdapter(store, renderer, output=__import__("io").StringIO())
        orch = _make_orchestrator(task_service, store, {}, fallback=manual)

        orch.run("persist-check", target_repo="/tmp/repo")

        task = store.load_task("persist-check")
        assert task.run_status == RunStatus.WAITING_ON_CURSOR


# ======================================================================
# Mixed adapter capabilities
# ======================================================================


class TestMixedAdapters:
    def test_auto_cursor_manual_claude(
        self,
        task_service: TaskService,
        store: FileStateStore,
        renderer: TemplateRenderer,
    ) -> None:
        """Cursor is automatic, Claude is manual → run completes cursor, waits on claude."""
        auto_stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        manual = ManualAdapter(store, renderer, output=__import__("io").StringIO())

        orch = _make_orchestrator(
            task_service,
            store,
            adapters={
                AgentRole.CURSOR: auto_stub,
                AgentRole.CLAUDE: manual,
                AgentRole.CODEX: auto_stub,
            },
        )

        result = orch.run("mixed", target_repo="/tmp/repo")

        assert result.is_waiting
        assert result.waiting_on == AgentRole.CLAUDE
        assert len(result.steps) >= 1

        # Verify cursor completed automatically
        cursor_steps = [s for s in result.steps if s.agent == AgentRole.CURSOR]
        assert any(s.status == ExecutionStatus.COMPLETED for s in cursor_steps)

    def test_mixed_resume_completes(
        self,
        task_service: TaskService,
        store: FileStateStore,
        renderer: TemplateRenderer,
    ) -> None:
        """After manual Claude step, resume completes with auto Codex."""
        auto_stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        manual = ManualAdapter(store, renderer, output=__import__("io").StringIO())

        orch = _make_orchestrator(
            task_service,
            store,
            adapters={
                AgentRole.CURSOR: auto_stub,
                AgentRole.CLAUDE: manual,
                AgentRole.CODEX: auto_stub,
            },
        )

        orch.run("mixed-resume", target_repo="/tmp/repo")

        # Write claude review
        task_dir = store.task_dir("mixed-resume")
        (task_dir / "02-claude-review-cycle-1.md").write_text(
            "# Review\n\n**Status**: approved\n"
        )

        result = orch.resume("mixed-resume")
        assert result.is_complete
        assert result.final_state == TaskState.APPROVED

    def test_no_adapter_suspends(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        """If no adapter at all for an agent, run suspends."""
        orch = _make_orchestrator(task_service, store, {}, fallback=None)

        result = orch.run("no-adapter", target_repo="/tmp/repo")

        assert result.run_status == RunStatus.SUSPENDED
        assert "No adapter" in result.message


# ======================================================================
# Resume on terminal task
# ======================================================================


class TestResumeTerminal:
    def test_resume_on_approved_task(
        self, task_service: TaskService, store: FileStateStore
    ) -> None:
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = _make_orchestrator(task_service, store, {}, fallback=stub)

        orch.run("already-done", target_repo="/tmp/repo")

        result = orch.resume("already-done")
        assert result.is_complete
        assert "terminal" in result.message.lower()
