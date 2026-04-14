"""Tests for AgentsConfig validation and ordering."""

import pytest

from orchestrator.infrastructure.config_loader import (
    AgentsConfig,
    OrchestratorConfig,
)


class TestAgentsConfigDefaults:
    def test_default_order(self):
        config = AgentsConfig()
        assert config.enabled == ["cursor", "claude", "codex"]

    def test_implementer(self):
        config = AgentsConfig()
        assert config.implementer == "cursor"

    def test_reviewers(self):
        config = AgentsConfig()
        assert config.reviewers == ["claude", "codex"]


class TestAgentsConfigValidation:
    def test_valid_3_agents(self):
        config = AgentsConfig(enabled=["cursor", "claude", "codex"])
        assert config.validate() == []

    def test_valid_2_agents(self):
        config = AgentsConfig(enabled=["claude", "codex"])
        assert config.validate() == []

    def test_too_few(self):
        config = AgentsConfig(enabled=["cursor"])
        errors = config.validate()
        assert len(errors) == 1
        assert "At least 2" in errors[0]

    def test_too_many(self):
        config = AgentsConfig(enabled=["cursor", "claude", "codex", "cursor"])
        errors = config.validate()
        assert any("Duplicate" in e for e in errors)

    def test_unsupported_agent(self):
        config = AgentsConfig(enabled=["cursor", "gpt4"])
        errors = config.validate()
        assert any("Unsupported" in e for e in errors)

    def test_duplicate_agents(self):
        config = AgentsConfig(enabled=["cursor", "cursor", "codex"])
        errors = config.validate()
        assert any("Duplicate" in e for e in errors)

    def test_empty(self):
        config = AgentsConfig(enabled=[])
        errors = config.validate()
        assert any("At least 2" in e for e in errors)


class TestAgentsConfigCustomOrder:
    def test_claude_first(self):
        config = AgentsConfig(enabled=["claude", "cursor", "codex"])
        assert config.implementer == "claude"
        assert config.reviewers == ["cursor", "codex"]

    def test_two_agents(self):
        config = AgentsConfig(enabled=["codex", "cursor"])
        assert config.implementer == "codex"
        assert config.reviewers == ["cursor"]


class TestOrchestratorConfigAgents:
    def test_load_with_agents(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "agents:\n  enabled: [claude, codex]\n"
        )
        config = OrchestratorConfig.load(str(config_file))
        assert config.agents.enabled == ["claude", "codex"]

    def test_load_without_agents_uses_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("workspace_dir: ./ws\n")
        config = OrchestratorConfig.load(str(config_file))
        assert config.agents.enabled == ["cursor", "claude", "codex"]

    def test_load_no_file_uses_defaults(self):
        config = OrchestratorConfig.load(None)
        assert config.agents.enabled == ["cursor", "claude", "codex"]
