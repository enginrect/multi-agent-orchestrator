"""Tests for shared user-facing CLI hint strings."""

from orchestrator.user_hints import (
    CLI_COMMAND_NAME,
    WORK_TYPE_ARG_HELP,
    hint_resume_github,
    hint_resume_task,
    resume_github_shell,
    resume_task_shell,
    task_advance_shell,
    task_archive_shell,
)


def test_cli_command_name_stable():
    assert CLI_COMMAND_NAME == "morch"


def test_work_type_help_nonempty():
    assert "feat" in WORK_TYPE_ARG_HELP
    assert "hotfix" in WORK_TYPE_ARG_HELP


def test_resume_shells():
    assert resume_task_shell("foo") == "morch resume task foo"
    assert resume_github_shell("issue-1") == "morch resume github issue-1"


def test_hints_prefix_resume():
    assert hint_resume_task("t") == "Resume: morch resume task t"
    assert hint_resume_github("issue-9") == "Resume: morch resume github issue-9"


def test_task_commands():
    assert task_advance_shell("x") == "morch task advance x"
    assert task_archive_shell("x") == "morch task archive x"
