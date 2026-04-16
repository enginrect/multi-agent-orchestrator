"""CLI entrypoint for morch — the multi-agent orchestrator.

Primary command: ``morch``
Backward-compatible alias: ``orchestrator``

Command groups:
    morch doctor                      System health check
    morch auth status                 All auth status
    morch auth <tool> status          Per-tool auth check
    morch auth <tool> login           Show login instructions
    morch agents list                 Show configured agent order
    morch agents doctor               Check agent readiness
    morch agents order <a> <b> [c]    Set agent execution order
    morch config show                 Show effective configuration
    morch run prompt <path.md>        Markdown-prompt driven execution
    morch run task <name>             File-artifact review pipeline
    morch run github <issue>          GitHub-native issue pipeline
    morch issue create                Create a GitHub issue
    morch issue list                  List GitHub issues
    morch issue view <number>         View issue details
    morch issue reopen <number>       Reopen a closed issue
    morch issue start                 Create issue + start workflow
    morch prompt list-templates       List available prompt templates
    morch prompt init <name>          Copy template to local file
    morch resume task <task-name>     Resume a paused file-artifact task
    morch resume github <task-name>   Resume a GitHub task
    morch status task <task-name>     Show file-artifact task status
    morch status github <task-name>   Show GitHub task status
    morch watch task <task-name>      Live-watch task progress
    morch task init/advance/validate/archive/list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters.factory import create_adapters_from_config, create_default_adapters
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
from .user_hints import (
    CLI_COMMAND_NAME,
    WORK_TYPE_ARG_HELP,
    hint_resume_github,
    hint_resume_task,
    task_advance_shell,
    task_archive_shell,
)


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


def _resolve_adapters(
    config: OrchestratorConfig,
    store: FileStateStore,
    renderer: TemplateRenderer,
) -> dict:
    """Resolve adapters from config or auto-create defaults for enabled agents.

    When an explicit ``adapters:`` section exists in the config, those
    adapters are used. Otherwise, default CLI adapters are created for
    each enabled agent (cursor-cli, claude-cli, codex-cli), making
    workflows automatic without requiring manual adapter configuration.
    """
    if config.adapters:
        return create_adapters_from_config(config.adapters, store, renderer)
    return create_default_adapters(config.agents.enabled, store)


def _build_run_orchestrator(args: argparse.Namespace) -> tuple[RunOrchestrator, OrchestratorConfig]:
    config = _load_config(args)
    store, renderer = _resolve_paths(config)
    store.ensure_workspace()
    artifact_svc = ArtifactService(store)
    task_svc = TaskService(config, store, renderer, artifact_svc)

    manual_fallback = ManualAdapter(store, renderer)
    adapters = _resolve_adapters(config, store, renderer)

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
    adapters = _resolve_adapters(config, store, renderer)

    local_repo = getattr(args, "local_repo", None) or config.github.local_repo_path
    orch = GitHubRunOrchestrator(
        task_service=task_service,
        github=github,
        store=store,
        adapters=adapters,
        fallback_adapter=manual_fallback,
        local_repo_path=local_repo,
    )
    return orch, config


# ------------------------------------------------------------------
# doctor
# ------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> None:
    """System health check — verify all tools and configuration."""
    config = _load_config(args)

    print(f"{CLI_COMMAND_NAME} doctor\n")

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
        print(f"Some checks failed. Run `{CLI_COMMAND_NAME} auth status` for details.")


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
    adapters = _resolve_adapters(config, store, renderer)

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
        print(hint_resume_task(result.task_name))
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


def _read_prompt_file(path_str: str) -> str:
    """Read a prompt file and return its content, or exit on error."""
    p = Path(path_str)
    if not p.is_file():
        print(f"Error: prompt file not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p.read_text()


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

    prompt_content = None
    prompt_file_path = getattr(args, "prompt_file", None)
    if prompt_file_path:
        prompt_content = _read_prompt_file(prompt_file_path)

    def on_step(msg: str) -> None:
        print(f"[github] {msg}")

    result = orch.run(
        issue_number=args.issue_number,
        work_type=work_type,
        on_step=on_step,
        prompt_content=prompt_content,
    )

    _print_github_run_result(result)


# ------------------------------------------------------------------
# resume
# ------------------------------------------------------------------


def _is_github_task(store: "FileStateStore", task_name: str) -> bool:
    """Peek at state.yaml to detect a GitHub-native task."""
    state_file = store.task_dir(task_name) / "state.yaml"
    if not state_file.is_file():
        state_file = store.task_dir(task_name, archived=True) / "state.yaml"
    if not state_file.is_file():
        return False
    try:
        import yaml as _yaml
        data = _yaml.safe_load(state_file.read_text())
        return isinstance(data, dict) and "issue_number" in data
    except Exception:
        return False


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume a task that is waiting for manual completion."""
    config = _load_config(args)
    store, _ = _resolve_paths(config)
    if _is_github_task(store, args.task_name):
        print(
            f"Error: '{args.task_name}' is a GitHub-native task.\n"
            f"Use:  {CLI_COMMAND_NAME} resume github {args.task_name}",
            file=sys.stderr,
        )
        sys.exit(1)

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
# watch
# ------------------------------------------------------------------


def cmd_watch_task(args: argparse.Namespace) -> None:
    """Live-poll task state and run log until interrupted."""
    import json
    import time as _time

    config = _load_config(args)
    store, _ = _resolve_paths(config)
    task_name = args.task_name
    interval = getattr(args, "interval", 3)

    try:
        while True:
            task = store.load_task(task_name)
            task_dir = store.task_dir(task_name)

            elapsed = ""
            try:
                from datetime import datetime, timezone

                created = datetime.fromisoformat(task.created_at)
                now = datetime.now(timezone.utc)
                delta = now - created
                elapsed = f"{int(delta.total_seconds())}s"
            except Exception:
                elapsed = "?"

            next_artifact = ""
            if task.history:
                last = task.history[-1]
                next_artifact = last.artifact or ""

            print("\033[2J\033[H", end="")
            print(f"=== {CLI_COMMAND_NAME} watch: {task_name} ===\n")
            print(f"  State:        {task.state.value}")
            print(f"  Run status:   {task.run_status.value}")
            print(f"  Cycle:        {task.cycle} / {task.max_cycles}")
            print(f"  Elapsed:      {elapsed}")
            if next_artifact:
                print(f"  Last artifact: {next_artifact}")
            print()

            log_path = task_dir / "run.log"
            if log_path.is_file():
                lines = log_path.read_text().splitlines()
                tail = lines[-10:] if len(lines) > 10 else lines
                print("  --- run log (last 10 events) ---")
                for line in tail:
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")[:19]
                        evt = entry.get("event", "?")
                        extra = {
                            k: v
                            for k, v in entry.items()
                            if k not in ("timestamp", "event")
                        }
                        extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
                        print(f"  {ts} [{evt}] {extra_str}")
                    except json.JSONDecodeError:
                        print(f"  {line[:120]}")
            else:
                print("  (no run log yet)")

            if task.is_terminal:
                print(f"\n  Task reached terminal state: {task.state.value}")
                break

            print(f"\n  Refreshing in {interval}s... (Ctrl+C to stop)")
            _time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Watch stopped.")


# ------------------------------------------------------------------
# issue lifecycle
# ------------------------------------------------------------------


def _build_github_service(args: argparse.Namespace) -> tuple[GitHubService, OrchestratorConfig]:
    config = _load_config(args)
    repo = getattr(args, "repo", None) or config.github.repo
    if not repo:
        print("Error: --repo is required (or set github.repo in config)", file=sys.stderr)
        sys.exit(1)
    return GitHubService(repo), config


def cmd_issue_create(args: argparse.Namespace) -> None:
    """Create a new GitHub issue."""
    gh, _ = _build_github_service(args)
    labels = [l.strip() for l in (args.labels or "").split(",") if l.strip()] or None
    body = args.body or ""

    if getattr(args, "prompt_file", None):
        prompt = _read_prompt_file(args.prompt_file)
        if body:
            body = f"{body}\n\n---\n\n{prompt}"
        else:
            body = prompt

    result = gh.create_issue(title=args.title, body=body, labels=labels)
    url = result.get("url", "")
    number = result.get("number", "?")
    print(f"Issue created: #{number}")
    if url:
        print(f"URL: {url}")


def cmd_issue_list(args: argparse.Namespace) -> None:
    """List GitHub issues."""
    gh, _ = _build_github_service(args)
    state = getattr(args, "state", "open") or "open"
    issues = gh.list_issues(state=state)
    if not issues:
        print(f"No {state} issues found.")
        return
    for issue in issues:
        labels = ", ".join(l.get("name", "") for l in issue.get("labels", []))
        label_str = f" [{labels}]" if labels else ""
        print(f"  #{issue['number']:>5}  {issue.get('state', ''):>6}  {issue.get('title', '')}{label_str}")


def cmd_issue_view(args: argparse.Namespace) -> None:
    """View a GitHub issue."""
    gh, _ = _build_github_service(args)
    issue = gh.get_issue(args.issue_number)
    print(f"Issue:    #{issue.get('number', '?')}")
    print(f"Title:    {issue.get('title', '')}")
    print(f"State:    {issue.get('state', '')}")
    print(f"URL:      {issue.get('url', '')}")
    labels = [l.get("name", "") for l in issue.get("labels", [])]
    if labels:
        print(f"Labels:   {', '.join(labels)}")
    body = issue.get("body", "") or ""
    if body:
        print(f"\n{body[:500]}")


def cmd_issue_reopen(args: argparse.Namespace) -> None:
    """Reopen a closed GitHub issue."""
    gh, _ = _build_github_service(args)
    gh.reopen_issue(args.issue_number)
    print(f"Issue #{args.issue_number} reopened.")


def cmd_issue_start(args: argparse.Namespace) -> None:
    """Create a new issue and immediately start the GitHub workflow."""
    gh, config = _build_github_service(args)

    body = args.body or ""
    prompt_content = None
    if getattr(args, "prompt_file", None):
        prompt_content = _read_prompt_file(args.prompt_file)
        if body:
            body = f"{body}\n\n---\n\n{prompt_content}"
        else:
            body = prompt_content

    result = gh.create_issue(title=args.title, body=body)
    issue_number = result.get("number")
    if not issue_number:
        print("Error: could not determine issue number from creation result", file=sys.stderr)
        sys.exit(1)

    print(f"Issue #{issue_number} created: {args.title}")

    args.issue_number = issue_number
    orch, _ = _build_github_orchestrator(args)

    work_type_str = getattr(args, "type", "feat") or "feat"
    try:
        work_type = WorkType(work_type_str)
    except ValueError:
        work_type = WorkType.FEAT

    def on_step(msg: str) -> None:
        print(f"[github] {msg}")

    run_result = orch.run(
        issue_number=issue_number,
        work_type=work_type,
        on_step=on_step,
        prompt_content=prompt_content,
    )

    _print_github_run_result(run_result)


# ------------------------------------------------------------------
# prompt template management
# ------------------------------------------------------------------

PROMPT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "prompts"


def cmd_prompt_list_templates(args: argparse.Namespace) -> None:
    """List available prompt templates."""
    if not PROMPT_TEMPLATES_DIR.is_dir():
        print("No prompt templates found.")
        return
    templates = sorted(f.stem for f in PROMPT_TEMPLATES_DIR.glob("*.md"))
    if not templates:
        print("No prompt templates found.")
        return
    print("Available prompt templates:\n")
    for name in templates:
        print(f"  {name}")
    print(f"\nUsage: {CLI_COMMAND_NAME} prompt init <name> --output .morch/prompts/my-task.md")


def cmd_prompt_init(args: argparse.Namespace) -> None:
    """Copy a prompt template to a user-local file."""
    import shutil
    template_name = args.template_name
    source = PROMPT_TEMPLATES_DIR / f"{template_name}.md"
    if not source.is_file():
        print(f"Error: template not found: {template_name}", file=sys.stderr)
        available = sorted(f.stem for f in PROMPT_TEMPLATES_DIR.glob("*.md")) if PROMPT_TEMPLATES_DIR.is_dir() else []
        if available:
            print(f"Available: {', '.join(available)}", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    print(f"Template '{template_name}' copied to: {output}")
    print(
        f"Edit the file, then use it with: "
        f"{CLI_COMMAND_NAME} run github <issue> --prompt-file {output}"
    )


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
    print(f"Next:         Edit 00-scope.md, then run: {task_advance_shell(task.name)}")


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
            print(f"\nTask approved! Run: {task_archive_shell(task.name)}")
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
        print(hint_resume_task(result.task_name))
    elif result.is_complete:
        if result.final_state.value == "approved":
            print(f"\nTask approved! Run: {task_archive_shell(result.task_name)}")
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
        print(hint_resume_github(result.task_name))
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
        prog=CLI_COMMAND_NAME,
        description=(
            f"{CLI_COMMAND_NAME} — multi-agent orchestrator for "
            "Cursor, Claude, and Codex workflows."
        ),
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
    p_run_gh.add_argument("--type", default="feat", help=WORK_TYPE_ARG_HELP)
    p_run_gh.add_argument(
        "--prompt-file", default=None,
        help="Path to a detailed prompt file for agent instructions",
    )
    p_run_gh.add_argument("--local-repo", default=None, help="Absolute path to local git clone (default: CWD)")
    p_run_gh.set_defaults(func=cmd_run_github)

    # ---- issue ----
    p_issue = sub.add_parser("issue", help="GitHub issue lifecycle")
    issue_sub = p_issue.add_subparsers(dest="issue_command", required=True)

    p_issue_create = issue_sub.add_parser("create", help="Create a new GitHub issue")
    p_issue_create.add_argument("--repo", "-r", default="", help="GitHub repository (owner/name)")
    p_issue_create.add_argument("--title", required=True, help="Issue title")
    p_issue_create.add_argument("--body", "-b", default="", help="Issue body")
    p_issue_create.add_argument("--labels", "-l", default="", help="Comma-separated labels")
    p_issue_create.add_argument("--prompt-file", default=None, help="Prompt file to include in body")
    p_issue_create.set_defaults(func=cmd_issue_create)

    p_issue_list = issue_sub.add_parser("list", help="List GitHub issues")
    p_issue_list.add_argument("--repo", "-r", default="", help="GitHub repository (owner/name)")
    p_issue_list.add_argument("--state", "-s", default="open", choices=["open", "closed", "all"], help="Issue state filter")
    p_issue_list.set_defaults(func=cmd_issue_list)

    p_issue_view = issue_sub.add_parser("view", help="View a GitHub issue")
    p_issue_view.add_argument("issue_number", type=int, help="Issue number")
    p_issue_view.add_argument("--repo", "-r", default="", help="GitHub repository (owner/name)")
    p_issue_view.set_defaults(func=cmd_issue_view)

    p_issue_reopen = issue_sub.add_parser("reopen", help="Reopen a closed GitHub issue")
    p_issue_reopen.add_argument("issue_number", type=int, help="Issue number")
    p_issue_reopen.add_argument("--repo", "-r", default="", help="GitHub repository (owner/name)")
    p_issue_reopen.set_defaults(func=cmd_issue_reopen)

    p_issue_start = issue_sub.add_parser("start", help="Create issue and start workflow immediately")
    p_issue_start.add_argument("--repo", "-r", default="", help="GitHub repository (owner/name)")
    p_issue_start.add_argument("--title", required=True, help="Issue title")
    p_issue_start.add_argument("--body", "-b", default="", help="Issue body")
    p_issue_start.add_argument("--prompt-file", default=None, help="Prompt file for detailed instructions")
    p_issue_start.add_argument("--type", default="feat", help=WORK_TYPE_ARG_HELP)
    p_issue_start.add_argument("--local-repo", default=None, help="Absolute path to local git clone (default: CWD)")
    p_issue_start.set_defaults(func=cmd_issue_start)

    # ---- prompt ----
    p_prompt = sub.add_parser("prompt", help="Prompt template management")
    prompt_sub = p_prompt.add_subparsers(dest="prompt_command", required=True)

    p_prompt_list = prompt_sub.add_parser("list-templates", help="List available prompt templates")
    p_prompt_list.set_defaults(func=cmd_prompt_list_templates)

    p_prompt_init = prompt_sub.add_parser("init", help="Copy a prompt template to a local file")
    p_prompt_init.add_argument("template_name", help="Template name (without .md)")
    p_prompt_init.add_argument("--output", "-o", required=True, help="Output file path")
    p_prompt_init.set_defaults(func=cmd_prompt_init)

    # ---- resume ----
    p_resume = sub.add_parser("resume", help="Resume a paused task")
    resume_sub = p_resume.add_subparsers(dest="resume_command")

    p_resume_github = resume_sub.add_parser("github", help="Resume a GitHub-backed task")
    p_resume_github.add_argument("task_name", help="Task name (e.g. issue-42)")
    p_resume_github.add_argument("--repo", "-r", default="", help="GitHub repository")
    p_resume_github.add_argument("--local-repo", default=None, help="Absolute path to local git clone (default: CWD)")
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

    # ---- watch ----
    p_watch = sub.add_parser("watch", help="Live-watch task progress")
    watch_sub = p_watch.add_subparsers(dest="watch_command", required=True)

    p_watch_task = watch_sub.add_parser("task", help="Watch a task's state and run log")
    p_watch_task.add_argument("task_name", help="Task name to watch")
    p_watch_task.add_argument(
        "--interval", "-n", type=int, default=3,
        help="Refresh interval in seconds (default: 3)",
    )
    p_watch_task.set_defaults(func=cmd_watch_task)

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
    p_ghrun_compat.add_argument("--type", default="feat", help=WORK_TYPE_ARG_HELP)
    p_ghrun_compat.add_argument("--prompt-file", default=None)
    p_ghrun_compat.add_argument("--local-repo", default=None)
    p_ghrun_compat.set_defaults(func=cmd_run_github)

    p_ghresume_compat = sub.add_parser("github-resume", help=argparse.SUPPRESS)
    p_ghresume_compat.add_argument("task_name")
    p_ghresume_compat.add_argument("--repo", "-r", default="")
    p_ghresume_compat.add_argument("--local-repo", default=None)
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
