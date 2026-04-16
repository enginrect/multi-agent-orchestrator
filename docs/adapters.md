# Adapters — Real Command Execution

## Overview

The orchestrator uses adapters to invoke agents. Each adapter implements
the `AgentAdapter` interface and declares a capability level. The run
orchestrator drives the execution loop and uses adapters to complete
each workflow step.

## Agent truth table

All three agents have real CLI-based non-interactive modes:

| Agent | Binary | Non-interactive command | Status |
|-------|--------|----------------------|--------|
| **Codex** | `/opt/homebrew/bin/codex` | `codex exec --full-auto "<prompt>"` | **Real auto** |
| **Claude** | `/Users/enginrect/.local/bin/claude` | `claude -p --permission-mode auto "<prompt>"` | **Real auto** |
| **Cursor** | `/usr/local/bin/cursor` | `cursor agent -p --trust --yolo "<prompt>"` | **Real auto** |

All three are first-class AUTOMATIC adapters.

## Adapter types

| Type | Capability | What it does |
|------|-----------|--------------|
| `manual` | MANUAL | Generates instructions, returns WAITING. Human completes. |
| `stub` | AUTOMATIC | Writes canned content. For testing only. |
| `command` | AUTOMATIC | Generic external command execution. |
| `codex-cli` | AUTOMATIC | Invokes Codex CLI via `codex exec --full-auto`. |
| `claude-cli` | AUTOMATIC | Invokes Claude CLI via `claude -p --permission-mode auto`. |
| `cursor-cli` | AUTOMATIC | Invokes Cursor Agent via `cursor agent -p --trust --yolo`. |

The `cursor-cli` adapter supports a `manual_fallback: true` setting that
downgrades it to MANUAL for environments without the Cursor CLI.

## How command adapters work

Every command adapter follows the same execution flow:

1. **Build prompt** — Collects task metadata, instructions, and content from
   all existing artifacts. Formats a prompt optimized for the target agent.

2. **Write prompt file** — Saves the prompt to `.prompt-<artifact>.md` in
   the task directory for auditability.

3. **Execute command** — Runs the configured command as a subprocess with:
   - Configurable timeout
   - Environment variable injection
   - Configurable working directory (`{task_dir}` or `{target_repo}`)

4. **Capture output** — Writes stdout/stderr to `.log-<artifact>.txt`.

5. **Verify artifact** — Checks that the expected artifact file was written
   by the command. If missing, returns FAILED.

6. **Detect outcome** — For review artifacts, parses `**Status**: ...` from
   the written file to determine the review outcome.

7. **Log to run.log** — Writes structured JSONL entries for debugging.

## Configuration

Adapters are configured in the YAML config file under the `adapters` key:

```yaml
adapters:
  cursor:
    type: cursor-cli
    settings:
      command: /usr/local/bin/cursor
      timeout: 600

  claude:
    type: claude-cli
    settings:
      command: /Users/enginrect/.local/bin/claude
      timeout: 300

  codex:
    type: codex-cli
    settings:
      command: /opt/homebrew/bin/codex
      timeout: 600
```

### Settings reference

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `command` | string | (varies per adapter) | Executable name or absolute path |
| `args` | list[string] | (varies per adapter) | Command arguments; `{prompt}` and `{prompt_file}` are replaced |
| `timeout` | int | 300-600 | Seconds before the process is killed |
| `env` | dict | {} | Extra environment variables; `$VAR` resolved from OS |
| `working_dir` | string | `{task_dir}` | `{task_dir}` or `{target_repo}` |

### Absolute paths vs PATH lookup

When running commands via `subprocess.run()`, shell functions and aliases
are **not available** — only real executables on `PATH` or absolute paths
work. This is especially important for Claude, which may have a shell
wrapper function that differs from the real binary.

**Always use absolute paths in production configs** to eliminate ambiguity:

```yaml
command: /Users/enginrect/.local/bin/claude    # correct — real binary
# command: claude                               # risky — may resolve to shell function
```

## Wiring Codex

**Prerequisites:**
- Codex CLI installed: `/opt/homebrew/bin/codex`
- `OPENAI_API_KEY` set in environment

**Config:**
```yaml
adapters:
  codex:
    type: codex-cli
    settings:
      command: /opt/homebrew/bin/codex
      timeout: 600
```

**What happens:**
The adapter runs `codex exec --full-auto "<prompt>"` in the task directory.
The `exec` subcommand is Codex's dedicated non-interactive mode. The
`--full-auto` flag enables sandboxed automatic execution without approval
prompts. The prompt tells Codex to read context artifacts, review the
implementation, and write its review to the expected artifact file.

**Default command:** `codex exec --full-auto {prompt}`

## Wiring Claude

**Prerequisites:**
- Claude CLI installed: `/Users/enginrect/.local/bin/claude`
- Authentication configured (run `claude auth` once)

**Config:**
```yaml
adapters:
  claude:
    type: claude-cli
    settings:
      command: /Users/enginrect/.local/bin/claude
      timeout: 300
```

**What happens:**
The adapter runs `claude -p --permission-mode auto --allowedTools Edit,Write,Read,Bash "<prompt>"`
in the task directory. The `-p` flag is print/non-interactive mode. The
`--permission-mode auto` flag auto-approves all tool use. The prompt tells
Claude to review the implementation, read the target repo, apply fixes if
needed, and write its review artifact with a `**Status**:` field.

**Important:** Use the absolute path to the real binary, not the bare command
name `claude`. A shell function wrapper may exist that is not visible to
`subprocess.run()`.

**Default command:** `claude -p --permission-mode auto --allowedTools Edit,Write,Read,Bash {prompt}`

## Wiring Cursor

**Prerequisites:**
- Cursor CLI installed: `/usr/local/bin/cursor`
- Cursor 2.6+ with the `agent` subcommand

**Config (automatic):**
```yaml
adapters:
  cursor:
    type: cursor-cli
    settings:
      command: /usr/local/bin/cursor
      timeout: 600
```

**What happens:**
The adapter runs `cursor agent -p --trust --yolo --workspace <target_repo> "<prompt>"`
in the task directory. The `-p` flag enables headless/print mode with
full tool access (write, shell). The `--trust` flag skips workspace trust
dialogs. The `--yolo` flag auto-approves all commands.

**Default command:** `cursor agent -p --trust --yolo --workspace {target_repo} {prompt}`

**Manual fallback:**
If Cursor CLI is not available, use `manual_fallback: true`:

```yaml
adapters:
  cursor:
    type: cursor-cli
    settings:
      manual_fallback: true
```

This writes a prompt file and returns WAITING, identical to ManualAdapter
behavior. The human implements changes and runs `morch resume task <task>`.

## Smoke tests

Run these commands to verify each agent CLI is working before using them
with the orchestrator. Each command should complete without error and
produce text output.

### Codex

```bash
# Verify binary exists
ls -la /opt/homebrew/bin/codex

# Verify OPENAI_API_KEY is set
echo "OPENAI_API_KEY is ${OPENAI_API_KEY:+set}"

# Smoke test: non-interactive exec
/opt/homebrew/bin/codex exec --full-auto "Print the words: codex smoke test passed"
```

### Claude

```bash
# Verify binary exists (use real path, not shell function)
ls -la /Users/enginrect/.local/bin/claude

# Verify auth status
/Users/enginrect/.local/bin/claude auth

# Smoke test: non-interactive print mode
/Users/enginrect/.local/bin/claude -p --permission-mode auto "Print the words: claude smoke test passed"
```

### Cursor

```bash
# Verify binary exists
ls -la /usr/local/bin/cursor

# Verify agent subcommand exists
/usr/local/bin/cursor agent --help

# Smoke test: non-interactive headless mode
/usr/local/bin/cursor agent -p --trust --yolo "Print the words: cursor smoke test passed"
```

### Full pipeline smoke test

```bash
# Create a test task with stub adapters (no real agents)
morch --config configs/adapters-stub.yaml run task smoke-test \
  --target-repo /tmp/test-repo

# Verify task completed
morch status task smoke-test

# Clean up
morch task archive smoke-test
```

## Example configs

| Config file | Cursor | Claude | Codex | Use case |
|-------------|--------|--------|-------|----------|
| `adapters-stub.yaml` | stub | stub | stub | Testing pipeline |
| `adapters-mixed.yaml` | manual | claude-cli | codex-cli | Reviews auto, impl manual |
| `adapters-real.yaml` | cursor-cli | claude-cli | codex-cli | Full automation |

## Troubleshooting

### Per-step logs

Every command adapter writes:
- `.prompt-<artifact>.md` — the exact prompt sent to the agent
- `.log-<artifact>.txt` — stdout/stderr and exit code

### Run log

`run.log` in the task directory is a JSONL file with structured entries:

```json
{"timestamp": "...", "event": "run_start", "task_name": "my-task"}
{"timestamp": "...", "event": "step_start", "agent": "codex", "adapter": "codex-cli", "artifact": "04-codex-review-cycle-1.md"}
{"timestamp": "...", "event": "adapter_invoke", "command": "codex", "timeout": 600}
{"timestamp": "...", "event": "adapter_completed", "artifact": "04-codex-review-cycle-1.md", "exit_code": 0}
{"timestamp": "...", "event": "step_completed", "agent": "codex", "review_outcome": "approved"}
{"timestamp": "...", "event": "run_complete", "final_state": "approved"}
```

### Common failures

| Error | Cause | Fix |
|-------|-------|-----|
| "Command not found: codex" | Binary not at expected path | Use absolute path in config |
| "Command not found: claude" | Shell function, not real binary | Use `/Users/enginrect/.local/bin/claude` |
| "Command timed out after 300s" | Agent took too long | Increase `timeout` in settings |
| "Artifact not written" | Agent didn't produce the file | Check `.log-` file for errors |
| "Exit code 1" | Agent process failed | Check `.log-` file for stderr |

### Shell function vs real binary

If `which claude` shows a shell function rather than a path, `subprocess.run()`
will fail with "command not found". Always check:

```bash
# Shows shell function or alias
type claude

# Shows real binary path
command -v claude
ls -la /Users/enginrect/.local/bin/claude
```

Use the real binary path in adapter config settings.
