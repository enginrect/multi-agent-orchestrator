# morch — Multi-Agent Orchestrator

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)

A developer tool that orchestrates multi-agent code review workflows
using **Cursor**, **Claude**, and **Codex**. Coordinates agents through
structured pipelines with explicit state tracking, provenance attribution,
cycle enforcement, and human-gated merge.

> **v0.2.0** — setup flow, agent auto-detection, structured logging,
> resource limit handling, bilingual documentation.
> See [CHANGELOG.md](CHANGELOG.md) for details.

## What it does

- **GitHub-native workflow** — `morch issue start --repo o/r --title "..."` manages
  the full issue → branch → PR → review → approval lifecycle on GitHub
- **File-artifact workflow** — `morch run task <name>` drives a structured review
  pipeline with handoff documents and local state tracking
- **Prompt-driven workflow** — `morch run prompt task.md` sends a markdown
  prompt through the agent pipeline
- **Agent provenance** — every GitHub comment and review carries explicit
  agent identity (`@cursor-agent`, `@claude-agent`, `@codex-agent`)
- **Review relay** — when an agent cannot post directly (e.g. Codex sandbox),
  the orchestrator relays the review to the PR with clear attribution
- **Auth management** — `morch doctor` verifies tool readiness across all agents
- **Waiting/resume semantics** — pauses when manual steps are needed,
  resumes cleanly

## Quick Start

### Prerequisites

- Python 3.11+
- [GitHub CLI (`gh`)](https://cli.github.com/) — authenticated via `gh auth login`
- At least one agent CLI installed:
  [Cursor](https://cursor.sh/),
  [Claude Code](https://docs.anthropic.com/en/docs/claude-code),
  [Codex](https://github.com/openai/codex)

### Install

```bash
git clone https://github.com/enginrect/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Setup

```bash
morch setup           # Auto-detect agents and configure paths
morch doctor          # Check system health
morch agents doctor   # Check agent readiness
```

### Run

```bash
# GitHub-native: create issue, open PR, drive multi-agent review
morch issue start \
  --repo myorg/myapp \
  --title "Add rate limiting to API" \
  --type feat \
  --prompt-file prompts/rate-limit.md \
  --local-repo /path/to/local/clone

# File-artifact: structured review pipeline
morch --config configs/adapters-real.yaml run task add-ingress-service \
  --target-repo /path/to/repo

# Prompt-driven: send a markdown file through the agent pipeline
morch run prompt task-description.md --target-repo /path/to/repo
```

## Supported Agents

| Agent  | Default Role    | CLI               |
|--------|----------------|--------------------|
| Cursor | Implementer    | `cursor agent -p`  |
| Claude | First reviewer | `claude -p`        |
| Codex  | Final reviewer | `codex exec`       |

Default order: **Cursor → Claude → Codex**. Configurable via `morch agents order`.

## Commands

```
morch setup                          Interactive agent setup & auto-detect
morch doctor                         System health check
morch auth status                    Auth status for all tools
morch agents list                    Show configured agent order
morch agents doctor                  Check agent readiness
morch config show                    Show effective config + agent paths

morch issue start --repo o/r ...     GitHub-native: create + drive
morch run github <issue>             GitHub-native: existing issue
morch run task <name>                File-artifact review pipeline
morch run prompt <path.md>           Prompt-driven execution

morch resume github <task>           Resume a paused GitHub task
morch resume task <task>             Resume a paused file-artifact task
morch status github <task>           Show GitHub task status
morch status task <task>             Show file-artifact task status
morch task list                      List all tasks
```

## Architecture

```
src/orchestrator/
├── domain/           Pure logic: models, state machine, workflow
├── application/      Use cases: task service, prompt runner, run orchestrator
├── infrastructure/   I/O: file store, config, auth checker, run logger
└── adapters/         Agent interfaces: manual, stub, command, codex, claude, cursor
```

## Documentation

- [Usage guide (English)](docs/usage-en.md) — complete setup-to-workflow user guide
- [Usage guide (Korean)](docs/usage-ko.md) — 한국어 사용 가이드
- [Technical specification (English)](docs/tech-spec.md) — architecture, state model, workflows
- [Technical specification (Korean)](docs/tech-spec-ko.md) — 기술 사양서 (한국어)
- [morch guide](docs/morch.md) — quick reference for setup, auth, agents
- [Architecture](docs/architecture.md) — design decisions, layers, adapter model
- [GitHub workflow](docs/github-workflow.md) — issue lifecycle, branch/PR conventions
- [Adapters](docs/adapters.md) — real command adapters, wiring agents
- [Workflow](docs/workflow.md) — file-artifact review pipeline

## Known Limitations

- **Final merge is always a human decision** — `morch` never auto-merges PRs
  (exception: self-hosting workflows in this repository)
- **Codex sandbox** — Codex's `--full-auto` mode blocks outbound GitHub API;
  reviews are relayed by the orchestrator
- **Single-repo scope** — each invocation targets one repository
- **Sequential execution** — agents run one at a time within a cycle

## Development

```bash
source .venv/bin/activate
pytest tests/ -v          # Run tests
pytest tests/ --cov       # With coverage
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache License 2.0](LICENSE)
