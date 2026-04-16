"""Claude command adapter — invokes Anthropic Claude Code CLI for real execution.

Uses ``claude -p`` (print mode) for non-interactive execution. The
``--permission-mode auto`` flag auto-approves tool use without prompts.

The prompt is delivered via **stdin**, which is how ``claude -p`` is
designed to receive input programmatically. This avoids OS argument
length limits that break with long review prompts.

**Important**: When running via subprocess, always use the real executable
path (e.g. ``/Users/<user>/.local/bin/claude``), not a shell function or
alias. Shell functions are not visible to ``subprocess.run()``.

Execution flow:
1. Constructs a Claude-optimized prompt with task context and previous artifacts
2. Pipes the prompt to ``claude -p --permission-mode auto ...`` via stdin
3. Verifies the expected artifact was written
4. Detects review outcome from artifact content

Requirements:
- ``claude`` CLI installed (e.g. ``/Users/<user>/.local/bin/claude``)
- Authentication configured (``claude auth`` or existing session)

This is a **real** adapter: it invokes an actual agent process.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.models import AgentRole
from ..domain.provenance import fix_commit_prefix, review_header
from ..infrastructure.file_state_store import FileStateStore
from .command import CommandAdapter


class ClaudeCommandAdapter(CommandAdapter):
    """Real Claude Code CLI adapter using ``claude -p``.

    Delivers prompt via stdin (not CLI arg) to avoid ARG_MAX issues
    with long prompts. The ``-p`` flag reads from stdin when no
    positional prompt argument is provided.
    """

    def __init__(
        self,
        store: FileStateStore,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        user = settings or {}
        allowed_tools = user.get("allowed_tools", "Edit,Write,Read,Bash")
        permission_mode = user.get("permission_mode", "auto")

        merged = {
            "command": "claude",
            "args": [
                "-p",
                "--permission-mode", permission_mode,
                "--allowedTools", allowed_tools,
            ],
            "timeout": 600,
            "working_dir": "{task_dir}",
            **user,
        }
        for consumed_key in ("allowed_tools", "permission_mode"):
            merged.pop(consumed_key, None)
        super().__init__(store, merged)

    def _use_stdin(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "claude-cli"

    def _build_prompt(
        self,
        task_name: str,
        artifact: str,
        instruction: str,
        context: dict[str, Any],
    ) -> str:
        if context.get("workflow_mode") == "github":
            return self._build_github_prompt(task_name, artifact, instruction, context)

        target_repo = context.get("target_repo", "")
        cycle = context.get("cycle", 1)
        task_dir = str(self._store.task_dir(task_name))
        artifact_path = str(self._store.artifact_path(task_name, artifact))

        context_block = self._gather_artifact_context(task_name)

        is_review = "review" in artifact.lower()

        prompt_parts = [
            f"You are **Claude**, the first reviewer in a multi-agent code review workflow.",
            f"",
            f"Task: {task_name}",
            f"Target repository: {target_repo}",
            f"Cycle: {cycle}",
            f"Task directory: {task_dir}",
            f"",
            f"## Your instruction",
            f"",
            instruction,
            f"",
            f"## Required output",
            f"",
            f"Write your review to: {artifact_path}",
            f"Use the Write tool to create the file.",
            f"",
        ]

        if is_review:
            prompt_parts.extend([
                f"The review MUST include a `**Status**:` field with one of:",
                f"- `approved` — implementation meets all requirements",
                f"- `changes-requested` — significant issues that need rework",
                f"- `minor-fixes-applied` — small issues you fixed directly",
                f"",
                f"Format:",
                f"```markdown",
                f"# Claude Review — Cycle {cycle}",
                f"",
                f"**Task**: {task_name}",
                f"**Status**: approved",
                f"",
                f"## Summary",
                f"...",
                f"",
                f"## Findings",
                f"...",
                f"```",
                f"",
            ])

        if target_repo:
            prompt_parts.extend([
                f"## Target repository",
                f"",
                f"Read the code at `{target_repo}` to understand the implementation.",
                f"If changes are needed, apply targeted fixes directly in the repo.",
                f"",
            ])

        prompt_parts.extend([
            f"## Existing artifacts",
            f"",
            context_block,
        ])

        return "\n".join(prompt_parts)

    def _build_github_prompt(
        self,
        task_name: str,
        artifact: str,
        instruction: str,
        context: dict[str, Any],
    ) -> str:
        github_repo = context.get("github_repo", "") or context.get("target_repo", "")
        cycle = context.get("cycle", 1)
        issue_number = context.get("issue_number", "")
        branch_name = context.get("branch_name", "")
        pr_number = context.get("pr_number", "")
        base_branch = context.get("base_branch", "main")

        prov_header = review_header(
            agent=AgentRole.CLAUDE,
            role="reviewer",
            pr_number=pr_number,
            cycle=cycle,
        )
        commit_prefix = fix_commit_prefix(AgentRole.CLAUDE, issue_number)

        parts = [
            "You are **Claude** (`@claude-agent`), the first reviewer in a GitHub-native multi-agent workflow.",
            "",
            f"Task: {task_name}",
            f"Repository: {github_repo}",
            f"Issue: #{issue_number}",
            f"PR: #{pr_number}",
            f"Branch: {branch_name}",
            f"Cycle: {cycle}",
            "",
            "## Your instruction",
            "",
            instruction,
            "",
            "## Review workflow",
            "",
            f"1. Read the PR diff: `gh pr diff {pr_number} --repo {github_repo}`",
            f"2. Review the changed files thoroughly",
            f"3. If minor fixes are needed, checkout the branch and push commits:",
            f"   `git checkout {branch_name} && git pull origin {branch_name}`",
            f"   Make changes, commit with prefix: `{commit_prefix}`, and push.",
            f"4. Post your review (MUST include the provenance header below):",
            f"   - If approved: `gh pr review {pr_number} --repo {github_repo} --approve --body '<review body>'`",
            f"   - If changes needed: `gh pr review {pr_number} --repo {github_repo} --request-changes --body '<review body>'`",
            "",
            "## Review body format",
            "",
            "Your review body MUST start with this provenance header:",
            f"```",
            prov_header,
            f"```",
            "",
            "Then include your summary, findings, and verdict.",
            "",
            "## Safety rules",
            "",
            f"- Do NOT push to `{base_branch}` directly",
            f"- Only push to `{branch_name}` for minor fixes",
            "- Use the PR review mechanism for feedback",
        ]

        return "\n".join(parts)
