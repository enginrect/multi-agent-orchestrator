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
import re
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


def _parse_issue_number_from_url(url: str) -> int | None:
    """Extract the issue number from a GitHub issue URL.

    ``gh issue create`` outputs URLs like:
      https://github.com/owner/repo/issues/42
    """
    match = re.search(r"/issues/(\d+)", url)
    if match:
        return int(match.group(1))
    parts = url.strip().rstrip("/").split("/")
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    return None


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
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise GitHubError(
                f"gh returned non-JSON output for: {' '.join(cmd)}\n"
                f"stdout (first 200 chars): {output[:200]!r}\n"
                f"stderr: {proc.stderr.strip()[:200]!r}",
                stderr=proc.stderr.strip(),
            ) from exc

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
        """Create a GitHub issue and return structured data.

        ``gh issue create`` outputs a plain URL, not JSON.  We parse the
        issue number from the URL, then fetch full issue data via
        ``get_issue`` for a reliable structured response.
        """
        cmd = ["issue", "create", *self._repo_args(), "--title", title, "--body", body]
        for label in labels or []:
            cmd.extend(["--label", label])
        url = self._run_gh(cmd, parse_json=False)

        issue_number = _parse_issue_number_from_url(url)
        if issue_number is None:
            raise GitHubError(
                f"Could not parse issue number from gh output: {url!r}"
            )

        return self.get_issue(issue_number)

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

    def reopen_issue(self, number: int) -> None:
        self._run_gh([
            "issue", "reopen", str(number),
            *self._repo_args(),
        ], parse_json=False)

    def list_issues(
        self,
        state: str = "open",
        limit: int = 30,
        labels: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        cmd = [
            "issue", "list",
            *self._repo_args(),
            "--state", state,
            "--limit", str(limit),
            "--json", "number,title,state,labels,url",
        ]
        for label in labels or []:
            cmd.extend(["--label", label])
        return self._run_gh(cmd)

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

    def add_pr_comment(self, pr_number: int, body: str) -> None:
        """Post a conversation comment on a pull request."""
        self._run_gh([
            "pr", "comment", str(pr_number),
            *self._repo_args(),
            "--body", body,
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

    # ------------------------------------------------------------------
    # Local repo validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_local_repo(local_repo_path: str, expected_repo: str) -> None:
        """Verify that a local git clone matches the expected GitHub repo.

        Compares the ``origin`` remote URL against ``expected_repo``
        (``owner/repo`` format).  Raises :class:`GitHubError` on mismatch
        to prevent cross-repo contamination.
        """
        import os

        if not os.path.isdir(local_repo_path):
            raise GitHubError(
                f"--local-repo path does not exist or is not a directory: "
                f"{local_repo_path}"
            )

        git_dir = os.path.join(local_repo_path, ".git")
        if not os.path.isdir(git_dir):
            raise GitHubError(
                f"--local-repo path is not a git repository "
                f"(no .git directory): {local_repo_path}"
            )

        try:
            proc = subprocess.run(
                ["git", "-C", local_repo_path, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise GitHubError("git CLI not found; cannot validate --local-repo")

        if proc.returncode != 0:
            raise GitHubError(
                f"Could not read 'origin' remote from {local_repo_path}: "
                f"{proc.stderr.strip()}"
            )

        remote_url = proc.stdout.strip()
        remote_slug = _extract_repo_slug(remote_url)
        expected_slug = expected_repo.lower().strip("/")

        if remote_slug != expected_slug:
            raise GitHubError(
                f"Local repo mismatch — aborting to prevent cross-repo contamination.\n"
                f"  --repo:       {expected_repo}\n"
                f"  --local-repo: {local_repo_path}\n"
                f"  origin URL:   {remote_url}\n"
                f"  resolved to:  {remote_slug}\n"
                f"  expected:     {expected_slug}"
            )

    @staticmethod
    def get_current_branch(repo_path: str) -> Optional[str]:
        """Return the currently checked-out branch in a local repo, or None."""
        try:
            proc = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                branch = proc.stdout.strip()
                return branch if branch != "HEAD" else None
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    @staticmethod
    def checkout_branch(repo_path: str, branch: str) -> bool:
        """Attempt to checkout a branch in the local repo. Returns True on success."""
        try:
            proc = subprocess.run(
                ["git", "-C", repo_path, "checkout", branch],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


def _extract_repo_slug(remote_url: str) -> str:
    """Normalize a git remote URL to ``owner/repo`` lowercase form.

    Handles SSH (``git@github.com:owner/repo.git``), HTTPS
    (``https://github.com/owner/repo.git``), and bare ``owner/repo``.
    """
    url = remote_url.strip()
    url = re.sub(r"\.git$", "", url)
    ssh_match = re.match(r"^git@[^:]+:(.+)$", url)
    if ssh_match:
        return ssh_match.group(1).lower().strip("/")
    https_match = re.match(r"^https?://[^/]+/(.+)$", url)
    if https_match:
        return https_match.group(1).lower().strip("/")
    return url.lower().strip("/")
