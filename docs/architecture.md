# Architecture

## Purpose

The multi-agent orchestrator manages file-based review workflows for
repository changes. It coordinates three AI agents (Cursor, Claude, Codex)
through a structured review pipeline with explicit state tracking and
single-command execution.

## Design principles

1. **File-based state** — All task state is stored as YAML files on disk.
   No database, no shared session state. Any agent on any machine can
   read the current state by examining the task directory.

2. **Explicit state machine** — Every valid state transition is declared
   in code. Invalid transitions are rejected with clear errors.

3. **Separation of concerns** — Domain logic (models, state machine) is
   pure Python with no I/O. Application services coordinate use cases.
   Infrastructure handles file operations. Adapters encapsulate agent
   invocation.

4. **Adapter pattern with capability model** — Agent invocation is behind
   an abstract interface. Each adapter declares its capability (manual,
   semi-auto, automatic). The run orchestrator adapts its behavior based
   on what each adapter can do.

5. **Single source of truth** — `state.yaml` in each task directory is
   the canonical task state. Artifact files are evidence; the state file
   is the record.

6. **Two-tier status** — `TaskState` tracks *where* in the review pipeline
   a task is. `RunStatus` tracks *what the execution engine is doing*.
   These are orthogonal concerns.

## Layers

```
┌─────────────────────────────────────────────┐
│                   CLI                        │
│    run · resume · init · advance · status    │
├─────────────────────────────────────────────┤
│              Application                     │
│  RunOrchestrator · GitHubRunOrchestrator     │
│  TaskService · GitHubTaskService             │
│  ArtifactSvc · PromptRunner                  │
├─────────────────────────────────────────────┤
│               Domain                         │
│  Task · TaskState · RunStatus · StateMachine │
│  AdapterCapability · ExecutionResult         │
├────────────────────┬────────────────────────┤
│  Infrastructure    │      Adapters              │
│  FileStateStore    │  ManualAdapter (MANUAL)    │
│  TemplateRenderer  │  StubAdapter (AUTOMATIC)   │
│  ConfigLoader      │  CommandAdapter (base)     │
│  RunLogger         │  CodexCLI / ClaudeCLI     │
│                    │  CursorCLI / Factory       │
└────────────────────┴──────────────────────────┘
```

### Domain (`src/orchestrator/domain/`)

Pure Python, no I/O, no external dependencies.

- **`models.py`** — Core types:
  - `Task` — Aggregate root, serializable to/from YAML
  - `TaskState` — Workflow phases (initialized → ... → approved/escalated/archived)
  - `RunStatus` — Execution states (idle, running, waiting_on_X, completed, suspended)
  - `AgentRole`, `ReviewOutcome`, `ArtifactSpec`, `StateTransition`
  - `AdapterCapability` — What an adapter can do (manual, semi_auto, automatic)
  - `ExecutionStatus`, `ExecutionResult` — Adapter invocation outcome

- **`state_machine.py`** — Transition table mapping `{from_state: [to_states]}`.
  `validate_transition()` raises `InvalidTransitionError` for undeclared
  transitions.

- **`workflow.py`** — Artifact catalog (cycle 1 and cycle 2 specs),
  `resolve_next_step()` function that determines the next action given
  current state and cycle, artifact-to-state mapping.

- **`errors.py`** — Domain exceptions: `InvalidTransitionError`,
  `TaskNotFoundError`, `TaskAlreadyExistsError`, `ArtifactMissingError`,
  `MaxCyclesExceededError`.

### Application (`src/orchestrator/application/`)

Use case orchestration. Depends on domain and infrastructure interfaces.

- **`run_orchestrator.py`** — Single-command execution engine. The core
  execution loop:
  1. Resolve next step from task state
  2. If artifact exists (resume case), advance and loop
  3. Invoke the adapter for the responsible agent
  4. If COMPLETED → advance and continue
  5. If WAITING → record waiting status, return
  6. If FAILED → suspend, return
  7. Loop until terminal or waiting

- **`task_service.py`** — Task lifecycle: `init_task`, `advance`, `archive`,
  `get_task`, `list_tasks`, `get_next_step`. The `advance` method reads
  the current state, checks for the expected artifact, auto-detects review
  outcomes, and transitions to the next state.

- **`artifact_service.py`** — Validates artifact presence against the
  workflow spec. Parses `**Status**: ...` from review artifacts to detect
  review outcomes.

- **`workflow_engine.py`** — Legacy single-step orchestration. Still used
  by `next` command for instruction generation.

### Infrastructure (`src/orchestrator/infrastructure/`)

File system operations and configuration.

- **`file_state_store.py`** — Reads/writes `state.yaml` per task. Manages
  `workspace/active/` and `workspace/archive/` directories.

- **`template_renderer.py`** — Loads markdown templates from `templates/artifacts/`.

- **`config_loader.py`** — Loads `OrchestratorConfig` from YAML, including
  the `adapters` section for per-agent adapter configuration.

- **`run_logger.py`** — JSONL append-only logger. Writes structured
  entries to `<task-dir>/run.log` for debugging and audit.

### Adapters (`src/orchestrator/adapters/`)

Agent invocation abstraction with capability model and real execution.

- **`base.py`** — Abstract `AgentAdapter` interface:
  - `name` — Human-readable identifier
  - `capability` — `AdapterCapability` (MANUAL, SEMI_AUTO, AUTOMATIC)
  - `execute()` → `ExecutionResult` (COMPLETED, WAITING, or FAILED)
  - `health_check()` — Connectivity verification

- **`manual.py`** — Capability: MANUAL. Writes instruction files and
  pre-populates artifact templates. Returns `WAITING`.

- **`stub.py`** — Capability: AUTOMATIC. Auto-completes steps with
  configurable review outcomes. For testing only.

- **`command.py`** — Base for all command-execution adapters. Handles
  subprocess invocation, timeout, env vars, prompt file writing,
  per-step log capture, artifact verification, outcome detection.

- **`codex.py`** — Real adapter. Invokes `codex` CLI with Codex-optimized
  prompts. Capability: AUTOMATIC.

- **`claude_adapter.py`** — Real adapter. Invokes `claude` CLI with
  Claude-optimized prompts. Capability: AUTOMATIC.

- **`cursor.py`** — Honest adapter. Without a configured command,
  returns WAITING (MANUAL). With a command, delegates to CommandAdapter
  (AUTOMATIC).

- **`factory.py`** — Creates adapters from config. Maps type strings
  (`manual`, `stub`, `command`, `codex-cli`, `claude-cli`, `cursor-cli`)
  to concrete adapter classes.

## Adapter capability model

```
┌────────────┬──────────────────────────────────────────┐
│ Capability │ Run orchestrator behavior                │
├────────────┼──────────────────────────────────────────┤
│ AUTOMATIC  │ Invoke → advance → continue loop         │
│ SEMI_AUTO  │ Invoke → may return WAITING or COMPLETED │
│ MANUAL     │ Generate instructions → return WAITING   │
└────────────┴──────────────────────────────────────────┘
```

The run orchestrator checks adapters per-agent. If no adapter is registered
for an agent, it falls back to the `fallback_adapter` (typically manual).
If no fallback exists, the run suspends.

### Adding a new adapter

```python
from orchestrator.adapters.base import AgentAdapter
from orchestrator.domain.models import AdapterCapability, ExecutionResult, ExecutionStatus

class McpClaudeAdapter(AgentAdapter):
    @property
    def name(self) -> str:
        return "mcp-claude"

    @property
    def capability(self) -> AdapterCapability:
        return AdapterCapability.AUTOMATIC

    def execute(self, task_name, artifact, template, instruction, context):
        # Invoke Claude via MCP, write artifact, return result
        ...
        return ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            artifact_written=True,
            review_outcome=ReviewOutcome.APPROVED,
        )
```

## Run status model

Orthogonal to `TaskState`. Tracks what the execution engine is doing.

| RunStatus | Meaning |
|-----------|---------|
| `idle` | Task exists but no run in progress |
| `running` | Execution loop is active |
| `waiting_on_cursor` | Paused; cursor step needs manual completion |
| `waiting_on_claude` | Paused; claude step needs manual completion |
| `waiting_on_codex` | Paused; codex step needs manual completion |
| `completed` | Run finished (task in terminal state) |
| `suspended` | Run stopped due to error or missing adapter |

## State machine

```
                    ┌─────────────┐
                    │ initialized │
                    └──────┬──────┘
                           │
                    ┌──────▼──────────────┐
                    │ cursor_implementing  │
                    └──────┬──────────────┘
                           │
                    ┌──────▼──────────────┐
               ┌────│  claude_reviewing   │────┐
               │    └─────────────────────┘    │
               │ changes-requested      approved/minor
               │                               │
        ┌──────▼──────────┐             ┌──────▼──────────┐
        │ cursor_reworking │────────────▶│ codex_reviewing  │
        └─────────────────┘             └───┬────┬────┬───┘
               ▲                    approved │    │    │ changes (cycle≥max)
               │ changes (cycle<max)         │    │    │
               └─────────────────────────────┘    │    │
                                                  │    │
                                           ┌──────▼┐  ┌▼─────────┐
                                           │approved│  │ escalated │
                                           └───┬───┘  └──────────┘
                                               │
                                           ┌───▼────┐
                                           │archived │
                                           └────────┘
```

## Task directory structure

```
workspace/active/<task-name>/
├── state.yaml                      # Source of truth (TaskState + RunStatus)
├── 00-scope.md                     # Task objective and criteria
├── 01-cursor-implementation.md     # Implementation handoff
├── 02-claude-review-cycle-1.md     # Claude's review
├── 03-cursor-response-cycle-1.md   # Cursor rework (if needed)
├── 04-codex-review-cycle-1.md      # Codex final review
├── 05-final-approval.md            # Sign-off (if approved)
├── .prompt-<artifact>.md            # Prompt sent to agent (command adapters)
└── .log-<artifact>.txt             # Agent stdout/stderr (command adapters)
```

## Key design decisions

1. **YAML over JSON for state** — More readable for human inspection,
   which matters because this is a file-based workflow where humans
   may need to debug state.

2. **No Jinja2 dependency** — Template rendering uses simple regex
   substitution to minimize dependencies.

3. **argparse over Click** — Standard library only for the CLI to
   keep the dependency footprint minimal.

4. **Cycle increment on Codex rejection** — The cycle counter increments
   when Codex requests changes, not when Claude does. Claude's rework
   request within a cycle is a sub-loop.

5. **Auto-detection of review outcomes** — The `advance` command parses
   `**Status**: ...` from artifact files. Operators can also pass
   `--outcome` explicitly to override.

6. **Two-tier status model** — `TaskState` (workflow phase) and `RunStatus`
   (execution state) are orthogonal. This avoids polluting the workflow
   state machine with execution concerns and keeps the state machine clean.

7. **Circuit breaker in execution loop** — The run orchestrator has a
   max-iterations guard (20) to prevent infinite loops from misconfigured
   adapters or state machine bugs.

8. **Adapter fallback chain** — Per-agent adapters take precedence over the
   fallback adapter. This supports mixed configurations where some agents
   are automated and others are manual.
