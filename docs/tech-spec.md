# Technical Specification — morch Multi-Agent Orchestrator

**Project:** morch (multi-agent orchestrator)  
**Package:** `multi-agent-orchestrator`  
**Document version:** 1.0  
**Product version:** 0.2.0  
**Language:** English (see [§9.5](#95-bilingual-documentation) for bilingual documentation notes)

This specification describes the architecture, workflows, state models, adapter layer, GitHub-native behavior, provenance, operational features introduced in v0.2.0, and known limitations of the orchestrator. It is intended for implementers, operators, and reviewers of the tool itself (not of target repositories under review).

---

## Table of contents

1. [Architecture](#1-architecture)
2. [Workflow model](#2-workflow-model)
3. [Adapters](#3-adapters)
4. [Task state model](#4-task-state-model)
5. [GitHub-native workflow](#5-github-native-workflow)
6. [Prompt-driven workflow](#6-prompt-driven-workflow)
7. [Provenance model](#7-provenance-model)
8. [Merge policy](#8-merge-policy)
9. [New in v0.2.0](#9-new-in-v020)
10. [Known limitations](#10-known-limitations)
11. [References](#11-references)

---

## 1. Architecture

morch follows **domain-driven design** with four layers under `src/orchestrator/`. Dependencies point inward: adapters and infrastructure depend on application services; application depends on domain; domain has no I/O.

### 1.1 Layer overview

| Layer | Path | Responsibility |
|-------|------|----------------|
| **Domain** | `domain/` | Pure logic: entities, value objects, state machines, workflow definitions, errors, provenance text builders |
| **Application** | `application/` | Use cases: task lifecycle, artifacts, runs, GitHub tasks, prompt execution, workflow engine |
| **Infrastructure** | `infrastructure/` | I/O: filesystem state, config, auth checks, HTTP/GitHub, templates, logging, setup |
| **Adapters** | `adapters/` | Agent-facing interfaces: manual, stub, command-line agents |

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI (cli.py)                        │
├─────────────────────────────────────────────────────────────┤
│  Application: TaskService, GitHubTaskService, ArtifactService │
│  RunOrchestrator, GitHubRunOrchestrator, PromptRunner,      │
│  WorkflowEngine                                             │
├─────────────────────────────────────────────────────────────┤
│  Domain: models, state_machine, workflow, github_* , errors   │
├──────────────────────────┬──────────────────────────────────┤
│  Infrastructure          │  Adapters                       │
│  FileStateStore, Config  │  Manual, Stub, Command, *CLI     │
│  AuthChecker, RunLogger  │                                  │
│  GitHubService, Template   │                                  │
│  SetupService, Logger      │                                  │
└──────────────────────────┴──────────────────────────────────┘
```

### 1.2 Domain (`domain/`)

| Component | Description |
|-----------|-------------|
| **`models.py`** | Core types: `Task`, `TaskState`, `RunStatus`, `AgentRole`, `ReviewOutcome`, `ArtifactSpec`, `StateTransition`, `AdapterCapability`, `ExecutionResult`, `ExecutionStatus` |
| **`state_machine.py`** | File-artifact workflow: `TRANSITIONS` map, `validate_transition()`, terminal detection |
| **`workflow.py`** | Artifact catalogs (`CYCLE_1_ARTIFACTS`, `CYCLE_2_ARTIFACTS`), `resolve_next_step()` for local tasks |
| **`github_models.py`** | `GitHubTask`, `GitHubTaskState`, `WorkType`, `GITHUB_TRANSITIONS`, validation helpers |
| **`github_workflow.py`** | `resolve_github_next_step()`, `generate_branch_name()`, `generate_pr_title()` |
| **`errors.py`** | `OrchestratorError` hierarchy: transitions, tasks, artifacts, cycles, workflow config, and **`AgentResourceLimitError`** subclasses (see [§9.4](#94-resource-limit-handling)) |
| **`provenance.py`** | Markdown templates for GitHub-visible attribution (comments, PR body blocks, review headers, relayed reviews) |

Domain code performs **no filesystem, network, or subprocess I/O**.

### 1.3 Application (`application/`)

| Service | Role |
|---------|------|
| **`TaskService`** | File-artifact task lifecycle: init, advance, archive, list, load; coordinates `FileStateStore`, templates, and `ArtifactService` |
| **`GitHubTaskService`** | GitHub-backed tasks: issue claim, branch/PR metadata, state persistence, **optional merge** after approval (`merge()`), integration with `GitHubService` |
| **`ArtifactService`** | Validates expected artifacts against workflow specs; parses review status lines from markdown |
| **`RunOrchestrator`** | Single-command driver for file-artifact pipeline: resolve next step → invoke adapter → advance or wait/suspend |
| **`GitHubRunOrchestrator`** | Same pattern for GitHub-native tasks: sync with GitHub state, invoke adapters, post provenance, handle relay when agents cannot post |
| **`PromptRunner`** | Prompt-driven pipeline: markdown file → sequential agents with synthetic `Task` state |
| **`WorkflowEngine`** | Legacy/single-step instruction generation (`next` command paths) |

### 1.4 Infrastructure (`infrastructure/`)

| Component | Role |
|-----------|------|
| **`FileStateStore`** | Canonical `state.yaml` per task under workspace `active/` and `archive/` |
| **`ConfigLoader`** | Loads `OrchestratorConfig` (workspace, templates, agents, adapters, GitHub patterns) |
| **`AuthChecker`** | Health and auth probes for external tools (`gh`, git, agent CLIs) used by `morch doctor` / `morch auth` |
| **`RunLogger`** | JSONL append-only **`run.log`** per task for audit and debugging |
| **`GitHubService`** | `gh`-backed operations: issues, PRs, reviews, merge primitives |
| **`TemplateRenderer`** | Loads markdown templates for artifacts and instructions |
| **`SetupService`** | **v0.2.0:** `morch setup` flow, PATH detection, `~/.morch/config.yaml` persistence (`SetupConfig`, `detect_agent`, `detect_all_agents`, `run_setup`) |
| **`Logger`** | **v0.2.0:** Python logging bootstrap: `get_logger()`, `MORCH_LOG_LEVEL`, file under `~/.morch/logs/morch.log` |

### 1.5 Adapters (`adapters/`)

| Adapter | Module | Typical capability |
|---------|--------|-------------------|
| **`ManualAdapter`** | `manual.py` | `MANUAL` — writes instructions/templates, returns **WAITING** |
| **`StubAdapter`** | `stub.py` | `AUTOMATIC` — completes steps for tests |
| **`CommandAdapter`** | `command.py` | `AUTOMATIC` — generic subprocess command |
| **`CursorCommandAdapter`** | `cursor.py` | Cursor CLI or manual fallback |
| **`ClaudeCommandAdapter`** | `claude_adapter.py` | Claude CLI with orchestrator prompts |
| **`CodexCommandAdapter`** | `codex.py` | Codex CLI with orchestrator prompts |

All concrete adapters implement **`AgentAdapter`** (`base.py`): `execute()` → `ExecutionResult`, `capability`, optional `health_check()`.

---

## 2. Workflow model

morch supports **three** workflow types. They share the same agent ordering concept (`AgentsConfig`) but differ in artifacts, storage, and collaboration surface.

### 2.1 Comparison

| Aspect | File-artifact | GitHub-native | Prompt-driven |
|--------|----------------|---------------|---------------|
| **Primary surface** | Markdown files in task dir | Issue, branch, PR, reviews | Local markdown prompt + task dir |
| **State file** | `state.yaml` (`Task`) | `state.yaml` (`GitHubTask`, `workflow_mode: github`) | `state.yaml` (`Task`, prompt metadata) |
| **Orchestrator** | `RunOrchestrator` | `GitHubRunOrchestrator` | `PromptRunner` |
| **Typical CLI** | `morch run task <name>` | `morch run github <issue>` | `morch run prompt <file.md>` |

### 2.2 File-artifact workflow

High-level lifecycle:

1. **Task init** — workspace directory, initial `Task` in `INITIALIZED` (or first transition recorded on run).
2. **Scope** — `00-scope.md` (Cursor).
3. **Implementation** — `01-cursor-implementation.md` (Cursor).
4. **Review cycles** — Claude then Codex artifacts (`02`…`04` cycle 1; optional cycle 2 with `06`…`09`).
5. **Approval / rework** — driven by `ReviewOutcome` embedded in artifacts; `TaskService.advance()` may auto-detect outcomes.
6. **Archive** — terminal states (`APPROVED`, `ESCALATED`, `ARCHIVED`) and workspace move rules.

See `domain/workflow.py` for the authoritative artifact list and `resolve_next_step()`.

### 2.3 GitHub-native workflow

Lifecycle (logical):

1. **Issue** — claim, labels, optional prompt file injected into instructions.
2. **Branch** — generated name (see [§5](#5-github-native-workflow)); Cursor implements on branch.
3. **PR** — opened with title/body conventions; provenance blocks.
4. **Review cycles** — Claude review → optional Cursor rework → Codex final review; max cycles enforced.
5. **Approval / merge** — pipeline reaches **`APPROVED`**; merge is policy-driven ([§8](#8-merge-policy)).

See `domain/github_models.py`, `domain/github_workflow.py`, and `docs/github-workflow.md`.

### 2.4 Prompt-driven workflow

- Input: **one markdown file** (user-authored prompt).
- `PromptRunner` creates a task directory, seeds a `Task` (typically starting in **`CURSOR_IMPLEMENTING`**), and passes file content as the first agent’s instruction.
- Subsequent enabled agents run in configured order; behavior matches adapter capabilities (automatic vs manual).
- Suitable for quick local runs without GitHub.

---

## 3. Adapters

### 3.1 Capability model

| `AdapterCapability` | Run orchestrator behavior |
|---------------------|---------------------------|
| **`AUTOMATIC`** | Invoke adapter; on **COMPLETED**, advance state and continue until wait/terminal |
| **`SEMI_AUTO`** | May return **WAITING** or **COMPLETED** |
| **`MANUAL`** | Emit instructions; expect **WAITING** until human completes work |

`RunStatus` reflects engine position (**WAITING_ON_***, **SUSPENDED**, etc.), not the same axis as `TaskState`.

### 3.2 Adapter types and CLIs

| Config `type` | Class | Notes |
|---------------|-------|-------|
| `manual` | `ManualAdapter` | **WAITING**; human-in-the-loop |
| `stub` | `StubAdapter` | **COMPLETED**; testing |
| `command` | `CommandAdapter` | **AUTOMATIC**; arbitrary command |
| `cursor-cli` | `CursorCommandAdapter` | Cursor CLI or manual behavior if not configured |
| `claude-cli` | `ClaudeCommandAdapter` | **AUTOMATIC** |
| `codex-cli` | `CodexCommandAdapter` | **AUTOMATIC** |

### 3.3 Factory and auto-wiring

- **`create_adapter(role, adapter_config, store, renderer)`** — maps `type` + `settings` to a concrete adapter (`factory.py`).
- **`create_adapters_from_config(adapters_config, ...)`** — builds a `dict[AgentRole, AgentAdapter]` from YAML.
- **`create_default_adapters(enabled_agents, store)`** — when no `adapters:` section exists, wires **cursor-cli**, **claude-cli**, **codex-cli** with empty settings so runs can be automatic without explicit adapter YAML.

CLI **`_resolve_adapters()`** uses explicit config if present, otherwise default CLI adapters. A **`ManualAdapter`** is always available as **`fallback_adapter`** when a role has no adapter.

---

## 4. Task state model

### 4.1 `TaskState` (file-artifact)

| State | Meaning |
|-------|---------|
| `initialized` | Task created; next step is scope/implementation per `resolve_next_step()` |
| `cursor_implementing` | Cursor owns the current implementation artifact |
| `claude_reviewing` | Claude review phase |
| `cursor_reworking` | Changes requested; Cursor addresses feedback |
| `codex_reviewing` | Codex final review phase |
| `approved` | Review pipeline approved |
| `escalated` | Human escalation (e.g. max cycles) |
| `archived` | Terminal archive state |

Valid transitions are enumerated in `domain/state_machine.py` (`TRANSITIONS`). **`validate_transition()`** raises **`InvalidTransitionError`** on illegal moves.

### 4.2 `GitHubTaskState` (GitHub-native)

| State | Meaning |
|-------|---------|
| `issue_claimed` | Issue claimed; orchestrator ready to drive implementation |
| `cursor_implementing` | Implementation on branch |
| `pr_opened` | PR exists; reviews pending |
| `claude_reviewing` | Claude review step |
| `cursor_reworking` | Rework after feedback |
| `codex_reviewing` | Codex approval gate |
| `approved` | PR approved through pipeline |
| `escalated` | Human required |
| `merged` | PR merged (terminal) |

Transitions: `GITHUB_TRANSITIONS` in `github_models.py`, validated by **`validate_github_transition()`**.

### 4.3 `RunStatus`

| Value | Typical use |
|-------|-------------|
| `idle` | No run in progress |
| `running` | Engine active |
| `waiting_on_cursor` | Blocked on Cursor (manual or external) |
| `waiting_on_claude` | Blocked on Claude |
| `waiting_on_codex` | Blocked on Codex |
| `completed` | Run finished successfully |
| `suspended` | Error or abort |

### 4.4 `ReviewOutcome`

| Value | Meaning |
|-------|---------|
| `approved` | Proceed / accept |
| `minor-fixes-applied` | Accepted with small fixes |
| `changes-requested` | Rework required |

Parsed from review artifacts and used to choose transitions and cycles.

---

## 5. GitHub-native workflow

### 5.1 Branch naming

Default pattern (configurable via `github.branch_pattern`):

```text
{type}/issue-{issue}/{agent}/cycle-{cycle}
```

Example:

```text
feat/issue-42/cursor/cycle-1
```

Implemented by **`generate_branch_name()`** in `domain/github_workflow.py`.

### 5.2 PR title pattern

Default pattern (configurable via `github.pr_title_pattern`):

```text
[{type}][Issue #{issue}][{agent}] {summary}
```

- `{type}` is the **human-readable** label from `WORK_TYPE_LABELS` (e.g. `Feature`, `Fix`).
- `{agent}` reflects the implementing agent label (e.g. `Cursor`).

Implemented by **`generate_pr_title()`**.

### 5.3 Provenance comments

`domain/provenance.py` defines reusable markdown for:

- Issue timeline (claim, PR opened, review start/complete, rework, approved).
- PR body footer block (`pr_body_block`).
- Review header (`review_header`).
- **Fallback** when a formal GitHub review cannot be posted (`comment_fallback_review`).
- **Relayed** review body when the agent runs in a restricted environment but the orchestrator can post (`comment_relayed_review`).

Each block includes a consistent **agent signature** via `agent_sig()` mapping logical roles to display names and handles.

### 5.4 Review relay mechanism

Some agents (notably **Codex** in sandboxed environments) may be unable to call `gh pr review` directly. In that case:

1. The adapter or orchestrator records the review content locally.
2. The orchestrator may post a **relayed** comment using `comment_relayed_review()`, explicitly stating the review is relayed and preserving **logical agent identity** in the header.

This preserves auditability and aligns with the provenance model ([§7](#7-provenance-model)).

---

## 6. Prompt-driven workflow

| Step | Behavior |
|------|----------|
| 1 | Read markdown prompt path; reject if missing |
| 2 | Ensure workspace; create task directory and `Task` with description referencing source file |
| 3 | Iterate **`AgentsConfig.enabled`** order |
| 4 | For each agent, resolve adapter (or fallback); invoke `execute()` |
| 5 | On **WAITING**, set appropriate `RunStatus` and return `PromptRunResult` |
| 6 | On failure, **SUSPENDED** with message |
| 7 | On success through pipeline, **COMPLETED** |

Artifacts and logs land under the file state store like other local tasks, enabling **`morch status task`** / **`morch resume task`**.

---

## 7. Provenance model

**Goal:** every GitHub-visible artifact must attribute **which logical agent** produced content, even when a single human GitHub account is used for automation.

Mechanisms:

- **`AGENT_IDENTITIES`** maps keys (`orchestrator`, `cursor`, `claude`, `codex`) to display name + handle.
- **`agent_sig()`** renders markdown-safe signatures for bodies and comments.
- **Relayed reviews** label orchestrator mediation without conflating Codex/Claude/Cursor identity.

Commit message hints (`fix_commit_prefix`) support traceability for minor fixes pushed to PR branches.

---

## 8. Merge policy

| Rule | Description |
|------|-------------|
| **Default** | **Human-gated merge.** User-facing docs state morch does **not** auto-merge as part of the default pipeline; the workflow completes at **`APPROVED`** and humans decide when to merge. |
| **Safety** | `GitHubTaskService.merge()` only merges tasks in **`approved`** state; otherwise raises. |
| **Self-hosting / morch repo** | Operators maintaining **this** repository (or other self-hosted environments) may invoke **`merge()`** programmatically, custom automation, or `gh pr merge` explicitly after approval. That is an **explicit operator action**, not unattended default behavior. |

This distinction keeps production repos safe while allowing controlled merge automation where policy allows.

---

## 9. New in v0.2.0

### 9.1 Setup flow

| Feature | Detail |
|---------|--------|
| Command | **`morch setup`** (via `run_setup()` in `setup_service.py`) |
| Auto-detection | **`detect_agent()` / `detect_all_agents()`** — PATH resolution, optional saved path from `SetupConfig` |
| Persistence | **`~/.morch/config.yaml`** — `agent_paths` map |
| Interactive mode | Prompts when binaries are missing (non-interactive: detect + save) |

### 9.2 Agent auto-detection

For each of **cursor**, **claude**, **codex**:

- Locate binary (`shutil.which` or configured path).
- Run **`--version`** (timeout-bounded subprocess).
- **Auth:** Cursor assumed managed by desktop app; Claude runs `claude auth status`; Codex checks `OPENAI_API_KEY` and `~/.codex/auth.json`.

### 9.3 Structured logging

| Mechanism | Location / behavior |
|-----------|---------------------|
| Python **`logging`** | `infrastructure/logger.py`: `get_logger()`, `MorchLogFilter` for `agent` / `phase` fields |
| Env | **`MORCH_LOG_LEVEL`** (default `INFO`) |
| File | **`~/.morch/logs/morch.log`** (detailed format with agent/phase) |
| JSONL | **`RunLogger`** — per-task `run.log` (existing; complements app-level logging) |

### 9.4 Resource limit handling

Hierarchy under **`AgentResourceLimitError`**:

| Class | Typical trigger |
|-------|-----------------|
| `AgentTokenLimitError` | Token / context length |
| `AgentRateLimitError` | Rate limits, HTTP 429 |
| `AgentQuotaLimitError` | Quota / billing |
| `AgentProviderRefusalError` | Capacity / HTTP 503 |

**`classify_resource_error(agent, stderr, exit_code)`** returns a specific subclass or `None` by pattern-matching stderr (and exit codes).

### 9.5 Bilingual documentation

Primary specifications and user guides are **English** (`docs/morch.md`, `docs/architecture.md`, this document). Where the project ships **localized** material (e.g. Korean README fragments, Cursor rules, or community docs), they are **additive** and must not contradict the English contracts for workflow, safety, and merge policy.

---

## 10. Known limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **Sequential agent execution** | Throughput is one agent at a time per task run | Split work across tasks/repos; run manual phases in parallel outside morch if policy allows |
| **Single-repo scope per invocation** | One `target_repo` / `--repo` per task | Run separate invocations for multiple repos |
| **Codex sandbox restrictions** | May be unable to post GitHub reviews directly | Relay mechanism ([§5.4](#54-review-relay-mechanism)); fallback comments |
| **No parallel agent execution** | No concurrent multi-agent steps within one pipeline | By design for determinism and simpler state; future versions would need explicit concurrency model |
| **Self-approval limitation** | Cursor, Claude, and Codex share a single `git`/`gh` identity; GitHub treats all reviews as from one account | Orchestrator logs a caveat at approval; the approval is an internal gate, not an independent GitHub review. Branch protection rules requiring distinct human reviewers still apply. |
| **Local repo branch left on workflow branch** | After a run, the local clone may be on the feature branch instead of `main` | Orchestrator now saves and restores the original branch after run/resume completes |

Additional practical constraints:

- **Max review cycles** (default 2) can escalate to human.
- **Adapter reliability** depends on external CLI versions and authentication.
- **File-artifact** and **GitHub** modes require different operational skills (`git`/`gh` fluency for GitHub-native).
- **Self-approval caveat**: In the current single-identity setup, all three agents (Cursor, Claude, Codex) post reviews via the same GitHub account. GitHub may treat approvals as self-approval and not count them toward branch protection approval requirements. This is an inherent limitation of the shared-identity model and is documented in the orchestrator logs and run output.

---

## 11. References

| Document | Purpose |
|----------|---------|
| `docs/architecture.md` | Layered design, adapter capabilities, extension points |
| `docs/workflow.md` | File-artifact stages and artifacts |
| `docs/github-workflow.md` | GitHub lifecycle, branch/PR conventions, safety rules |
| `docs/morch.md` | User-facing CLI and workflow selection |
| `docs/adapters.md` | Adapter configuration |
| `AGENTS.md` / `CLAUDE.md` | Repository purpose and agent boundaries |
| `CHANGELOG.md` | Version history |

---

## Appendix A — Config excerpt (illustrative)

```yaml
workspace_dir: workspace
template_dir: templates/artifacts
max_cycles: 2

agents:
  enabled: [cursor, claude, codex]

adapters:
  cursor:
    type: cursor-cli
    settings: {}
  claude:
    type: claude-cli
    settings: {}
  codex:
    type: codex-cli
    settings: {}

github:
  repo: owner/name
  branch_pattern: "{type}/issue-{issue}/{agent}/cycle-{cycle}"
  pr_title_pattern: "[{type}][Issue #{issue}][{agent}] {summary}"
  base_branch: main
```

---

## Appendix B — Error hierarchy (domain)

```text
OrchestratorError
├── InvalidTransitionError
├── TaskNotFoundError
├── TaskAlreadyExistsError
├── ArtifactMissingError
├── MaxCyclesExceededError
├── WorkflowConfigError
└── AgentResourceLimitError
    ├── AgentTokenLimitError
    ├── AgentRateLimitError
    ├── AgentQuotaLimitError
    └── AgentProviderRefusalError
```

---

*End of technical specification.*
