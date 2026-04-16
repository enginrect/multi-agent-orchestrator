"""Tests for agent provenance and the GitHub workflow loop fix.

Part A — Provenance:
- Issue claim comment includes Orchestrator provenance
- PR body includes Cursor provenance
- Claude review prompt includes provenance header
- Codex review prompt includes provenance header
- Fallback review comment preserves agent identity
- Multi-agent reviews are separate (not merged)
- Orchestrator posts milestone comments

Part B — Loop fix:
- Successful PR creation advances state (no repeat)
- Same-step repetition guard triggers
- Delayed PR detection is retried before giving up
- Force-advance works when PR undetectable
- Loop cannot stay on open_pr forever after success
"""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from orchestrator.domain.models import AgentRole, ExecutionResult, ExecutionStatus, RunStatus
from orchestrator.domain.provenance import (
    AGENT_IDENTITIES,
    agent_sig,
    comment_approved,
    comment_fallback_review,
    comment_issue_claimed,
    comment_pr_opened,
    comment_review_completed,
    comment_review_started,
    fix_commit_prefix,
    pr_body_block,
    review_header,
)


# ======================================================================
# Part A — Provenance template unit tests
# ======================================================================


class TestAgentIdentities:
    def test_all_agents_have_identities(self):
        assert "orchestrator" in AGENT_IDENTITIES
        for role in AgentRole:
            assert role.value in AGENT_IDENTITIES

    def test_agent_sig_format(self):
        sig = agent_sig("orchestrator")
        assert "Orchestrator" in sig
        assert "@multi-orchestrator-agent" in sig

    def test_agent_sig_cursor(self):
        sig = agent_sig(AgentRole.CURSOR.value)
        assert "Cursor" in sig
        assert "@cursor-agent" in sig

    def test_agent_sig_claude(self):
        sig = agent_sig(AgentRole.CLAUDE.value)
        assert "@claude-agent" in sig

    def test_agent_sig_codex(self):
        sig = agent_sig(AgentRole.CODEX.value)
        assert "@codex-agent" in sig


class TestIssueClaimComment:
    def test_includes_orchestrator_identity(self):
        body = comment_issue_claimed("feat/issue-1/cursor/cycle-1", cycle=1)
        assert "@multi-orchestrator-agent" in body
        assert "Orchestrator" in body

    def test_includes_branch(self):
        body = comment_issue_claimed("refactor/issue-5/cursor/cycle-1", cycle=1)
        assert "refactor/issue-5/cursor/cycle-1" in body

    def test_includes_workflow_metadata(self):
        body = comment_issue_claimed("feat/issue-1/cursor/cycle-1", cycle=1)
        assert "morch GitHub-native" in body
        assert "Cycle" in body


class TestPrBodyBlock:
    def test_includes_cursor_identity(self):
        block = pr_body_block(AgentRole.CURSOR, "implementation", 3, 1)
        assert "@cursor-agent" in block
        assert "Cursor" in block

    def test_includes_role_and_issue(self):
        block = pr_body_block(AgentRole.CURSOR, "implementation", 7, 2)
        assert "implementation" in block
        assert "#7" in block
        assert "Cycle: 2" in block

    def test_includes_workflow_tag(self):
        block = pr_body_block(AgentRole.CURSOR, "implementation", 1, 1)
        assert "morch GitHub-native" in block


class TestReviewHeader:
    def test_claude_review_header(self):
        header = review_header(AgentRole.CLAUDE, "reviewer", 4, 1)
        assert "@claude-agent" in header
        assert "reviewer" in header

    def test_codex_review_header(self):
        header = review_header(AgentRole.CODEX, "final reviewer", 4, 1)
        assert "@codex-agent" in header
        assert "final reviewer" in header


class TestMilestoneComments:
    def test_pr_opened_comment(self):
        body = comment_pr_opened(AgentRole.CURSOR, 5, 1)
        assert "@cursor-agent" in body
        assert "PR #5" in body

    def test_review_started_claude(self):
        body = comment_review_started(AgentRole.CLAUDE, 5, 1)
        assert "@claude-agent" in body
        assert "reviewer" in body

    def test_review_started_codex(self):
        body = comment_review_started(AgentRole.CODEX, 5, 1)
        assert "@codex-agent" in body
        assert "final reviewer" in body

    def test_review_completed_with_status(self):
        body = comment_review_completed(AgentRole.CLAUDE, 5, 1, status="approved")
        assert "@claude-agent" in body
        assert "approved" in body

    def test_approved_comment(self):
        body = comment_approved(5, 1)
        assert "@multi-orchestrator-agent" in body
        assert "human merge" in body

    def test_fallback_review_comment(self):
        body = comment_fallback_review(AgentRole.CODEX, 5, 1)
        assert "@codex-agent" in body
        assert "Could not post formal GitHub review" in body


class TestFixCommitPrefix:
    def test_claude_fix_prefix(self):
        assert fix_commit_prefix(AgentRole.CLAUDE, 3) == "chore(claude): apply review fix for issue #3"

    def test_codex_fix_prefix(self):
        assert fix_commit_prefix(AgentRole.CODEX, 7) == "chore(codex): apply review fix for issue #7"


# ======================================================================
# Part A — Integration: claim uses provenance
# ======================================================================


class TestClaimIssueProvenance:
    def _make_service(self, tmp_path):
        from orchestrator.application.github_task_service import GitHubTaskService
        from orchestrator.infrastructure.file_state_store import FileStateStore

        store = FileStateStore(workspace_dir=tmp_path / "workspace")
        store.ensure_workspace()

        github = MagicMock()
        github.repo = "owner/repo"
        github.get_issue.return_value = {"title": "Test", "body": "desc"}
        return GitHubTaskService(store=store, github=github), github

    def test_claim_comment_includes_orchestrator_provenance(self, tmp_path):
        svc, gh = self._make_service(tmp_path)
        svc.claim_issue(1)
        gh.add_issue_comment.assert_called_once()
        comment_body = gh.add_issue_comment.call_args[0][1]
        assert "@multi-orchestrator-agent" in comment_body

    def test_claim_comment_includes_branch(self, tmp_path):
        svc, gh = self._make_service(tmp_path)
        svc.claim_issue(1)
        comment_body = gh.add_issue_comment.call_args[0][1]
        assert "feat/issue-1/cursor/cycle-1" in comment_body


# ======================================================================
# Part A — Integration: adapter prompts include provenance
# ======================================================================


class TestCursorPrBodyProvenance:
    def test_pr_body_includes_cursor_provenance(self):
        from orchestrator.adapters.cursor import CursorCommandAdapter

        store = MagicMock()
        store.task_dir.return_value = MagicMock()
        adapter = CursorCommandAdapter(store, {"manual_fallback": True})

        context = {
            "workflow_mode": "github",
            "github_repo": "owner/repo",
            "target_repo": "/local/path",
            "cycle": 1,
            "issue_number": 3,
            "issue_title": "Refactor",
            "work_type": "refactor",
            "branch_name": "refactor/issue-3/cursor/cycle-1",
            "pr_number": None,
            "base_branch": "main",
            "pr_title_pattern": "[{type}][Issue #{issue}][{agent}] {summary}",
        }

        prompt = adapter._build_prompt("issue-3", "github-pr-cycle-1", "Implement", context)
        assert "@cursor-agent" in prompt
        assert "morch GitHub-native" in prompt


class TestClaudeReviewProvenance:
    def test_github_prompt_includes_claude_identity(self):
        from orchestrator.adapters.claude_adapter import ClaudeCommandAdapter

        store = MagicMock()
        store.task_dir.return_value = MagicMock()
        adapter = ClaudeCommandAdapter(store)

        context = {
            "workflow_mode": "github",
            "github_repo": "owner/repo",
            "cycle": 1,
            "issue_number": 3,
            "branch_name": "feat/issue-3/cursor/cycle-1",
            "pr_number": 4,
            "base_branch": "main",
        }

        prompt = adapter._build_prompt("issue-3", "github-review", "Review", context)
        assert "@claude-agent" in prompt
        assert "provenance header" in prompt

    def test_github_prompt_uses_github_repo(self):
        from orchestrator.adapters.claude_adapter import ClaudeCommandAdapter

        store = MagicMock()
        store.task_dir.return_value = MagicMock()
        adapter = ClaudeCommandAdapter(store)

        context = {
            "workflow_mode": "github",
            "github_repo": "owner/repo",
            "target_repo": "/local/clone",
            "cycle": 1,
            "issue_number": 3,
            "branch_name": "feat/issue-3/cursor/cycle-1",
            "pr_number": 4,
            "base_branch": "main",
        }

        prompt = adapter._build_prompt("issue-3", "github-review", "Review", context)
        assert "gh pr diff 4 --repo owner/repo" in prompt
        assert "gh pr review 4 --repo owner/repo" in prompt


class TestCodexReviewProvenance:
    def test_github_prompt_includes_codex_identity(self):
        from orchestrator.adapters.codex import CodexCommandAdapter

        store = MagicMock()
        store.task_dir.return_value = MagicMock()
        adapter = CodexCommandAdapter(store)

        context = {
            "workflow_mode": "github",
            "github_repo": "owner/repo",
            "cycle": 1,
            "issue_number": 3,
            "pr_number": 4,
            "base_branch": "main",
        }

        prompt = adapter._build_prompt("issue-3", "github-final-review", "Review", context)
        assert "@codex-agent" in prompt
        assert "final reviewer" in prompt

    def test_github_prompt_uses_github_repo(self):
        from orchestrator.adapters.codex import CodexCommandAdapter

        store = MagicMock()
        store.task_dir.return_value = MagicMock()
        adapter = CodexCommandAdapter(store)

        context = {
            "workflow_mode": "github",
            "github_repo": "owner/repo",
            "target_repo": "/local/clone",
            "cycle": 1,
            "issue_number": 3,
            "pr_number": 4,
            "base_branch": "main",
        }

        prompt = adapter._build_prompt("issue-3", "github-final-review", "Review", context)
        assert "gh pr diff 4 --repo owner/repo" in prompt


class TestReviewsAreNotMerged:
    def test_claude_and_codex_have_distinct_identities(self):
        from orchestrator.adapters.claude_adapter import ClaudeCommandAdapter
        from orchestrator.adapters.codex import CodexCommandAdapter

        store = MagicMock()
        store.task_dir.return_value = MagicMock()

        ctx = {
            "workflow_mode": "github",
            "github_repo": "owner/repo",
            "cycle": 1,
            "issue_number": 3,
            "branch_name": "feat/issue-3/cursor/cycle-1",
            "pr_number": 4,
            "base_branch": "main",
        }

        claude_prompt = ClaudeCommandAdapter(store)._build_prompt("t", "r", "R", ctx)
        codex_prompt = CodexCommandAdapter(store)._build_prompt("t", "r", "R", ctx)

        assert "@claude-agent" in claude_prompt
        assert "@codex-agent" not in claude_prompt

        assert "@codex-agent" in codex_prompt
        assert "@claude-agent" not in codex_prompt


# ======================================================================
# Part B — Loop fix tests
# ======================================================================


def _make_orchestrator():
    """Build a GitHubRunOrchestrator with mocked dependencies."""
    from orchestrator.application.github_run_orchestrator import GitHubRunOrchestrator
    from orchestrator.application.github_task_service import GitHubTaskService

    store = MagicMock()
    github = MagicMock()
    github.repo = "owner/repo"

    task_service = MagicMock(spec=GitHubTaskService)
    task_service.base_branch = "main"
    task_service.pr_title_pattern = "[{type}][Issue #{issue}][{agent}] {summary}"

    orch = GitHubRunOrchestrator(
        task_service=task_service,
        github=github,
        store=store,
    )
    return orch, task_service, github


class TestPostStepAdvanceForceAdvance:
    """_post_step_advance must advance even when PR is not detected."""

    def test_advance_called_when_pr_detected_on_first_try(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType
        from orchestrator.domain.github_workflow import GitHubNextStep, GitHubTaskState

        orch, ts, _ = _make_orchestrator()
        ts.detect_pr.return_value = 10

        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
            state=GitHubTaskState.CURSOR_IMPLEMENTING,
        )
        step = GitHubNextStep(
            agent=AgentRole.CURSOR, action="open_pr",
            instruction="", state_after=GitHubTaskState.PR_OPENED,
        )

        orch._post_step_advance(task, step)
        ts.advance.assert_called_once_with("issue-5")

    def test_advance_called_even_when_pr_never_detected(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType
        from orchestrator.domain.github_workflow import GitHubNextStep, GitHubTaskState

        orch, ts, _ = _make_orchestrator()
        ts.detect_pr.return_value = None

        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
            state=GitHubTaskState.CURSOR_IMPLEMENTING,
        )
        step = GitHubNextStep(
            agent=AgentRole.CURSOR, action="open_pr",
            instruction="", state_after=GitHubTaskState.PR_OPENED,
        )

        orch._post_step_advance(task, step)
        ts.advance.assert_called_once_with("issue-5")

    def test_advance_called_for_implement_action_too(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType
        from orchestrator.domain.github_workflow import GitHubNextStep, GitHubTaskState

        orch, ts, _ = _make_orchestrator()
        ts.detect_pr.return_value = None

        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
            state=GitHubTaskState.ISSUE_CLAIMED,
        )
        step = GitHubNextStep(
            agent=AgentRole.CURSOR, action="implement",
            instruction="", state_after=GitHubTaskState.CURSOR_IMPLEMENTING,
        )

        orch._post_step_advance(task, step)
        ts.advance.assert_called_once()


class TestPrDetectionRetry:
    """_detect_pr_with_retry polls and eventually gives up."""

    def test_succeeds_on_third_attempt(self):
        orch, ts, _ = _make_orchestrator()
        ts.detect_pr.side_effect = [None, None, 10]

        with patch("orchestrator.application.github_run_orchestrator.time.sleep"):
            result = orch._detect_pr_with_retry("issue-5", max_attempts=4, delay=0)

        assert result == 10
        assert ts.detect_pr.call_count == 3

    def test_returns_none_after_exhausted(self):
        orch, ts, _ = _make_orchestrator()
        ts.detect_pr.return_value = None

        with patch("orchestrator.application.github_run_orchestrator.time.sleep"):
            result = orch._detect_pr_with_retry("issue-5", max_attempts=3, delay=0)

        assert result is None
        assert ts.detect_pr.call_count == 3

    def test_succeeds_on_first_attempt_no_sleep(self):
        orch, ts, _ = _make_orchestrator()
        ts.detect_pr.return_value = 7

        with patch("orchestrator.application.github_run_orchestrator.time.sleep") as mock_sleep:
            result = orch._detect_pr_with_retry("issue-5", max_attempts=4, delay=1.0)

        assert result == 7
        mock_sleep.assert_not_called()


class TestSameStepRepetitionGuard:
    """The loop must not repeat the same step more than _MAX_SAME_STEP_REPEATS times."""

    def test_loop_suspends_on_repeated_step(self):
        from orchestrator.application.github_run_orchestrator import (
            GitHubRunOrchestrator,
            _MAX_SAME_STEP_REPEATS,
        )
        from orchestrator.domain.github_models import GitHubTask, GitHubTaskState, WorkType

        orch, ts, gh = _make_orchestrator()

        stuck_task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
            state=GitHubTaskState.CURSOR_IMPLEMENTING,
        )
        ts.get_task.return_value = stuck_task
        ts.detect_pr.return_value = None

        fake_adapter = MagicMock()
        fake_adapter.name = "mock"
        fake_adapter.capability = MagicMock()
        fake_adapter.capability.value = "automatic"
        fake_adapter.execute.return_value = ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            artifact_written=True,
            message="ok",
        )
        orch.adapters[AgentRole.CURSOR] = fake_adapter

        with patch("orchestrator.application.github_run_orchestrator.time.sleep"):
            result = orch._execute_loop(stuck_task)

        assert result.run_status == RunStatus.SUSPENDED
        assert "repeated" in result.message.lower() or "same" in result.message.lower()
        assert fake_adapter.execute.call_count <= _MAX_SAME_STEP_REPEATS + 1

    def test_loop_does_not_trigger_guard_on_advancing_steps(self):
        from orchestrator.domain.github_models import GitHubTask, GitHubTaskState, WorkType

        orch, ts, gh = _make_orchestrator()

        call_count = [0]

        def progressing_get_task(name):
            call_count[0] += 1
            states = [
                GitHubTaskState.CURSOR_IMPLEMENTING,
                GitHubTaskState.PR_OPENED,
                GitHubTaskState.CLAUDE_REVIEWING,
                GitHubTaskState.CODEX_REVIEWING,
                GitHubTaskState.APPROVED,
            ]
            idx = min(call_count[0] - 1, len(states) - 1)
            return GitHubTask(
                name="issue-5", repo="owner/repo", issue_number=5,
                issue_title="Test", work_type=WorkType.FEAT,
                branch_name="feat/issue-5/cursor/cycle-1",
                state=states[idx],
                pr_number=10,
            )

        ts.get_task.side_effect = progressing_get_task
        ts.detect_pr.return_value = 10
        ts.detect_review_state.return_value = "APPROVED"

        fake_adapter = MagicMock()
        fake_adapter.name = "mock"
        fake_adapter.capability = MagicMock()
        fake_adapter.capability.value = "automatic"
        fake_adapter.execute.return_value = ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            artifact_written=True,
            message="ok",
        )
        for role in AgentRole:
            orch.adapters[role] = fake_adapter

        gh.get_latest_review_state.return_value = "APPROVED"

        with patch("orchestrator.application.github_run_orchestrator.time.sleep"):
            result = orch._execute_loop(
                GitHubTask(
                    name="issue-5", repo="owner/repo", issue_number=5,
                    issue_title="Test", work_type=WorkType.FEAT,
                    branch_name="feat/issue-5/cursor/cycle-1",
                    state=GitHubTaskState.CURSOR_IMPLEMENTING,
                    pr_number=10,
                )
            )

        assert result.run_status == RunStatus.COMPLETED


class TestOrchestratorProvenanceHelpers:
    def test_post_provenance_posts_issue_comment(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType

        orch, _, gh = _make_orchestrator()
        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
        )

        orch._post_provenance(task, "Test provenance body")
        gh.add_issue_comment.assert_called_once_with(5, "Test provenance body")

    def test_post_provenance_tolerates_github_error(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType
        from orchestrator.infrastructure.github_service import GitHubError

        orch, _, gh = _make_orchestrator()
        gh.add_issue_comment.side_effect = GitHubError("network error")

        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
        )
        orch._post_provenance(task, "body")

    def test_check_formal_review_posted_true_when_review_exists(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType

        orch, _, gh = _make_orchestrator()
        gh.get_latest_review_state.return_value = "APPROVED"

        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
            pr_number=10,
        )
        assert orch._check_formal_review_posted(task, AgentRole.CLAUDE) is True

    def test_check_formal_review_posted_false_when_none(self):
        from orchestrator.domain.github_models import GitHubTask, WorkType

        orch, _, gh = _make_orchestrator()
        gh.get_latest_review_state.return_value = None

        task = GitHubTask(
            name="issue-5", repo="owner/repo", issue_number=5,
            issue_title="Test", work_type=WorkType.FEAT,
            branch_name="feat/issue-5/cursor/cycle-1",
            pr_number=10,
        )
        assert orch._check_formal_review_posted(task, AgentRole.CODEX) is False
