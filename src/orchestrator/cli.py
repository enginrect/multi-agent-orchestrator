"""CLI entrypoint for morch — the multi-agent orchestrator.

Primary command: ``morch``
Backward-compatible alias: ``orchestrator``

Command groups:
    morch doctor                     System health check
    morch auth status                All auth status
    morch auth <tool> status         Per-tool auth check
    morch auth <tool> login          Show login instructions
    morch agents list                Show configured agent order
    morch agents doctor              Check agent readiness
    morch agents order <a> <b> [c]   Set agent execution order
    morch config show                Show effective configuration
    morch run prompt <path.md>       Markdown-prompt driven execution
    morch run task <name>            File-artifact review pipeline
    morch run github <issue>         GitHub-native issue pipeline
    morch resume <task-name>         Resume a paused task
    morch resume github <task-name>  Resume a GitHub task
    morch status <task-name>         Show task status
    morch status github <task-name>  Show GitHub task status
    morch task init/advance/validate/archive/list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters.factory import create_adapters_from_config
from .adapters.manual import ManualAdapter
from .application.artifact_service import ArtifactService
from .application.github_run_orchestrator import GitHubRunOrchestrator, GitHubRunResult
from .application.github_task_service import GitHubTaskService
from .application.prompt_runner import PromptRunner
from .application.run_orchestrator import RunOrchestrator
from .application.task_service import TaskService
from .application.workflow_engine import WorkflowEngine
from .domain.errors import OrchestratorError
from .domain.github_models import WorkType
from .domain.models import AgentRole, ReviewOutcome, RunStatus
from .infrastructure.auth_checker import check_all, check_tool
from .infrastructure.config_loader import (
    SUPPORTED_AGENTS,
    AgentsConfig,
    OrchestratorConfig,
)
from .infrastructure.file_state_store import FileStateStore
from .infrastructure.github_service import GitHubService
from .infrastructure.template_renderer import TemplateRenderer


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _resolve_target_repo(raw: str) -> str:
    if not raw:
        return raw
    return str(Path(raw).resolve())


def _resolve_paths(config: OrchestratorConfig) -> tuple[FileStateStore, TemplateRenderer]:
    workspace = Path(config.workspace_dir)
    if not workspace.is_absolute():
        workspace = Path.cwd() / workspace

    template = Path(config.template_dir)
    if not template.is_absolute():
        template = Path.cwd() / template

    return FileStateStore(workspace), TemplateRenderer(template)


def _load_config(args: argparse.Namespace) -> OrchestratorConfig:
    config_path = getattr(args, "config", None)
    config = OrchestratorConfig.load(config_path)
    if hasattr(args, "workspace") and args.workspace:
        config.workspace_dir = args.workspace
    return config


def _build_services(
    args: argparse.Namespace,
) -> tuple[TaskService, WorkflowEngine, OrchestratorConfig]:
    config = _load_config(args)
    store, renderer = _resolve_paths(config)
    store.ensure_workspace()
    artifact_svc = ArtifactService(store)
    task_svc = TaskService(config, store, renderer, artifact_svc)
    engine = WorkflowEngine(task_svc, artifact_svc)
    return task_svc, engine, config


def _build_run_orchestrator(args: argparse.Namespace) -> tuple[RunOrchestrator, OrchestratorConfig]:
    config = _load_config(args)
    store, renderer = _resolve_paths(config)
    store.ensure_workspace()
    artifact_svc = ArtifactService(store)
    task_svc = TaskService(config, store, renderer, artifact_svc)

    manual_fallback = ManualAdapter(store, renderer)

    if config.adapters:
        adapters = create_adapters_from_config(config.adapters, store, renderer)
    else:
        adapters = {}

    run_orch = RunOrchestrator(
        task_service=task_svc,
        artifact_service=artifact_svc,
        store=store,
        adapters=adapters,
        fallback_adapter=manual_fallback,
    )
    return run_orch, config


def _build_github_orchestrator(
    args: argparse.Namespace,
) -> tuple[GitHubRunOrchestrator, OrchestratorConfig]:
    config = _load_config(args)
    store, renderer = _resolve_paths(config)
    store.ensure_workspace()

    repo = getattr(args, "repo", None) or config.github.repo
    if not repo:
        print("Error: --repo is required (or set github.repo in config)", file=sys.stderr)
        sys.exit(1)

    github = GitHubService(repo)

    task_service = GitHubTaskService(
        store=store,
        github=github,
        branch_pattern=config.github.branch_pattern,
        pr_title_pattern=config.github.pr_title_pattern,
        labels=config.github.labels,
        base_branch=config.github.base_branch,
        max_cycles=config.max_cycles,
    )

    manual_fallback = ManualAdapter(store, renderer)

    if config.adapters:
        adapters = create_adapters_from_config(config.adapters, store, renderer)
    else:
        adapters = {}

    orch = GitHubRunOrchestrator(
        task_service=task_service,
        github=github,
        store=store,
        adapters=adapters,
        fallback_adapter=manual_fallback,
    )
    return orch, config


# ------------------------------------------------------------------
# doctor
# ------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> None:
    """System health check — verify all tools and configuration."""
    config = _load_config(args)

    print("morch doctor\n")

    print("Tools:")
    statuses = check_all()
    all_ok = True
    for s in statuses:
        icon = "OK" if s.ready else ("INSTALLED" if s.installed else "MISSING")
        mark = "+" if s.ready else ("~" if s.installed else "!")
        version = f" ({s.version})" if s.version else ""
        print(f"  [{mark}] {s.tool:<10s} {icon}{version}")
        if not s.ready:
            all_ok = False
            if s.login_hint:
                print(f"      hint: {s.login_hint}")

    print(f"\nAgents ({len(config.agents.enabled)} enabled):")
    for i, agent in enumerate(config.agents.enabled, 1):
        role = "implementer" if i == 1 else f"reviewer {i - 1}"
        print(f"  {i}. {agent} ({role})")

    errors = config.agents.validate()
    if errors:
        print("\nAgent config errors:")
        for e in errors:
            print(f"  ! {e}")
        all_ok = False

    print()
    if all_ok:
        print("All checks passed.")
    else:
        print("Some checks failed. Run `morch auth status` for details.")


# ------------------------------------------------------------------
# auth
# ------------------------------------------------------------------


def cmd_auth_status(args: argparse.Namespace) -> None:
    """Show auth status for all tools."""
    tool_name = getattr(args, "tool_name", None)

    if tool_name:
        s = check_tool(tool_name)
        _print_auth_status(s)
    else:
        for s in check_all():
            _print_auth_status(s)
            print()


def cmd_auth_login(args: argparse.Namespace) -> None:
    """Show login instructions for a tool."""
    tool_name = args.tool_name
    s = check_tool(tool_name)

    if s.ready:
        print(f"{s.tool}: already authenticated")
        return

    if not s.installed:
        print(f"{s.tool}: not installed")
        print(f"  Install: {s.login_hint}")
        return

    print(f"{s.tool}: not authenticated")
    if s.login_hint:
        print(f"  Run: {s.login_hint}")


def _print_auth_status(s) -> None:
    """Print a single AuthStatus."""
    icon = "+" if s.ready else ("~" if s.installed else "!")
    state = "ready" if s.ready else ("installed, not authenticated" if s.installed else "not installed")
    print(f"[{icon}] {s.tool}")
    print(f"    Status:  {state}")
    if s.version:
        print(f"    Version: {s.version}")
    if s.path:
        print(f"    Path:    {s.path}")
    if s.message:
        print(f"    Info:    {s.message}")
    if not s.ready and s.login_hint:
        print(f"    Fix:     {s.login_hint}")


# ------------------------------------------------------------------
# agents
# ------------------------------------------------------------------


def cmd_agents_list(args: argparse.Namespace) -> None:
    """List configured agents and their order."""
    config = _load_config(args)

    print("Configured agents:")
    for i, agent in enumerate(config.agents.enabled, 1):
        role = "implementer" if i == 1 else f"reviewer {i - 1}"
        print(f"  {i}. {agent} ({role})")

    print(f"\nSupported agents: {', '.join(SUPPORTED_AGENTS)}")


def cmd_agents_doctor(args: argparse.Namespace) -> None:
    """Check readiness of each configured agent."""
    config = _load_config(args)

    for agent in config.agents.enabled:
        s = check_tool(agent)
        mark = "+" if s.ready else "!"
        state = "ready" if s.ready else ("installed" if s.installed else "missing")
        print(f"  [{mark}] {agent}: {state}")
        if not s.ready and s.login_hint:
            print(f"      fix: {s.login_hint}")


def cmd_agents_order(args: argparse.Namespace) -> None:
    """Set agent execution order (display only — write to config to persist)."""
    new_order = args.agents

    test_config = AgentsConfig(enabled=new_order)
    errors = test_config.validate()
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("New agent order:")
    for i, agent in enumerate(new_order, 1):
        role = "implementer" if i == 1 else f"reviewer {i - 1}"
        print(f"  {i}. {agent} ({role})")

    print(f"\nTo persist, add to your config file:")
    print(f"  agents:")
    print(f"    enabled: [{', '.join(new_order)}]")


# ------------------------------------------------------------------
# config
# ------------------------------------------------------------------


def cmd_config_show(args: argparse.Namespace) -> None:
    """Show effective configuration."""
    config = _load_config(args)
    config_path = getattr(args, "config", None) or "(defaults)"

    print(f"Config source: {config_path}")
    print(f"Workspace:     {config.workspace_dir}")
    print(f"Templates:     {config.template_dir}")
    print(f"Max cycles:    {config.max_cycles}")
    if config.default_target_repo:
        print(f"Target repo:   {config.default_target_repo}")

    print(f"\nAgents: {' -> '.join(config.agents.enabled)}")

    if config.github.repo:
        print(f"\nGitHub:")
        print(f"  Repository:     {config.github.repo}")
        print(f"  Base branch:    {config.github.base_branch}")
        print(f"  Branch pattern: {config.github.branch_pattern}")
        print(f"  PR title:       {config.github.pr_title_pattern}")

    if config.adapters:
        print(f"\nAdapters:")
        for role, conf in config.adapters.items():
            atype = conf.get("type", "?") if isinstance(conf, dict) else conf
            print(f"  {role}: {atype}")


# ------------------------------------------------------------------
# run prompt
# ------------------------------------------------------------------


def cmd_run_prompt(args: argparse.Namespace) -> None:
    """Execute a markdown prompt through the agent pipeline."""
    config = _load_config(args)
    store, renderer = _resolve_paths(config)

    manual_fallback = ManualAdapter(store, renderer)
    if config.adapters:
        adapters = create_adapters_from_config(config.adapters, store, renderer)
    else:
        adapters = {}

    target_repo = _resolve_target_repo(
        getattr(args, "target_repo", "") or config.default_target_repo
    )

    runner = PromptRunner(
        store=store,
        agents_config=config.agents,
        adapters=adapters,
        fallback_adapter=manual_fallback,
    )

    def on_step(msg: str) -> None:
        print(f"[prompt] {msg}")

    result = runner.run(
        prompt_path=args.prompt_path,
        task_name=getattr(args, "name", None),
        target_repo=target_repo,
        on_step=on_step,
    )

    print(f"\nTask:       {result.task_name}")
    print(f"Prompt:     {result.prompt_file}")
    print(f"Run status: {result.run_status.value}")

    if result.steps:
        print(f"Steps:      {len(result.steps)}")
        for step in result.steps:
            marker = "+" if step.status.value == "completed" else "~"
            print(f"  {marker} [{step.agent}] {step.role}: {step.message}")

    if result.waiting_on:
        print(f"\nWaiting on: {result.waiting_on}")
        print(f"Resume: morch resume {result.task_name}")
    elif result.is_complete:
        print("\nAll agents completed successfully.")
    else:
        print(f"\n{result.message}")


# ------------------------------------------------------------------
# run task (existing file-artifact workflow)
# ------------------------------------------------------------------


def cmd_run_task(args: argparse.Namespace) -> None:
    """Create a task and drive it through the review pipeline."""
    run_orch, config = _build_run_orchestrator(args)

    def on_step(msg: str) -> None:
        print(f"[run] {msg}")

    target_repo = _resolve_target_repo(args.target_repo or config.default_target_repo)

    result = run_orch.run(
        task_name=args.task_name,
        target_repo=target_repo,
        description=args.description or "",
        on_step=on_step,
    )

    _print_run_result(result)


# ------------------------------------------------------------------
# run github (existing GitHub-native workflow)
# ------------------------------------------------------------------


def cmd_run_github(args: argparse.Namespace) -> None:
    """Claim a GitHub issue and drive it through the review pipeline."""
    orch, config = _build_github_orchestrator(args)

    work_type_str = getattr(args, "type", "feat") or "feat"
    try:
        work_type = WorkType(work_type_str)
    except ValueError:
        valid = ", ".join(wt.value for wt in WorkType)
        print(f"Error: unknown work type '{work_type_str}'. Valid: {valid}", file=sys.stderr)
        sys.exit(1)

    def on_step(msg: str) -> None:
        print(f"[github] {msg}")

    result = orch.run(
        issue_number=args.issue_number,
        work_type=work_type,
        on_step=on_step,
    )

    _print_github_run_result(result)


# ------------------------------------------------------------------
# resume
# ------------------------------------------------------------------


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume a task that is waiting for manual completion."""
    run_orch, _ = _build_run_orchestrator(args)

    def on_step(msg: str) -> None:
        print(f"[resume] {msg}")

    result = run_orch.resume(
        task_name=args.task_name,
        on_step=on_step,
    )

    _print_run_result(result)


def cmd_resume_github(args: argparse.Namespace) -> None:
    """Resume a GitHub-backed task that is waiting."""
    orch, _ = _build_github_orchestrator(args)

    def on_step(msg: str) -> None:
        print(f"[resume] {msg}")

    result = orch.resume(
        task_name=args.task_name,
        on_step=on_step,
    )

    _print_github_run_result(result)


# ------------------------------------------------------------------
# status
# ------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    _, engine, _ = _build_services(args)
    summary = engine.get_task_summary(args.task_name)

    print(f"Task:         {summary['task_name']}")
    print(f"Target repo:  {summary['target_repo'] or '(not set)'}")
    print(f"State:        {summary['state']}")
    print(f"Cycle:        {summary['cycle']} / {summary['max_cycles']}")
    print(f"Created:      {summary['created_at']}")
    print(f"Updated:      {summary['updated_at']}")
    print(f"Artifacts:    {', '.join(summary['artifacts']) or '(none)'}")

    if summary["missing_required"]:
        print(f"Missing:      {', '.join(summary['missing_required'])}")

    if summary["next_step"]:
        ns = summary["next_step"]
        print(f"\nNext step:")
        print(f"  Agent:      {ns['agent']}")
        print(f"  Artifact:   {ns['artifact']}")
        print(f"  {ns['instruction']}")
    else:
        print(f"\nNo further steps (terminal state).")


def cmd_status_github(args: argparse.Namespace) -> None:
    """Show status of a GitHub-backed task."""
    config = _load_config(args)
    store, _ = _resolve_paths(config)

    repo = getattr(args, "repo", None) or config.github.repo
    if not repo:
        print("Error: --repo is required (or set github.repo in config)", file=sys.stderr)
        sys.exit(1)

    github = GitHubService(repo)
    task_service = GitHubTaskService(
        store=store,
        github=github,
        branch_pattern=config.github.branch_pattern,
        labels=config.github.labels,
        base_branch=config.github.base_branch,
    )

    task = task_service.get_task(args.task_name)

    print(f"Task:         {task.name}")
    print(f"Repository:   {task.repo}")
    print(f"Issue:        #{task.issue_number} — {task.issue_title}")
    print(f"Work type:    {task.work_type.value}")
    print(f"State:        {task.state.value}")
    print(f"Run status:   {task.run_status.value}")
    print(f"Cycle:        {task.cycle} / {task.max_cycles}")
    print(f"Branch:       {task.branch_name}")
    if task.pr_number:
        print(f"PR:           #{task.pr_number}")
    if task.pr_url:
        print(f"PR URL:       {task.pr_url}")
    print(f"Created:      {task.created_at}")
    print(f"Updated:      {task.updated_at}")


# ------------------------------------------------------------------
# task subcommands (init, advance, validate, archive, list, next)
# ------------------------------------------------------------------


def cmd_task_init(args: argparse.Namespace) -> None:
    task_svc, _, config = _build_services(args)
    target_repo = _resolve_target_repo(args.target_repo or config.default_target_repo)
    task = task_svc.init_task(
        task_name=args.task_name,
        target_repo=target_repo,
        description=args.description or "",
    )
    print(f"Task created: {task.name}")
    print(f"State:        {task.state.value}")
    print(f"Workspace:    {config.workspace_dir}/active/{task.name}/")
    print(f"Next:         Edit 00-scope.md, then run: morch task advance {task.name}")


def cmd_task_advance(args: argparse.Namespace) -> None:
    task_svc, _, _ = _build_services(args)

    outcome = None
    if args.outcome:
        outcome = ReviewOutcome(args.outcome)

    task = task_svc.advance(args.task_name, review_outcome=outcome)
    print(f"Task:  {task.name}")
    print(f"State: {task.state.value}")
    print(f"Cycle: {task.cycle}")

    if task.is_terminal:
        if task.state.value == "approved":
            print(f"\nTask approved! Run: morch task archive {task.name}")
        elif task.state.value == "escalated":
            print(f"\nTask escalated — human intervention required.")
        elif task.state.value == "archived":
            print(f"\nTask archived.")
    else:
        next_step = task_svc.get_next_step(task.name)
        if next_step:
            print(f"\nNext: [{next_step.agent.value}] Write {next_step.artifact}")
            print(f"      {next_step.instruction}")


def cmd_task_next(args: argparse.Namespace) -> None:
    _, engine, _ = _build_services(args)
    result = engine.run_next_step(args.task_name)
    print(result.message)


def cmd_task_validate(args: argparse.Namespace) -> None:
    task_svc, _, _ = _build_services(args)
    task = task_svc.get_task(args.task_name)

    store, _ = _resolve_paths(task_svc.config)
    artifact_svc = ArtifactService(store)
    result = artifact_svc.validate(task)

    print(f"Task:    {result.task_name}")
    print(f"Cycle:   {result.cycle}")
    print(f"Valid:   {'yes' if result.is_valid else 'NO'}")
    print()

    for a in result.artifacts:
        status = "OK" if a.exists else ("MISSING" if a.required else "optional")
        marker = "x" if a.exists else (" " if not a.required else "!")
        print(f"  [{marker}] {a.filename:<40s} ({a.author}) {status}")

    if result.missing_required:
        print(f"\nMissing required: {', '.join(result.missing_required)}")


def cmd_task_archive(args: argparse.Namespace) -> None:
    task_svc, _, config = _build_services(args)
    task = task_svc.archive(args.task_name)
    print(f"Task '{task.name}' archived to {config.workspace_dir}/archive/{task.name}/")


def cmd_task_list(args: argparse.Namespace) -> None:
    task_svc, _, _ = _build_services(args)
    tasks = task_svc.list_tasks(include_archived=args.all)

    if tasks["active"]:
        print("Active tasks:")
        for name in tasks["active"]:
            task = task_svc.get_task(name)
            print(f"  {name:<30s} [{task.state.value}] cycle {task.cycle}")
    else:
        print("No active tasks.")

    if args.all and tasks.get("archived"):
        print("\nArchived tasks:")
        for name in tasks["archived"]:
            print(f"  {name}")


# ------------------------------------------------------------------
# Output formatters
# ------------------------------------------------------------------


def _print_run_result(result) -> None:
    print(f"\nTask:       {result.task_name}")
    print(f"State:      {result.final_state.value}")
    print(f"Run status: {result.run_status.value}")

    if result.steps:
        print(f"Steps:      {len(result.steps)}")
        for step in result.steps:
            marker = "+" if step.status.value == "completed" else "~"
            print(f"  {marker} [{step.agent.value}] {step.artifact}: {step.message}")

    if result.is_waiting:
        print(f"\nWaiting on: {result.waiting_on.value if result.waiting_on else 'unknown'}")
        print(f"Resume: morch resume task {result.task_name}")
    elif result.is_complete:
        if result.final_state.value == "approved":
            print(f"\nTask approved! Run: morch task archive {result.task_name}")
        elif result.final_state.value == "escalated":
            print(f"\nTask escalated — human intervention required.")
    elif result.run_status == RunStatus.SUSPENDED:
        print(f"\nRun suspended: {result.message}")


def _print_github_run_result(result: GitHubRunResult) -> None:
    print(f"\nTask:       {result.task_name}")
    print(f"State:      {result.final_state.value}")
    print(f"Run status: {result.run_status.value}")

    if result.pr_number:
        print(f"PR:         #{result.pr_number}")
    if result.pr_url:
        print(f"PR URL:     {result.pr_url}")

    if result.steps:
        print(f"Steps:      {len(result.steps)}")
        for step in result.steps:
            marker = "+" if step.status.value == "completed" else "~"
            print(f"  {marker} [{step.agent.value}] {step.action}: {step.message}")

    if result.is_waiting:
        print(f"\nWaiting on: {result.waiting_on.value if result.waiting_on else 'unknown'}")
        print(f"Run: morch resume github {result.task_name}")
    elif result.is_complete:
        if result.final_state.value == "approved":
            print(f"\nPR approved! Merge when ready.")
        elif result.final_state.value == "merged":
            print(f"\nPR merged successfully.")
        elif result.final_state.value == "escalated":
            print(f"\nTask escalated — human intervention required.")
    elif result.run_status == RunStatus.SUSPENDED:
        print(f"\nRun suspended: {result.message}")


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="morch",
        description="morch — multi-agent orchestrator for Cursor, Claude, and Codex workflows.",
    )
    parser.add_argument("--config", "-c", help="Path to config file", default=None)
    parser.add_argument("--workspace", "-w", help="Override workspace directory", default=None)

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- doctor ----
    p_doctor = sub.add_parser("doctor", help="System health check")
    p_doctor.set_defaults(func=cmd_doctor)

    # ---- auth ----
    p_auth = sub.add_parser("auth", help="Auth status and login for tools")
    auth_sub = p_auth.add_subparsers(dest="auth_command")

    p_auth_status = auth_sub.add_parser("status", help="Show auth status for all tools")
    p_auth_status.set_defaults(func=cmd_auth_status)

    for tool in ("cursor", "claude", "codex", "github", "git"):
        p_tool = auth_sub.add_parser(tool, help=f"{tool} auth")
        tool_sub = p_tool.add_subparsers(dest="tool_action")
        p_tool_status = tool_sub.add_parser("status", help=f"Check {tool} auth")
        p_tool_status.set_defaults(func=cmd_auth_status, tool_name=tool)
        p_tool_login = tool_sub.add_parser("login", help=f"Show {tool} login instructions")
        p_tool_login.set_defaults(func=cmd_auth_login, tool_name=tool)

    # ---- agents ----
    p_agents = sub.add_parser("agents", help="Agent configuration and ordering")
    agents_sub = p_agents.add_subparsers(dest="agents_command")

    p_agents_list = agents_sub.add_parser("list", help="List configured agents")
    p_agents_list.set_defaults(func=cmd_agents_list)

    p_agents_doctor = agents_sub.add_parser("doctor", help="Check agent readiness")
    p_agents_doctor.set_defaults(func=cmd_agents_doctor)

    p_agents_order = agents_sub.add_parser("order", help="Set agent execution order")
    p_agents_order.add_argument("agents", nargs="+", help="Agent names in order")
    p_agents_order.set_defaults(func=cmd_agents_order)

    # ---- config ----
    p_config = sub.add_parser("config", help="Configuration management")
    config_sub = p_config.add_subparsers(dest="config_command")

    p_config_show = config_sub.add_parser("show", help="Show effective config")
    p_config_show.set_defaults(func=cmd_config_show)

    # ---- run ----
    p_run = sub.add_parser("run", help="Execute a workflow")
    run_sub = p_run.add_subparsers(dest="run_command", required=True)

    # run prompt
    p_run_prompt = run_sub.add_parser("prompt", help="Markdown-prompt driven execution")
    p_run_prompt.add_argument("prompt_path", help="Path to markdown prompt file")
    p_run_prompt.add_argument("--name", "-n", default=None, help="Task name override")
    p_run_prompt.add_argument("--target-repo", "-t", default="", help="Path to target repository")
    p_run_prompt.set_defaults(func=cmd_run_prompt)

    # run task
    p_run_task = run_sub.add_parser("task", help="File-artifact review pipeline")
    p_run_task.add_argument("task_name", help="Task name (becomes directory name)")
    p_run_task.add_argument("--target-repo", "-t", default="", help="Path to target repository")
    p_run_task.add_argument("--description", "-d", default="", help="Short task description")
    p_run_task.set_defaults(func=cmd_run_task)

    # run github
    p_run_gh = run_sub.add_parser("github", help="GitHub-native issue pipeline")
    p_run_gh.add_argument("issue_number", type=int, help="GitHub issue number")
    p_run_gh.add_argument("--repo", "-r", default="", help="GitHub repository (owner/name)")
    p_run_gh.add_argument(
        "--type", default="feat",
        help="Work type: feat, modify, fix, refactor, docs, chore, ops, test, hotfix",
    )
    p_run_gh.set_defaults(func=cmd_run_github)

    # ---- resume ----
    p_resume = sub.add_parser("resume", help="Resume a paused task")
    resume_sub = p_resume.add_subparsers(dest="resume_command")

    p_resume_github = resume_sub.add_parser("github", help="Resume a GitHub-backed task")
    p_resume_github.add_argument("task_name", help="Task name (e.g. issue-42)")
    p_resume_github.add_argument("--repo", "-r", default="", help="GitHub repository")
    p_resume_github.set_defaults(func=cmd_resume_github)

    # resume <task-name> (file-artifact, when not "github")
    p_resume_task = resume_sub.add_parser("task", help="Resume a file-artifact task")
    p_resume_task.add_argument("task_name", help="Task name")
    p_resume_task.set_defaults(func=cmd_resume)

    # ---- status ----
    p_status = sub.add_parser("status", help="Show task status")
    status_sub = p_status.add_subparsers(dest="status_command")

    p_status_github = status_sub.add_parser("github", help="Show GitHub task status")
    p_status_github.add_argument("task_name", help="Task name (e.g. issue-42)")
    p_status_github.add_argument("--repo", "-r", default="", help="GitHub repository")
    p_status_github.set_defaults(func=cmd_status_github)

    p_status_task = status_sub.add_parser("task", help="Show file-artifact task status")
    p_status_task.add_argument("task_name", help="Task name")
    p_status_task.set_defaults(func=cmd_status)

    # ---- task (manual subcommands) ----
    p_task = sub.add_parser("task", help="Manual task management")
    task_sub = p_task.add_subparsers(dest="task_command", required=True)

    p_task_init = task_sub.add_parser("init", help="Create a new review task")
    p_task_init.add_argument("task_name")
    p_task_init.add_argument("--target-repo", "-t", default="")
    p_task_init.add_argument("--description", "-d", default="")
    p_task_init.set_defaults(func=cmd_task_init)

    p_task_advance = task_sub.add_parser("advance", help="Advance task to next state")
    p_task_advance.add_argument("task_name")
    p_task_advance.add_argument(
        "--outcome", "-o",
        choices=["approved", "changes-requested", "minor-fixes-applied"],
        default=None,
    )
    p_task_advance.set_defaults(func=cmd_task_advance)

    p_task_next = task_sub.add_parser("next", help="Show or execute next step")
    p_task_next.add_argument("task_name")
    p_task_next.set_defaults(func=cmd_task_next)

    p_task_validate = task_sub.add_parser("validate", help="Validate task artifacts")
    p_task_validate.add_argument("task_name")
    p_task_validate.set_defaults(func=cmd_task_validate)

    p_task_archive = task_sub.add_parser("archive", help="Archive an approved task")
    p_task_archive.add_argument("task_name")
    p_task_archive.set_defaults(func=cmd_task_archive)

    p_task_list = task_sub.add_parser("list", help="List tasks")
    p_task_list.add_argument("--all", "-a", action="store_true")
    p_task_list.set_defaults(func=cmd_task_list)

    # ---- backward compat: top-level aliases ----
    # These preserve the old `orchestrator run/init/...` interface.

    p_run_compat = sub.add_parser("run-task", help=argparse.SUPPRESS)
    p_run_compat.add_argument("task_name")
    p_run_compat.add_argument("--target-repo", "-t", default="")
    p_run_compat.add_argument("--description", "-d", default="")
    p_run_compat.set_defaults(func=cmd_run_task)

    p_init_compat = sub.add_parser("init", help=argparse.SUPPRESS)
    p_init_compat.add_argument("task_name")
    p_init_compat.add_argument("--target-repo", "-t", default="")
    p_init_compat.add_argument("--description", "-d", default="")
    p_init_compat.set_defaults(func=cmd_task_init)

    p_advance_compat = sub.add_parser("advance", help=argparse.SUPPRESS)
    p_advance_compat.add_argument("task_name")
    p_advance_compat.add_argument("--outcome", "-o", choices=["approved", "changes-requested", "minor-fixes-applied"], default=None)
    p_advance_compat.set_defaults(func=cmd_task_advance)

    p_validate_compat = sub.add_parser("validate", help=argparse.SUPPRESS)
    p_validate_compat.add_argument("task_name")
    p_validate_compat.set_defaults(func=cmd_task_validate)

    p_archive_compat = sub.add_parser("archive", help=argparse.SUPPRESS)
    p_archive_compat.add_argument("task_name")
    p_archive_compat.set_defaults(func=cmd_task_archive)

    p_list_compat = sub.add_parser("list", help=argparse.SUPPRESS)
    p_list_compat.add_argument("--all", "-a", action="store_true")
    p_list_compat.set_defaults(func=cmd_task_list)

    p_next_compat = sub.add_parser("next", help=argparse.SUPPRESS)
    p_next_compat.add_argument("task_name")
    p_next_compat.set_defaults(func=cmd_task_next)

    p_ghrun_compat = sub.add_parser("github-run", help=argparse.SUPPRESS)
    p_ghrun_compat.add_argument("issue_number", type=int)
    p_ghrun_compat.add_argument("--repo", "-r", default="")
    p_ghrun_compat.add_argument("--type", default="feat")
    p_ghrun_compat.set_defaults(func=cmd_run_github)

    p_ghresume_compat = sub.add_parser("github-resume", help=argparse.SUPPRESS)
    p_ghresume_compat.add_argument("task_name")
    p_ghresume_compat.add_argument("--repo", "-r", default="")
    p_ghresume_compat.set_defaults(func=cmd_resume_github)

    p_ghstatus_compat = sub.add_parser("github-status", help=argparse.SUPPRESS)
    p_ghstatus_compat.add_argument("task_name")
    p_ghstatus_compat.add_argument("--repo", "-r", default="")
    p_ghstatus_compat.set_defaults(func=cmd_status_github)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except OrchestratorError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
