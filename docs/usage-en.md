# morch — User Guide (English)

Complete guide to **morch** (multi-agent orchestrator): installation, setup, authentication, configuration, local and GitHub workflows, prompts, troubleshooting, and examples.

---

## 1. What is morch?

**morch** is a command-line tool that orchestrates **multi-agent code review workflows** across **Cursor**, **Claude Code**, and **Codex**. It coordinates who does what, in what order, using:

- A **file-artifact pipeline** for any repository (markdown artifacts under a task workspace), or  
- A **GitHub-native pipeline** (issues, branches, pull requests, and PR reviews).

The design is **file-based**: agents do not share a live session; state lives in YAML and artifacts on disk (and on GitHub for the GitHub flow). That makes workflows inspectable, resumable, and suitable for mixed environments.

Primary CLI name: **`morch`**. A backward-compatible alias **`orchestrator`** points to the same entry point.

---

## 2. Installation

From source:

```bash
git clone https://github.com/enginrect/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
morch --help
# or
orchestrator --help
```

Requirements: **Python 3.11+**. Core dependencies include **PyYAML**; dev extras add **pytest** and **pytest-cov**.

---

## 3. Setup (`morch setup`)

Before running workflows, ensure the agent CLIs are discoverable.

`morch setup` runs **interactive setup** that:

1. **Auto-detects** the **cursor**, **claude**, and **codex** binaries (PATH, then saved paths).
2. Runs version checks and basic auth awareness where applicable.
3. **Prompts** for a **custom file path** when a tool is missing from PATH (optional).
4. **Persists** discovered or entered paths to **`~/.morch/config.yaml`** under an `agent_paths` map.

```bash
morch setup
```

After setup, use `morch config show` or `morch doctor` to confirm detection.

---

## 4. Authentication (`morch auth`)

### 4.1 Status overview

```bash
morch auth status
```

Shows all tracked tools: **git**, **github** (`gh`), **cursor**, **claude**, **codex**.

### 4.2 Per-tool status and login hints

```bash
morch auth <tool> status
morch auth <tool> login
```

Where `<tool>` is one of: `git`, `github`, `cursor`, `claude`, `codex`.

| Tool | What “ready” means | How to authenticate |
|------|--------------------|---------------------|
| **git** | `git` is installed and **user.name** / **user.email** are set | `git config --global user.name "..."` and `user.email` |
| **github** | **GitHub CLI** (`gh`) is installed and logged in | `gh auth login` |
| **cursor** | **Cursor CLI** is on PATH | Sign in via the **Cursor desktop app**; CLI availability is treated as usable |
| **claude** | **Claude Code** CLI is installed | `claude auth login` (and `claude auth status` must succeed) |
| **codex** | **Codex** CLI plus auth via **`OPENAI_API_KEY`** and/or **`~/.codex/auth.json`** (login session / tokens) | Follow Codex install docs; ensure env or auth file as detected by morch |

`morch auth <tool> login` does not always perform OAuth itself; it often **prints the recommended command** when not authenticated.

---

## 5. Doctor (`morch doctor`, `morch agents doctor`)

### 5.1 System health

```bash
morch doctor
```

- Lists tool status (installed / authenticated / missing).
- Shows **enabled agents** and roles (first = implementer, rest = reviewers).
- Surfaces **agent config validation** errors (e.g. invalid `agents.enabled`).

### 5.2 Agent readiness

```bash
morch agents doctor
```

Checks each **configured** agent in order against the same readiness logic used elsewhere (install + auth hints).

Related:

```bash
morch agents list
morch agents order cursor claude codex   # prints YAML snippet; persist manually in config
```

---

## 6. Configuration (`morch config show`)

### 6.1 Loading config

Pass an explicit YAML file:

```bash
morch --config /path/to/morch.yaml config show
morch -c /path/to/morch.yaml run task my-feature -t /path/to/repo
```

If no `-c` / `--config` is given, morch uses **built-in defaults** (no automatic load from `~/.morch` for orchestrator settings—only **agent paths** live there from `morch setup`).

### 6.2 YAML shape (illustrative)

| Section | Purpose |
|---------|---------|
| `workspace_dir` | Task workspace root (`./workspace` by default): `active/` and `archive/` |
| `template_dir` | Artifact templates (`./templates/artifacts`) |
| `max_cycles` | Max review cycles before escalation (default **2**) |
| `default_target_repo` | Default `-t` for local runs when omitted |
| `agents.enabled` | Ordered list: **2–3** of `cursor`, `claude`, `codex` (first = implementer) |
| `github` | `repo`, `base_branch`, `branch_pattern`, `pr_title_pattern`, `labels`, `local_repo_path` |
| `adapters` | Optional per-role adapter overrides; if omitted, defaults are created for enabled agents |

Example:

```yaml
workspace_dir: ./workspace
template_dir: ./templates/artifacts
max_cycles: 2
default_target_repo: ""

agents:
  enabled: [cursor, claude, codex]

github:
  repo: org/repo
  base_branch: main
  branch_pattern: "{type}/issue-{issue}/{agent}/cycle-{cycle}"
  pr_title_pattern: "[{type}][Issue #{issue}][{agent}] {summary}"
  local_repo_path: ""

# adapters:
#   cursor:
#     type: ...
```

### 6.3 Environment variables

| Variable | Effect |
|----------|--------|
| **`MORCH_LOG_LEVEL`** | Log level for morch loggers (default **`INFO`**). Typical values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

Logging writes to **`~/.morch/logs/morch.log`** (and warnings+ to stderr) when the logging subsystem initializes.

### 6.4 Setup file vs orchestrator config

| Path | Contents |
|------|----------|
| `~/.morch/config.yaml` | **`agent_paths`** — paths to `cursor` / `claude` / `codex` binaries (from `morch setup`) |
| Your chosen `-c` YAML | Workspace, agents, GitHub, adapters, `max_cycles`, etc. |

---

## 7. Local workflows — File-artifact pipeline

Drive a review using **markdown artifacts** under the workspace (see `docs/workflow.md` for the full artifact sequence).

Typical **one-shot** run (creates/advances via the run orchestrator):

```bash
morch run task my-feature --target-repo /path/to/repo
```

Resume after a pause (waiting on an agent):

```bash
morch resume task my-feature
```

Inspect state:

```bash
morch status task my-feature
```

Optional live view:

```bash
morch watch task my-feature
```

When the workflow completes and you are done with the task directory:

```bash
morch task archive my-feature
```

**Manual path** (same pipeline, stepwise): `morch task init`, edit artifacts, `morch task advance`, etc.—see `docs/workflow.md`.

---

## 8. GitHub-native workflows

Requires **`gh`** authenticated (`morch auth github status`) and usually a **`github.repo`** (via `-r` or config).

Claim an issue and run the pipeline:

```bash
morch run github 42 --repo org/repo
```

Resume and status (task names are typically like **`issue-42`**):

```bash
morch resume github issue-42
morch status github issue-42 --repo org/repo
```

Optional:

- **`--type`** — work type: `feat`, `fix`, `refactor`, `docs`, `chore`, `ops`, `test`, `hotfix`, etc.
- **`--local-repo`** — absolute path to the local clone used for context (defaults described in help).

---

## 9. Prompt files

List bundled templates (shipped under the package `templates/prompts`):

```bash
morch prompt list-templates
```

Copy a template to edit:

```bash
morch prompt init my-template --output prompts/task.md
```

Use with a GitHub run:

```bash
morch run github 42 --repo org/repo --prompt-file prompts/task.md
```

Prompt content is injected into adapter context and may be stored alongside the task (e.g. `prompt.md`). You can also combine prompts with **`morch issue create`** / **`morch issue start`** via `--prompt-file`.

Markdown-driven execution without the full task artifact sequence:

```bash
morch run prompt path/to/prompt.md --target-repo /path/to/repo --name optional-task-name
```

---

## 10. Issue flows

All issue commands require **`--repo org/repo`** unless `github.repo` is set in your morch YAML.

| Command | Purpose |
|---------|---------|
| `morch issue create --repo org/repo --title "..." [--body ...] [--labels a,b]` | Create an issue |
| `morch issue list --repo org/repo [--state open\|closed\|all]` | List issues |
| `morch issue view <n> --repo org/repo` | View issue details |
| `morch issue reopen <n> --repo org/repo` | Reopen a closed issue |
| `morch issue start --repo org/repo --title "..." [--body ...] [--type feat] [--prompt-file ...]` | Create issue **and** start the GitHub workflow immediately |

---

## 11. Review flows — Cycles, max cycles, escalation

- **`max_cycles`** (config, default **2**) bounds how many review **cycles** can play out before the task is treated as **escalated** / requiring human intervention (aligned with the artifact spec in `docs/workflow.md`).
- **Local file workflow**: advancing with **changes-requested** loops rework; after the last cycle, unresolved issues lead to **escalation** rather than endless retries.
- **GitHub workflow**: reviews are driven by PR state and adapter steps; **Codex** acts as the approval gate in the resolved step sequence. Repeated stalls may **suspend** the run (see run logs) to avoid infinite loops.

Use **`morch status`** (or **`morch status github`**) to read **cycle / max_cycles**, current **state**, and **next step** hints.

---

## 12. Merge and release behavior

- **Human-gated merge (default UX)**  
  When a GitHub task reaches **approved**, the CLI typically reports success such as **“PR approved! Merge when ready.”** Merging into the default branch is expected to be a **human** action on GitHub (or your standard review/merge policy), not an silent auto-merge in the default path.

- **Agents**  
  Prompts and adapters instruct reviewers (e.g. Codex) **not to merge** the PR by themselves—review only.

- **Self-hosting / automation**  
  The core library includes facilities to record a **merged** state and call GitHub merge APIs (e.g. for integrations). The stock CLI may not expose every merge variant; for **self-hosted** or custom automation, treat the **GitHub API / `gh`** and your org’s **branch protection** as the source of truth for merge permissions.

- **Local file workflow**  
  “Release” is outside morch: after **approval**, **`morch task archive`** tidies workspace storage; git push/merge is yours.

---

## 13. Troubleshooting

| Symptom | What to try |
|---------|-------------|
| **Agent CLI not found** | Run **`morch setup`**; confirm **`~/.morch/config.yaml`** `agent_paths`; put binaries on `PATH`. |
| **Auth failures** | **`morch auth status`** and per-tool **`morch auth <tool> login`**; for **`gh`**, **`gh auth login`**; for **claude**, **`claude auth login`**; for **codex**, env key and **`~/.codex/auth.json`**. |
| **Git identity missing** | Configure **`git config` user.name / user.email** (required for meaningful git operations). |
| **Resource or internal errors** | Inspect **`~/.morch/logs/morch.log`**; set **`MORCH_LOG_LEVEL=DEBUG`** for more detail. |
| **GitHub API rate limits / flakes** | Retry after a short delay; reduce parallel automation; ensure **`gh`** auth is healthy. |
| **Run suspended / same step repeats** | Read the printed message; check **`morch status`** / task **`run.log`** under the task directory; resume with the suggested **`morch resume`** command. |
| **Wrong task type for resume** | File-artifact tasks use **`morch resume task`**; GitHub tasks use **`morch resume github issue-<n>`**. |

---

## 14. Examples

### 14.1 New contributor: first local review

```bash
source .venv/bin/activate
morch setup
morch doctor
morch run task add-logging --target-repo ~/src/my-service
# follow on-screen "Waiting on" / resume hints
morch resume task add-logging
morch status task add-logging
morch task archive add-logging
```

### 14.2 GitHub issue already filed

```bash
export MORCH_LOG_LEVEL=INFO
morch auth status
morch run github 123 --repo myorg/my-repo --type fix
morch resume github issue-123 --repo myorg/my-repo
```

### 14.3 Issue + run in one step

```bash
morch issue start --repo myorg/my-repo --title "Add health endpoint" --type feat
```

### 14.4 Custom agent order (review persistence)

Preview order:

```bash
morch agents order claude cursor codex
```

Copy the printed **`agents.enabled`** block into your **`morch`** YAML and pass **`-c`** when running commands.

### 14.5 Configuration inspection

```bash
morch -c ./morch.yaml config show
```

---

## Quick reference

| Topic | Command |
|-------|---------|
| Setup agent paths | `morch setup` |
| Health | `morch doctor` / `morch agents doctor` |
| Auth | `morch auth status` |
| Effective config | `morch config show` |
| Local run | `morch run task <name> -t <repo>` |
| Local resume | `morch resume task <name>` |
| GitHub run | `morch run github <issue#> -r org/repo` |
| GitHub resume | `morch resume github issue-<issue#>` |
| Prompt template | `morch prompt init <template> -o <file>` |
| Archive local task | `morch task archive <name>` |

---

## Further reading

- `README.md` — project overview  
- `docs/architecture.md` — design and layers  
- `docs/workflow.md` — artifact sequence and review outcomes  
- `AGENTS.md` / `CLAUDE.md` — contributor orientation  

---

*This guide reflects morch’s CLI-oriented workflows. Behavior may evolve between releases; use `morch --help` and subcommand help for the exact flags on your version.*
