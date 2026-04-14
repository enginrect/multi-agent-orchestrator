# GitHub-Native Workflow

The orchestrator supports a GitHub-native workflow where GitHub Issues, branches,
pull requests, and PR reviews are the primary collaboration surface. Local file
artifacts are replaced by GitHub primitives — agents collaborate through PRs
instead of markdown files in a task directory.

## Overview

```
Issue created
      │
      ▼
┌─────────────┐
│ISSUE_CLAIMED│  orchestrator claims issue, generates branch name
└─────┬───────┘
      ▼
┌────────────────────┐
│CURSOR_IMPLEMENTING │  Cursor creates branch from base, implements, commits, pushes
└─────┬──────────────┘
      ▼
┌───────────┐
│PR_OPENED  │  Cursor opens PR targeting base branch
└─────┬─────┘
      ▼
┌─────────────────┐
│CLAUDE_REVIEWING  │  Claude reviews PR via gh pr review
└─────┬───────────┘
      │
      ├──approved──────────────────┐
      │                            ▼
      │                   ┌─────────────────┐
      │                   │CODEX_REVIEWING   │ Codex final review
      │                   └──┬──────────┬───┘
      │                      │          │
      │                 approved   changes requested
      │                      │          │
      │                      ▼          ▼
      │               ┌──────────┐  ┌───────────────────┐
      │               │APPROVED  │  │CURSOR_REWORKING    │
      │               └────┬─────┘  └────────┬──────────┘
      │                    ▼                  │
      │               ┌────────┐              │ (back to review)
      │               │MERGED  │              ▼
      │               └────────┘         loop (max 2 cycles)
      │                                       │
      └──changes requested────►               ▼
                                        ┌───────────┐
                                        │ESCALATED  │ human needed
                                        └───────────┘
```

## Work Types

All tasks are classified by work type. This replaces domain-specific prefixes
and makes the orchestrator suitable for any codebase: backend, frontend, mobile,
infrastructure, automation, documentation, or any other repository.

| Work Type | Label | Use for |
|-----------|-------|---------|
| `feat` | Feature | New functionality |
| `modify` | Modify | Enhancement to existing functionality |
| `fix` | Fix | Bug fixes |
| `refactor` | Refactor | Code restructuring without behavior change |
| `docs` | Docs | Documentation only |
| `chore` | Chore | Dependency updates, cleanup, tooling |
| `ops` | Ops | Operational improvements, CI/CD, monitoring |
| `test` | Test | Test additions/improvements |
| `hotfix` | Hotfix | Urgent production fixes |

Work type is specified via the `--type` CLI flag (default: `feat`).

## Issue Lifecycle

| Phase | Issue State | Issue Labels |
|-------|-------------|--------------|
| Created | OPEN | (user labels) |
| Claimed by orchestrator | OPEN | `orchestrator:claimed` |
| Implementation in progress | OPEN | `orchestrator:in-progress` |
| PR opened, under review | OPEN | `orchestrator:review` |
| PR approved and merged | CLOSED | `orchestrator:approved` |
| Escalated to human | OPEN | (unchanged) |

The orchestrator comments on the issue at key transitions to provide
visibility into the automated workflow.

## Branch Naming Convention

Default pattern: `{type}/issue-{issue}/{agent}/cycle-{cycle}`

| Placeholder | Description | Examples |
|-------------|-------------|----------|
| `{type}` | Work type value | `feat`, `fix`, `refactor`, `docs` |
| `{issue}` | GitHub issue number | `42`, `123` |
| `{agent}` | Agent performing the work | `cursor`, `claude`, `codex` |
| `{cycle}` | Review cycle number | `1`, `2` |

Examples:
- `feat/issue-42/cursor/cycle-1` — feature implementation, first cycle
- `fix/issue-99/cursor/cycle-2` — bug fix rework, second cycle
- `docs/issue-15/cursor/cycle-1` — documentation task

The pattern is configurable via the `github.branch_pattern` config key.
Agents must work exclusively on their assigned branch. Direct pushes to
the base branch are prohibited.

### Branch creation behavior

- Work begins from the configured base branch after a fresh fetch
- Cursor typically creates the implementation branch: `git fetch origin main && git checkout -b <branch> origin/main`
- Claude and Codex primarily review through the PR flow
- Follow-up branches for rework cycles use the same naming convention with an incremented cycle number
- Agents should not reuse branches from previous cycles

## PR Title Convention

Default pattern: `[{type}][Issue #{issue}][{agent}] {summary}`

| Placeholder | Description | Examples |
|-------------|-------------|----------|
| `{type}` | Human-readable type label | `Feature`, `Fix`, `Refactor` |
| `{issue}` | GitHub issue number | `42` |
| `{agent}` | Agent that created the PR | `Cursor` |
| `{summary}` | Short description from the issue title | `Add login page` |

Examples:
- `[Feature][Issue #42][Cursor] Add user authentication`
- `[Fix][Issue #99][Cursor] Resolve null pointer in checkout`
- `[Docs][Issue #15][Cursor] Update API reference`
- `[Refactor][Issue #71][Cursor] Extract payment service`
- `[Hotfix][Issue #200][Cursor] Fix production crash on startup`

The pattern is configurable via `github.pr_title_pattern`.

## Pull Request Lifecycle

1. **Creation**: Cursor creates the PR after implementing changes.
   The PR title follows the configured pattern and the body includes
   `Resolves #<issue>` for auto-close on merge.

2. **Review**: Claude reviews the PR using `gh pr review`. The review
   can approve, request changes, or leave comments.

3. **Final review**: Codex performs the approval gate review. If approved,
   the task advances to APPROVED state.

4. **Rework**: If changes are requested, Cursor addresses feedback by
   pushing follow-up commits to the PR branch. The review cycle repeats
   (max 2 cycles).

5. **Merge**: After approval, the orchestrator can merge the PR via
   `gh pr merge` (squash by default, with branch deletion).

## Agent Responsibilities

| Agent | GitHub Actions |
|-------|---------------|
| **Cursor** | Create branch from base, implement, commit, push, open PR, push rework commits |
| **Claude** | Read PR diff, post PR review (approve/request-changes), push minor fixes to branch |
| **Codex** | Read PR diff + review history, post final PR review (approve/request-changes) |

Agents manage git operations themselves. The orchestrator provides context
(branch name, PR number, repository, work type) and the agent CLI handles
checkout, commit, push, and review posting.

## Safety Rules

These constraints are enforced in code:

1. **No direct push to base branch**: Branch names must match the configured
   pattern. Agents cannot target the base branch directly.

2. **PRs must target the configured base branch**: The orchestrator verifies
   PR base ref.

3. **Merge gated on APPROVED state**: The orchestrator will not merge a PR
   that has not been approved through the review pipeline.

4. **Max 2 review cycles**: After 2 cycles of changes-requested reviews,
   the task escalates to human intervention.

5. **Auth verification**: `gh auth status` is verified at initialization.

6. **Repo scoping**: All `gh` commands include `--repo` to ensure operations
   are scoped to the configured repository.

## CLI Commands

### `github-run`

Claim an issue and drive it through the full review pipeline.

```bash
orchestrator github-run <issue-number> --repo owner/name [--type TYPE] [--config file]
```

Example:
```bash
orchestrator github-run 42 --repo myorg/myapp --type feat --config configs/github-default.yaml
orchestrator github-run 99 --repo myorg/myapp --type fix
orchestrator github-run 15 --repo myorg/docs --type docs
```

### `github-resume`

Continue a task that was paused waiting for manual completion.

```bash
orchestrator github-resume <task-name> [--repo owner/name] [--config file]
```

Example:
```bash
orchestrator github-resume issue-42 --config configs/github-default.yaml
```

### `github-status`

Show the current status of a GitHub-backed task.

```bash
orchestrator github-status <task-name> [--repo owner/name] [--config file]
```

## Configuration

Add a `github` section to your orchestrator config YAML:

```yaml
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
```

### Config fields

| Field | Default | Description |
|-------|---------|-------------|
| `repo` | (required) | GitHub repository in `owner/name` format |
| `base_branch` | `main` | Target branch for PRs |
| `branch_pattern` | `{type}/issue-{issue}/{agent}/cycle-{cycle}` | Branch naming template |
| `pr_title_pattern` | `[{type}][Issue #{issue}][{agent}] {summary}` | PR title template |
| `labels.claimed` | `orchestrator:claimed` | Label added when issue is claimed |
| `labels.in_progress` | `orchestrator:in-progress` | Label during implementation |
| `labels.review` | `orchestrator:review` | Label during PR review |
| `labels.approved` | `orchestrator:approved` | Label after approval |

### Pattern placeholders

Branch pattern supports: `{type}`, `{issue}`, `{agent}`, `{cycle}`.
PR title pattern supports: `{type}`, `{issue}`, `{agent}`, `{summary}`.

The `--repo` CLI flag overrides `github.repo` from config. Adapter
configuration (the `adapters:` section) is shared between file-based
and GitHub-native modes.

## Prerequisites

- **`gh` CLI**: Must be installed and authenticated (`gh auth login`).
- **Agent CLIs**: Cursor, Claude, and Codex CLIs for automated execution.
- **Repository access**: Push access to the target repository.
- **Branch protection** (recommended): Configure branch protection rules
  on the base branch to require PR reviews before merge.

## Coexistence with File-Based Mode

The GitHub-native workflow coexists with the existing file-based workflow.
Both share the same adapter infrastructure and workspace directory for
local state tracking. Commands are separate:

| File-based | GitHub-native |
|------------|---------------|
| `orchestrator run` | `orchestrator github-run` |
| `orchestrator resume` | `orchestrator github-resume` |
| `orchestrator status` | `orchestrator github-status` |
| `orchestrator init` | (issue serves as init) |
| `orchestrator advance` | (automatic via PR state) |

Local `state.yaml` files in the workspace track the orchestrator's
internal state for GitHub tasks, but the primary collaboration surface
is GitHub.
