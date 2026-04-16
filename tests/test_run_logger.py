"""Tests for the JSONL run logger."""

import json
from pathlib import Path

import pytest

from orchestrator.infrastructure.run_logger import RunLogger


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "task"
    d.mkdir()
    return d


class TestRunLogger:
    def test_log_creates_file(self, task_dir):
        logger = RunLogger(task_dir)
        logger.log("test_event", key="value")
        assert logger.path.is_file()

    def test_entries_are_jsonl(self, task_dir):
        logger = RunLogger(task_dir)
        logger.log("event1", data="a")
        logger.log("event2", data="b")

        lines = logger.path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "event" in entry

    def test_read_entries(self, task_dir):
        logger = RunLogger(task_dir)
        logger.log("start", task="t1")
        logger.log("done", result="ok")

        entries = logger.read_entries()
        assert len(entries) == 2
        assert entries[0]["event"] == "start"
        assert entries[1]["event"] == "done"
        assert entries[0]["task"] == "t1"

    def test_read_empty(self, task_dir):
        logger = RunLogger(task_dir)
        assert logger.read_entries() == []

    def test_append_semantics(self, task_dir):
        logger = RunLogger(task_dir)
        logger.log("first")
        logger.log("second")
        logger.log("third")
        assert len(logger.read_entries()) == 3

    def test_timestamp_present(self, task_dir):
        logger = RunLogger(task_dir)
        logger.log("event")
        entry = logger.read_entries()[0]
        assert "T" in entry["timestamp"]


class TestRunLogIntegration:
    """Verify run.log is created during orchestrator run."""

    def test_run_creates_log(self, tmp_path):
        from orchestrator.adapters.stub import StubAdapter
        from orchestrator.application.artifact_service import ArtifactService
        from orchestrator.application.run_orchestrator import RunOrchestrator
        from orchestrator.application.task_service import TaskService
        from orchestrator.domain.models import ReviewOutcome
        from orchestrator.infrastructure.config_loader import OrchestratorConfig
        from orchestrator.infrastructure.file_state_store import FileStateStore
        from orchestrator.infrastructure.template_renderer import TemplateRenderer

        workspace = tmp_path / "workspace"
        template_dir = Path(__file__).parent.parent / "templates" / "artifacts"
        store = FileStateStore(workspace)
        store.ensure_workspace()
        renderer = TemplateRenderer(template_dir)
        config = OrchestratorConfig(workspace_dir=str(workspace), template_dir=str(template_dir))
        artifact_svc = ArtifactService(store)
        task_svc = TaskService(config, store, renderer, artifact_svc)
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)

        orch = RunOrchestrator(
            task_service=task_svc,
            artifact_service=artifact_svc,
            store=store,
            adapters={},
            fallback_adapter=stub,
        )
        orch.run("log-test", target_repo="/tmp/repo")

        log_path = store.task_dir("log-test") / "run.log"
        assert log_path.is_file()

        logger = RunLogger(store.task_dir("log-test"))
        entries = logger.read_entries()
        events = [e["event"] for e in entries]
        assert "run_start" in events
        assert "run_complete" in events
        assert any("step" in e for e in events)
