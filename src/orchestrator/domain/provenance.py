"""Agent provenance templates for GitHub-visible workflow attribution.

Every GitHub artifact (issue comment, PR body, review body, fallback
comment) must clearly identify the logical agent that produced it.
Even when the underlying GitHub account is the same, the workflow must
show distinct logical actor identity for each step.
"""

from __future__ import annotations

from .models import AgentRole

# Canonical agent identities used in all GitHub-visible text.
AGENT_IDENTITIES: dict[str, tuple[str, str]] = {
    "orchestrator": ("Orchestrator", "@multi-orchestrator-agent"),
    AgentRole.CURSOR.value: ("Cursor", "@cursor-agent"),
    AgentRole.CLAUDE.value: ("Claude", "@claude-agent"),
    AgentRole.CODEX.value: ("Codex", "@codex-agent"),
}


def agent_sig(key: str) -> str:
    """Return ``**Name** (\\`@handle\\`)`` for use in markdown bodies."""
    name, handle = AGENT_IDENTITIES[key]
    return f"**{name}** (`{handle}`)"


# ------------------------------------------------------------------
# Issue timeline comments
# ------------------------------------------------------------------

def comment_issue_claimed(branch: str, cycle: int, implementer: str = "Cursor") -> str:
    sig = agent_sig("orchestrator")
    return (
        f"🤖 {sig}\n\n"
        f"Claimed issue. Implementation starting.\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| Branch | `{branch}` |\n"
        f"| Workflow | morch GitHub-native |\n"
        f"| Cycle | {cycle} |\n"
        f"| Implementer | {implementer} |"
    )


def comment_pr_opened(agent: AgentRole, pr_number: int, cycle: int) -> str:
    sig = agent_sig(agent.value)
    return f"🤖 {sig}\n\nOpened PR #{pr_number} for cycle {cycle}."


def comment_review_started(agent: AgentRole, pr_number: int, cycle: int) -> str:
    role = "reviewer" if agent == AgentRole.CLAUDE else "final reviewer"
    sig = agent_sig(agent.value)
    return f"🤖 {sig}\n\nReview started on PR #{pr_number} (cycle {cycle}, role: {role})."


def comment_review_completed(
    agent: AgentRole,
    pr_number: int,
    cycle: int,
    status: str = "",
) -> str:
    role = "reviewer" if agent == AgentRole.CLAUDE else "final reviewer"
    sig = agent_sig(agent.value)
    status_line = f"\n\nVerdict: **{status}**" if status else ""
    return (
        f"🤖 {sig}\n\n"
        f"Review completed on PR #{pr_number} (cycle {cycle}, role: {role}).{status_line}"
    )


def comment_rework_requested(
    agent: AgentRole,
    pr_number: int,
    cycle: int,
) -> str:
    sig = agent_sig(agent.value)
    return (
        f"🤖 {sig}\n\n"
        f"Changes requested on PR #{pr_number}. Rework cycle {cycle + 1} starting."
    )


def comment_approved(pr_number: int, cycle: int) -> str:
    sig = agent_sig("orchestrator")
    return (
        f"🤖 {sig}\n\n"
        f"PR #{pr_number} approved after cycle {cycle}. Ready for human merge."
    )


def comment_fallback_review(
    agent: AgentRole,
    pr_number: int,
    cycle: int,
    reason: str = "sandbox/API limitation",
) -> str:
    sig = agent_sig(agent.value)
    return (
        f"🤖 {sig}\n\n"
        f"Could not post formal GitHub review on PR #{pr_number} "
        f"due to {reason}. Review recorded locally in task workspace."
    )


# ------------------------------------------------------------------
# PR body provenance block
# ------------------------------------------------------------------

def pr_body_block(
    agent: AgentRole,
    role: str,
    issue_number: int,
    cycle: int,
) -> str:
    """Provenance metadata block appended to PR body."""
    sig = agent_sig(agent.value)
    return (
        f"---\n"
        f"🤖 {sig} | Role: {role} | Issue: #{issue_number} "
        f"| Cycle: {cycle} | Workflow: morch GitHub-native"
    )


# ------------------------------------------------------------------
# Review body provenance header
# ------------------------------------------------------------------

def review_header(
    agent: AgentRole,
    role: str,
    pr_number: int,
    cycle: int,
) -> str:
    """Provenance header prepended to review bodies."""
    sig = agent_sig(agent.value)
    return (
        f"🤖 {sig} | Role: {role} | PR: #{pr_number} | Cycle: {cycle}"
    )


# ------------------------------------------------------------------
# Commit message conventions (for agent-applied fixes)
# ------------------------------------------------------------------

def fix_commit_prefix(agent: AgentRole, issue_number: int) -> str:
    """Suggested commit message prefix for agent-applied fixes."""
    return f"chore({agent.value}): apply review fix for issue #{issue_number}"
