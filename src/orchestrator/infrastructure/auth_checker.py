"""Auth and tool availability checks for morch.

Each check returns an ``AuthStatus`` indicating whether the tool is
installed, authenticated, and ready to use.  The ``login_hint`` field
provides a human-readable instruction when auth is missing.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class AuthStatus:
    """Result of an auth check for one tool."""

    tool: str
    installed: bool
    authenticated: bool
    version: str = ""
    path: str = ""
    message: str = ""
    login_hint: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.authenticated


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _run_quiet(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"


def _find_binary(name: str) -> Optional[str]:
    return shutil.which(name)


# ------------------------------------------------------------------
# Per-tool checks
# ------------------------------------------------------------------

def check_git() -> AuthStatus:
    """Check git installation and identity configuration."""
    path = _find_binary("git")
    if not path:
        return AuthStatus(
            tool="git",
            installed=False,
            authenticated=False,
            login_hint="Install git: https://git-scm.com/downloads",
        )

    rc, version, _ = _run_quiet(["git", "--version"])
    if rc != 0:
        return AuthStatus(tool="git", installed=False, authenticated=False)

    rc_name, name, _ = _run_quiet(["git", "config", "user.name"])
    rc_email, email, _ = _run_quiet(["git", "config", "user.email"])

    has_identity = rc_name == 0 and bool(name) and rc_email == 0 and bool(email)

    return AuthStatus(
        tool="git",
        installed=True,
        authenticated=has_identity,
        version=version.replace("git version ", ""),
        path=path,
        message=f"{name} <{email}>" if has_identity else "no user identity configured",
        login_hint='git config --global user.name "Your Name" && git config --global user.email "you@example.com"',
    )


def check_github() -> AuthStatus:
    """Check gh CLI installation and authentication."""
    path = _find_binary("gh")
    if not path:
        return AuthStatus(
            tool="github",
            installed=False,
            authenticated=False,
            login_hint="Install gh: https://cli.github.com/ then run: gh auth login",
        )

    rc, version, _ = _run_quiet(["gh", "--version"])
    ver = ""
    if rc == 0:
        for line in version.splitlines():
            if line.startswith("gh version"):
                ver = line.split()[2] if len(line.split()) >= 3 else version
                break

    rc_auth, out, err = _run_quiet(["gh", "auth", "status"])
    authed = rc_auth == 0

    return AuthStatus(
        tool="github",
        installed=True,
        authenticated=authed,
        version=ver,
        path=path,
        message=out.splitlines()[0] if out else (err.splitlines()[0] if err else ""),
        login_hint="gh auth login",
    )


def check_cursor() -> AuthStatus:
    """Check Cursor CLI availability."""
    path = _find_binary("cursor")
    if not path:
        return AuthStatus(
            tool="cursor",
            installed=False,
            authenticated=False,
            login_hint="Install Cursor: https://www.cursor.com/ — the CLI is bundled with the desktop app",
        )

    rc, version, _ = _run_quiet(["cursor", "--version"])
    return AuthStatus(
        tool="cursor",
        installed=True,
        authenticated=True,
        version=version if rc == 0 else "",
        path=path,
        message="Cursor CLI available (auth managed by the desktop app)",
        login_hint="Open Cursor desktop app and sign in",
    )


def check_claude() -> AuthStatus:
    """Check Claude Code CLI availability and auth."""
    path = _find_binary("claude")
    if not path:
        return AuthStatus(
            tool="claude",
            installed=False,
            authenticated=False,
            login_hint="Install Claude Code: npm install -g @anthropic-ai/claude-code",
        )

    rc, version, _ = _run_quiet(["claude", "--version"])

    rc_auth, out, err = _run_quiet(["claude", "auth", "status"])
    authed = rc_auth == 0

    return AuthStatus(
        tool="claude",
        installed=True,
        authenticated=authed,
        version=version if rc == 0 else "",
        path=path,
        message=out if authed else (err or "not authenticated"),
        login_hint="claude auth login",
    )


def check_codex() -> AuthStatus:
    """Check Codex CLI availability and auth (requires OPENAI_API_KEY)."""
    import os

    path = _find_binary("codex")
    if not path:
        return AuthStatus(
            tool="codex",
            installed=False,
            authenticated=False,
            login_hint="Install Codex: npm install -g @openai/codex",
        )

    rc, version, _ = _run_quiet(["codex", "--version"])

    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    return AuthStatus(
        tool="codex",
        installed=True,
        authenticated=has_key,
        version=version if rc == 0 else "",
        path=path,
        message="OPENAI_API_KEY set" if has_key else "OPENAI_API_KEY not set",
        login_hint="export OPENAI_API_KEY=sk-...",
    )


# ------------------------------------------------------------------
# Aggregate
# ------------------------------------------------------------------

ALL_TOOLS = ("git", "github", "cursor", "claude", "codex")

_CHECKERS = {
    "git": check_git,
    "github": check_github,
    "cursor": check_cursor,
    "claude": check_claude,
    "codex": check_codex,
}


def check_tool(name: str) -> AuthStatus:
    """Check a single tool by name."""
    checker = _CHECKERS.get(name)
    if not checker:
        return AuthStatus(
            tool=name,
            installed=False,
            authenticated=False,
            message=f"Unknown tool: {name}",
        )
    return checker()


def check_all() -> list[AuthStatus]:
    """Check all supported tools."""
    return [check_tool(name) for name in ALL_TOOLS]
