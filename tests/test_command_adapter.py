"""Tests for command-based adapters.

Covers:
- CommandAdapter success / failure / timeout / command-not-found
- Artifact existence verification after execution
- Per-step log file creation
- CodexCommandAdapter prompt construction
- ClaudeCommandAdapter prompt construction and stdin delivery
- CursorCommandAdapter manual fallback
- Progress spinner lifecycle
- stdin vs arg delivery mode
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

import pytest

from orchestrator.adapters.command import CommandAdapter, _ProgressSpinner
from orchestrator.adapters.codex import CodexCommandAdapter
from orchestrator.adapters.claude_adapter import ClaudeCommandAdapter
from orchestrator.adapters.cursor import CursorCommandAdapter
from orchestrator.domain.models import (
    AdapterCapability,
    ExecutionStatus,
    ReviewOutcome,
)
from orchestrator.infrastructure.file_state_store import FileStateStore


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
def task_dir(store: FileStateStore) -> Path:
    """Create a minimal task directory."""
    d = store.active_dir / "test-task"
    d.mkdir(parents=True)
    (d / "state.yaml").write_text("name: test-task\ntarget_repo: /tmp/repo\nstate: cursor_implementing\n")
    (d / "00-scope.md").write_text("# Scope\n\nTest scope.\n")
    return d


def _make_context() -> dict:
    return {
        "task": {"name": "test-task"},
        "cycle": 1,
        "target_repo": "/tmp/repo",
        "agent": "cursor",
    }


def _make_github_context() -> dict:
    return {
        "task": {"name": "test-task"},
        "cycle": 1,
        "target_repo": "/tmp/repo",
        "github_repo": "owner/repo",
        "issue_number": 42,
        "branch_name": "feat/issue-42/cursor/cycle-1",
        "agent": "cursor",
        "workflow_mode": "github",
    }


# ======================================================================
# CommandAdapter — subprocess success
# ======================================================================


class TestCommandAdapterSuccess:
    def test_success_with_artifact_written(self, store, task_dir):
        """Command exits 0 and writes the artifact."""
        adapter = CommandAdapter(store, {"command": "echo", "args": ["hello"], "timeout": 10})

        artifact = "01-cursor-implementation.md"
        (task_dir / artifact).write_text("# Implementation\n\nDone.\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="ok", stderr=""
            )
            result = adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.COMPLETED
        assert result.artifact_written is True

    def test_review_outcome_detected(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})

        artifact = "02-claude-review-cycle-1.md"
        (task_dir / artifact).write_text("# Review\n\n**Status**: changes-requested\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute("test-task", artifact, "tpl", "Review", _make_context())

        assert result.status == ExecutionStatus.COMPLETED
        assert result.review_outcome == ReviewOutcome.CHANGES_REQUESTED


# ======================================================================
# CommandAdapter — GitHub mode (no local artifact required)
# ======================================================================


class TestCommandAdapterGitHubMode:
    def test_github_mode_succeeds_without_artifact_file(self, store, task_dir):
        """In GitHub mode, command exit 0 is sufficient — no local file needed."""
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = adapter.execute(
                "test-task", "github-pr-cycle-1.md", "tpl", "Open PR",
                _make_github_context(),
            )

        assert result.status == ExecutionStatus.COMPLETED
        assert "GitHub mode" in result.message

    def test_github_mode_with_optional_artifact(self, store, task_dir):
        """If a local artifact is written in GitHub mode, it's used for outcome."""
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})
        artifact = "github-review-cycle-1.md"
        (task_dir / artifact).write_text("# Review\n\n**Status**: approved\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute(
                "test-task", artifact, "tpl", "Review",
                _make_github_context(),
            )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.artifact_written is True
        assert result.review_outcome == ReviewOutcome.APPROVED

    def test_non_github_mode_still_requires_artifact(self, store, task_dir):
        """File-artifact mode must keep the mandatory artifact check."""
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute(
                "test-task", "01-missing.md", "tpl", "Do it",
                _make_context(),
            )

        assert result.status == ExecutionStatus.FAILED
        assert "not written" in result.message

    def test_github_mode_failure_still_fails(self, store, task_dir):
        """Non-zero exit in GitHub mode is still a failure."""
        adapter = CommandAdapter(store, {"command": "false", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
            result = adapter.execute(
                "test-task", "github-pr-cycle-1.md", "tpl", "Open PR",
                _make_github_context(),
            )

        assert result.status == ExecutionStatus.FAILED

    def test_github_mode_logs_adapter_completed_github_mode(self, store, task_dir):
        """The run log must record the GitHub-mode completion event."""
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute(
                "test-task", "github-pr-cycle-1.md", "tpl", "Open PR",
                _make_github_context(),
            )

        from orchestrator.infrastructure.run_logger import RunLogger
        logger = RunLogger(task_dir)
        entries = logger.read_entries()
        events = [e["event"] for e in entries]
        assert "adapter_completed_github_mode" in events


# ======================================================================
# CommandAdapter — subprocess failure
# ======================================================================


class TestCommandAdapterFailure:
    def test_nonzero_exit_code(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "false", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = adapter.execute("test-task", "01-impl.md", "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.FAILED
        assert "code 1" in result.message

    def test_artifact_not_written(self, store, task_dir):
        """Command exits 0 but doesn't write the artifact."""
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute("test-task", "01-missing.md", "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.FAILED
        assert "not written" in result.message


# ======================================================================
# CommandAdapter — timeout
# ======================================================================


class TestCommandAdapterTimeout:
    def test_timeout_returns_failed(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "sleep", "args": ["999"], "timeout": 1})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
            result = adapter.execute("test-task", "01-impl.md", "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.FAILED
        assert "timed out" in result.message


# ======================================================================
# CommandAdapter — command not found
# ======================================================================


class TestCommandAdapterNotFound:
    def test_command_not_found(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "nonexistent-agent-xyz", "args": [], "timeout": 10})

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("nonexistent-agent-xyz")
            result = adapter.execute("test-task", "01-impl.md", "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.FAILED
        assert "not found" in result.message.lower()


# ======================================================================
# CommandAdapter — log file creation
# ======================================================================


class TestCommandAdapterLogs:
    def test_step_log_written(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Impl\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="out", stderr="err")
            adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        log_file = task_dir / f".log-{artifact}.txt"
        assert log_file.is_file()
        content = log_file.read_text()
        assert "out" in content
        assert "err" in content

    def test_prompt_file_written(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Impl\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        prompt_file = task_dir / f".prompt-{artifact}.md"
        assert prompt_file.is_file()
        assert "test-task" in prompt_file.read_text()

    def test_run_log_entries_written(self, store, task_dir):
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Impl\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        from orchestrator.infrastructure.run_logger import RunLogger
        logger = RunLogger(task_dir)
        entries = logger.read_entries()
        assert len(entries) >= 2
        events = [e["event"] for e in entries]
        assert "adapter_invoke" in events
        assert "adapter_completed" in events


# ======================================================================
# CodexCommandAdapter
# ======================================================================


class TestCodexCommandAdapter:
    def test_name(self, store):
        adapter = CodexCommandAdapter(store)
        assert adapter.name == "codex-cli"

    def test_capability_is_automatic(self, store):
        adapter = CodexCommandAdapter(store)
        assert adapter.capability == AdapterCapability.AUTOMATIC

    def test_prompt_includes_codex_role(self, store, task_dir):
        adapter = CodexCommandAdapter(store)
        prompt = adapter._build_prompt("test-task", "04-codex-review-cycle-1.md", "Review code", _make_context())
        assert "Codex" in prompt
        assert "test-task" in prompt
        assert "Status" in prompt

    def test_settings_override(self, store):
        adapter = CodexCommandAdapter(store, {"timeout": 900, "command": "my-codex"})
        assert adapter._timeout == 900
        assert adapter._settings["command"] == "my-codex"


# ======================================================================
# ClaudeCommandAdapter
# ======================================================================


class TestClaudeCommandAdapter:
    def test_name(self, store):
        adapter = ClaudeCommandAdapter(store)
        assert adapter.name == "claude-cli"

    def test_capability_is_automatic(self, store):
        adapter = ClaudeCommandAdapter(store)
        assert adapter.capability == AdapterCapability.AUTOMATIC

    def test_prompt_includes_claude_role(self, store, task_dir):
        adapter = ClaudeCommandAdapter(store)
        prompt = adapter._build_prompt("test-task", "02-claude-review-cycle-1.md", "Review", _make_context())
        assert "Claude" in prompt
        assert "Status" in prompt


# ======================================================================
# CursorCommandAdapter
# ======================================================================


class TestCursorCommandAdapter:
    def test_default_is_automatic(self, store):
        """Default CursorCommandAdapter is AUTOMATIC (cursor agent -p)."""
        adapter = CursorCommandAdapter(store, {})
        assert adapter.capability == AdapterCapability.AUTOMATIC
        assert adapter.name == "cursor-cli"

    def test_manual_fallback_returns_manual_capability(self, store):
        adapter = CursorCommandAdapter(store, {"manual_fallback": True})
        assert adapter.capability == AdapterCapability.MANUAL
        assert adapter.name == "cursor-manual"

    def test_manual_fallback_returns_waiting(self, store, task_dir):
        adapter = CursorCommandAdapter(store, {"manual_fallback": True})
        result = adapter.execute("test-task", "01-impl.md", "tpl", "Implement", _make_context())
        assert result.status == ExecutionStatus.WAITING
        assert "manual" in result.message.lower()

    def test_manual_fallback_writes_prompt_file(self, store, task_dir):
        adapter = CursorCommandAdapter(store, {"manual_fallback": True})
        adapter.execute("test-task", "01-impl.md", "tpl", "Implement", _make_context())
        assert (task_dir / ".prompt-01-impl.md.md").is_file()

    def test_with_custom_command(self, store):
        adapter = CursorCommandAdapter(store, {"command": "/usr/local/bin/cursor"})
        assert adapter.capability == AdapterCapability.AUTOMATIC
        assert adapter.name == "cursor-cli"

    def test_default_delegates_to_base(self, store, task_dir):
        """Default adapter invokes cursor agent via subprocess."""
        adapter = CursorCommandAdapter(store, {"timeout": 5})
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Impl\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.COMPLETED


# ======================================================================
# Environment variable injection
# ======================================================================


class TestEnvInjection:
    def test_static_env_value(self, store, task_dir):
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": [],
            "timeout": 5,
            "env": {"MY_VAR": "hello"},
        })
        env = adapter._build_env()
        assert env["MY_VAR"] == "hello"

    def test_env_ref_from_os(self, store, task_dir):
        import os
        os.environ["TEST_SECRET_ABC"] = "secret123"
        try:
            adapter = CommandAdapter(store, {
                "command": "echo",
                "args": [],
                "timeout": 5,
                "env": {"INJECTED": "$TEST_SECRET_ABC"},
            })
            env = adapter._build_env()
            assert env["INJECTED"] == "secret123"
        finally:
            del os.environ["TEST_SECRET_ABC"]


# ======================================================================
# Placeholder interpolation in command args
# ======================================================================


class TestPlaceholderInterpolation:
    def test_target_repo_resolved_in_args(self, store, task_dir):
        """The {target_repo} placeholder in args must resolve to the actual path."""
        adapter = CommandAdapter(store, {
            "command": "cursor",
            "args": ["agent", "-p", "--workspace", "{target_repo}", "{prompt}"],
            "timeout": 5,
        })
        ctx = _make_context()
        ctx["target_repo"] = "/tmp/my-real-repo"
        prompt_file = task_dir / ".prompt-test.md"
        prompt_file.write_text("test prompt")

        cmd = adapter._build_command("test prompt", prompt_file, ctx, "test-task")

        assert "/tmp/my-real-repo" in cmd
        assert "{target_repo}" not in " ".join(cmd)

    def test_task_dir_resolved_in_args(self, store, task_dir):
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": ["--dir", "{task_dir}"],
            "timeout": 5,
        })
        prompt_file = task_dir / ".prompt-test.md"
        prompt_file.write_text("")

        cmd = adapter._build_command("p", prompt_file, _make_context(), "test-task")

        assert str(task_dir) in " ".join(cmd)
        assert "{task_dir}" not in " ".join(cmd)

    def test_prompt_file_resolved_in_args(self, store, task_dir):
        adapter = CommandAdapter(store, {
            "command": "cat",
            "args": ["{prompt_file}"],
            "timeout": 5,
        })
        prompt_file = task_dir / ".prompt-test.md"
        prompt_file.write_text("")

        cmd = adapter._build_command("p", prompt_file, _make_context(), "test-task")

        assert str(prompt_file) in cmd
        assert "{prompt_file}" not in " ".join(cmd)

    def test_no_unresolved_placeholders(self, store, task_dir):
        """All known placeholders must be resolved."""
        adapter = CommandAdapter(store, {
            "command": "agent",
            "args": ["{prompt}", "{prompt_file}", "{target_repo}", "{task_dir}"],
            "timeout": 5,
        })
        ctx = _make_context()
        ctx["target_repo"] = "/tmp/repo"
        prompt_file = task_dir / ".prompt.md"
        prompt_file.write_text("")

        cmd = adapter._build_command("hello", prompt_file, ctx, "test-task")

        for arg in cmd[1:]:
            assert "{" not in arg, f"Unresolved placeholder in: {arg}"

    def test_cursor_workspace_gets_real_path(self, store, task_dir):
        """CursorCommandAdapter --workspace must contain the real repo path."""
        adapter = CursorCommandAdapter(store, {"timeout": 5})
        ctx = _make_context()
        ctx["target_repo"] = "/Users/me/repos/my-project"
        prompt_file = task_dir / ".prompt.md"
        prompt_file.write_text("")

        cmd = adapter._build_command("do stuff", prompt_file, ctx, "test-task")

        ws_idx = cmd.index("--workspace")
        ws_value = cmd[ws_idx + 1]
        assert ws_value == "/Users/me/repos/my-project"
        assert "{target_repo}" not in ws_value

    def test_unresolved_known_placeholder_fails_fast(self, store, task_dir):
        """If a known placeholder like {target_repo} somehow remains, fail fast."""
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": ["--workspace", "prefix-{target_repo}-suffix"],
            "timeout": 5,
        })
        # Patch _build_command to skip normal resolution and leave the token literal
        original = adapter._build_command

        def broken_build(prompt, prompt_file, context, task_name):
            return ["echo", "--workspace", "prefix-{target_repo}-suffix"]

        adapter._build_command = broken_build
        result = adapter.execute("test-task", "01-impl.md", "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.FAILED
        assert "nresolved" in result.message
        assert "{target_repo}" in result.message

    def test_unknown_placeholder_is_not_flagged(self, store, task_dir):
        """Unknown {foo} patterns in args are NOT treated as template errors."""
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": ["--flag", "{unknown_thing}"],
            "timeout": 5,
        })
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Done\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        assert result.status == ExecutionStatus.COMPLETED

    def test_json_in_prompt_not_flagged(self, store, task_dir):
        """JSON braces in the prompt body must NOT trigger false positives."""
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": ["{prompt}"],
            "timeout": 5,
        })
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Done\n")

        json_prompt = 'Example: {"timestamp": "2026-01-01", "event": "run_start"}'

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # Call _build_command directly to verify no false positive
            prompt_file = task_dir / ".prompt-test.md"
            prompt_file.write_text(json_prompt)
            cmd = adapter._build_command(json_prompt, prompt_file, _make_context(), "test-task")

            # The rendered prompt contains braces but no known placeholders
            assert "{prompt}" not in " ".join(cmd)
            assert '{"timestamp"' in " ".join(cmd)

            # Full execute should also succeed
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute("test-task", artifact, "tpl", json_prompt, _make_context())

        assert result.status == ExecutionStatus.COMPLETED

    def test_markdown_code_blocks_not_flagged(self, store, task_dir):
        """Markdown with code blocks containing {} must not cause false positives."""
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": ["{prompt}"],
            "timeout": 5,
        })
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Done\n")

        md_prompt = "```python\nfor x in range(10):\n    d = {}\n    d[x] = {x: True}\n```"

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute("test-task", artifact, "tpl", md_prompt, _make_context())

        assert result.status == ExecutionStatus.COMPLETED

    def test_empty_target_repo_falls_back_to_task_dir(self, store, task_dir):
        """When target_repo is empty, {target_repo} resolves to task_dir."""
        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": ["--workspace", "{target_repo}"],
            "timeout": 5,
        })
        ctx = _make_context()
        ctx["target_repo"] = ""
        prompt_file = task_dir / ".prompt.md"
        prompt_file.write_text("")

        cmd = adapter._build_command("p", prompt_file, ctx, "test-task")

        assert str(task_dir) in " ".join(cmd)
        assert "{target_repo}" not in " ".join(cmd)


# ======================================================================
# Claude stdin delivery
# ======================================================================


class TestClaudeStdinDelivery:
    """Verify that ClaudeCommandAdapter delivers prompt via stdin."""

    def test_claude_uses_stdin(self, store):
        adapter = ClaudeCommandAdapter(store)
        assert adapter._use_stdin() is True

    def test_claude_args_have_no_prompt_placeholder(self, store):
        adapter = ClaudeCommandAdapter(store)
        args = adapter._settings.get("args", [])
        assert "{prompt}" not in args

    def test_claude_command_invoked_with_stdin_input(self, store, task_dir):
        """subprocess.run must receive input= with prompt text."""
        adapter = ClaudeCommandAdapter(store, {"timeout": 5})
        artifact = "02-claude-review-cycle-1.md"
        (task_dir / artifact).write_text("# Review\n\n**Status**: approved\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute("test-task", artifact, "tpl", "Review this", _make_context())

        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("input") is not None or call_kwargs[1].get("input") is not None
        stdin_input = call_kwargs.kwargs.get("input") or call_kwargs[1].get("input")
        assert "Claude" in stdin_input
        assert "test-task" in stdin_input

    def test_claude_prompt_not_in_command_args(self, store, task_dir):
        """The rendered prompt must NOT appear as a CLI arg (only via stdin)."""
        adapter = ClaudeCommandAdapter(store, {"timeout": 5})
        artifact = "02-claude-review-cycle-1.md"
        (task_dir / artifact).write_text("# Review\n\n**Status**: approved\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute("test-task", artifact, "tpl", "Review this", _make_context())

        cmd = mock_run.call_args[0][0]
        joined = " ".join(cmd)
        assert "You are **Claude**" not in joined

    def test_claude_long_prompt_handled_safely(self, store, task_dir):
        """Even very long prompts should not fail since they go via stdin."""
        adapter = ClaudeCommandAdapter(store, {"timeout": 5})
        artifact = "02-claude-review-cycle-1.md"
        (task_dir / artifact).write_text("# Review\n\n**Status**: approved\n")

        long_instruction = "Review " * 50000

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = adapter.execute(
                "test-task", artifact, "tpl", long_instruction, _make_context()
            )

        assert result.status == ExecutionStatus.COMPLETED
        stdin_input = mock_run.call_args.kwargs.get("input") or mock_run.call_args[1].get("input")
        assert len(stdin_input) > 100000


# ======================================================================
# Codex stdin delivery
# ======================================================================


class TestCodexStdinDelivery:
    """Verify that CodexCommandAdapter delivers prompt via stdin."""

    def test_codex_uses_stdin(self, store):
        adapter = CodexCommandAdapter(store)
        assert adapter._use_stdin() is True

    def test_codex_args_have_no_prompt_placeholder(self, store):
        adapter = CodexCommandAdapter(store)
        args = adapter._settings.get("args", [])
        assert "{prompt}" not in args

    def test_codex_command_invoked_with_stdin_input(self, store, task_dir):
        adapter = CodexCommandAdapter(store, {"timeout": 5})
        artifact = "04-codex-review-cycle-1.md"
        (task_dir / artifact).write_text("# Review\n\n**Status**: approved\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute("test-task", artifact, "tpl", "Review this", _make_context())

        call_kwargs = mock_run.call_args
        stdin_input = call_kwargs.kwargs.get("input") or call_kwargs[1].get("input")
        assert stdin_input is not None
        assert "Codex" in stdin_input


# ======================================================================
# Cursor does NOT use stdin (arg-based delivery)
# ======================================================================


class TestCursorArgDelivery:
    """CursorCommandAdapter delivers prompt via CLI arg, not stdin."""

    def test_cursor_does_not_use_stdin(self, store):
        adapter = CursorCommandAdapter(store, {})
        assert adapter._use_stdin() is False

    def test_cursor_command_has_no_stdin_input(self, store, task_dir):
        adapter = CursorCommandAdapter(store, {"timeout": 5})
        artifact = "01-cursor-implementation.md"
        (task_dir / artifact).write_text("# Impl\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

        call_kwargs = mock_run.call_args
        stdin_input = call_kwargs.kwargs.get("input") or call_kwargs[1].get("input")
        assert stdin_input is None


# ======================================================================
# Progress spinner
# ======================================================================


class TestProgressSpinner:
    def test_spinner_starts_and_stops(self):
        import io
        buf = io.StringIO()
        spinner = _ProgressSpinner("my-task", "claude", "review", output=buf)
        spinner.start()
        import time
        time.sleep(1.2)
        spinner.stop()
        output = buf.getvalue()
        assert "claude" in output
        assert "my-task" in output
        assert "review" in output

    def test_spinner_shows_elapsed_time(self):
        import io
        import time
        buf = io.StringIO()
        spinner = _ProgressSpinner("task-x", "codex", "validate", output=buf)
        spinner.start()
        time.sleep(1.5)
        spinner.stop()
        assert "elapsed:" in buf.getvalue()

    def test_spinner_stop_is_idempotent(self):
        import io
        buf = io.StringIO()
        spinner = _ProgressSpinner("t", "a", "p", output=buf)
        spinner.start()
        spinner.stop()
        spinner.stop()

    def test_execute_starts_and_stops_spinner(self, store, task_dir):
        """The spinner must be active during subprocess execution."""
        adapter = CommandAdapter(store, {"command": "echo", "args": [], "timeout": 10})
        artifact = "01-impl.md"
        (task_dir / artifact).write_text("# Impl\n")

        with patch("orchestrator.adapters.command.subprocess.run") as mock_run, \
             patch("orchestrator.adapters.command._ProgressSpinner") as mock_spinner_cls:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_spinner = MagicMock()
            mock_spinner_cls.return_value = mock_spinner

            adapter.execute("test-task", artifact, "tpl", "Do it", _make_context())

            mock_spinner.start.assert_called_once()
            mock_spinner.stop.assert_called()
