# AGENTS.md

## Repo purpose

This repository implements a **multi-agent orchestrator** — a file-based
coordination layer for the Cursor → Claude → Codex review workflow.

It is **not** a workload platform or infrastructure repository. It manages
review tasks for other repositories (e.g. `workload-cluster-add-on`).

## What the orchestrator does

- Creates task directories with structured artifacts
- Tracks review state through an explicit state machine
- Validates artifact sequence and review outcomes
- Enforces max review cycles with human escalation
- Generates instructions for manual or automated agent execution

## Multi-agent workflow

| Agent | Role | Artifacts |
|-------|------|-----------|
| **Cursor** | Primary implementer | 00-scope, 01-impl, 03/07-response |
| **Claude** | Reviewer + fixer | 02/06-review |
| **Codex** | Final reviewer | 04/08-review, 05/09-approval |

See `docs/workflow.md` for the full specification.

## Working agreement

**Allowed:**
- Create, modify, rename, delete files in the working tree
- Edit Python source, tests, docs, templates, configs
- Run tests and validate

**Not allowed:**
- `git commit`, `git push`, merge, or PR submission
- Embedding secrets or credentials in any file
- Treating `reference/` files as production source of truth

## Architecture

```
src/orchestrator/
├── domain/          Models, state machine, workflow definitions
├── application/     Task service, artifact validation, workflow engine
├── infrastructure/  File state store, template renderer, config loader
└── adapters/        Agent adapter interface + manual/stub implementations
```

See `docs/architecture.md` for the full design.

## Read first

1. `README.md`
2. `docs/architecture.md`
3. `docs/workflow.md`
4. `docs/usage.md`
