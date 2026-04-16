# Changelog

All notable changes to this project will be documented in this file.

This project uses [Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-04-16

Patch release fixing a reliability bug where agent steps could time out
prematurely on large-scope workflows.

### Fixed

- **Configurable agent timeout** — agent execution timeout is now
  configurable per adapter via config YAML and overridable per run
  via `--timeout` CLI flag
- **Raised default timeouts** — Cursor 600s→1200s, Codex 600s→900s,
  Claude 300s→600s to accommodate real-world workloads
- **Informative timeout errors** — timeout failures now identify the
  agent, action/phase, and configured timeout value, with guidance
  on how to increase it
- **Spinner shows timeout** — the progress spinner displays elapsed
  time against the configured limit (e.g. `120s/1200s`)

### Usage

```bash
# Override timeout for a single run (all agents)
morch issue start --repo o/r --title "..." --timeout 1800

# Or set per-adapter in config YAML
adapters:
  cursor:
    type: cursor-cli
    settings:
      timeout: 3600
```

## [0.2.0] — 2026-04-16

Feature release focused on self-sufficiency, observability, and documentation.
This release was developed and validated using **morch itself** as the
primary workflow engine (self-hosted development).

### New Features

- **`morch setup`** — interactive agent CLI setup flow:
  - Auto-detects `cursor`, `claude`, and `codex` commands on PATH
  - Shows version and authentication status for each agent
  - Prompts for custom paths when auto-detection fails
  - Persists configuration in `~/.morch/config.yaml`

- **Agent auto-detection** — `morch config show` now displays:
  - Detected agent paths and versions
  - Authentication state per agent
  - Custom configured paths from `~/.morch/config.yaml`
  - Distinction between "not installed" vs "installed but not authenticated"

- **Structured application logging** — new `orchestrator.infrastructure.logger`:
  - Python `logging` framework with stderr (warnings+) and file handlers
  - Persistent log file at `~/.morch/logs/morch.log`
  - `MORCH_LOG_LEVEL` environment variable (default: INFO)
  - Agent and phase context in log records

- **Agent resource limit error handling** — new error hierarchy:
  - `AgentResourceLimitError` base class
  - `AgentTokenLimitError` — token/context length exceeded
  - `AgentRateLimitError` — rate limit / HTTP 429
  - `AgentQuotaLimitError` — billing / quota exceeded
  - `AgentProviderRefusalError` — provider capacity / HTTP 503
  - `classify_resource_error()` utility for pattern-based detection
  - Integrated into `CommandAdapter` for automatic classification

- **Technical specification** — comprehensive tech spec documentation:
  - English: `docs/tech-spec.md`
  - Korean: `docs/tech-spec-ko.md`
  - Covers architecture, workflow model, adapters, state machine,
    GitHub integration, provenance, and merge policy

- **Complete usage documentation** — full user guide:
  - English: `docs/usage-en.md`
  - Korean: `docs/usage-ko.md`
  - Covers installation, setup, auth, configuration, workflows,
    troubleshooting, and examples

### Improvements

- CLI now logs all commands and errors to persistent log file
- `KeyboardInterrupt` handling in CLI with clean exit
- `morch config show` enhanced with agent detection output
- Command adapter logs resource-limit failures with structured detail

### Architecture

- New `infrastructure/setup_service.py` — agent detection and setup
- New `infrastructure/logger.py` — structured application logging
- New error classes in `domain/errors.py` for resource limits
- All new modules follow existing DDD layer conventions

### Known Limitations

- **Final merge is always a human decision** — `morch` does not
  auto-merge PRs (exception: self-hosting workflows in this repository)
- **Codex sandbox** — Codex's `--full-auto` mode blocks outbound
  GitHub API access; reviews are relayed by the orchestrator
- **Single-repo scope** — each `morch` invocation targets one repository
- **No parallel agent execution** — agents run sequentially within
  a workflow cycle

## [0.1.0] — 2026-04-16

Initial public release of **morch**, a multi-agent orchestrator for
Cursor, Claude, and Codex code review workflows.

### Features

- **CLI (`morch`)** — single entrypoint for all orchestration commands:
  `issue start`, `run`, `resume`, `status`, `task list`, `doctor`, `auth`
- **File-artifact workflow** — local task directories with sequential
  artifact production (scope → implementation → review → approval)
- **GitHub-native workflow** — issue-driven, PR-based pipeline where
  agents collaborate through branches, commits, and PR reviews
- **Multi-agent sequencing** — configurable agent order with support
  for Cursor (implementer), Claude (reviewer), and Codex (final reviewer)
- **Prompt-file support** — `--prompt-file` flag injects detailed
  instructions into agent context for repeatable workflows
- **Agent provenance** — every GitHub comment, review, and commit
  carries explicit agent identity (`@cursor-agent`, `@claude-agent`,
  `@codex-agent`, `@multi-orchestrator-agent`)
- **Review relay** — when an agent cannot post directly to GitHub
  (e.g. Codex sandbox restrictions), the orchestrator relays the
  review content to the PR with clear attribution
- **Dual auth detection** — Codex readiness checks both API-key and
  login-based (`~/.codex/auth.json`) authentication
- **State machine** — explicit state transitions for both file-artifact
  and GitHub-native workflows with rerun safety
- **Cycle control** — configurable max review cycles with automatic
  escalation to human review
- **`morch doctor`** — checks installation and auth status of all
  required tools (gh, cursor, claude, codex)

### Architecture

- Domain-driven design: `domain/`, `application/`, `infrastructure/`,
  `adapters/`
- Pluggable adapter system with real CLI adapters and stub/manual
  fallbacks
- YAML-based task state persistence
- Structured run logging (`run.log`) for every workflow execution

### Known Limitations

- **Final merge is always a human decision** — `morch` does not
  auto-merge PRs
- **Codex sandbox** — Codex's `--full-auto` mode blocks outbound
  GitHub API access; reviews are relayed by the orchestrator
- **Single-repo scope** — each `morch` invocation targets one
  repository
- **No parallel agent execution** — agents run sequentially within
  a workflow cycle

### Versioning

This project follows [Semantic Versioning](https://semver.org/):

- **MAJOR** — breaking changes to CLI interface or workflow contracts
- **MINOR** — new features, new workflow modes, new agent support
- **PATCH** — bug fixes, documentation updates, internal improvements
