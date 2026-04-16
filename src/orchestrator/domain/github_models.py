"""GitHub-native domain models and state machine.

Defines the state machine for issue-driven, PR-based workflows where
agents collaborate through branches, commits, and PR reviews instead
of file artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .errors import InvalidTransitionError
from .models import AgentRole, RunStatus, _now_iso


class WorkType(str, Enum):
    """Classification of the work being done.

    Repository-agnostic: applies to backend, frontend, mobile, infra,
    automation, documentation, or any other codebase.
    """

    FEAT = "feat"
    MODIFY = "modify"
    FIX = "fix"
    REFACTOR = "refactor"
    DOCS = "docs"
    CHORE = "chore"
    OPS = "ops"
    TEST = "test"
    HOTFIX = "hotfix"


WORK_TYPE_LABELS: dict[WorkType, str] = {
    WorkType.FEAT: "Feature",
    WorkType.MODIFY: "Modify",
    WorkType.FIX: "Fix",
    WorkType.REFACTOR: "Refactor",
    WorkType.DOCS: "Docs",
    WorkType.CHORE: "Chore",
    WorkType.OPS: "Ops",
    WorkType.TEST: "Test",
    WorkType.HOTFIX: "Hotfix",
}


class GitHubTaskState(str, Enum):
    """States for a GitHub-native review workflow.

    Lifecycle: Issue claimed -> Cursor implements on branch -> PR opened ->
    Claude reviews PR -> Codex reviews PR -> PR approved -> PR merged.
    """

    ISSUE_CLAIMED = "issue_claimed"
    CURSOR_IMPLEMENTING = "cursor_implementing"
    PR_OPENED = "pr_opened"
    CLAUDE_REVIEWING = "claude_reviewing"
    CURSOR_REWORKING = "cursor_reworking"
    CODEX_REVIEWING = "codex_reviewing"
    APPROVED = "approved"
    ESCALATED = "escalated"
    MERGED = "merged"


GITHUB_TRANSITIONS: dict[GitHubTaskState, list[GitHubTaskState]] = {
    GitHubTaskState.ISSUE_CLAIMED: [
        GitHubTaskState.CURSOR_IMPLEMENTING,
    ],
    GitHubTaskState.CURSOR_IMPLEMENTING: [
        GitHubTaskState.PR_OPENED,
    ],
    GitHubTaskState.PR_OPENED: [
        GitHubTaskState.CLAUDE_REVIEWING,
    ],
    GitHubTaskState.CLAUDE_REVIEWING: [
        GitHubTaskState.CODEX_REVIEWING,
        GitHubTaskState.CURSOR_REWORKING,
    ],
    GitHubTaskState.CURSOR_REWORKING: [
        GitHubTaskState.CODEX_REVIEWING,
        GitHubTaskState.CLAUDE_REVIEWING,
    ],
    GitHubTaskState.CODEX_REVIEWING: [
        GitHubTaskState.APPROVED,
        GitHubTaskState.CURSOR_REWORKING,
        GitHubTaskState.ESCALATED,
    ],
    GitHubTaskState.APPROVED: [
        GitHubTaskState.MERGED,
    ],
    GitHubTaskState.ESCALATED: [],
    GitHubTaskState.MERGED: [],
}


def validate_github_transition(
    current: GitHubTaskState, target: GitHubTaskState
) -> None:
    allowed = GITHUB_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise InvalidTransitionError(current.value, target.value)


def is_github_terminal(state: GitHubTaskState) -> bool:
    return len(GITHUB_TRANSITIONS.get(state, [])) == 0


# ------------------------------------------------------------------
# GitHub-specific data structures
# ------------------------------------------------------------------


@dataclass
class GitHubStateTransition:
    """Record of a single state change in a GitHub workflow."""

    from_state: GitHubTaskState
    to_state: GitHubTaskState
    timestamp: str
    pr_number: Optional[int] = None
    review_state: Optional[str] = None
    note: Optional[str] = None


@dataclass
class GitHubTask:
    """A review task backed by a GitHub Issue.

    Tracks the full lifecycle from issue claim through PR merge.
    Local ``state.yaml`` serves as the orchestrator's tracking ledger;
    GitHub is the primary collaboration surface.
    """

    name: str
    repo: str
    issue_number: int
    issue_title: str = ""
    work_type: WorkType = WorkType.FEAT
    state: GitHubTaskState = GitHubTaskState.ISSUE_CLAIMED
    run_status: RunStatus = RunStatus.IDLE
    cycle: int = 1
    max_cycles: int = 2
    branch_name: str = ""
    pr_number: Optional[int] = None
    pr_url: str = ""
    description: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    history: list[GitHubStateTransition] = field(default_factory=list)

    def record_transition(
        self,
        new_state: GitHubTaskState,
        pr_number: Optional[int] = None,
        review_state: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        transition = GitHubStateTransition(
            from_state=self.state,
            to_state=new_state,
            timestamp=_now_iso(),
            pr_number=pr_number,
            review_state=review_state,
            note=note,
        )
        self.history.append(transition)
        self.state = new_state
        self.updated_at = _now_iso()

    @property
    def is_terminal(self) -> bool:
        return is_github_terminal(self.state)

    @property
    def task_dir_name(self) -> str:
        return f"issue-{self.issue_number}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "repo": self.repo,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "work_type": self.work_type.value,
            "state": self.state.value,
            "run_status": self.run_status.value,
            "cycle": self.cycle,
            "max_cycles": self.max_cycles,
            "branch_name": self.branch_name,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workflow_mode": "github",
            "history": [
                {
                    "from_state": t.from_state.value,
                    "to_state": t.to_state.value,
                    "timestamp": t.timestamp,
                    "pr_number": t.pr_number,
                    "review_state": t.review_state,
                    "note": t.note,
                }
                for t in self.history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> GitHubTask:
        history = [
            GitHubStateTransition(
                from_state=GitHubTaskState(h["from_state"]),
                to_state=GitHubTaskState(h["to_state"]),
                timestamp=h["timestamp"],
                pr_number=h.get("pr_number"),
                review_state=h.get("review_state"),
                note=h.get("note"),
            )
            for h in data.get("history", [])
        ]
        wt_raw = data.get("work_type", "feat")
        try:
            work_type = WorkType(wt_raw)
        except ValueError:
            work_type = WorkType.FEAT

        return cls(
            name=data["name"],
            repo=data["repo"],
            issue_number=data["issue_number"],
            issue_title=data.get("issue_title", ""),
            work_type=work_type,
            state=GitHubTaskState(data["state"]),
            run_status=RunStatus(data.get("run_status", "idle")),
            cycle=data.get("cycle", 1),
            max_cycles=data.get("max_cycles", 2),
            branch_name=data.get("branch_name", ""),
            pr_number=data.get("pr_number"),
            pr_url=data.get("pr_url", ""),
            description=data.get("description", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            history=history,
        )
