"""Tests for the GitHub-native domain models, state machine, and workflow.

Covers:
- GitHubTaskState transitions (valid and invalid)
- GitHubTask serialization (to_dict / from_dict)
- resolve_github_next_step for each state
- generate_branch_name
"""

import pytest

from orchestrator.domain.errors import InvalidTransitionError
from orchestrator.domain.github_models import (
    GITHUB_TRANSITIONS,
    WORK_TYPE_LABELS,
    GitHubTask,
    GitHubTaskState,
    WorkType,
    is_github_terminal,
    validate_github_transition,
)
from orchestrator.domain.github_workflow import (
    generate_branch_name,
    generate_pr_title,
    resolve_github_next_step,
)
from orchestrator.domain.models import AgentRole


# ======================================================================
# State machine transitions
# ======================================================================


class TestGitHubTransitions:
    def test_issue_claimed_to_implementing(self):
        validate_github_transition(
            GitHubTaskState.ISSUE_CLAIMED,
            GitHubTaskState.CURSOR_IMPLEMENTING,
        )

    def test_implementing_to_pr_opened(self):
        validate_github_transition(
            GitHubTaskState.CURSOR_IMPLEMENTING,
            GitHubTaskState.PR_OPENED,
        )

    def test_pr_opened_to_claude_reviewing(self):
        validate_github_transition(
            GitHubTaskState.PR_OPENED,
            GitHubTaskState.CLAUDE_REVIEWING,
        )

    def test_claude_reviewing_to_codex_reviewing(self):
        validate_github_transition(
            GitHubTaskState.CLAUDE_REVIEWING,
            GitHubTaskState.CODEX_REVIEWING,
        )

    def test_claude_reviewing_to_cursor_reworking(self):
        validate_github_transition(
            GitHubTaskState.CLAUDE_REVIEWING,
            GitHubTaskState.CURSOR_REWORKING,
        )

    def test_cursor_reworking_to_codex_reviewing(self):
        validate_github_transition(
            GitHubTaskState.CURSOR_REWORKING,
            GitHubTaskState.CODEX_REVIEWING,
        )

    def test_cursor_reworking_to_claude_reviewing(self):
        validate_github_transition(
            GitHubTaskState.CURSOR_REWORKING,
            GitHubTaskState.CLAUDE_REVIEWING,
        )

    def test_codex_reviewing_to_approved(self):
        validate_github_transition(
            GitHubTaskState.CODEX_REVIEWING,
            GitHubTaskState.APPROVED,
        )

    def test_codex_reviewing_to_cursor_reworking(self):
        validate_github_transition(
            GitHubTaskState.CODEX_REVIEWING,
            GitHubTaskState.CURSOR_REWORKING,
        )

    def test_codex_reviewing_to_escalated(self):
        validate_github_transition(
            GitHubTaskState.CODEX_REVIEWING,
            GitHubTaskState.ESCALATED,
        )

    def test_approved_to_merged(self):
        validate_github_transition(
            GitHubTaskState.APPROVED,
            GitHubTaskState.MERGED,
        )


class TestGitHubInvalidTransitions:
    def test_issue_claimed_to_approved(self):
        with pytest.raises(InvalidTransitionError):
            validate_github_transition(
                GitHubTaskState.ISSUE_CLAIMED,
                GitHubTaskState.APPROVED,
            )

    def test_merged_to_anything(self):
        with pytest.raises(InvalidTransitionError):
            validate_github_transition(
                GitHubTaskState.MERGED,
                GitHubTaskState.ISSUE_CLAIMED,
            )

    def test_escalated_to_anything(self):
        with pytest.raises(InvalidTransitionError):
            validate_github_transition(
                GitHubTaskState.ESCALATED,
                GitHubTaskState.CURSOR_IMPLEMENTING,
            )

    def test_pr_opened_to_approved(self):
        with pytest.raises(InvalidTransitionError):
            validate_github_transition(
                GitHubTaskState.PR_OPENED,
                GitHubTaskState.APPROVED,
            )


class TestGitHubTerminalStates:
    def test_merged_is_terminal(self):
        assert is_github_terminal(GitHubTaskState.MERGED)

    def test_escalated_is_terminal(self):
        assert is_github_terminal(GitHubTaskState.ESCALATED)

    def test_implementing_is_not_terminal(self):
        assert not is_github_terminal(GitHubTaskState.CURSOR_IMPLEMENTING)

    def test_all_states_have_transition_entry(self):
        for state in GitHubTaskState:
            assert state in GITHUB_TRANSITIONS


# ======================================================================
# GitHubTask serialization
# ======================================================================


class TestGitHubTaskSerialization:
    def _make_task(self) -> GitHubTask:
        return GitHubTask(
            name="issue-42",
            repo="owner/repo",
            issue_number=42,
            issue_title="Fix the bug",
            work_type=WorkType.FIX,
            branch_name="fix/issue-42/cursor/cycle-1",
            pr_number=99,
            pr_url="https://github.com/owner/repo/pull/99",
            description="A test task",
        )

    def test_roundtrip(self):
        task = self._make_task()
        task.record_transition(
            GitHubTaskState.CURSOR_IMPLEMENTING,
            note="Started",
        )
        data = task.to_dict()
        restored = GitHubTask.from_dict(data)

        assert restored.name == task.name
        assert restored.repo == task.repo
        assert restored.issue_number == task.issue_number
        assert restored.work_type == task.work_type
        assert restored.state == task.state
        assert restored.branch_name == task.branch_name
        assert restored.pr_number == task.pr_number
        assert restored.pr_url == task.pr_url
        assert len(restored.history) == 1

    def test_to_dict_includes_workflow_mode(self):
        task = self._make_task()
        data = task.to_dict()
        assert data["workflow_mode"] == "github"
        assert data["work_type"] == "fix"

    def test_from_dict_defaults(self):
        data = {
            "name": "issue-1",
            "repo": "owner/repo",
            "issue_number": 1,
            "state": "issue_claimed",
        }
        task = GitHubTask.from_dict(data)
        assert task.work_type == WorkType.FEAT
        assert task.cycle == 1
        assert task.max_cycles == 2
        assert task.branch_name == ""
        assert task.pr_number is None

    def test_from_dict_unknown_work_type_falls_back(self):
        data = {
            "name": "issue-1",
            "repo": "owner/repo",
            "issue_number": 1,
            "state": "issue_claimed",
            "work_type": "banana",
        }
        task = GitHubTask.from_dict(data)
        assert task.work_type == WorkType.FEAT

    def test_is_terminal_property(self):
        task = self._make_task()
        assert not task.is_terminal
        task.state = GitHubTaskState.MERGED
        assert task.is_terminal


# ======================================================================
# GitHub workflow step resolution
# ======================================================================


class TestResolveGitHubNextStep:
    def _make_task(self, state: GitHubTaskState, **kwargs) -> GitHubTask:
        defaults = {
            "name": "issue-42",
            "repo": "owner/repo",
            "issue_number": 42,
            "issue_title": "Fix bug",
            "work_type": WorkType.FIX,
            "branch_name": "fix/issue-42/cursor/cycle-1",
            "pr_number": 99,
        }
        defaults.update(kwargs)
        task = GitHubTask(**defaults)
        task.state = state
        return task

    def test_issue_claimed_returns_implement(self):
        task = self._make_task(GitHubTaskState.ISSUE_CLAIMED)
        step = resolve_github_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CURSOR
        assert step.action == "implement"
        assert "branch" in step.instruction.lower()
        assert "[Fix]" in step.instruction

    def test_cursor_implementing_returns_open_pr(self):
        task = self._make_task(GitHubTaskState.CURSOR_IMPLEMENTING)
        step = resolve_github_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CURSOR
        assert step.action == "open_pr"

    def test_pr_opened_returns_claude_review(self):
        task = self._make_task(GitHubTaskState.PR_OPENED)
        step = resolve_github_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CLAUDE
        assert step.action == "review_pr"

    def test_claude_reviewing_returns_review(self):
        task = self._make_task(GitHubTaskState.CLAUDE_REVIEWING)
        step = resolve_github_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CLAUDE
        assert "PR #99" in step.instruction

    def test_cursor_reworking_returns_rework(self):
        task = self._make_task(GitHubTaskState.CURSOR_REWORKING)
        step = resolve_github_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CURSOR
        assert step.action == "rework"

    def test_codex_reviewing_returns_final_review(self):
        task = self._make_task(GitHubTaskState.CODEX_REVIEWING)
        step = resolve_github_next_step(task)
        assert step is not None
        assert step.agent == AgentRole.CODEX
        assert step.action == "final_review"

    def test_merged_returns_none(self):
        task = self._make_task(GitHubTaskState.MERGED)
        assert resolve_github_next_step(task) is None

    def test_escalated_returns_none(self):
        task = self._make_task(GitHubTaskState.ESCALATED)
        assert resolve_github_next_step(task) is None

    def test_approved_returns_none(self):
        task = self._make_task(GitHubTaskState.APPROVED)
        assert resolve_github_next_step(task) is None


# ======================================================================
# Branch name generation
# ======================================================================


class TestGenerateBranchName:
    def test_default_pattern(self):
        name = generate_branch_name(42)
        assert name == "feat/issue-42/cursor/cycle-1"

    def test_fix_type(self):
        name = generate_branch_name(42, work_type="fix")
        assert name == "fix/issue-42/cursor/cycle-1"

    def test_cycle_2(self):
        name = generate_branch_name(42, work_type="feat", cycle=2)
        assert name == "feat/issue-42/cursor/cycle-2"

    def test_agent_param(self):
        name = generate_branch_name(42, work_type="docs", agent="claude")
        assert name == "docs/issue-42/claude/cycle-1"

    def test_custom_pattern(self):
        name = generate_branch_name(
            7,
            work_type="refactor",
            agent="cursor",
            cycle=1,
            pattern="{type}/issue-{issue}-{agent}-c{cycle}",
        )
        assert name == "refactor/issue-7-cursor-c1"

    def test_all_work_types(self):
        for wt in WorkType:
            name = generate_branch_name(1, work_type=wt.value)
            assert name.startswith(f"{wt.value}/")


class TestGeneratePRTitle:
    def test_default_pattern_feat(self):
        title = generate_pr_title(WorkType.FEAT, 42, "Cursor", "Add login page")
        assert title == "[Feature][Issue #42][Cursor] Add login page"

    def test_fix_type(self):
        title = generate_pr_title(WorkType.FIX, 99, "Cursor", "Resolve crash")
        assert title == "[Fix][Issue #99][Cursor] Resolve crash"

    def test_all_work_types_have_labels(self):
        for wt in WorkType:
            title = generate_pr_title(wt, 1, "Cursor", "test")
            label = WORK_TYPE_LABELS[wt]
            assert f"[{label}]" in title

    def test_custom_pattern(self):
        title = generate_pr_title(
            WorkType.DOCS,
            15,
            "Cursor",
            "Update README",
            pattern="{type}: #{issue} {summary} ({agent})",
        )
        assert title == "Docs: #15 Update README (Cursor)"
