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

`morch` supports three distinct workflows. Choose the one that matches
your collaboration model.

### Prompt-driven execution (local)

The simplest way to use `morch` — provide a markdown file as the task prompt.
Everything runs locally; no GitHub interaction occurs.

```bash
morch run prompt task-description.md --target-repo /path/to/repo
```

- The first configured agent receives the markdown content as its instruction.
- Subsequent agents review the output in order.
- Artifacts are written to the local task directory (`workspace/active/<task>/`).
- Automatic adapters proceed through all agents without pausing.
- Suspends only if an adapter is explicitly manual or a step fails.
- Creates a canonical task: visible via `morch status task`, `morch watch task`,
  `morch resume task`, and `morch task list`.

**When to use**: Quick local reviews, prototyping, testing agent behavior,
or when you don't need GitHub issue tracking.

### File-artifact workflow (local)

The structured review pipeline using local file artifacts and the
Cursor → Claude → Codex review cycle:

```bash
morch --config configs/adapters-real.yaml run task my-feature --target-repo /path/to/repo
```

Agents produce handoff documents in the task directory. State transitions
are tracked in local files. See [workflow.md](workflow.md) for details.

**When to use**: Full structured review lifecycle with state machine
enforcement, cycle limits, and escalation.

### GitHub-native workflow (remote)

Issue-driven pipeline using GitHub branches, PRs, and reviews.
This is the correct path for team-visible, production-grade review workflows.

```bash
morch run github 42 --repo owner/name --type feat
# When the checkout you want agents to use is not the current directory:
morch run github 42 --repo owner/name --local-repo /path/to/clone
```

- Claims a GitHub issue, generates a branch, opens a PR.
- Agents collaborate through the PR: reviewing, pushing fixes, approving.
- State is tracked in both local task state and GitHub labels/PR status.
- Requires `git` and `gh` CLI with repo access.
- `--local-repo` (or `github.local_repo_path` in config) sets the filesystem
  path agents use as their workspace. Prompts still pass the GitHub `owner/name`
  slug to `gh` so PR commands stay correct.

**When to use**: Real issue tracking, team-visible reviews, CI-connected
workflows, and production repositories.

See [github-workflow.md](github-workflow.md) for the full lifecycle, branch
naming, and PR conventions.

### Choosing a workflow

| Need | Workflow | Command |
|------|----------|---------|
| Quick local test | Prompt-driven | `morch run prompt task.md` |
| Structured local review | File-artifact | `morch run task my-feature` |
| GitHub issue tracking | GitHub-native | `morch run github 42 --repo o/r` |
| Create + start in one step | Issue start | `morch issue start --title "..." --repo o/r` |

## GitHub Issue Lifecycle

`morch` can manage the full issue lifecycle: create, list, view, reopen,
and start workflows — all from the CLI.

### Create an issue

```bash
morch issue create --repo owner/name --title "Add logging" --body "Details..."
morch issue create --repo owner/name --title "Fix bug" --prompt-file .morch/prompts/fix-bug.md
```

### List and view issues

```bash
morch issue list --repo owner/name
morch issue list --repo owner/name --state closed
morch issue view 42 --repo owner/name
```

### Reopen a closed issue

```bash
morch issue reopen 42 --repo owner/name
```

### Create + start workflow in one step

```bash
morch issue start --repo owner/name --title "Smoke test" --type test \
  --prompt-file .morch/prompts/smoke-test.md
```

This creates the issue and immediately starts the full GitHub-native pipeline.

### Continue from an existing issue

```bash
morch run github 42 --repo owner/name --prompt-file .morch/prompts/issue-42.md
```

### Merge policy

`morch` will **never** merge to the base branch automatically. Final merge
is always a human approval action. The pipeline ends at the `APPROVED` state;
the human decides whether and when to merge.

## Prompt Files

### Why prompt files?

Issue titles and bodies are often too brief for detailed agent instructions.
A prompt file provides the full source-of-truth for what the agents should do.

### Directory model

| Path | Tracked | Purpose |
|------|---------|---------|
| `.morch/prompts/` | No (gitignored) | User-local prompt files for active work |
| `templates/prompts/` | Yes (committed) | Reusable prompt templates distributed with the repo |

### Using prompt files

Attach a prompt file to any GitHub workflow run:

```bash
morch run github 42 --repo o/r --prompt-file .morch/prompts/issue-42.md
morch issue start --repo o/r --title "..." --prompt-file .morch/prompts/task.md
```

The prompt file content is:
1. Saved to the task directory as `prompt.md` for auditability
2. Injected into every agent's instructions as the "Detailed task prompt" section
3. Used as the source-of-truth for implementation and review guidance

### Managing templates

```bash
morch prompt list-templates                                     # see available templates
morch prompt init smoke-test --output .morch/prompts/my-test.md # copy and customize
morch prompt init github-issue-task --output .morch/prompts/feature.md
```

Available templates:
- `smoke-test` — minimal pipeline validation task
- `github-issue-task` — structured template for real feature/fix work

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
| `morch run github <issue> [--prompt-file ...]` | GitHub-native issue pipeline |
| `morch resume task <task>` | Resume a paused file-artifact task |
| `morch resume github <task>` | Resume a paused GitHub task |

### GitHub issue lifecycle

| Command | Description |
|---------|-------------|
| `morch issue create --title "..." --repo o/r` | Create a GitHub issue |
| `morch issue list --repo o/r [--state ...]` | List issues |
| `morch issue view <n> --repo o/r` | View issue details |
| `morch issue reopen <n> --repo o/r` | Reopen a closed issue |
| `morch issue start --title "..." --repo o/r [--prompt-file ...]` | Create + start workflow |

### Prompt template management

| Command | Description |
|---------|-------------|
| `morch prompt list-templates` | List available prompt templates |
| `morch prompt init <name> --output <path>` | Copy template to local file |

### Status and observability

| Command | Description |
|---------|-------------|
| `morch status task <task>` | Show file-artifact task status |
| `morch status github <task>` | Show GitHub task status |
| `morch watch task <task>` | Live-watch task state and run log |

### Manual task management

| Command | Description |
|---------|-------------|
| `morch task init <name>` | Create a new review task |
| `morch task advance <name>` | Advance task to next state |
| `morch task next <name>` | Show next step |
| `morch task validate <name>` | Validate task artifacts |
| `morch task archive <name>` | Archive an approved task |
| `morch task list [--all]` | List tasks |

## Adapter resolution

`morch` automatically creates CLI adapters for enabled agents when no
explicit `adapters:` section is present in the config. This means
`morch run prompt task.md` works out of the box with automatic execution
as long as the agent CLIs are installed:

| Agent | Default adapter | CLI command |
|-------|----------------|-------------|
| cursor | cursor-cli | `cursor agent -p --trust --yolo` |
| claude | claude-cli | `claude -p --permission-mode auto` |
| codex | codex-cli | `codex exec --full-auto` |

When auto-detected adapters are used, the pipeline proceeds automatically.
Suspension only occurs when:
- An adapter is explicitly set to `manual` type
- An agent's CLI is not installed (command-not-found error)
- A step fails

To override defaults, add an explicit `adapters:` section to your config.

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

# Optional: override default adapter settings
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

## GitHub Smoke Test Checklist

Use this checklist when running the first GitHub-native test on a real repository.

### Prerequisites

```bash
# 1. Verify auth
morch auth status          # all tools should show ✅
morch auth github status   # must have repo access
morch auth cursor status
morch auth claude status

# 2. Verify gh CLI can access the target repo
gh repo view owner/repo-name

# 3. Verify local clone exists and is up to date
cd /path/to/local/clone
git pull origin main
git status                 # clean working tree
```

### Configuration

Either use `--repo` on the command line or set it in config:

```yaml
# configs/github-default.yaml
github:
  repo: "owner/repo-name"
  base_branch: "main"
```

### Run

```bash
# Option A: create issue + start in one step
morch prompt init smoke-test --output .morch/prompts/smoke.md
morch issue start --repo owner/repo-name --type test \
  --title "Orchestrator smoke test" --prompt-file .morch/prompts/smoke.md

# Option B: attach to an existing issue
morch run github <issue-number> --repo owner/repo-name --type feat \
  --prompt-file .morch/prompts/task.md

# Monitor:
morch status github issue-<number>
morch watch task issue-<number>
```

### Verify

- [ ] Issue was claimed (label applied)
- [ ] Branch was created with the correct naming pattern
- [ ] PR was opened targeting the configured base branch
- [ ] First agent (Cursor) pushed implementation commits
- [ ] Second agent (Claude) posted a PR review
- [ ] Third agent (Codex) posted final review (if 3-agent pipeline)
- [ ] Task state reflects the current pipeline position
- [ ] `morch status github issue-<number>` shows correct state

### Troubleshooting

| Symptom | Check |
|---------|-------|
| "repo not found" | `gh repo view owner/name` — check access |
| "issue not found" | Issue must be open and not already claimed |
| Branch creation fails | Ensure base branch exists and you have push access |
| Agent timeout | Increase timeout in config or `--config` override |
| "Command not found: cursor" | `morch auth cursor status` — install/path issue |

## Backward Compatibility

The `orchestrator` command remains available as an alias for `morch`.
Old command forms like `orchestrator run`, `orchestrator github-run`, etc.
continue to work through hidden backward-compatible subcommands.
