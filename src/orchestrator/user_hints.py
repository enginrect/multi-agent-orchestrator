"""User-facing CLI strings shared across the orchestrator.

Single source for resume/next-step lines and repeated argparse help text.
"""

from __future__ import annotations

CLI_COMMAND_NAME = "morch"

WORK_TYPE_ARG_HELP = (
    "Work type: feat, modify, fix, refactor, docs, chore, ops, test, hotfix"
)


def resume_task_shell(task_name: str) -> str:
    """Shell snippet only, e.g. ``morch resume task foo``."""
    return f"{CLI_COMMAND_NAME} resume task {task_name}"


def resume_github_shell(task_name: str) -> str:
    """Shell snippet only, e.g. ``morch resume github issue-42``."""
    return f"{CLI_COMMAND_NAME} resume github {task_name}"


def hint_resume_task(task_name: str) -> str:
    """Full line when paused on a file-artifact task."""
    return f"Resume: {resume_task_shell(task_name)}"


def hint_resume_github(task_name: str) -> str:
    """Full line when paused on a GitHub-backed task."""
    return f"Resume: {resume_github_shell(task_name)}"


def task_advance_shell(task_name: str) -> str:
    return f"{CLI_COMMAND_NAME} task advance {task_name}"


def task_archive_shell(task_name: str) -> str:
    return f"{CLI_COMMAND_NAME} task archive {task_name}"
