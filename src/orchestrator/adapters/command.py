"""Command-based adapter — executes an external command to complete a step.

Base implementation for all command-execution adapters. Handles:
- Subprocess invocation with configurable command, args, env, cwd, timeout
- Prompt delivery via CLI arg or stdin (subclass-controlled)
- Prompt file writing for auditability
- Per-step log capture (stdout/stderr)
- Artifact existence verification after command execution
- Review outcome detection from written artifacts
- Live progress spinner during long-running commands
- Structured error handling (timeout, command-not-found, non-zero exit)

Subclasses (CodexCommandAdapter, ClaudeCommandAdapter, etc.) override
``_build_prompt`` and ``_build_command`` to customize for their agent.
Subclasses that need stdin delivery override ``_use_stdin`` to return True.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..domain.models import (
    AdapterCapability,
    ExecutionResult,
    ExecutionStatus,
    ReviewOutcome,
)
from ..infrastructure.file_state_store import FileStateStore
from ..infrastructure.run_logger import RunLogger
from .base import AgentAdapter

DEFAULT_TIMEOUT = 300  # 5 minutes

_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _ProgressSpinner:
    """Thread-based progress indicator for long-running subprocess steps."""

    def __init__(
        self,
        task_name: str,
        agent: str,
        phase: str,
        output=None,
    ) -> None:
        self._task_name = task_name
        self._agent = agent
        self._phase = phase
        self._output = output or sys.stderr
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_time = 0.0

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._start_time
            char = _SPINNER_CHARS[idx % len(_SPINNER_CHARS)]
            msg = (
                f"\r{char} [{self._agent}] {self._phase} "
                f"| task: {self._task_name} "
                f"| elapsed: {int(elapsed)}s"
            )
            try:
                self._output.write(msg)
                self._output.flush()
            except OSError:
                break
            idx += 1
            self._stop.wait(0.15)
        try:
            self._output.write("\r" + " " * 80 + "\r")
            self._output.flush()
        except OSError:
            pass


class CommandAdapter(AgentAdapter):
    """Executes an external command to complete a workflow step.

    Settings (from config):
        command: str — executable name or path (e.g. "codex", "claude")
        args: list[str] — argument template; ``{prompt}`` and ``{prompt_file}``
            are replaced at runtime
        timeout: int — seconds before the process is killed
        env: dict[str, str] — extra environment variables; values starting
            with ``$`` are resolved from the current environment
        working_dir: str — ``{task_dir}`` or ``{target_repo}``; default: task dir
    """

    def __init__(
        self,
        store: FileStateStore,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        self._store = store
        self._settings = settings or {}
        self._timeout = int(self._settings.get("timeout", DEFAULT_TIMEOUT))

    @property
    def name(self) -> str:
        cmd = self._settings.get("command", "command")
        return f"command:{cmd}"

    @property
    def capability(self) -> AdapterCapability:
        return AdapterCapability.AUTOMATIC

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        task_name: str,
        artifact: str,
        instruction: str,
        context: dict[str, Any],
    ) -> str:
        """Build the prompt string sent to the command.

        Subclasses override this for agent-specific prompt formatting.
        Detects GitHub workflow mode from context and adjusts accordingly.
        """
        if context.get("workflow_mode") == "github":
            return self._build_github_prompt(task_name, artifact, instruction, context)

        task_data = context.get("task", {})
        target_repo = context.get("target_repo", "")
        cycle = context.get("cycle", 1)
        task_dir = str(self._store.task_dir(task_name))
        artifact_path = str(self._store.artifact_path(task_name, artifact))

        context_block = self._gather_artifact_context(task_name)

        return (
            f"You are completing a review workflow step.\n\n"
            f"Task: {task_name}\n"
            f"Target repository: {target_repo}\n"
            f"Cycle: {cycle}\n"
            f"Task directory: {task_dir}\n\n"
            f"## Instruction\n\n{instruction}\n\n"
            f"## Required output\n\n"
            f"Write the artifact file to:\n  {artifact_path}\n\n"
            f"Filename: {artifact}\n\n"
            f"If this is a review artifact, include a **Status** field with one of:\n"
            f"- approved\n"
            f"- changes-requested\n"
            f"- minor-fixes-applied\n\n"
            f"## Existing artifacts for context\n\n{context_block}\n"
        )

    def _build_github_prompt(
        self,
        task_name: str,
        artifact: str,
        instruction: str,
        context: dict[str, Any],
    ) -> str:
        """Build a prompt for GitHub-native workflow mode."""
        repo = context.get("target_repo", "")
        cycle = context.get("cycle", 1)
        issue_number = context.get("issue_number", "")
        work_type = context.get("work_type", "feat")
        branch_name = context.get("branch_name", "")
        pr_number = context.get("pr_number", "")
        base_branch = context.get("base_branch", "main")

        prompt_content = context.get("prompt_content", "")

        parts = [
            f"You are completing a GitHub-native review workflow step.\n\n"
            f"Task: {task_name}\n"
            f"Repository: {repo}\n"
            f"Issue: #{issue_number}\n"
            f"Work type: {work_type}\n"
            f"Branch: {branch_name}\n"
            f"PR: {'#' + str(pr_number) if pr_number else '(not yet created)'}\n"
            f"Base branch: {base_branch}\n"
            f"Cycle: {cycle}\n\n"
            f"## Instruction\n\n{instruction}\n\n",
        ]

        if prompt_content:
            parts.append(
                f"## Detailed task prompt\n\n{prompt_content}\n\n"
            )

        parts.append(
            f"## Safety rules\n\n"
            f"- Do NOT push to {base_branch} directly\n"
            f"- Work only on branch: {branch_name}\n"
            f"- All changes go through pull requests\n"
        )

        return "".join(parts)

    def _build_command(
        self,
        prompt: str,
        prompt_file: Path,
        context: dict[str, Any],
        task_name: str,
    ) -> list[str]:
        """Build the command list to execute.

        Resolves all ``{placeholder}`` tokens in args:
        ``{prompt}``, ``{prompt_file}``, ``{target_repo}``, ``{task_dir}``.
        """
        cmd = self._settings.get("command", "echo")
        raw_args = self._settings.get("args", ["-p", "{prompt}"])

        task_dir = str(self._store.task_dir(task_name))
        target_repo = context.get("target_repo", "") or task_dir

        final_args = []
        for arg in raw_args:
            arg = arg.replace("{prompt_file}", str(prompt_file))
            arg = arg.replace("{target_repo}", target_repo)
            arg = arg.replace("{task_dir}", task_dir)
            arg = arg.replace("{prompt}", prompt)
            final_args.append(arg)

        return [cmd, *final_args]

    def _resolve_working_dir(self, context: dict[str, Any], task_name: str) -> str:
        """Determine the working directory for the subprocess."""
        wd_template = self._settings.get("working_dir", "{task_dir}")
        task_dir = str(self._store.task_dir(task_name))
        target_repo = context.get("target_repo", task_dir)
        return wd_template.replace("{task_dir}", task_dir).replace(
            "{target_repo}", target_repo or task_dir
        )

    def _use_stdin(self) -> bool:
        """Whether to deliver the prompt via stdin instead of CLI arg.

        Subclasses override this to return True when their CLI expects
        stdin input (e.g. ``claude -p`` reads from stdin).
        """
        return False

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def execute(
        self,
        task_name: str,
        artifact: str,
        template: str,
        instruction: str,
        context: dict[str, Any],
    ) -> ExecutionResult:
        task_dir = self._store.task_dir(task_name)
        logger = RunLogger(task_dir)

        prompt = self._build_prompt(task_name, artifact, instruction, context)

        prompt_file = task_dir / f".prompt-{artifact}.md"
        prompt_file.write_text(prompt)

        cmd = self._build_command(prompt, prompt_file, context, task_name)
        env = self._build_env()
        cwd = self._resolve_working_dir(context, task_name)

        stdin_text: Optional[str] = None
        if self._use_stdin():
            stdin_text = prompt

        _KNOWN_PLACEHOLDERS = ("{prompt}", "{prompt_file}", "{target_repo}", "{task_dir}")
        unresolved = [
            token
            for arg in cmd
            for token in _KNOWN_PLACEHOLDERS
            if token in arg
        ]
        if unresolved:
            logger.log("adapter_unresolved_placeholders", tokens=unresolved)
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Unresolved placeholders in command args: {unresolved}",
            )

        logger.log(
            "adapter_invoke",
            adapter=self.name,
            artifact=artifact,
            command=cmd[0],
            timeout=self._timeout,
            working_dir=cwd,
            stdin="yes" if stdin_text else "no",
        )

        agent = context.get("agent", self.name)
        phase = context.get("phase", artifact)
        spinner = _ProgressSpinner(task_name, agent, phase)
        spinner.start()

        try:
            proc = subprocess.run(
                cmd,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as e:
            spinner.stop()
            logger.log("adapter_timeout", artifact=artifact, timeout=self._timeout)
            self._write_step_log(task_dir, artifact, e.stdout or "", e.stderr or "", -1)
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Command timed out after {self._timeout}s",
            )
        except FileNotFoundError:
            spinner.stop()
            logger.log("adapter_command_not_found", command=cmd[0])
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Command not found: {cmd[0]}",
            )
        except OSError as e:
            spinner.stop()
            logger.log("adapter_error", error=str(e))
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"OS error: {e}",
            )
        finally:
            spinner.stop()

        self._write_step_log(task_dir, artifact, proc.stdout, proc.stderr, proc.returncode)

        if proc.returncode != 0:
            logger.log(
                "adapter_failed",
                artifact=artifact,
                exit_code=proc.returncode,
                stderr_tail=proc.stderr[-500:] if proc.stderr else "",
            )
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Command exited with code {proc.returncode}",
            )

        artifact_path = self._store.artifact_path(task_name, artifact)
        if not artifact_path.is_file():
            logger.log("adapter_artifact_missing", artifact=artifact)
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                artifact_written=False,
                message=f"Command succeeded but artifact not written: {artifact}",
            )

        outcome = self._detect_outcome(task_name, artifact)

        logger.log(
            "adapter_completed",
            artifact=artifact,
            exit_code=0,
            review_outcome=outcome.value if outcome else None,
        )

        return ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            artifact_written=True,
            message=f"Command completed: {artifact}",
            review_outcome=outcome,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_env(self) -> dict[str, str]:
        """Merge current environment with configured extras."""
        env = dict(os.environ)
        for key, value in self._settings.get("env", {}).items():
            if isinstance(value, str) and value.startswith("$"):
                env[key] = os.environ.get(value[1:], "")
            else:
                env[key] = str(value)
        return env

    def _gather_artifact_context(self, task_name: str) -> str:
        """Read all existing non-dotfile artifacts for prompt context."""
        lines = []
        for name in self._store.list_artifacts(task_name):
            if name.startswith("."):
                continue
            path = self._store.artifact_path(task_name, name)
            try:
                content = path.read_text()
                if len(content) > 3000:
                    content = content[:3000] + "\n...(truncated)"
                lines.append(f"### {name}\n\n```\n{content}\n```\n")
            except OSError:
                lines.append(f"### {name}\n\n(could not read)\n")
        return "\n".join(lines) if lines else "(no artifacts yet)"

    def _detect_outcome(
        self, task_name: str, artifact: str
    ) -> Optional[ReviewOutcome]:
        """Parse **Status**: from the written artifact."""
        path = self._store.artifact_path(task_name, artifact)
        if not path.is_file():
            return None
        content = path.read_text()
        match = re.search(
            r"\*\*Status\*\*:\s*(approved|changes-requested|minor-fixes-applied)",
            content,
            re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return ReviewOutcome(match.group(1).lower().strip())
        except ValueError:
            return None

    def _write_step_log(
        self,
        task_dir: Path,
        artifact: str,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> None:
        log_path = task_dir / f".log-{artifact}.txt"
        log_path.write_text(
            f"exit_code: {exit_code}\n\n"
            f"--- stdout ---\n{stdout}\n\n"
            f"--- stderr ---\n{stderr}\n"
        )
