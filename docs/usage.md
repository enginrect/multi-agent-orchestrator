# Usage Guide

The primary CLI name is **`morch`** (`orchestrator` is the same entry point from `pyproject.toml`). File-artifact flows use nested commands: `morch run task`, `morch resume task`, `morch status task`. For the full command matrix and setup, see [morch.md](morch.md).

## Installation

```bash
cd multi-agent-orchestrator
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Default configuration is in `configs/default.yaml`:

```yaml
workspace_dir: ./workspace
template_dir: ./templates/artifacts
max_cycles: 2
default_target_repo: ""
```

### Adapter configuration

Adapters control how each agent is invoked. Add an `adapters` section:

```yaml
adapters:
  cursor:
    type: cursor-cli       # manual fallback (no CLI)
  claude:
    type: claude-cli       # real Claude CLI
    settings:
      timeout: 300
  codex:
    type: codex-cli        # real Codex CLI
    settings:
      timeout: 600
```

See [docs/adapters.md](adapters.md) for full adapter reference.

**Example configs:**
- `configs/adapters-stub.yaml` — all stub (testing)
- `configs/adapters-mixed.yaml` — recommended (Claude + Codex real, Cursor manual)
- `configs/adapters-real.yaml` — full automation

Override with `--config` or `--workspace`:

```bash
morch --config configs/adapters-mixed.yaml run task my-task --target-repo /path
morch --workspace /path/to/reviews run task my-task
```

## CLI Commands

### `morch run task` (recommended)

Create a task and drive the full review pipeline in one command.

```bash
morch run task <task-name> --target-repo <path> [--description TEXT]
```

Backward-compatible alias: `orchestrator run-task <task-name> ...`.

Behavior depends on configured adapters:
- **All automatic** — runs to completion without stopping
- **Mixed** — runs auto steps, pauses at manual steps
- **All manual** — pauses immediately, generates instructions for first step

Example output (auto adapters):
```
[run] Task created: add-metrics (state: cursor_implementing)
[run] [cursor] Invoking stub adapter for 01-cursor-implementation.md...
[run] [cursor] Completed: 01-cursor-implementation.md
[run] [claude] Invoking stub adapter for 02-claude-review-cycle-1.md...
[run] [claude] Completed: 02-claude-review-cycle-1.md (approved)
[run] [codex] Invoking stub adapter for 04-codex-review-cycle-1.md...
[run] [codex] Completed: 04-codex-review-cycle-1.md (approved)

Task:       add-metrics
State:      approved
Run status: completed
```

Example output (manual adapter):
```
[run] Task created: add-metrics (state: cursor_implementing)
[run] [cursor] Invoking manual adapter for 01-cursor-implementation.md...
[run] [cursor] Waiting for manual completion. Run: morch resume task add-metrics

Task:       add-metrics
State:      cursor_implementing
Run status: waiting_on_cursor
Waiting on: cursor
Run: morch resume task add-metrics
```

### `morch resume task`

Continue a task that was paused waiting for manual completion.

```bash
morch resume task <task-name>
```

GitHub-backed tasks use `morch resume github <task-name>`. Backward-compatible alias: `orchestrator github-resume`.

Call this after writing the expected artifact file externally. The
orchestrator detects the new artifact, advances the state, and continues
execution until the next pause or completion.

### `morch task init`

Create a new review task (manual step-by-step mode).

```bash
morch task init <task-name> [--target-repo PATH] [--description TEXT]
```

Backward-compatible alias: `orchestrator init`.

Creates `workspace/active/<task-name>/` with:
- `state.yaml` — task state
- `00-scope.md` — scope template (edit this first)

### `morch status task`

Show current task state and next step.

```bash
morch status task <task-name>
```

### `morch task next`

Show detailed next-step instructions. In manual mode, this prints
what the operator needs to do.

```bash
morch task next <task-name>
```

Backward-compatible alias: `orchestrator next`.

### `morch task advance`

Advance the task to the next state after completing a step (manual mode).

```bash
morch task advance <task-name> [--outcome approved|changes-requested|minor-fixes-applied]
```

Backward-compatible alias: `orchestrator advance`.

The `--outcome` flag is optional. The orchestrator auto-detects the
review outcome by parsing `**Status**: ...` from the artifact file.

### `morch task validate`

Check artifact completeness for the current cycle.

```bash
morch task validate <task-name>
```

Backward-compatible alias: `orchestrator validate`.

### `morch task archive`

Move an approved task from `active/` to `archive/`.

```bash
morch task archive <task-name>
```

Backward-compatible alias: `orchestrator archive`.

### `morch task list`

List all tasks.

```bash
morch task list           # Active tasks only
morch task list --all     # Include archived
```

Backward-compatible alias: `orchestrator list`.

## Workflow: single-command orchestration (mixed real/manual)

```bash
# Run with real adapters (recommended config)
morch --config configs/adapters-mixed.yaml run task add-metrics-server \
  --target-repo ~/repos/workload-cluster-add-on \
  --description "Add metrics-server as a platform service"

# Cursor step pauses (manual). Implement the changes, write the artifact,
# then resume:
morch --config configs/adapters-mixed.yaml resume task add-metrics-server

# Claude and Codex run automatically. If approved:
morch task archive add-metrics-server
```

## Workflow: full automatic (stub or real)

```bash
# All agents auto-complete (stub for testing, or real with all CLIs)
morch --config configs/adapters-stub.yaml run task add-metrics-server \
  --target-repo ~/repos/workload-cluster-add-on

# Completes without pausing → directly approved
morch task archive add-metrics-server
```

## Workflow: manual step-by-step

```bash
# 1. Create task
morch task init add-metrics-server \
  --target-repo ~/repos/workload-cluster-add-on \
  --description "Add metrics-server as a platform service"

# 2. Edit scope
$EDITOR workspace/active/add-metrics-server/00-scope.md

# 3. Implement changes in target repo, then write handoff
$EDITOR workspace/active/add-metrics-server/01-cursor-implementation.md
morch task advance add-metrics-server

# 4. Hand off to Claude — show instructions
morch task next add-metrics-server

# 5. Claude writes review (on their machine/account)
$EDITOR workspace/active/add-metrics-server/02-claude-review-cycle-1.md
morch task advance add-metrics-server

# 6. If changes requested, Cursor responds
morch status task add-metrics-server  # Check if rework needed
$EDITOR workspace/active/add-metrics-server/03-cursor-response-cycle-1.md
morch task advance add-metrics-server

# 7. Codex final review
$EDITOR workspace/active/add-metrics-server/04-codex-review-cycle-1.md
morch task advance add-metrics-server

# 8. If approved, archive
morch task archive add-metrics-server
```

## Targeting a different repository

The orchestrator manages review artifacts in its own `workspace/`
directory. The `--target-repo` flag records which repository the
task is about, so agents know where to find the actual code.

For cross-machine workflows, the workspace directory can be a
shared location (e.g., a Git repo that all agents have access to).

## Debugging state

Task state is stored in plain YAML:

```bash
cat workspace/active/<task-name>/state.yaml
```

Key fields:
- `state` — Current workflow phase (e.g., `claude_reviewing`)
- `run_status` — Execution engine state (e.g., `waiting_on_claude`)
- `cycle` — Current review cycle (1 or 2)
- `history` — Full transition log

### Run log

Every `run` / `resume` writes structured JSONL to `run.log`:

```bash
cat workspace/active/<task-name>/run.log
```

Entries include `run_start`, `step_start`, `adapter_invoke`,
`adapter_completed`, `step_waiting`, `step_failed`, `run_complete`.

### Per-step logs

Command adapters write per-step files:
- `.prompt-<artifact>.md` — exact prompt sent to the agent
- `.log-<artifact>.txt` — stdout, stderr, exit code

These are invaluable for debugging why an agent didn't produce the
expected artifact.

History of all transitions is preserved, making it possible to
reconstruct the full lifecycle of any task.
