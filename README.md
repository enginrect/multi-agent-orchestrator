# morch — Multi-Agent Orchestrator

A developer tool that orchestrates multi-agent review workflows using
Cursor, Claude, and Codex. Coordinates agents through structured pipelines
with explicit state tracking, cycle enforcement, and human escalation.

## What it does

- **Prompt-driven (local)** — `morch run prompt task.md` sends a markdown
  prompt through the agent pipeline locally, no GitHub interaction
- **File-artifact (local)** — `morch run task` drives a structured review
  pipeline with handoff documents and state tracking
- **GitHub-native (remote)** — `morch run github 42 --repo o/r` manages the
  full issue → branch → PR → review lifecycle on GitHub
- **Agent ordering** — configurable 2- or 3-agent pipelines with
  `morch agents order`
- **Auth management** — `morch doctor` and `morch auth` verify tool readiness
- **Waiting/resume semantics** — pauses when manual steps are needed,
  resumes cleanly

## Quick start

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Check system health
morch doctor

# Prompt-driven: send a markdown file through the agent pipeline
morch run prompt task-description.md --target-repo /path/to/repo

# File-artifact: structured review pipeline
morch --config configs/adapters-real.yaml run task add-ingress-service \
  --target-repo /path/to/repo

# GitHub-native: issue-driven pipeline
morch run github 42 --repo myorg/myapp --type feat
```

## Supported agents

| Agent | Default Role | CLI |
|-------|-------------|-----|
| Cursor | Implementer | `cursor agent -p` |
| Claude | First reviewer | `claude -p` |
| Codex | Final reviewer | `codex exec` |

Default order: `cursor -> claude -> codex`. Configurable via `morch agents order`.

## Commands

```
morch doctor                         System health check
morch auth status                    Auth status for all tools
morch auth <tool> status             Per-tool auth check
morch agents list                    Show configured agent order
morch agents doctor                  Check agent readiness
morch agents order <a> <b> [c]       Validate/display agent order (persist in config)
morch config show                    Show effective configuration

morch run prompt <path.md>           Markdown-prompt driven execution
morch run task <name>                File-artifact review pipeline
morch run github <issue>             GitHub-native issue pipeline
morch resume task <task>             Resume a paused file-artifact task
morch resume github <task>           Resume a paused GitHub task
morch status task <task>             Show file-artifact task status
morch status github <task>           Show GitHub task status
morch watch task <task>              Live-watch task state and run log

morch task init/advance/validate/archive/list
```

## Architecture

```
src/orchestrator/
├── domain/           # Pure logic: models, state machine, workflow
├── application/      # Use cases: task service, prompt runner, run orchestrator
├── infrastructure/   # I/O: file store, config, auth checker, run logger
└── adapters/         # Agent interfaces: manual, stub, command, codex, claude, cursor
```

## Documentation

- [morch guide](docs/morch.md) — Setup, auth, agents, workflows, commands
- [Architecture](docs/architecture.md) — Design decisions, layers, adapter model
- [GitHub workflow](docs/github-workflow.md) — Issue lifecycle, branch/PR conventions
- [Adapters](docs/adapters.md) — Real command adapters, wiring agents
- [Workflow](docs/workflow.md) — File-artifact review pipeline

## Development

```bash
source .venv/bin/activate
pytest tests/ -v          # Run tests
pytest tests/ --cov       # With coverage
```
