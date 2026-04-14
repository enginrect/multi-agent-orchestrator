"""Tests for the morch CLI parser and command routing."""

import pytest

from orchestrator.cli import build_parser


class TestParserStructure:
    def test_prog_name(self):
        parser = build_parser()
        assert parser.prog == "morch"

    def test_doctor_command(self):
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"
        assert hasattr(args, "func")

    def test_auth_status(self):
        parser = build_parser()
        args = parser.parse_args(["auth", "status"])
        assert args.command == "auth"
        assert hasattr(args, "func")

    def test_auth_cursor_status(self):
        parser = build_parser()
        args = parser.parse_args(["auth", "cursor", "status"])
        assert args.tool_name == "cursor"

    def test_auth_github_login(self):
        parser = build_parser()
        args = parser.parse_args(["auth", "github", "login"])
        assert args.tool_name == "github"

    def test_agents_list(self):
        parser = build_parser()
        args = parser.parse_args(["agents", "list"])
        assert args.command == "agents"

    def test_agents_order(self):
        parser = build_parser()
        args = parser.parse_args(["agents", "order", "claude", "codex"])
        assert args.agents == ["claude", "codex"]

    def test_config_show(self):
        parser = build_parser()
        args = parser.parse_args(["config", "show"])
        assert args.command == "config"

    def test_run_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["run", "prompt", "task.md"])
        assert args.prompt_path == "task.md"

    def test_run_prompt_with_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "run", "prompt", "task.md",
            "--name", "my-task",
            "--target-repo", "/path/to/repo",
        ])
        assert args.name == "my-task"
        assert args.target_repo == "/path/to/repo"

    def test_run_task(self):
        parser = build_parser()
        args = parser.parse_args(["run", "task", "my-feature"])
        assert args.task_name == "my-feature"

    def test_run_github(self):
        parser = build_parser()
        args = parser.parse_args(["run", "github", "42", "--repo", "owner/name"])
        assert args.issue_number == 42
        assert args.repo == "owner/name"

    def test_run_github_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["run", "github", "42", "--type", "fix"])
        assert args.type == "fix"

    def test_resume_github(self):
        parser = build_parser()
        args = parser.parse_args(["resume", "github", "issue-42"])
        assert args.task_name == "issue-42"
        assert args.resume_command == "github"

    def test_resume_task(self):
        parser = build_parser()
        args = parser.parse_args(["resume", "task", "my-task"])
        assert args.task_name == "my-task"
        assert args.resume_command == "task"

    def test_status_github(self):
        parser = build_parser()
        args = parser.parse_args(["status", "github", "issue-42"])
        assert args.task_name == "issue-42"
        assert args.status_command == "github"

    def test_status_task(self):
        parser = build_parser()
        args = parser.parse_args(["status", "task", "my-task"])
        assert args.task_name == "my-task"
        assert args.status_command == "task"

    def test_task_init(self):
        parser = build_parser()
        args = parser.parse_args(["task", "init", "my-task"])
        assert args.task_name == "my-task"

    def test_task_advance(self):
        parser = build_parser()
        args = parser.parse_args(["task", "advance", "my-task", "--outcome", "approved"])
        assert args.outcome == "approved"

    def test_task_list(self):
        parser = build_parser()
        args = parser.parse_args(["task", "list", "--all"])
        assert args.all is True


class TestBackwardCompat:
    """Old orchestrator-style commands should still parse."""

    def test_init_compat(self):
        parser = build_parser()
        args = parser.parse_args(["init", "my-task"])
        assert args.task_name == "my-task"
        assert hasattr(args, "func")

    def test_advance_compat(self):
        parser = build_parser()
        args = parser.parse_args(["advance", "my-task"])
        assert hasattr(args, "func")

    def test_validate_compat(self):
        parser = build_parser()
        args = parser.parse_args(["validate", "my-task"])
        assert hasattr(args, "func")

    def test_archive_compat(self):
        parser = build_parser()
        args = parser.parse_args(["archive", "my-task"])
        assert hasattr(args, "func")

    def test_list_compat(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert hasattr(args, "func")

    def test_github_run_compat(self):
        parser = build_parser()
        args = parser.parse_args(["github-run", "42"])
        assert args.issue_number == 42

    def test_github_resume_compat(self):
        parser = build_parser()
        args = parser.parse_args(["github-resume", "issue-42"])
        assert args.task_name == "issue-42"

    def test_github_status_compat(self):
        parser = build_parser()
        args = parser.parse_args(["github-status", "issue-42"])
        assert args.task_name == "issue-42"
