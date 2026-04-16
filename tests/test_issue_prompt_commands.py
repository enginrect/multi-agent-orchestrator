"""Tests for morch issue lifecycle, prompt-file handling, and prompt template commands.

Covers:
- CLI parser for issue create/list/view/reopen/start
- CLI parser for prompt list-templates/init
- --prompt-file on run github
- GitHubService.reopen_issue and list_issues
- GitHubRunOrchestrator prompt_content injection
- Prompt template discovery and init
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.cli import build_parser, PROMPT_TEMPLATES_DIR
from orchestrator.infrastructure.github_service import GitHubService


# ======================================================================
# Parser tests — issue commands
# ======================================================================


class TestIssueParserCommands:
    def test_issue_create(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "create", "--repo", "o/r", "--title", "Bug fix",
        ])
        assert args.command == "issue"
        assert args.issue_command == "create"
        assert args.repo == "o/r"
        assert args.title == "Bug fix"
        assert hasattr(args, "func")

    def test_issue_create_with_body(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "create", "--repo", "o/r", "--title", "Fix",
            "--body", "Details here",
        ])
        assert args.body == "Details here"

    def test_issue_create_with_prompt_file(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "create", "--repo", "o/r", "--title", "Fix",
            "--prompt-file", "/tmp/prompt.md",
        ])
        assert args.prompt_file == "/tmp/prompt.md"

    def test_issue_create_with_labels(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "create", "--repo", "o/r", "--title", "Fix",
            "--labels", "bug,urgent",
        ])
        assert args.labels == "bug,urgent"

    def test_issue_list(self):
        parser = build_parser()
        args = parser.parse_args(["issue", "list", "--repo", "o/r"])
        assert args.issue_command == "list"
        assert args.repo == "o/r"
        assert hasattr(args, "func")

    def test_issue_list_with_state(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "list", "--repo", "o/r", "--state", "closed",
        ])
        assert args.state == "closed"

    def test_issue_view(self):
        parser = build_parser()
        args = parser.parse_args(["issue", "view", "42", "--repo", "o/r"])
        assert args.issue_command == "view"
        assert args.issue_number == 42
        assert hasattr(args, "func")

    def test_issue_reopen(self):
        parser = build_parser()
        args = parser.parse_args(["issue", "reopen", "7", "--repo", "o/r"])
        assert args.issue_command == "reopen"
        assert args.issue_number == 7
        assert hasattr(args, "func")

    def test_issue_start(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "start", "--repo", "o/r", "--title", "Smoke test",
        ])
        assert args.issue_command == "start"
        assert args.title == "Smoke test"
        assert hasattr(args, "func")

    def test_issue_start_with_prompt_file(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "start", "--repo", "o/r", "--title", "Test",
            "--prompt-file", ".morch/prompts/test.md",
            "--type", "test",
        ])
        assert args.prompt_file == ".morch/prompts/test.md"
        assert args.type == "test"

    def test_issue_start_default_type(self):
        parser = build_parser()
        args = parser.parse_args([
            "issue", "start", "--repo", "o/r", "--title", "X",
        ])
        assert args.type == "feat"


# ======================================================================
# Parser tests — prompt commands
# ======================================================================


class TestPromptParserCommands:
    def test_prompt_list_templates(self):
        parser = build_parser()
        args = parser.parse_args(["prompt", "list-templates"])
        assert args.command == "prompt"
        assert args.prompt_command == "list-templates"
        assert hasattr(args, "func")

    def test_prompt_init(self):
        parser = build_parser()
        args = parser.parse_args([
            "prompt", "init", "smoke-test",
            "--output", ".morch/prompts/my-test.md",
        ])
        assert args.prompt_command == "init"
        assert args.template_name == "smoke-test"
        assert args.output == ".morch/prompts/my-test.md"
        assert hasattr(args, "func")


# ======================================================================
# Parser tests — --prompt-file on run github
# ======================================================================


class TestRunGitHubPromptFile:
    def test_run_github_with_prompt_file(self):
        parser = build_parser()
        args = parser.parse_args([
            "run", "github", "42", "--repo", "o/r",
            "--prompt-file", ".morch/prompts/issue-42.md",
        ])
        assert args.prompt_file == ".morch/prompts/issue-42.md"
        assert args.issue_number == 42

    def test_run_github_prompt_file_default_none(self):
        parser = build_parser()
        args = parser.parse_args(["run", "github", "42", "--repo", "o/r"])
        assert args.prompt_file is None

    def test_github_run_compat_prompt_file(self):
        parser = build_parser()
        args = parser.parse_args([
            "github-run", "42", "--prompt-file", "/tmp/p.md",
        ])
        assert args.prompt_file == "/tmp/p.md"


# ======================================================================
# GitHubService — new methods
# ======================================================================


@pytest.fixture
def svc() -> GitHubService:
    return GitHubService("owner/repo")


class TestReopenIssue:
    def test_calls_gh_reopen(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
            svc.reopen_issue(42)
        args = mock.call_args[0][0]
        assert "reopen" in args
        assert "42" in args
        assert "--repo" in args


class TestListIssues:
    def test_returns_issue_list(self, svc):
        data = [{"number": 1, "title": "A"}, {"number": 2, "title": "B"}]
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(data),
                stderr="",
            )
            result = svc.list_issues()
        assert len(result) == 2
        assert result[0]["number"] == 1

    def test_respects_state(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="[]", stderr=""
            )
            svc.list_issues(state="closed")
        args = mock.call_args[0][0]
        assert "--state" in args
        idx = args.index("--state")
        assert args[idx + 1] == "closed"

    def test_includes_label_filter(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="[]", stderr=""
            )
            svc.list_issues(labels=["bug"])
        args = mock.call_args[0][0]
        assert "--label" in args
        assert "bug" in args

    def test_empty_list(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="[]", stderr=""
            )
            result = svc.list_issues()
        assert result == []


# ======================================================================
# GitHubRunOrchestrator — prompt_content injection
# ======================================================================


class TestOrchestratorPromptContent:
    @pytest.fixture
    def workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        return ws

    @pytest.fixture
    def store(self, workspace):
        from orchestrator.infrastructure.file_state_store import FileStateStore
        return FileStateStore(str(workspace))

    @pytest.fixture
    def mock_github(self):
        gh = MagicMock(spec=GitHubService)
        gh.repo = "owner/repo"
        gh.get_issue.return_value = {
            "number": 42,
            "title": "Test",
            "body": "Body",
            "state": "OPEN",
            "labels": [],
            "url": "https://github.com/owner/repo/issues/42",
        }
        gh.add_issue_comment.return_value = None
        gh.add_labels.return_value = None
        gh.list_prs.return_value = []
        return gh

    def test_prompt_content_in_context(self, store, mock_github):
        from orchestrator.application.github_task_service import GitHubTaskService
        from orchestrator.application.github_run_orchestrator import GitHubRunOrchestrator

        task_svc = GitHubTaskService(store=store, github=mock_github)
        orch = GitHubRunOrchestrator(
            task_service=task_svc,
            github=mock_github,
            store=store,
        )

        orch._prompt_content = "Do the thing carefully."
        task = task_svc.claim_issue(42)
        ctx = orch._build_context(task)
        assert ctx["prompt_content"] == "Do the thing carefully."

    def test_no_prompt_content_means_no_key(self, store, mock_github):
        from orchestrator.application.github_task_service import GitHubTaskService
        from orchestrator.application.github_run_orchestrator import GitHubRunOrchestrator

        task_svc = GitHubTaskService(store=store, github=mock_github)
        orch = GitHubRunOrchestrator(
            task_service=task_svc,
            github=mock_github,
            store=store,
        )

        task = task_svc.claim_issue(42)
        ctx = orch._build_context(task)
        assert "prompt_content" not in ctx

    def test_prompt_file_saved_to_task_dir(self, store, mock_github):
        from orchestrator.adapters.stub import StubAdapter
        from orchestrator.application.github_task_service import GitHubTaskService
        from orchestrator.application.github_run_orchestrator import GitHubRunOrchestrator
        from orchestrator.domain.models import AgentRole, ExecutionResult, ExecutionStatus

        task_svc = GitHubTaskService(store=store, github=mock_github)

        stub = StubAdapter(
            store,
            ExecutionResult(status=ExecutionStatus.WAITING, message="waiting"),
        )
        orch = GitHubRunOrchestrator(
            task_service=task_svc,
            github=mock_github,
            store=store,
            adapters={AgentRole.CURSOR: stub},
        )

        orch.run(issue_number=42, prompt_content="Detailed instructions")

        prompt_file = store.task_dir("issue-42") / "prompt.md"
        assert prompt_file.is_file()
        assert prompt_file.read_text() == "Detailed instructions"


# ======================================================================
# Prompt template discovery and init
# ======================================================================


class TestPromptTemplates:
    def test_templates_directory_exists(self):
        assert PROMPT_TEMPLATES_DIR.is_dir()

    def test_smoke_test_template_exists(self):
        assert (PROMPT_TEMPLATES_DIR / "smoke-test.md").is_file()

    def test_github_issue_task_template_exists(self):
        assert (PROMPT_TEMPLATES_DIR / "github-issue-task.md").is_file()

    def test_templates_are_non_empty(self):
        for f in PROMPT_TEMPLATES_DIR.glob("*.md"):
            assert f.stat().st_size > 0, f"{f.name} should not be empty"


class TestPromptInit:
    def test_copy_template(self, tmp_path):
        source = PROMPT_TEMPLATES_DIR / "smoke-test.md"
        dest = tmp_path / "prompts" / "my-test.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        assert dest.is_file()
        assert dest.read_text() == source.read_text()


# ======================================================================
# Adapter prompt contains prompt_content
# ======================================================================


class TestAdapterPromptInjection:
    def test_github_prompt_includes_prompt_content(self):
        from orchestrator.adapters.command import CommandAdapter
        from orchestrator.infrastructure.file_state_store import FileStateStore

        store = MagicMock(spec=FileStateStore)
        store.task_dir.return_value = Path("/tmp/task-dir")
        store.artifact_path.return_value = Path("/tmp/task-dir/artifact.md")
        store.list_artifacts.return_value = []

        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": [],
        })

        context = {
            "workflow_mode": "github",
            "target_repo": "owner/repo",
            "cycle": 1,
            "issue_number": 42,
            "work_type": "feat",
            "branch_name": "feat/issue-42/cursor/cycle-1",
            "pr_number": None,
            "base_branch": "main",
            "prompt_content": "Implement the widget feature with tests.",
        }

        prompt = adapter._build_prompt("task-42", "artifact.md", "implement", context)
        assert "Detailed task prompt" in prompt
        assert "Implement the widget feature with tests." in prompt

    def test_github_prompt_without_prompt_content(self):
        from orchestrator.adapters.command import CommandAdapter
        from orchestrator.infrastructure.file_state_store import FileStateStore

        store = MagicMock(spec=FileStateStore)
        store.task_dir.return_value = Path("/tmp/task-dir")
        store.artifact_path.return_value = Path("/tmp/task-dir/artifact.md")
        store.list_artifacts.return_value = []

        adapter = CommandAdapter(store, {
            "command": "echo",
            "args": [],
        })

        context = {
            "workflow_mode": "github",
            "target_repo": "owner/repo",
            "cycle": 1,
            "issue_number": 42,
            "work_type": "feat",
            "branch_name": "feat/issue-42/cursor/cycle-1",
            "pr_number": None,
            "base_branch": "main",
        }

        prompt = adapter._build_prompt("task-42", "artifact.md", "implement", context)
        assert "Detailed task prompt" not in prompt


# ======================================================================
# .gitignore includes .morch/
# ======================================================================


class TestGitignore:
    def test_morch_dir_in_gitignore(self):
        gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
        if gitignore.is_file():
            content = gitignore.read_text()
            assert ".morch/" in content
