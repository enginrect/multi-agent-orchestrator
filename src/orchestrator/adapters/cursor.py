"""Cursor command adapter — invokes Cursor Agent CLI for real execution.

Uses ``cursor agent -p`` (print/headless mode) for non-interactive execution.
The ``--trust`` flag skips workspace trust dialogs and ``--yolo`` auto-approves
all commands. Combined, these provide fully autonomous headless operation.

Execution flow:
1. Constructs a Cursor-optimized prompt with task context
2. Runs ``cursor agent -p --trust --yolo --workspace <path> "<prompt>"``
3. Verifies the expected artifact was written
4. Detects review outcome from artifact content (for rework response artifacts)

Requirements:
- ``cursor`` CLI installed (e.g. ``/usr/local/bin/cursor``)
- The ``agent`` subcommand must be available (Cursor 2.6+)

This is a **real** adapter. If the ``cursor`` binary is not available or if
the ``agent`` subcommand is not present, the adapter still supports a manual
fallback when ``command`` is explicitly set to empty string in config.

Manual fallback:
If settings include ``manual_fallback: true`` (and no command override is
given), the adapter writes a prompt file and returns WAITING. This preserves
backward compatibility for environments where Cursor CLI is not installed.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.models import (
    AdapterCapability,
    AgentRole,
    ExecutionResult,
    ExecutionStatus,
)
from ..domain.provenance import pr_body_block
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.run_logger import RunLogger
from ..user_hints import resume_task_shell
from .command import CommandAdapter


class CursorCommandAdapter(CommandAdapter):
    """Real Cursor Agent CLI adapter using ``cursor agent -p``.

    By default, this adapter invokes ``cursor agent`` in headless mode
    with ``-p --trust --yolo``. Override the command or args via settings.

    Set ``manual_fallback: true`` in settings to disable automatic
    execution and fall back to manual mode (writes prompt, returns WAITING).
    """

    def __init__(
        self,
        store: FileStateStore,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        user = settings or {}
        self._manual_fallback = bool(user.pop("manual_fallback", False))

        merged = {
            "command": "cursor",
            "args": [
                "agent", "-p",
                "--trust", "--yolo",
                "--workspace", "{target_repo}",
                "{prompt}",
            ],
            "timeout": 600,
            "working_dir": "{task_dir}",
            **user,
        }
        self._has_command = not self._manual_fallback
        super().__init__(store, merged)

    @property
    def name(self) -> str:
        if self._manual_fallback:
            return "cursor-manual"
        return "cursor-cli"

    @property
    def capability(self) -> AdapterCapability:
        if self._manual_fallback:
            return AdapterCapability.MANUAL
        return AdapterCapability.AUTOMATIC

    def execute(
        self,
        task_name: str,
        artifact: str,
        template: str,
        instruction: str,
        context: dict[str, Any],
    ) -> ExecutionResult:
        if self._manual_fallback:
            task_dir = self._store.task_dir(task_name)
            logger = RunLogger(task_dir)
            logger.log(
                "cursor_manual_fallback",
                artifact=artifact,
                reason="manual_fallback enabled in config",
            )

            prompt = self._build_prompt(task_name, artifact, instruction, context)
            prompt_file = task_dir / f".prompt-{artifact}.md"
            prompt_file.write_text(prompt)

            return ExecutionResult(
                status=ExecutionStatus.WAITING,
                artifact_written=False,
                message=(
                    f"Cursor manual fallback: implement and write {artifact}. "
                    f"Prompt saved to .prompt-{artifact}.md. "
                    f"Then run: {resume_task_shell(task_name)}"
                ),
            )

        return super().execute(task_name, artifact, template, instruction, context)

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

        return (
            f"You are **Cursor**, the primary implementer in a multi-agent review workflow.\n\n"
            f"Task: {task_name}\n"
            f"Target repository: {target_repo}\n"
            f"Cycle: {cycle}\n"
            f"Task directory: {task_dir}\n\n"
            f"## Your instruction\n\n{instruction}\n\n"
            f"## Required output\n\n"
            f"1. Implement the changes in the target repository at `{target_repo}`\n"
            f"2. Write a handoff document to: {artifact_path}\n\n"
            f"The handoff document should describe what you changed, why, and\n"
            f"what reviewers should focus on.\n\n"
            f"## Existing artifacts\n\n{context_block}\n"
        )

    def _build_github_prompt(
        self,
        task_name: str,
        artifact: str,
        instruction: str,
        context: dict[str, Any],
    ) -> str:
        github_repo = context.get("github_repo", "") or context.get("target_repo", "")
        repo = github_repo
        cycle = context.get("cycle", 1)
        issue_number = context.get("issue_number", "")
        issue_title = context.get("issue_title", "")
        work_type = context.get("work_type", "feat")
        branch_name = context.get("branch_name", "")
        pr_number = context.get("pr_number")
        base_branch = context.get("base_branch", "main")
        pr_title_pattern = context.get(
            "pr_title_pattern",
            "[{type}][Issue #{issue}][{agent}] {summary}",
        )

        from ..domain.github_models import WORK_TYPE_LABELS, WorkType
        try:
            wt = WorkType(work_type)
        except ValueError:
            wt = WorkType.FEAT
        type_label = WORK_TYPE_LABELS.get(wt, wt.value.capitalize())
        pr_title = pr_title_pattern.format(
            type=type_label,
            issue=issue_number,
            agent="Cursor",
            summary=issue_title,
        )

        parts = [
            "You are **Cursor**, the primary implementer in a GitHub-native multi-agent workflow.",
            "",
            f"Task: {task_name}",
            f"Repository: {repo}",
            f"Issue: #{issue_number} — {issue_title}",
            f"Work type: {type_label}",
            f"Branch: {branch_name}",
            f"Base branch: {base_branch}",
            f"Cycle: {cycle}",
            "",
            "## Your instruction",
            "",
            instruction,
            "",
            "## Git workflow",
            "",
        ]

        prov_block = pr_body_block(
            agent=AgentRole.CURSOR,
            role="implementation",
            issue_number=issue_number,
            cycle=cycle,
        )
        pr_body = f"Resolves #{issue_number}\\n\\n{prov_block}"

        if pr_number:
            parts.extend([
                f"A PR (#{pr_number}) is already open. Push follow-up commits to `{branch_name}`.",
                "",
            ])
        else:
            parts.extend([
                f"1. Fetch latest and create branch: `git fetch origin {base_branch} && git checkout -b {branch_name} origin/{base_branch}`",
                f"2. Implement the changes for issue #{issue_number}",
                f"3. Commit with a descriptive message referencing the issue",
                f"4. Push: `git push -u origin {branch_name}`",
                f"5. Open a PR: `gh pr create --repo {repo} "
                f"--title '{pr_title}' "
                f"--head {branch_name} --base {base_branch} "
                f"--body $'{pr_body}'`",
                "",
            ])

        parts.extend([
            "## Branch creation",
            "",
            f"- Always start from a fresh `{base_branch}`: fetch before branching",
            f"- Branch naming follows: `{branch_name}`",
            "",
            "## Safety rules",
            "",
            f"- Do NOT push to `{base_branch}` directly",
            f"- Work only on branch `{branch_name}`",
            "- All changes go through pull requests",
        ])

        return "\n".join(parts)
