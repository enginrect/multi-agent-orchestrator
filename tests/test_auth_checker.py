"""Tests for auth_checker — mocked subprocess calls."""

from unittest.mock import patch, MagicMock

from orchestrator.infrastructure.auth_checker import (
    AuthStatus,
    check_git,
    check_github,
    check_cursor,
    check_claude,
    check_codex,
    check_tool,
    check_all,
    ALL_TOOLS,
)


class TestCheckGit:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_not_installed(self, mock_find):
        s = check_git()
        assert not s.installed
        assert not s.ready

    @patch("orchestrator.infrastructure.auth_checker._run_quiet")
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/usr/bin/git")
    def test_installed_with_identity(self, mock_find, mock_run):
        mock_run.side_effect = [
            (0, "git version 2.43.0", ""),
            (0, "Test User", ""),
            (0, "test@example.com", ""),
        ]
        s = check_git()
        assert s.installed
        assert s.authenticated
        assert s.ready
        assert "2.43.0" in s.version

    @patch("orchestrator.infrastructure.auth_checker._run_quiet")
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/usr/bin/git")
    def test_installed_no_identity(self, mock_find, mock_run):
        mock_run.side_effect = [
            (0, "git version 2.43.0", ""),
            (1, "", ""),
            (1, "", ""),
        ]
        s = check_git()
        assert s.installed
        assert not s.authenticated


class TestCheckGitHub:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_not_installed(self, mock_find):
        s = check_github()
        assert not s.installed

    @patch("orchestrator.infrastructure.auth_checker._run_quiet")
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/usr/local/bin/gh")
    def test_installed_and_authed(self, mock_find, mock_run):
        mock_run.side_effect = [
            (0, "gh version 2.50.0", ""),
            (0, "Logged in as user", ""),
        ]
        s = check_github()
        assert s.installed
        assert s.authenticated
        assert s.ready


class TestCheckCursor:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_not_installed(self, mock_find):
        s = check_cursor()
        assert not s.installed

    @patch("orchestrator.infrastructure.auth_checker._run_quiet", return_value=(0, "1.0.0", ""))
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/usr/local/bin/cursor")
    def test_installed(self, mock_find, mock_run):
        s = check_cursor()
        assert s.installed
        assert s.authenticated
        assert s.ready


class TestCheckClaude:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_not_installed(self, mock_find):
        s = check_claude()
        assert not s.installed

    @patch("orchestrator.infrastructure.auth_checker._run_quiet")
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/usr/local/bin/claude")
    def test_installed_and_authed(self, mock_find, mock_run):
        mock_run.side_effect = [
            (0, "1.2.3", ""),
            (0, "Authenticated", ""),
        ]
        s = check_claude()
        assert s.installed
        assert s.authenticated


class TestCheckCodex:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_not_installed(self, mock_find):
        s = check_codex()
        assert not s.installed

    @patch("os.environ", {"OPENAI_API_KEY": "sk-test"})
    @patch("orchestrator.infrastructure.auth_checker._run_quiet", return_value=(0, "0.1.0", ""))
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/opt/homebrew/bin/codex")
    def test_installed_with_key(self, mock_find, mock_run):
        s = check_codex()
        assert s.installed
        assert s.authenticated

    @patch("os.environ", {})
    @patch("orchestrator.infrastructure.auth_checker._run_quiet", return_value=(0, "0.1.0", ""))
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value="/opt/homebrew/bin/codex")
    def test_installed_no_key(self, mock_find, mock_run):
        s = check_codex()
        assert s.installed
        assert not s.authenticated


class TestCheckTool:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_unknown_tool(self, mock_find):
        s = check_tool("unknown")
        assert not s.installed
        assert "Unknown" in s.message

    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_known_tool(self, mock_find):
        s = check_tool("git")
        assert not s.installed


class TestCheckAll:
    @patch("orchestrator.infrastructure.auth_checker._find_binary", return_value=None)
    def test_returns_all_tools(self, mock_find):
        results = check_all()
        assert len(results) == len(ALL_TOOLS)
        tools = {s.tool for s in results}
        assert tools == set(ALL_TOOLS)
