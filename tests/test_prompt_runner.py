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


class TestPromptRunnerCapabilityAwareness:
    """Verify that automatic adapters proceed without suspension."""

    def test_automatic_adapters_complete_without_waiting(self, store, prompt_file):
        """When all adapters are AUTOMATIC, the run completes end-to-end."""
        config = AgentsConfig(enabled=["cursor", "claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)

        assert stub.capability.value == "automatic"

        adapters = {
            AgentRole.CURSOR: stub,
            AgentRole.CLAUDE: stub,
            AgentRole.CODEX: stub,
        }

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        assert result.is_complete
        assert result.run_status == RunStatus.COMPLETED
        assert result.waiting_on is None
        for step in result.steps:
            assert step.status == ExecutionStatus.COMPLETED

    def test_manual_fallback_causes_waiting(self, store, prompt_file):
        """When no adapters match and fallback is manual, the run suspends."""
        from orchestrator.adapters.manual import ManualAdapter
        from orchestrator.infrastructure.template_renderer import TemplateRenderer

        config = AgentsConfig(enabled=["cursor", "claude"])
        manual = ManualAdapter(store, TemplateRenderer(""))

        assert manual.capability.value == "manual"

        runner = PromptRunner(
            store=store,
            agents_config=config,
            adapters={},
            fallback_adapter=manual,
        )
        result = runner.run(prompt_file)

        assert result.run_status == RunStatus.SUSPENDED
        assert result.waiting_on == "cursor"
        assert len(result.steps) == 1
        assert result.steps[0].status == ExecutionStatus.WAITING

    def test_mixed_adapters_suspend_on_manual(self, store, prompt_file):
        """First agent auto-completes, second is manual → suspends on second."""
        from orchestrator.adapters.manual import ManualAdapter
        from orchestrator.infrastructure.template_renderer import TemplateRenderer

        config = AgentsConfig(enabled=["cursor", "claude"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        manual = ManualAdapter(store, TemplateRenderer(""))

        runner = PromptRunner(
            store=store,
            agents_config=config,
            adapters={AgentRole.CURSOR: stub, AgentRole.CLAUDE: manual},
        )
        result = runner.run(prompt_file)

        assert result.run_status == RunStatus.SUSPENDED
        assert result.waiting_on == "claude"
        assert len(result.steps) == 2
        assert result.steps[0].status == ExecutionStatus.COMPLETED
        assert result.steps[1].status == ExecutionStatus.WAITING


class TestPromptRunnerArtifactNaming:
    """Artifact names must use .md extension to match the canonical convention."""

    def test_implementation_artifact_has_md_extension(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {
            AgentRole.CURSOR: stub,
            AgentRole.CLAUDE: stub,
            AgentRole.CODEX: stub,
        }

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        runner.run(prompt_file)

        assert stub.call_log[0]["artifact"] == "01-cursor-implementation.md"

    def test_review_artifacts_have_md_extension(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {
            AgentRole.CURSOR: stub,
            AgentRole.CLAUDE: stub,
            AgentRole.CODEX: stub,
        }

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        runner.run(prompt_file)

        assert stub.call_log[1]["artifact"] == "02-claude-review.md"
        assert stub.call_log[2]["artifact"] == "03-codex-review.md"

    def test_2_agent_artifacts_have_md_extension(self, store, prompt_file):
        config = AgentsConfig(enabled=["claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CLAUDE: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        runner.run(prompt_file)

        assert stub.call_log[0]["artifact"] == "01-claude-implementation.md"
        assert stub.call_log[1]["artifact"] == "02-codex-review.md"

    def test_all_artifact_names_end_with_md(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "claude", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {
            AgentRole.CURSOR: stub,
            AgentRole.CLAUDE: stub,
            AgentRole.CODEX: stub,
        }

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        runner.run(prompt_file)

        for call in stub.call_log:
            assert call["artifact"].endswith(".md"), (
                f"Artifact name missing .md: {call['artifact']}"
            )

    def test_written_artifact_file_exists(self, store, prompt_file):
        """The artifact file written by the stub must be findable by the store."""
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CURSOR: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        assert store.artifact_exists(result.task_name, "01-cursor-implementation.md")
        assert store.artifact_exists(result.task_name, "02-codex-review.md")


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


class TestPromptRunnerTaskPersistence:
    """Prompt tasks must be discoverable via the same lookup as normal tasks."""

    def test_completed_task_has_state_yaml(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CURSOR: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        assert store.task_exists(result.task_name)

    def test_completed_task_loadable(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CURSOR: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        task = store.load_task(result.task_name)
        assert task.name == result.task_name
        assert task.run_status == RunStatus.COMPLETED

    def test_completed_task_in_list(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CURSOR: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        active = store.list_active_tasks()
        assert result.task_name in active

    def test_suspended_task_loadable(self, store, prompt_file):
        from orchestrator.adapters.manual import ManualAdapter
        from orchestrator.infrastructure.template_renderer import TemplateRenderer

        config = AgentsConfig(enabled=["cursor", "claude"])
        manual = ManualAdapter(store, TemplateRenderer(""))

        runner = PromptRunner(
            store=store,
            agents_config=config,
            adapters={},
            fallback_adapter=manual,
        )
        result = runner.run(prompt_file)

        assert result.run_status == RunStatus.SUSPENDED
        task = store.load_task(result.task_name)
        assert task.name == result.task_name
        assert task.run_status == RunStatus.SUSPENDED

    def test_suspended_task_in_list(self, store, prompt_file):
        from orchestrator.adapters.manual import ManualAdapter
        from orchestrator.infrastructure.template_renderer import TemplateRenderer

        config = AgentsConfig(enabled=["cursor", "claude"])
        manual = ManualAdapter(store, TemplateRenderer(""))

        runner = PromptRunner(
            store=store,
            agents_config=config,
            adapters={},
            fallback_adapter=manual,
        )
        result = runner.run(prompt_file)

        active = store.list_active_tasks()
        assert result.task_name in active

    def test_no_adapter_still_persists_task(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "claude"])
        runner = PromptRunner(store=store, agents_config=config, adapters={})

        result = runner.run(prompt_file)

        assert store.task_exists(result.task_name)
        task = store.load_task(result.task_name)
        assert task.run_status == RunStatus.SUSPENDED

    def test_task_target_repo_persisted(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CURSOR: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file, target_repo="/tmp/my-repo")

        task = store.load_task(result.task_name)
        assert task.target_repo == "/tmp/my-repo"

    def test_task_has_history(self, store, prompt_file):
        config = AgentsConfig(enabled=["cursor", "codex"])
        stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
        adapters = {AgentRole.CURSOR: stub, AgentRole.CODEX: stub}

        runner = PromptRunner(store=store, agents_config=config, adapters=adapters)
        result = runner.run(prompt_file)

        task = store.load_task(result.task_name)
        assert len(task.history) >= 1
