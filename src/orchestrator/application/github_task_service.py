"""GitHub-backed task lifecycle operations.

Creates and advances tasks that are backed by GitHub Issues. Task state
is tracked locally in ``state.yaml`` but the primary collaboration
surface is GitHub (PRs, reviews, comments).
"""

from __future__ import annotations

from typing import Optional

from ..domain.errors import TaskNotFoundError
from ..domain.github_models import (
    GitHubTask,
    GitHubTaskState,
    WorkType,
    validate_github_transition,
)
from ..domain.github_workflow import generate_branch_name, generate_pr_title, resolve_github_next_step
from ..domain.models import ReviewOutcome
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.github_service import GitHubService

import yaml


class GitHubTaskService:
    """Manages the lifecycle of GitHub-backed tasks."""

    def __init__(
        self,
        store: FileStateStore,
        github: GitHubService,
        branch_pattern: str = "{type}/issue-{issue}/{agent}/cycle-{cycle}",
        pr_title_pattern: str = "[{type}][Issue #{issue}][{agent}] {summary}",
        labels: Optional[dict[str, str]] = None,
        base_branch: str = "main",
        max_cycles: int = 2,
    ) -> None:
        self.store = store
        self.github = github
        self.branch_pattern = branch_pattern
        self.pr_title_pattern = pr_title_pattern
        self.labels = labels or {}
        self.base_branch = base_branch
        self.max_cycles = max_cycles

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def claim_issue(
        self,
        issue_number: int,
        work_type: WorkType = WorkType.FEAT,
    ) -> GitHubTask:
        """Claim a GitHub issue and create a local task for tracking.

        Args:
            issue_number: GitHub issue number.
            work_type: Classification of the work (feat, fix, refactor, ...).
        """
        issue = self.github.get_issue(issue_number)

        task_name = f"issue-{issue_number}"
        branch = generate_branch_name(
            issue_number,
            work_type=work_type.value,
            agent="cursor",
            cycle=1,
            pattern=self.branch_pattern,
        )

        task = GitHubTask(
            name=task_name,
            repo=self.github.repo,
            issue_number=issue_number,
            issue_title=issue.get("title", ""),
            work_type=work_type,
            description=issue.get("body", "") or "",
            branch_name=branch,
            max_cycles=self.max_cycles,
        )

        self.store.ensure_workspace()
        self.store.create_task_dir(task_name)
        self._save_github_task(task)

        if label := self.labels.get("claimed"):
            self.github.add_labels(issue_number, [label])

        self.github.add_issue_comment(
            issue_number,
            f"Orchestrator claimed this issue. Branch: `{branch}`",
        )

        task.record_transition(
            GitHubTaskState.CURSOR_IMPLEMENTING,
            note="Issue claimed, ready for implementation.",
        )
        self._save_github_task(task)

        return task

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_task(self, task_name: str) -> GitHubTask:
        return self._load_github_task(task_name)

    def list_tasks(self) -> list[str]:
        all_tasks = self.store.list_active_tasks()
        github_tasks = []
        for name in all_tasks:
            try:
                data = self._load_raw(name)
                if data.get("workflow_mode") == "github":
                    github_tasks.append(name)
            except (TaskNotFoundError, KeyError):
                continue
        return github_tasks

    # ------------------------------------------------------------------
    # Advance
    # ------------------------------------------------------------------

    def advance(
        self,
        task_name: str,
        review_state: Optional[str] = None,
    ) -> GitHubTask:
        """Advance a GitHub task based on its current state and GitHub signals.

        For PR_OPENED / CLAUDE_REVIEWING / CODEX_REVIEWING, the review
        state is read from GitHub if not provided.
        """
        task = self._load_github_task(task_name)

        if task.is_terminal:
            return task

        new_state = self._resolve_next_state(task, review_state)
        if new_state is None:
            return task

        validate_github_transition(task.state, new_state)

        if (
            new_state == GitHubTaskState.CURSOR_REWORKING
            and task.state == GitHubTaskState.CODEX_REVIEWING
        ):
            task.cycle += 1
            new_branch = generate_branch_name(
                task.issue_number,
                work_type=task.work_type.value,
                agent="cursor",
                cycle=task.cycle,
                pattern=self.branch_pattern,
            )
            task.branch_name = new_branch

        task.record_transition(
            new_state,
            pr_number=task.pr_number,
            review_state=review_state,
        )
        self._save_github_task(task)

        self._update_labels(task)

        return task

    def register_pr(self, task_name: str, pr_number: int, pr_url: str = "") -> GitHubTask:
        """Record that a PR was opened for this task."""
        task = self._load_github_task(task_name)

        task.pr_number = pr_number
        task.pr_url = pr_url

        if task.state == GitHubTaskState.CURSOR_IMPLEMENTING:
            task.record_transition(
                GitHubTaskState.PR_OPENED,
                pr_number=pr_number,
                note=f"PR #{pr_number} opened.",
            )

        self._save_github_task(task)
        return task

    def detect_pr(self, task_name: str) -> Optional[int]:
        """Check GitHub for a PR on the task's branch. Returns PR number or None."""
        task = self._load_github_task(task_name)
        if task.pr_number:
            return task.pr_number

        prs = self.github.list_prs(head=task.branch_name, state="open")
        if prs:
            pr = prs[0]
            self.register_pr(task_name, pr["number"], pr.get("url", ""))
            return pr["number"]
        return None

    def detect_review_state(self, task_name: str) -> Optional[str]:
        """Read the latest review decision from GitHub for the task's PR."""
        task = self._load_github_task(task_name)
        if not task.pr_number:
            return None
        return self.github.get_latest_review_state(task.pr_number)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, task_name: str, method: str = "squash") -> GitHubTask:
        """Merge the PR and close the issue."""
        task = self._load_github_task(task_name)
        if task.state != GitHubTaskState.APPROVED:
            raise ValueError(
                f"Cannot merge task '{task_name}' in state '{task.state.value}'. "
                f"Only approved tasks can be merged."
            )
        if not task.pr_number:
            raise ValueError(f"No PR number recorded for task '{task_name}'.")

        self.github.merge_pr(task.pr_number, method=method)

        task.record_transition(
            GitHubTaskState.MERGED,
            pr_number=task.pr_number,
            note=f"PR #{task.pr_number} merged via {method}.",
        )
        self._save_github_task(task)

        self.github.close_issue(task.issue_number)

        if label := self.labels.get("approved"):
            self.github.add_labels(task.issue_number, [label])

        return task

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_next_state(
        self,
        task: GitHubTask,
        review_state: Optional[str],
    ) -> Optional[GitHubTaskState]:
        state = task.state

        if state == GitHubTaskState.ISSUE_CLAIMED:
            return GitHubTaskState.CURSOR_IMPLEMENTING

        if state == GitHubTaskState.CURSOR_IMPLEMENTING:
            return GitHubTaskState.PR_OPENED

        if state == GitHubTaskState.PR_OPENED:
            return GitHubTaskState.CLAUDE_REVIEWING

        if state == GitHubTaskState.CLAUDE_REVIEWING:
            rs = review_state or ""
            if rs.upper() in ("CHANGES_REQUESTED",):
                return GitHubTaskState.CURSOR_REWORKING
            return GitHubTaskState.CODEX_REVIEWING

        if state == GitHubTaskState.CURSOR_REWORKING:
            return GitHubTaskState.CODEX_REVIEWING

        if state == GitHubTaskState.CODEX_REVIEWING:
            rs = review_state or ""
            if rs.upper() in ("APPROVED",):
                return GitHubTaskState.APPROVED
            if rs.upper() in ("CHANGES_REQUESTED",):
                if task.cycle >= task.max_cycles:
                    return GitHubTaskState.ESCALATED
                return GitHubTaskState.CURSOR_REWORKING
            return GitHubTaskState.APPROVED

        if state == GitHubTaskState.APPROVED:
            return GitHubTaskState.MERGED

        return None

    def _update_labels(self, task: GitHubTask) -> None:
        """Update issue labels based on current state."""
        state = task.state
        label_map = {
            GitHubTaskState.CURSOR_IMPLEMENTING: "in_progress",
            GitHubTaskState.PR_OPENED: "review",
            GitHubTaskState.CLAUDE_REVIEWING: "review",
            GitHubTaskState.CODEX_REVIEWING: "review",
            GitHubTaskState.APPROVED: "approved",
        }
        label_key = label_map.get(state)
        if label_key and label_key in self.labels:
            self.github.add_labels(task.issue_number, [self.labels[label_key]])

    def _save_github_task(self, task: GitHubTask) -> None:
        state_file = self.store.task_dir(task.name) / "state.yaml"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            yaml.dump(
                task.to_dict(), f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def _load_github_task(self, task_name: str) -> GitHubTask:
        data = self._load_raw(task_name)
        return GitHubTask.from_dict(data)

    def _load_raw(self, task_name: str) -> dict:
        state_file = self.store.task_dir(task_name) / "state.yaml"
        if not state_file.is_file():
            raise TaskNotFoundError(task_name)
        with open(state_file) as f:
            return yaml.safe_load(f)
