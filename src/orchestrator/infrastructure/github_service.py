"""GitHub integration via the ``gh`` CLI.

Wraps ``gh issue``, ``gh pr``, and ``gh api`` commands for:
- Issue lifecycle (view, create, comment, label, close)
- Pull request lifecycle (create, view, list, merge)
- PR review management (list reviews, post review)
- Branch existence checks

All methods are synchronous, returning parsed JSON dicts.
Authentication is handled externally via ``gh auth login``.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

from ..domain.errors import OrchestratorError


class GitHubError(OrchestratorError):
    """Raised when a ``gh`` CLI operation fails."""

    def __init__(self, message: str, stderr: str = "", exit_code: int = 1) -> None:
        self.stderr = stderr
        self.exit_code = exit_code
        super().__init__(message)


class IssueNotFoundError(GitHubError):
    """Raised when a GitHub issue does not exist."""


class PRNotFoundError(GitHubError):
    """Raised when a GitHub pull request does not exist."""


class GitHubAuthError(GitHubError):
    """Raised when ``gh`` is not authenticated."""


class GitHubService:
    """Wraps the ``gh`` CLI for GitHub API operations.

    Args:
        repo: Full repository name in ``owner/repo`` format.
        gh_command: Path to ``gh`` binary (default: ``gh``).
    """

    def __init__(self, repo: str, gh_command: str = "gh") -> None:
        self.repo = repo
        self._gh = gh_command

    def verify_auth(self) -> bool:
        """Check that ``gh`` is authenticated. Returns True or raises."""
        try:
            result = subprocess.run(
                [self._gh, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                raise GitHubAuthError(
                    f"gh is not authenticated: {result.stderr.strip()}",
                    stderr=result.stderr,
                    exit_code=result.returncode,
                )
            return True
        except FileNotFoundError:
            raise GitHubAuthError(f"gh CLI not found at: {self._gh}")

    # ------------------------------------------------------------------
    # Internal runner
    # ------------------------------------------------------------------

    def _run_gh(
        self,
        args: list[str],
        *,
        parse_json: bool = True,
        timeout: int = 30,
    ) -> Any:
        """Run a ``gh`` subcommand and return parsed output.

        All repo-scoped commands automatically include ``--repo``.
        """
        cmd = [self._gh, *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise GitHubError(f"gh CLI not found: {self._gh}")
        except subprocess.TimeoutExpired:
            raise GitHubError(f"gh command timed out after {timeout}s: {' '.join(cmd)}")

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if "not found" in stderr.lower() or "could not resolve" in stderr.lower():
                if "issue" in " ".join(args):
                    raise IssueNotFoundError(f"Issue not found: {stderr}", stderr=stderr)
                if "pr" in " ".join(args):
                    raise PRNotFoundError(f"PR not found: {stderr}", stderr=stderr)
            raise GitHubError(
                f"gh command failed (exit {proc.returncode}): {stderr}",
                stderr=stderr,
                exit_code=proc.returncode,
            )

        if not parse_json:
            return proc.stdout.strip()

        output = proc.stdout.strip()
        if not output:
            return {}
        return json.loads(output)

    def _repo_args(self) -> list[str]:
        return ["--repo", self.repo]

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def get_issue(self, number: int) -> dict[str, Any]:
        return self._run_gh([
            "issue", "view", str(number),
            *self._repo_args(),
            "--json", "number,title,body,state,labels,assignees,url",
        ])

    def create_issue(
        self,
        title: str,
        body: str,
        labels: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        cmd = ["issue", "create", *self._repo_args(), "--title", title, "--body", body]
        for label in labels or []:
            cmd.extend(["--label", label])
        return self._run_gh(cmd)

    def add_issue_comment(self, number: int, body: str) -> None:
        self._run_gh([
            "issue", "comment", str(number),
            *self._repo_args(),
            "--body", body,
        ], parse_json=False)

    def add_labels(self, number: int, labels: list[str]) -> None:
        if not labels:
            return
        cmd = ["issue", "edit", str(number), *self._repo_args()]
        for label in labels:
            cmd.extend(["--add-label", label])
        self._run_gh(cmd, parse_json=False)

    def remove_labels(self, number: int, labels: list[str]) -> None:
        if not labels:
            return
        cmd = ["issue", "edit", str(number), *self._repo_args()]
        for label in labels:
            cmd.extend(["--remove-label", label])
        self._run_gh(cmd, parse_json=False)

    def close_issue(self, number: int) -> None:
        self._run_gh([
            "issue", "close", str(number),
            *self._repo_args(),
        ], parse_json=False)

    # ------------------------------------------------------------------
    # Pull Requests
    # ------------------------------------------------------------------

    def get_pr(self, number: int) -> dict[str, Any]:
        return self._run_gh([
            "pr", "view", str(number),
            *self._repo_args(),
            "--json", "number,title,body,state,url,headRefName,baseRefName,"
                      "mergeable,reviewDecision,additions,deletions,changedFiles",
        ])

    def list_prs(
        self,
        head: Optional[str] = None,
        state: str = "open",
    ) -> list[dict[str, Any]]:
        cmd = [
            "pr", "list",
            *self._repo_args(),
            "--state", state,
            "--json", "number,title,headRefName,state,url",
        ]
        if head:
            cmd.extend(["--head", head])
        return self._run_gh(cmd)

    def create_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        result = self._run_gh([
            "pr", "create",
            *self._repo_args(),
            "--title", title,
            "--body", body,
            "--head", head,
            "--base", base,
        ], parse_json=False)
        prs = self.list_prs(head=head, state="open")
        return prs[0] if prs else {"url": result}

    def merge_pr(
        self,
        number: int,
        method: str = "squash",
    ) -> None:
        self._run_gh([
            "pr", "merge", str(number),
            *self._repo_args(),
            f"--{method}",
            "--delete-branch",
        ], parse_json=False)

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    def get_pr_reviews(self, pr_number: int) -> list[dict[str, Any]]:
        return self._run_gh([
            "api",
            f"repos/{self.repo}/pulls/{pr_number}/reviews",
        ])

    def get_latest_review_state(self, pr_number: int) -> Optional[str]:
        """Return the most recent non-COMMENTED review state, or None."""
        reviews = self.get_pr_reviews(pr_number)
        for review in reversed(reviews):
            state = review.get("state", "").upper()
            if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                return state
        return None

    def create_pr_review(
        self,
        pr_number: int,
        event: str,
        body: str,
    ) -> None:
        """Post a PR review.

        Args:
            event: APPROVE, REQUEST_CHANGES, or COMMENT.
        """
        flag_map = {
            "APPROVE": "--approve",
            "REQUEST_CHANGES": "--request-changes",
            "COMMENT": "--comment",
        }
        flag = flag_map.get(event.upper(), "--comment")
        self._run_gh([
            "pr", "review", str(pr_number),
            *self._repo_args(),
            flag,
            "--body", body,
        ], parse_json=False)

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------

    def branch_exists(self, branch: str) -> bool:
        try:
            result = self._run_gh([
                "api",
                f"repos/{self.repo}/branches/{branch}",
            ])
            return bool(result)
        except GitHubError:
            return False
