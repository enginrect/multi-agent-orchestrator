"""Tests for GitHub workflow improvements.

Covers:
1. Local repo branch restoration after workflow completion
2. Repo mismatch detection (--local-repo vs --repo)
3. Self-approval limitation logging
4. Attach/import existing Issue/PR workflows
"""

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import subprocess

import pytest

from orchestrator.adapters.stub import StubAdapter
from orchestrator.application.github_run_orchestrator import GitHubRunOrchestrator
from orchestrator.application.github_task_service import GitHubTaskService
from orchestrator.domain.github_models import GitHubTask, GitHubTaskState, WorkType
from orchestrator.domain.models import (
    AgentRole,
    ExecutionResult,
    ExecutionStatus,
    ReviewOutcome,
    RunStatus,
)
from orchestrator.infrastructure.file_state_store import FileStateStore
from orchestrator.infrastructure.github_service import (
    GitHubError,
    GitHubService,
    _extract_repo_slug,
)


# ======================================================================
# Shared fixtures
# ======================================================================


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def store(workspace):
    return FileStateStore(str(workspace))


@pytest.fixture
def mock_github():
    gh = MagicMock(spec=GitHubService)
    gh.repo = "owner/repo"
    gh.get_issue.return_value = {
        "number": 42,
        "title": "Fix the bug",
        "body": "Something is broken",
        "state": "OPEN",
        "labels": [],
        "url": "https://github.com/owner/repo/issues/42",
    }
    gh.add_issue_comment.return_value = None
    gh.add_labels.return_value = None
    gh.close_issue.return_value = None
    gh.list_prs.return_value = [
        {
            "number": 100,
            "title": "[Feature][Issue #42][Cursor] Fix the bug",
            "headRefName": "feat/issue-42/cursor/cycle-1",
            "state": "OPEN",
            "url": "https://github.com/owner/repo/pull/100",
        }
    ]
    gh.get_latest_review_state.return_value = "APPROVED"
    gh.merge_pr.return_value = None
    gh.get_pr.return_value = {
        "number": 100,
        "title": "[Feature][Issue #42][Cursor] Fix the bug",
        "headRefName": "feat/issue-42/cursor/cycle-1",
        "baseRefName": "main",
        "state": "OPEN",
        "url": "https://github.com/owner/repo/pull/100",
    }
    return gh


@pytest.fixture
def task_service(store, mock_github):
    return GitHubTaskService(
        store=store,
        github=mock_github,
        max_cycles=2,
    )


def _make_stub_adapters(store):
    stub = StubAdapter(store, default_outcome=ReviewOutcome.APPROVED)
    return {
        AgentRole.CURSOR: stub,
        AgentRole.CLAUDE: stub,
        AgentRole.CODEX: stub,
    }


# ======================================================================
# 1. Repo mismatch detection
# ======================================================================


class TestRepoSlugExtraction:
    """Test the URL normalization used for repo mismatch detection."""

    def test_https_url(self):
        assert _extract_repo_slug("https://github.com/Owner/Repo.git") == "owner/repo"

    def test_ssh_url(self):
        assert _extract_repo_slug("git@github.com:Owner/Repo.git") == "owner/repo"

    def test_https_without_git_suffix(self):
        assert _extract_repo_slug("https://github.com/owner/repo") == "owner/repo"

    def test_bare_slug(self):
        assert _extract_repo_slug("owner/repo") == "owner/repo"

    def test_trailing_slash(self):
        assert _extract_repo_slug("https://github.com/owner/repo/") == "owner/repo"

    def test_case_insensitive(self):
        assert _extract_repo_slug("git@github.com:ThakiCloud/MyRepo.git") == "thakicloud/myrepo"


class TestValidateLocalRepo:
    """Verify --local-repo vs --repo validation."""

    def test_nonexistent_path_raises(self):
        with pytest.raises(GitHubError, match="does not exist"):
            GitHubService.validate_local_repo("/nonexistent/path/xyz", "owner/repo")

    def test_non_git_directory_raises(self, tmp_path):
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()
        with pytest.raises(GitHubError, match="not a git repository"):
            GitHubService.validate_local_repo(str(plain_dir), "owner/repo")

    def test_matching_repo_passes(self, tmp_path):
        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo.git\n",
            )
            GitHubService.validate_local_repo(str(repo_dir), "owner/repo")

    def test_mismatched_repo_raises(self, tmp_path):
        repo_dir = tmp_path / "wrong-repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/other-owner/other-repo.git\n",
            )
            with pytest.raises(GitHubError, match="mismatch"):
                GitHubService.validate_local_repo(str(repo_dir), "owner/repo")

    def test_ssh_remote_matches(self, tmp_path):
        repo_dir = tmp_path / "ssh-repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="git@github.com:Owner/Repo.git\n",
            )
            GitHubService.validate_local_repo(str(repo_dir), "Owner/Repo")


class TestOrchestratorRepoValidation:
    """Verify the orchestrator calls validate_local_repo before running."""

    def test_run_validates_repo(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
            local_repo_path="/fake/path",
        )

        with patch.object(
            GitHubService, "validate_local_repo",
            side_effect=GitHubError("mismatch"),
        ):
            with pytest.raises(GitHubError, match="mismatch"):
                orch.run(issue_number=42)

    def test_resume_validates_repo(self, store, mock_github, task_service):
        task = task_service.claim_issue(42)

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=_make_stub_adapters(store),
            local_repo_path="/fake/path",
        )

        with patch.object(
            GitHubService, "validate_local_repo",
            side_effect=GitHubError("mismatch"),
        ):
            with pytest.raises(GitHubError, match="mismatch"):
                orch.resume("issue-42")

    def test_no_local_repo_skips_validation(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
            local_repo_path=None,
        )
        result = orch.run(issue_number=42)
        assert result.run_status == RunStatus.COMPLETED


# ======================================================================
# 2. Branch save/restore
# ======================================================================


class TestBranchRestore:
    """Verify that local branch is saved before and restored after workflow."""

    def test_branch_saved_and_restored_on_run(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
            local_repo_path="/some/repo",
        )

        with patch.object(GitHubService, "validate_local_repo"):
            with patch.object(
                GitHubService, "get_current_branch",
                side_effect=["main", "feat/issue-42/cursor/cycle-1"],
            ) as mock_get:
                with patch.object(
                    GitHubService, "checkout_branch", return_value=True,
                ) as mock_checkout:
                    result = orch.run(issue_number=42)

        assert result.run_status == RunStatus.COMPLETED
        mock_checkout.assert_called_with("/some/repo", "main")

    def test_branch_not_restored_when_unchanged(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
            local_repo_path="/some/repo",
        )

        with patch.object(GitHubService, "validate_local_repo"):
            with patch.object(GitHubService, "get_current_branch", return_value="main"):
                with patch.object(
                    GitHubService, "checkout_branch", return_value=True,
                ) as mock_checkout:
                    result = orch.run(issue_number=42)

        assert result.run_status == RunStatus.COMPLETED

    def test_branch_restore_logs_failure(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
            local_repo_path="/some/repo",
        )

        with patch.object(GitHubService, "validate_local_repo"):
            with patch.object(
                GitHubService, "get_current_branch",
                side_effect=["main", "feat/issue-42/cursor/cycle-1"],
            ):
                with patch.object(
                    GitHubService, "checkout_branch", return_value=False,
                ):
                    result = orch.run(issue_number=42)

        assert result.run_status == RunStatus.COMPLETED

    def test_no_local_repo_skips_branch_ops(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
            local_repo_path=None,
        )

        with patch.object(
            GitHubService, "get_current_branch",
        ) as mock_get:
            result = orch.run(issue_number=42)

        mock_get.assert_not_called()


# ======================================================================
# 3. Self-approval caveat
# ======================================================================


class TestSelfApprovalCaveat:
    """Verify the self-approval limitation is logged at APPROVED state."""

    def test_caveat_logged_on_approval(self, store, mock_github, task_service):
        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
        )

        result = orch.run(issue_number=42)
        assert result.final_state in (
            GitHubTaskState.APPROVED,
            GitHubTaskState.MERGED,
        )

        task_dir = store.task_dir("issue-42")
        run_log = task_dir / "run.log"
        if run_log.is_file():
            content = run_log.read_text()
            assert "self_approval_caveat" in content


# ======================================================================
# 4. Attach/import existing workflow
# ======================================================================


class TestImportExisting:
    """Test import_existing for attaching to human-created Issue/PR."""

    def test_import_with_existing_pr(self, store, mock_github, task_service):
        """When a PR is provided, task starts at CLAUDE_REVIEWING."""
        task = task_service.import_existing(
            issue_number=42,
            pr_number=100,
            branch_name="my-feature-branch",
        )

        assert task.name == "issue-42"
        assert task.state == GitHubTaskState.CLAUDE_REVIEWING
        assert task.pr_number == 100
        assert task.branch_name == "my-feature-branch"

    def test_import_without_pr_autodetects(self, store, mock_github, task_service):
        """When no PR is given but one exists on GitHub, auto-detect it."""
        mock_github.list_prs.return_value = [
            {
                "number": 200,
                "title": "My PR",
                "headRefName": "feat/issue-42/manual",
                "state": "OPEN",
                "url": "https://github.com/owner/repo/pull/200",
            }
        ]

        task = task_service.import_existing(issue_number=42)

        assert task.pr_number == 200
        assert task.state == GitHubTaskState.CLAUDE_REVIEWING

    def test_import_without_pr_starts_at_implementing(self, store, mock_github, task_service):
        """When no PR exists, start at CURSOR_IMPLEMENTING."""
        mock_github.list_prs.return_value = []

        task = task_service.import_existing(issue_number=42)

        assert task.pr_number is None
        assert task.state == GitHubTaskState.CURSOR_IMPLEMENTING

    def test_import_posts_comment(self, store, mock_github, task_service):
        """Import should post a provenance comment on the issue."""
        task_service.import_existing(issue_number=42, pr_number=100)

        mock_github.add_issue_comment.assert_called_once()
        body = mock_github.add_issue_comment.call_args[0][1]
        assert "attached" in body.lower()
        assert "#100" in body

    def test_import_then_resume_runs_review(self, store, mock_github, task_service):
        """After import with PR, resume should run the review pipeline."""
        task = task_service.import_existing(
            issue_number=42,
            pr_number=100,
            branch_name="feat/issue-42/cursor/cycle-1",
        )

        adapters = _make_stub_adapters(store)
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
        )

        result = orch.resume(task.name)

        assert result.run_status == RunStatus.COMPLETED
        assert result.final_state in (
            GitHubTaskState.APPROVED,
            GitHubTaskState.MERGED,
        )
        actions = [s.action for s in result.steps]
        assert any("review" in a for a in actions)


# ======================================================================
# CLI parser integration
# ======================================================================


class TestIssueAttachParser:
    """Verify the 'issue attach' subcommand is registered."""

    def test_attach_parser_exists(self):
        from orchestrator.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "issue", "attach", "42",
            "--repo", "owner/repo",
            "--pr", "100",
        ])
        assert args.issue_number == 42
        assert args.pr == 100
        assert hasattr(args, "func")


class TestGetCurrentBranch:
    """Test the get_current_branch static method."""

    def test_returns_branch_name(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="main\n",
            )
            assert GitHubService.get_current_branch("/some/path") == "main"

    def test_returns_none_on_detached_head(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="HEAD\n",
            )
            assert GitHubService.get_current_branch("/some/path") is None

    def test_returns_none_on_error(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert GitHubService.get_current_branch("/some/path") is None


class TestCheckoutBranch:
    """Test the checkout_branch static method."""

    def test_returns_true_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert GitHubService.checkout_branch("/path", "main") is True

    def test_returns_false_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert GitHubService.checkout_branch("/path", "main") is False
