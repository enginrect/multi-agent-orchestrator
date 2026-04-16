"""Integration tests for the GitHub-native run orchestrator.

Tests the full execution loop with mocked GitHubService and stub adapters:
- Happy path: issue -> implement -> PR -> review -> approve -> merge
- Rework path: changes requested -> rework -> re-review -> approve
- Escalation: max cycles exceeded
- Resume semantics
- Adapter failure handling
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.adapters.stub import StubAdapter
from orchestrator.application.github_run_orchestrator import GitHubRunOrchestrator
from orchestrator.application.github_task_service import GitHubTaskService
from orchestrator.domain.github_models import GitHubTaskState
from orchestrator.domain.models import (
    AgentRole,
    ExecutionResult,
    ExecutionStatus,
    ReviewOutcome,
    RunStatus,
)
from orchestrator.infrastructure.file_state_store import FileStateStore
from orchestrator.infrastructure.github_service import GitHubService


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
    """A fully mocked GitHubService."""
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
# Happy path
# ======================================================================


class TestGitHubRunHappyPath:
    def test_full_happy_path(self, store, mock_github, task_service):
        """Issue -> implement -> PR -> claude review -> codex review -> approve."""
        adapters = _make_stub_adapters(store)

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
        )

        result = orch.run(issue_number=42)

        assert result.task_name == "issue-42"
        assert result.final_state in (
            GitHubTaskState.APPROVED,
            GitHubTaskState.MERGED,
        )
        assert result.run_status == RunStatus.COMPLETED
        assert result.pr_number == 100
        assert len(result.steps) > 0


# ======================================================================
# Rework path
# ======================================================================


class TestGitHubRunRework:
    def test_changes_requested_triggers_rework(self, store, mock_github, task_service):
        """Claude requests changes -> Cursor reworks -> re-review -> approve."""
        call_count = {"n": 0}

        def review_state_side_effect(pr_number):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return "CHANGES_REQUESTED"
            return "APPROVED"

        mock_github.get_latest_review_state.side_effect = review_state_side_effect

        adapters = _make_stub_adapters(store)

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
        )

        result = orch.run(issue_number=42)

        assert result.run_status == RunStatus.COMPLETED
        assert result.final_state in (
            GitHubTaskState.APPROVED,
            GitHubTaskState.MERGED,
        )
        actions = [s.action for s in result.steps]
        assert any("rework" in a for a in actions) or len(result.steps) > 3


# ======================================================================
# Escalation
# ======================================================================


class TestGitHubRunEscalation:
    def test_max_cycles_escalates(self, store, mock_github, task_service):
        """After max cycles of changes requested, task escalates."""
        mock_github.get_latest_review_state.return_value = "CHANGES_REQUESTED"

        adapters = _make_stub_adapters(store)

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=adapters,
        )

        result = orch.run(issue_number=42)

        assert result.final_state == GitHubTaskState.ESCALATED
        assert result.run_status == RunStatus.COMPLETED


# ======================================================================
# Resume
# ======================================================================


class TestGitHubRunResume:
    def test_resume_terminal_task(self, store, mock_github, task_service):
        """Resuming a terminal task returns immediately."""
        task = task_service.claim_issue(42)
        task.state = GitHubTaskState.MERGED
        task_service._save_github_task(task)

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters=_make_stub_adapters(store),
        )

        result = orch.resume("issue-42")
        assert result.run_status == RunStatus.COMPLETED
        assert "terminal" in result.message.lower()


# ======================================================================
# No adapter
# ======================================================================


class TestGitHubRunNoAdapter:
    def test_missing_adapter_suspends(self, store, mock_github, task_service):
        """If no adapter is configured, the run suspends."""
        mock_github.list_prs.return_value = []
        mock_github.get_latest_review_state.return_value = None

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters={},
            fallback_adapter=None,
        )

        result = orch.run(issue_number=42)

        assert result.run_status == RunStatus.SUSPENDED
        assert "no adapter" in result.message.lower()


# ======================================================================
# Adapter failure
# ======================================================================


class TestGitHubRunAdapterFailure:
    def test_failed_adapter_suspends(self, store, mock_github, task_service):
        """An adapter returning FAILED suspends the run."""
        mock_github.list_prs.return_value = []
        mock_github.get_latest_review_state.return_value = None

        failing_adapter = MagicMock()
        failing_adapter.name = "failing"
        failing_adapter.capability = MagicMock(value="automatic")
        failing_adapter.execute.return_value = ExecutionResult(
            status=ExecutionStatus.FAILED,
            artifact_written=False,
            message="Command exited with code 1",
        )

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters={
                AgentRole.CURSOR: failing_adapter,
                AgentRole.CLAUDE: failing_adapter,
                AgentRole.CODEX: failing_adapter,
            },
        )

        result = orch.run(issue_number=42)

        assert result.run_status == RunStatus.SUSPENDED
        assert "failed" in result.message.lower()


# ======================================================================
# Waiting adapter
# ======================================================================


class TestGitHubRunWaiting:
    def test_waiting_adapter_returns_waiting(self, store, mock_github, task_service):
        """An adapter returning WAITING pauses the run."""
        mock_github.list_prs.return_value = []
        mock_github.get_latest_review_state.return_value = None

        waiting_adapter = MagicMock()
        waiting_adapter.name = "manual"
        waiting_adapter.capability = MagicMock(value="manual")
        waiting_adapter.execute.return_value = ExecutionResult(
            status=ExecutionStatus.WAITING,
            artifact_written=False,
            message="Please complete manually",
        )

        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
            adapters={
                AgentRole.CURSOR: waiting_adapter,
                AgentRole.CLAUDE: waiting_adapter,
                AgentRole.CODEX: waiting_adapter,
            },
        )

        result = orch.run(issue_number=42)

        assert result.is_waiting
        assert result.waiting_on == AgentRole.CURSOR


# ======================================================================
# Artifact naming
# ======================================================================


class TestActionToArtifact:
    def test_artifact_names_have_md_extension(self, store, mock_github, task_service):
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
        )
        from orchestrator.domain.github_models import GitHubTask, GitHubTaskState
        from orchestrator.domain.github_workflow import GitHubNextStep

        task = GitHubTask(name="issue-1", repo="owner/repo", issue_number=1)
        task.cycle = 1

        for action in ("implement", "open_pr", "review_pr", "final_review", "rework"):
            step = GitHubNextStep(
                agent=AgentRole.CURSOR, action=action,
                instruction="", state_after=GitHubTaskState.CURSOR_IMPLEMENTING,
            )
            artifact = orch._action_to_artifact(step, task)
            assert artifact.endswith(".md"), f"artifact for '{action}' lacks .md: {artifact}"

    def test_unknown_action_has_md_extension(self, store, mock_github, task_service):
        orch = GitHubRunOrchestrator(
            task_service=task_service,
            github=mock_github,
            store=store,
        )
        from orchestrator.domain.github_models import GitHubTask, GitHubTaskState
        from orchestrator.domain.github_workflow import GitHubNextStep

        task = GitHubTask(name="issue-1", repo="owner/repo", issue_number=1)
        step = GitHubNextStep(
            agent=AgentRole.CURSOR, action="custom_action",
            instruction="", state_after=GitHubTaskState.CURSOR_IMPLEMENTING,
        )
        artifact = orch._action_to_artifact(step, task)
        assert artifact.endswith(".md")
