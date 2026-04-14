# morch — Multi-Agent Orchestrator

`morch` is a developer tool that orchestrates multi-agent review workflows
using Cursor, Claude, and Codex. It coordinates agents through structured
pipelines — file-based, GitHub-native, or prompt-driven — with explicit
state tracking, cycle enforcement, and human escalation.

## Supported Agents

| Agent | Role | CLI |
|-------|------|-----|
| **Cursor** | Implementation, rework | `cursor agent -p` |
| **Claude** | First review, minor fixes | `claude -p` |
| **Codex** | Final review, approval gate | `codex exec` |

Only these three agents are supported. The execution order is configurable.

## Setup

### Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Check system health

```bash
morch doctor
```

This verifies that all required tools are installed and authenticated.

### Auth

Check auth status for all tools:

```bash
morch auth status
```

Per-tool auth:

```bash
morch auth cursor status
morch auth claude status
morch auth codex status
morch auth github status
morch auth git status
```

Get login instructions:

```bash
morch auth claude login
morch auth github login
```

## Agent Ordering

The default agent order is `cursor -> claude -> codex`. The first agent
in the order acts as the implementer; the remaining agents are reviewers.

View current order:

```bash
morch agents list
```

Check agent readiness:

```bash
morch agents doctor
```

Set a custom order:

```bash
morch agents order cursor claude codex    # default
morch agents order claude codex           # 2-agent setup
morch agents order cursor codex claude    # Codex reviews first
```

Rules:
- Minimum 2 agents, maximum 3
- Only `cursor`, `claude`, `codex` are supported
- Order determines who implements and who reviews

To persist a custom order, add to your config file:

```yaml
agents:
  enabled: [cursor, claude, codex]
```

## Workflows

### Prompt-driven execution

The simplest way to use `morch` — provide a markdown file as the task prompt:

```bash
morch run prompt task-description.md --target-repo /path/to/repo
```

The first configured agent receives the markdown content as its instruction.
Subsequent agents review the output in order. The pipeline completes
automatically or pauses if manual intervention is needed.

### File-artifact workflow

The structured review pipeline using local file artifacts:

```bash
morch run task my-feature --target-repo /path/to/repo -c configs/adapters-real.yaml
```

Agents produce handoff documents in the task directory. State transitions
are tracked in local files. See [workflow.md](workflow.md) for details.

### GitHub-native workflow

Issue-driven pipeline using GitHub branches, PRs, and reviews:

```bash
morch run github 42 --repo owner/name --type feat
```

Agents collaborate through PRs. See [github-workflow.md](github-workflow.md)
for the full lifecycle, branch naming, and PR conventions.

## Commands

### System

| Command | Description |
|---------|-------------|
| `morch doctor` | System health check |
| `morch auth status` | Auth status for all tools |
| `morch auth <tool> status` | Per-tool auth check |
| `morch auth <tool> login` | Show login instructions |
| `morch agents list` | Show configured agent order |
| `morch agents doctor` | Check agent readiness |
| `morch agents order <a> <b> [c]` | Validate and display new order |
| `morch config show` | Show effective configuration |

### Execution

| Command | Description |
|---------|-------------|
| `morch run prompt <path.md>` | Markdown-prompt driven execution |
| `morch run task <name>` | File-artifact review pipeline |
| `morch run github <issue>` | GitHub-native issue pipeline |
| `morch resume task <task>` | Resume a paused file-artifact task |
| `morch resume github <task>` | Resume a paused GitHub task |

### Status

| Command | Description |
|---------|-------------|
| `morch status task <task>` | Show file-artifact task status |
| `morch status github <task>` | Show GitHub task status |

### Manual task management

| Command | Description |
|---------|-------------|
| `morch task init <name>` | Create a new review task |
| `morch task advance <name>` | Advance task to next state |
| `morch task next <name>` | Show next step |
| `morch task validate <name>` | Validate task artifacts |
| `morch task archive <name>` | Archive an approved task |
| `morch task list [--all]` | List tasks |

## Configuration

Configuration is loaded from YAML files. Use `--config` to specify a file,
or rely on defaults.

```yaml
workspace_dir: ./workspace
template_dir: ./templates/artifacts
max_cycles: 2

agents:
  enabled: [cursor, claude, codex]

github:
  repo: "owner/repo-name"
  base_branch: "main"
  branch_pattern: "{type}/issue-{issue}/{agent}/cycle-{cycle}"
  pr_title_pattern: "[{type}][Issue #{issue}][{agent}] {summary}"
  labels:
    claimed: "orchestrator:claimed"
    in_progress: "orchestrator:in-progress"
    review: "orchestrator:review"
    approved: "orchestrator:approved"

adapters:
  cursor:
    type: cursor-cli
    settings:
      command: /usr/local/bin/cursor
      timeout: 600
  claude:
    type: claude-cli
    settings:
      command: claude
      timeout: 300
  codex:
    type: codex-cli
    settings:
      command: codex
      timeout: 600
```

## Backward Compatibility

The `orchestrator` command remains available as an alias for `morch`.
Old command forms like `orchestrator run`, `orchestrator github-run`, etc.
continue to work through hidden backward-compatible subcommands.
