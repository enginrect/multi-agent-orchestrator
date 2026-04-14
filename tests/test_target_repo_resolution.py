"""Tests for target_repo path resolution.

Ensures relative paths are resolved to absolute at the CLI boundary,
absolute paths remain unchanged, and the resolved path flows through
to task state and adapter context.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import os

import pytest

from orchestrator.cli import _resolve_target_repo
from orchestrator.adapters.stub import StubAdapter
from orchestrator.application.artifact_service import ArtifactService
from orchestrator.application.run_orchestrator import RunOrchestrator
from orchestrator.application.task_service import TaskService
from orchestrator.domain.models import AgentRole, ReviewOutcome, RunStatus, TaskState
from orchestrator.infrastructure.config_loader import OrchestratorConfig
from orchestrator.infrastructure.file_state_store import FileStateStore
from orchestrator.infrastructure.template_renderer import TemplateRenderer


# ======================================================================
# _resolve_target_repo unit tests
# ======================================================================


class TestResolveTargetRepo:
    def test_relative_path_resolves_to_absolute(self):
        result = _resolve_target_repo("../some-repo")
        assert Path(result).is_absolute()
        assert result == str(Path("../some-repo").resolve())

    def test_absolute_path_unchanged(self):
        result = _resolve_target_repo("/Users/me/repos/my-repo")
        assert result == "/Users/me/repos/my-repo"

    def test_empty_string_passthrough(self):
        result = _resolve_target_repo("")
        assert result == ""

    def test_dot_relative_resolves(self):
        result = _resolve_target_repo("./my-repo")
        assert Path(result).is_absolute()
        assert result.endswith("/my-repo")

    def test_bare_name_resolves_to_cwd_child(self):
        """A bare directory name resolves to CWD/name."""
        result = _resolve_target_repo("workload-cluster-add-on")
        expected = str(Path.cwd() / "workload-cluster-add-on")
        assert result == expected

    def test_resolve_is_canonical(self, tmp_path):
        """Redundant components like /foo/bar/../baz are resolved."""
        p = tmp_path / "a" / "b" / ".." / "c"
        result = _resolve_target_repo(str(p))
        assert ".." not in result
        assert result == str(tmp_path / "a" / "c")


# ======================================================================
# Integration: task state stores resolved absolute path
# ======================================================================


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
def task_service(
    workspace: Path, template_dir: Path, store: FileStateStore, renderer: TemplateRenderer
) -> TaskService:
    config = OrchestratorConfig(
        workspace_dir=str(workspace),
        template_dir=str(template_dir),
    )
    return TaskService(config, store, renderer, ArtifactService(store))


class TestTaskStatePreservesResolvedPath:
    def test_absolute_target_repo_stored_in_task(
        self, task_service: TaskService, store: FileStateStore
    ):
        """When an absolute path is passed, task state stores it as-is."""
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = RunOrchestrator(
            task_service=task_service,
            artifact_service=ArtifactService(store),
            store=store,
            adapters={},
            fallback_adapter=stub,
        )
        orch.run("abs-test", target_repo="/tmp/my-real-repo")

        task = store.load_task("abs-test")
        assert task.target_repo == "/tmp/my-real-repo"

    def test_pre_resolved_relative_persists_as_absolute(
        self, task_service: TaskService, store: FileStateStore
    ):
        """Simulates the CLI resolving a relative path before passing to run()."""
        resolved = _resolve_target_repo("../workload-cluster-add-on")
        assert Path(resolved).is_absolute()

        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        orch = RunOrchestrator(
            task_service=task_service,
            artifact_service=ArtifactService(store),
            store=store,
            adapters={},
            fallback_adapter=stub,
        )
        orch.run("rel-test", target_repo=resolved)

        task = store.load_task("rel-test")
        assert task.target_repo == resolved
        assert Path(task.target_repo).is_absolute()

    def test_adapter_context_receives_absolute_path(
        self, task_service: TaskService, store: FileStateStore
    ):
        """The context dict passed to adapters must contain the absolute path."""
        resolved = _resolve_target_repo("../some-other-repo")
        captured_contexts: list[dict] = []

        class CapturingStub(StubAdapter):
            def execute(self, task_name, artifact, template, instruction, context):
                captured_contexts.append(dict(context))
                return super().execute(task_name, artifact, template, instruction, context)

        stub = CapturingStub(store, default_outcome=ReviewOutcome.APPROVED)
        orch = RunOrchestrator(
            task_service=task_service,
            artifact_service=ArtifactService(store),
            store=store,
            adapters={},
            fallback_adapter=stub,
        )
        orch.run("ctx-test", target_repo=resolved)

        assert len(captured_contexts) > 0
        for ctx in captured_contexts:
            assert Path(ctx["target_repo"]).is_absolute()
            assert ctx["target_repo"] == resolved
