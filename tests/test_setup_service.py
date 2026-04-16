"""Tests for the setup service — agent detection and config persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.infrastructure.setup_service import (
    AGENT_COMMANDS,
    AgentDetectionResult,
    SetupConfig,
    detect_agent,
    detect_all_agents,
)


class TestSetupConfig:
    """Tests for SetupConfig load/save."""

    def test_load_returns_defaults_when_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "orchestrator.infrastructure.setup_service.MORCH_CONFIG_FILE",
            tmp_path / "nonexistent.yaml",
        )
        config = SetupConfig.load()
        assert config.agent_paths == {}

    def test_save_and_load_round_trip(self, tmp_path: Path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr(
            "orchestrator.infrastructure.setup_service.MORCH_CONFIG_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            "orchestrator.infrastructure.setup_service.MORCH_CONFIG_FILE",
            config_file,
        )

        config = SetupConfig(agent_paths={"cursor": "/usr/bin/cursor", "claude": "/opt/claude"})
        config.save()

        assert config_file.is_file()

        loaded = SetupConfig.load()
        assert loaded.agent_paths == {"cursor": "/usr/bin/cursor", "claude": "/opt/claude"}

    def test_load_handles_corrupt_yaml(self, tmp_path: Path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(": : : not valid yaml [[[")
        monkeypatch.setattr(
            "orchestrator.infrastructure.setup_service.MORCH_CONFIG_FILE",
            config_file,
        )
        config = SetupConfig.load()
        assert config.agent_paths == {}


class TestDetectAgent:
    """Tests for detect_agent."""

    def test_unknown_agent_returns_not_installed(self):
        result = detect_agent("unknown-agent")
        assert result.installed is False
        assert result.name == "unknown-agent"

    @patch("orchestrator.infrastructure.setup_service.shutil.which", return_value=None)
    def test_missing_binary_returns_not_installed(self, mock_which, monkeypatch):
        monkeypatch.setattr(
            "orchestrator.infrastructure.setup_service.MORCH_CONFIG_FILE",
            Path("/nonexistent/config.yaml"),
        )
        result = detect_agent("cursor")
        assert result.installed is False
        assert result.install_hint != ""

    @patch("orchestrator.infrastructure.setup_service.subprocess.run")
    @patch("orchestrator.infrastructure.setup_service.shutil.which", return_value="/usr/local/bin/cursor")
    def test_cursor_detected_as_installed(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0\n", stderr="")
        result = detect_agent("cursor")
        assert result.installed is True
        assert result.authenticated is True
        assert result.path == "/usr/local/bin/cursor"
        assert "1.0.0" in result.version

    @patch("orchestrator.infrastructure.setup_service.subprocess.run")
    @patch("orchestrator.infrastructure.setup_service.shutil.which", return_value="/usr/local/bin/claude")
    def test_claude_auth_check(self, mock_which, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="2.0.0\n", stderr=""),  # --version
            MagicMock(returncode=1, stdout="", stderr="not logged in"),  # auth status
        ]
        result = detect_agent("claude")
        assert result.installed is True
        assert result.authenticated is False


class TestDetectAllAgents:
    """Tests for detect_all_agents."""

    @patch("orchestrator.infrastructure.setup_service.detect_agent")
    def test_returns_result_per_agent(self, mock_detect):
        mock_detect.return_value = AgentDetectionResult(name="test", installed=False)
        results = detect_all_agents()
        assert len(results) == len(AGENT_COMMANDS)
