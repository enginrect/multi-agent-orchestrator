"""GitHub-native workflow step resolution.

Determines the next action for a GitHub-backed task based on its current
state, cycle, and PR/review status. Parallel to ``workflow.py`` which
handles the file-artifact workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .github_models import WORK_TYPE_LABELS, GitHubTask, GitHubTaskState, WorkType
from .models import AgentRole


@dataclass
class GitHubNextStep:
    """What should happen next for a GitHub-backed task."""

    agent: AgentRole
    action: str
    instruction: str
    state_after: GitHubTaskState


def resolve_github_next_step(task: GitHubTask) -> Optional[GitHubNextStep]:
    """Determine the next action based on current GitHub task state.

    Returns None if the task is in a terminal state.
    """
    state = task.state
    cycle = task.cycle

    if state == GitHubTaskState.ISSUE_CLAIMED:
        branch = task.branch_name or generate_branch_name(
            issue_number=task.issue_number,
            work_type=task.work_type.value,
            agent="cursor",
            cycle=cycle,
        )
        pr_title = generate_pr_title(
            work_type=task.work_type,
            issue_number=task.issue_number,
            agent="Cursor",
            summary=task.issue_title,
        )
        return GitHubNextStep(
            agent=AgentRole.CURSOR,
            action="implement",
            instruction=(
                f"Create branch '{branch}' from the base branch. "
                f"Implement the changes described in issue #{task.issue_number}: "
                f"{task.issue_title}. "
                f"Commit your changes, push the branch, and open a pull request. "
                f"The PR title must follow this format: '{pr_title}'."
            ),
            state_after=GitHubTaskState.CURSOR_IMPLEMENTING,
        )

    if state == GitHubTaskState.CURSOR_IMPLEMENTING:
        return GitHubNextStep(
            agent=AgentRole.CURSOR,
            action="open_pr",
            instruction=(
                f"Continue implementing on branch '{task.branch_name}'. "
                f"When done, commit, push, and open a PR targeting main. "
                f"If the PR is already open, push additional commits."
            ),
            state_after=GitHubTaskState.PR_OPENED,
        )

    if state == GitHubTaskState.PR_OPENED:
        return GitHubNextStep(
            agent=AgentRole.CLAUDE,
            action="review_pr",
            instruction=(
                f"Review PR #{task.pr_number} in {task.repo} (cycle {cycle}). "
                f"Read the diff and all changed files. "
                f"Post a PR review: approve if the implementation is correct, "
                f"or request changes if there are issues. "
                f"You may apply minor fixes by pushing commits to the PR branch."
            ),
            state_after=GitHubTaskState.CLAUDE_REVIEWING,
        )

    if state == GitHubTaskState.CLAUDE_REVIEWING:
        return GitHubNextStep(
            agent=AgentRole.CLAUDE,
            action="review_pr",
            instruction=(
                f"Review PR #{task.pr_number} in {task.repo} (cycle {cycle}). "
                f"Read the diff and all changed files. "
                f"Post your review using the GitHub PR review flow: "
                f"approve, request changes, or comment. "
                f"Apply minor fixes directly by pushing to the branch if needed."
            ),
            state_after=GitHubTaskState.CODEX_REVIEWING,
        )

    if state == GitHubTaskState.CURSOR_REWORKING:
        return GitHubNextStep(
            agent=AgentRole.CURSOR,
            action="rework",
            instruction=(
                f"Address the review feedback on PR #{task.pr_number} "
                f"(cycle {cycle}). Read the review comments, make the "
                f"requested changes, commit, and push to the PR branch "
                f"'{task.branch_name}'."
            ),
            state_after=GitHubTaskState.CODEX_REVIEWING,
        )

    if state == GitHubTaskState.CODEX_REVIEWING:
        return GitHubNextStep(
            agent=AgentRole.CODEX,
            action="final_review",
            instruction=(
                f"Final review of PR #{task.pr_number} in {task.repo} "
                f"(cycle {cycle}). Read the full diff and all review history. "
                f"Post a PR review: approve if the implementation meets all "
                f"requirements, or request changes if there are blocking issues. "
                f"This is the approval gate."
            ),
            state_after=GitHubTaskState.APPROVED,
        )

    return None


def generate_branch_name(
    issue_number: int,
    work_type: str = "feat",
    agent: str = "cursor",
    cycle: int = 1,
    pattern: str = "{type}/issue-{issue}/{agent}/cycle-{cycle}",
) -> str:
    """Generate a branch name from the configured pattern.

    The pattern supports these placeholders:
    - ``{type}`` — work type (feat, fix, refactor, ...)
    - ``{issue}`` — issue number
    - ``{agent}`` — agent name (cursor, claude, codex)
    - ``{cycle}`` — review cycle number
    """
    return pattern.format(
        type=work_type,
        issue=issue_number,
        agent=agent,
        cycle=cycle,
    )


def generate_pr_title(
    work_type: WorkType,
    issue_number: int,
    agent: str,
    summary: str,
    pattern: str = "[{type}][Issue #{issue}][{agent}] {summary}",
) -> str:
    """Generate a PR title from the configured pattern.

    The pattern supports these placeholders:
    - ``{type}`` — human-readable work type label (Feature, Fix, ...)
    - ``{issue}`` — issue number
    - ``{agent}`` — agent name (Cursor, Claude, Codex)
    - ``{summary}`` — short description from the issue title
    """
    label = WORK_TYPE_LABELS.get(work_type, work_type.value.capitalize())
    return pattern.format(
        type=label,
        issue=issue_number,
        agent=agent,
        summary=summary,
    )
