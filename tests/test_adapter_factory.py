"""Tests for config-driven adapter selection.

Covers:
- Factory creates correct adapter types
- Unknown type raises ValueError
- Config with settings passes them through
- create_adapters_from_config maps roles correctly
- Config loading with adapters section
"""

from pathlib import Path

import pytest
import yaml

from orchestrator.adapters.claude_adapter import ClaudeCommandAdapter
from orchestrator.adapters.codex import CodexCommandAdapter
from orchestrator.adapters.command import CommandAdapter
from orchestrator.adapters.cursor import CursorCommandAdapter
from orchestrator.adapters.factory import create_adapter, create_adapters_from_config
from orchestrator.adapters.manual import ManualAdapter
from orchestrator.adapters.stub import StubAdapter
from orchestrator.domain.models import AgentRole
from orchestrator.infrastructure.config_loader import OrchestratorConfig
from orchestrator.infrastructure.file_state_store import FileStateStore
from orchestrator.infrastructure.template_renderer import TemplateRenderer


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "active").mkdir()
    (ws / "archive").mkdir()
    return ws


@pytest.fixture
def store(workspace: Path) -> FileStateStore:
    return FileStateStore(workspace)


@pytest.fixture
def template_dir() -> Path:
    return Path(__file__).parent.parent / "templates" / "artifacts"


@pytest.fixture
def renderer(template_dir: Path) -> TemplateRenderer:
    return TemplateRenderer(template_dir)


# ======================================================================
# create_adapter — individual adapter creation
# ======================================================================


class TestCreateAdapter:
    def test_manual(self, store, renderer):
        adapter = create_adapter(AgentRole.CURSOR, {"type": "manual"}, store, renderer)
        assert isinstance(adapter, ManualAdapter)

    def test_stub(self, store, renderer):
        adapter = create_adapter(AgentRole.CURSOR, {"type": "stub"}, store, renderer)
        assert isinstance(adapter, StubAdapter)

    def test_command(self, store, renderer):
        adapter = create_adapter(
            AgentRole.CURSOR,
            {"type": "command", "settings": {"command": "my-script", "timeout": 120}},
            store,
            renderer,
        )
        assert isinstance(adapter, CommandAdapter)
        assert adapter._timeout == 120

    def test_codex_cli(self, store, renderer):
        adapter = create_adapter(AgentRole.CODEX, {"type": "codex-cli"}, store, renderer)
        assert isinstance(adapter, CodexCommandAdapter)
        assert adapter.name == "codex-cli"

    def test_claude_cli(self, store, renderer):
        adapter = create_adapter(AgentRole.CLAUDE, {"type": "claude-cli"}, store, renderer)
        assert isinstance(adapter, ClaudeCommandAdapter)
        assert adapter.name == "claude-cli"

    def test_cursor_cli(self, store, renderer):
        adapter = create_adapter(AgentRole.CURSOR, {"type": "cursor-cli"}, store, renderer)
        assert isinstance(adapter, CursorCommandAdapter)

    def test_unknown_type_raises(self, store, renderer):
        with pytest.raises(ValueError, match="Unknown adapter type"):
            create_adapter(AgentRole.CURSOR, {"type": "nonexistent"}, store, renderer)

    def test_default_is_manual(self, store, renderer):
        adapter = create_adapter(AgentRole.CURSOR, {}, store, renderer)
        assert isinstance(adapter, ManualAdapter)

    def test_settings_passed_to_codex(self, store, renderer):
        adapter = create_adapter(
            AgentRole.CODEX,
            {"type": "codex-cli", "settings": {"timeout": 900}},
            store,
            renderer,
        )
        assert adapter._timeout == 900


# ======================================================================
# create_adapters_from_config — bulk creation
# ======================================================================


class TestCreateAdaptersFromConfig:
    def test_all_manual(self, store, renderer):
        cfg = {
            "cursor": {"type": "manual"},
            "claude": {"type": "manual"},
            "codex": {"type": "manual"},
        }
        adapters = create_adapters_from_config(cfg, store, renderer)
        assert len(adapters) == 3
        for role in (AgentRole.CURSOR, AgentRole.CLAUDE, AgentRole.CODEX):
            assert isinstance(adapters[role], ManualAdapter)

    def test_mixed_config(self, store, renderer):
        cfg = {
            "cursor": {"type": "cursor-cli"},
            "claude": {"type": "claude-cli", "settings": {"timeout": 300}},
            "codex": {"type": "codex-cli"},
        }
        adapters = create_adapters_from_config(cfg, store, renderer)
        assert isinstance(adapters[AgentRole.CURSOR], CursorCommandAdapter)
        assert isinstance(adapters[AgentRole.CLAUDE], ClaudeCommandAdapter)
        assert isinstance(adapters[AgentRole.CODEX], CodexCommandAdapter)

    def test_unknown_role_ignored(self, store, renderer):
        cfg = {
            "cursor": {"type": "manual"},
            "unknown_agent": {"type": "manual"},
        }
        adapters = create_adapters_from_config(cfg, store, renderer)
        assert len(adapters) == 1

    def test_empty_config(self, store, renderer):
        adapters = create_adapters_from_config({}, store, renderer)
        assert len(adapters) == 0


# ======================================================================
# Config loading with adapters section
# ======================================================================


class TestConfigLoading:
    def test_load_adapters_from_yaml(self, tmp_path):
        config_data = {
            "workspace_dir": "./workspace",
            "adapters": {
                "cursor": {"type": "manual"},
                "claude": {"type": "claude-cli", "settings": {"timeout": 300}},
                "codex": {"type": "codex-cli"},
            },
        }
        config_file = tmp_path / "test-config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = OrchestratorConfig.load(config_file)
        assert "cursor" in config.adapters
        assert config.adapters["claude"]["type"] == "claude-cli"
        assert config.adapters["codex"]["type"] == "codex-cli"

    def test_config_without_adapters(self, tmp_path):
        config_file = tmp_path / "minimal.yaml"
        config_file.write_text("workspace_dir: ./workspace\n")

        config = OrchestratorConfig.load(config_file)
        assert config.adapters == {}

    def test_no_config_file(self):
        config = OrchestratorConfig.load(None)
        assert config.adapters == {}
