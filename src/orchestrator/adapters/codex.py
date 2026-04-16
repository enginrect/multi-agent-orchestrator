"""Codex command adapter — invokes OpenAI Codex CLI for real execution.

Uses ``codex exec`` which is the dedicated non-interactive subcommand.
The ``--full-auto`` flag enables sandboxed automatic execution without
approval prompts.

Execution flow:
1. Constructs a Codex-optimized prompt with task context
2. Runs ``codex exec --full-auto "<prompt>"``
3. Verifies the expected artifact was written
4. Detects review outcome from artifact content

Requirements:
- ``codex`` CLI installed (e.g. ``/opt/homebrew/bin/codex``)
- ``OPENAI_API_KEY`` set in environment (or injected via adapter config)

This is a **real** adapter: it invokes an actual agent process.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.models import AgentRole
from ..domain.provenance import review_header
from ..infrastructure.file_state_store import FileStateStore
from .command import CommandAdapter


class CodexCommandAdapter(CommandAdapter):
    """Real Codex CLI adapter using ``codex exec``.

    Delivers prompt via stdin to avoid ARG_MAX issues with long prompts.
    """

    def __init__(
        self,
        store: FileStateStore,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        merged = {
            "command": "codex",
            "args": ["exec", "--full-auto"],
            "timeout": 900,
            "working_dir": "{task_dir}",
            **(settings or {}),
        }
        super().__init__(store, merged)

    def _use_stdin(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "codex-cli"

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

        is_review = "review" in artifact.lower() or "approval" in artifact.lower()

        prompt_parts = [
            f"You are **Codex**, the final reviewer in a multi-agent review workflow.",
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
            f"Write your output to: {artifact_path}",
            f"",
        ]

        if is_review:
            prompt_parts.extend([
                f"The artifact MUST include a `**Status**:` field with one of:",
                f"- `approved` — implementation is acceptable",
                f"- `changes-requested` — significant issues found",
                f"- `minor-fixes-applied` — small issues fixed inline",
                f"",
                f"Use this exact format at the top of the file:",
                f"```",
                f"# Codex Final Review — Cycle {cycle}",
                f"",
                f"**Task**: {task_name}",
                f"**Status**: approved",
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
                f"Read the target repository at `{target_repo}` to understand the",
                f"codebase before writing your review.",
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
        pr_number = context.get("pr_number", "")
        base_branch = context.get("base_branch", "main")

        prov_header = review_header(
            agent=AgentRole.CODEX,
            role="final reviewer",
            pr_number=pr_number,
            cycle=cycle,
        )

        parts = [
            "You are **Codex** (`@codex-agent`), the final reviewer and approval gate in a GitHub-native workflow.",
            "",
            f"Task: {task_name}",
            f"Repository: {github_repo}",
            f"Issue: #{issue_number}",
            f"PR: #{pr_number}",
            f"Cycle: {cycle}",
            "",
            "## Your instruction",
            "",
            instruction,
            "",
            "## Review workflow",
            "",
            f"1. Read the full PR diff: `gh pr diff {pr_number} --repo {github_repo}`",
            f"2. Read all previous review comments: `gh pr view {pr_number} --repo {github_repo} --comments`",
            f"3. Run any validation commands if applicable",
            f"4. Post your final review (MUST include the provenance header below):",
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
            f"- Do NOT merge the PR — only review it",
            f"- Do NOT push to `{base_branch}`",
            "- This is the approval gate: be thorough",
        ]

        return "\n".join(parts)
