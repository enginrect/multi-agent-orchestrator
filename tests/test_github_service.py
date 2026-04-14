"""Tests for GitHubService — gh CLI wrapper.

All subprocess calls are mocked. These tests verify:
- Correct gh command construction
- JSON parsing from gh output
- Error handling (not found, auth failure, timeout)
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.infrastructure.github_service import (
    GitHubAuthError,
    GitHubError,
    GitHubService,
    IssueNotFoundError,
    PRNotFoundError,
)


@pytest.fixture
def svc() -> GitHubService:
    return GitHubService("owner/repo")


class TestVerifyAuth:
    def test_auth_success(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="Logged in", stderr="")
            assert svc.verify_auth() is True

    def test_auth_failure(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=1, stdout="", stderr="not logged in")
            with pytest.raises(GitHubAuthError, match="not authenticated"):
                svc.verify_auth()

    def test_gh_not_found(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.side_effect = FileNotFoundError("gh")
            with pytest.raises(GitHubAuthError, match="not found"):
                svc.verify_auth()


class TestGetIssue:
    def test_returns_parsed_json(self, svc):
        issue_data = {"number": 42, "title": "Fix bug", "state": "OPEN", "body": "desc"}
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(issue_data),
                stderr="",
            )
            result = svc.get_issue(42)
        assert result["number"] == 42
        assert result["title"] == "Fix bug"

    def test_issue_not_found(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="issue not found",
            )
            with pytest.raises(IssueNotFoundError):
                svc.get_issue(9999)


class TestCreateIssue:
    def test_creates_with_labels(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"number": 1, "url": "https://..."}),
                stderr="",
            )
            result = svc.create_issue("title", "body", labels=["bug", "urgent"])
        args = mock.call_args[0][0]
        assert "--label" in args
        assert "bug" in args
        assert "urgent" in args


class TestAddIssueComment:
    def test_calls_gh_correctly(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
            svc.add_issue_comment(42, "Hello from orchestrator")
        args = mock.call_args[0][0]
        assert "comment" in args
        assert "42" in args


class TestListPRs:
    def test_list_with_head_filter(self, svc):
        pr_data = [{"number": 10, "headRefName": "issue-42/cursor/1"}]
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(pr_data),
                stderr="",
            )
            result = svc.list_prs(head="issue-42/cursor/1")
        assert len(result) == 1
        assert result[0]["number"] == 10

    def test_empty_list(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="[]", stderr=""
            )
            result = svc.list_prs()
        assert result == []


class TestGetPR:
    def test_returns_pr_data(self, svc):
        pr_data = {"number": 10, "state": "OPEN", "reviewDecision": "APPROVED"}
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(pr_data),
                stderr="",
            )
            result = svc.get_pr(10)
        assert result["reviewDecision"] == "APPROVED"

    def test_pr_not_found(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=1, stdout="", stderr="pr not found"
            )
            with pytest.raises(PRNotFoundError):
                svc.get_pr(9999)


class TestGetPRReviews:
    def test_returns_reviews(self, svc):
        reviews = [
            {"state": "COMMENTED", "body": "looks ok"},
            {"state": "APPROVED", "body": "LGTM"},
        ]
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(reviews),
                stderr="",
            )
            result = svc.get_pr_reviews(10)
        assert len(result) == 2

    def test_latest_review_state_approved(self, svc):
        reviews = [
            {"state": "CHANGES_REQUESTED"},
            {"state": "APPROVED"},
        ]
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(reviews),
                stderr="",
            )
            state = svc.get_latest_review_state(10)
        assert state == "APPROVED"

    def test_latest_review_state_ignores_comments(self, svc):
        reviews = [{"state": "COMMENTED"}]
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(reviews),
                stderr="",
            )
            state = svc.get_latest_review_state(10)
        assert state is None

    def test_latest_review_state_no_reviews(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="[]", stderr=""
            )
            state = svc.get_latest_review_state(10)
        assert state is None


class TestCreatePRReview:
    def test_approve_review(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
            svc.create_pr_review(10, "APPROVE", "LGTM")
        args = mock.call_args[0][0]
        assert "--approve" in args

    def test_request_changes_review(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
            svc.create_pr_review(10, "REQUEST_CHANGES", "Fix issues")
        args = mock.call_args[0][0]
        assert "--request-changes" in args


class TestBranchExists:
    def test_branch_found(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"name": "main"}),
                stderr="",
            )
            assert svc.branch_exists("main") is True

    def test_branch_not_found(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=1, stdout="", stderr="not found"
            )
            assert svc.branch_exists("nonexistent") is False


class TestErrorHandling:
    def test_timeout(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
            with pytest.raises(GitHubError, match="timed out"):
                svc.get_issue(1)

    def test_gh_binary_not_found(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.side_effect = FileNotFoundError("gh")
            with pytest.raises(GitHubError, match="not found"):
                svc.get_issue(1)

    def test_generic_failure(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=1, stdout="", stderr="some error"
            )
            with pytest.raises(GitHubError, match="some error"):
                svc.add_issue_comment(1, "test")

    def test_repo_args_included(self, svc):
        with patch("orchestrator.infrastructure.github_service.subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"number": 1}),
                stderr="",
            )
            svc.get_issue(1)
        args = mock.call_args[0][0]
        assert "--repo" in args
        assert "owner/repo" in args
