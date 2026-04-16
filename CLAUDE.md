# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Purpose

This repository implements a **multi-agent orchestrator** for file-based
review workflows. It coordinates the Cursor → Claude → Codex pipeline
for reviewing changes in target repositories.

## Your role

When working in this repo, Claude operates as a **developer and reviewer**
of the orchestrator itself. The orchestrator manages reviews of *other*
repos — do not confuse the orchestrator's code with the target repo's code.

## Architecture

```
src/orchestrator/
├── domain/          Pure logic: models, state machine, workflow
├── application/     Use cases: task service, artifact validation, engine
├── infrastructure/  I/O: file state store, templates, config
└── adapters/        Agent interfaces: manual, stub, (future: MCP/API)
```

Key files:
- `src/orchestrator/domain/state_machine.py` — all valid state transitions
- `src/orchestrator/domain/workflow.py` — artifact sequence definitions
- `src/orchestrator/application/task_service.py` — task lifecycle logic
- `src/orchestrator/cli.py` — CLI commands

## Validation commands

```bash
source .venv/bin/activate
pytest tests/ -v
orchestrator --help
```

## Agent boundaries

- **Allowed:** edit Python source, tests, docs, templates, configs
- **Not allowed:** `git commit`, `git push`, merge, PR submission,
  embedding secrets

## Completion checklist

Before declaring work done:
- [ ] `pytest tests/ -v` passes
- [ ] `orchestrator --help` works
- [ ] No secrets or production values introduced
- [ ] Documentation updated if structure changed

## Read first

1. `README.md`
2. `docs/architecture.md`
3. `docs/workflow.md`
