"""Tests for the markdown-prompt driven execution runner."""

import pytest

from orchestrator.adapters.stub import StubAdapter
from orchestrator.application.prompt_runner import PromptRunner
from orchestrator.domain.models import (
    AgentRole,
    ExecutionResult,
    ExecutionStatus,
    ReviewOutcome,
    RunStatus,
)
from orchestrator.infrastructure.config_loader import AgentsConfig
from orchestrator.infrastructure.file_state_store import FileStateStore


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def store(workspace):
    return FileStateStore(str(workspace))


@pytest.fixture
def prompt_file(tmp_path):
    f = tmp_path / "task.md"
    f.write_text("# My Task\n\nImplement a login page with OAuth support.\n")
    return f


class TestPromptRunnerHappyPath:
    def test_3_agents_complete(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {
            AgentRole.CURSOR: stub,
            AgentRole.CLAUDE: stub,
            AgentRole.CODEX: stub,
        }

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        assert result.is_complete
        assert result.run_status == RunStatus.COMPLETED
        assert len(result.steps) == 3
        assert result.steps[0].agent == "cursor"
        assert result.steps[0].role == "implement"
        assert result.steps[1].agent == "claude"
        assert result.steps[1].role == "review"
        assert result.steps[2].agent == "codex"
        assert result.steps[2].role == "review"

    def test_2_agents_complete(self, store, prompt_file):
        config = AgentsConfig(enabled=["claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {
            AgentRole.CLAUDE: stub,
            AgentRole.CODEX: stub,
        }

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        assert result.is_complete
        assert len(result.steps) == 2
        assert result.steps[0].role == "implement"
        assert result.steps[1].role == "review"


class TestPromptRunnerMissingFile:
    def test_missing_prompt_file(self, store, tmp_path):
        config = AgentsConfig(enabled=["cursor", "claude"])
        runner = PromptRunner(store=store, agents_config=config, adapters={})

        result = runner.run(tmp_path / "nonexistent.md")
        assert result.run_status == RunStatus.SUSPENDED
        assert "not found" in result.message


class TestPromptRunnerNoAdapter:
    def test_no_adapter_suspends(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "claude"])
        runner = PromptRunner(store=store, agents_config=config, adapters={})

        result = runner.run(prompt_file)
        assert result.run_status == RunStatus.SUSPENDED
        assert "No adapter" in result.message


class TestPromptRunnerFallback:
    def test_fallback_adapter(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)

        runner = PromptRunner(
            store=store,
            agents_config=config,
            adapters={},
            fallback_adapter=stub,
        )
        result = runner.run(prompt_file)
        assert result.is_complete
        assert len(result.steps) == 2


class TestPromptRunnerTaskNaming:
    def test_default_task_name(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)

        runner = PromptRunner(
            store=store, agents_config=config,
            adapters={AgentRole.CURSOR: stub, AgentRole.CODEX: stub},
        )
        result = runner.run(prompt_file)
        assert result.task_name == "prompt-task"

    def test_custom_task_name(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)

        runner = PromptRunner(
            store=store, agents_config=config,
            adapters={AgentRole.CURSOR: stub, AgentRole.CODEX: stub},
        )
        result = runner.run(prompt_file, task_name="my-custom-task")
        assert result.task_name == "my-custom-task"


class TestPromptRunnerPromptCopy:
    def test_prompt_copied_to_task_dir(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)

        runner = PromptRunner(
            store=store, agents_config=config,
            adapters={AgentRole.CURSOR: stub, AgentRole.CODEX: stub},
        )
        result = runner.run(prompt_file)

        task_dir = store.task_dir(result.task_name)
        assert (task_dir / "prompt.md").is_file()
        content = (task_dir / "prompt.md").read_text()
        assert "login page" in content
