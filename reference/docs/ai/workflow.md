# Multi-Agent Review Workflow

## Agents

| Agent | Tool | Role | Account |
|-------|------|------|---------|
| **Cursor** (Opus) | Cursor IDE | Primary implementer | Any |
| **Claude** | Claude Code / claude.ai | Reviewer + targeted fixer | May be a separate personal account |
| **Codex** | OpenAI Codex CLI | Final reviewer | May share Cursor's account or be separate |

## Design constraint

Agents may run on different accounts and machines. The workflow is
**file-based and repo-based** — no shared session state, no IDE-specific
context. Every agent picks up work by reading files in the repository.

## Workflow

```
┌──────────┐    01-impl     ┌──────────┐   02-review    ┌──────────┐
│  Cursor   │──────────────▶│  Claude   │──────────────▶│  Codex   │
│ implement │               │  review   │               │  final   │
│  00 + 01  │◀──────────────│  + fix    │◀──────────────│  review  │
└──────────┘  (if cycle 2)  └──────────┘  (if cycle 2) └──────────┘
                                                              │
                                                        05-final-approval
```

## Task granularity

One task directory = one PR-sized logical change set. Do not combine unrelated
changes into a single review task. If a task grows beyond one PR, split it.

## Artifact sequence (mandatory)

Every task lives in `reviews/active/<task-name>/`. Files are numbered and
created in order. Templates are in `reviews/templates/`.

| File | Author | Required | When |
|------|--------|----------|------|
| `00-scope.md` | Cursor | Always | Before implementation starts |
| `01-cursor-implementation.md` | Cursor | Always | After implementation + self-validation |
| `02-claude-review-cycle-1.md` | Claude | Always | After reading 00 + 01 |
| `03-cursor-response-cycle-1.md` | Cursor | If changes requested | After addressing Claude/Codex findings |
| `04-codex-review-cycle-1.md` | Codex | Always | After reading all prior artifacts |
| `05-final-approval.md` | Codex | When approved | Task is done |

Cycle 2 (if needed): `06-claude-review-cycle-2.md`, `07-cursor-response-cycle-2.md`,
`08-codex-review-cycle-2.md`, `09-final-approval.md`.

**Maximum 2 cycles.** If issues persist after cycle 2, escalate to human.

## Step-by-step

### Step 1 — Cursor implements

1. Create `reviews/active/<task-name>/`
2. Write `00-scope.md` — objective, acceptance criteria, file scope, risks
3. Implement the changes in the working tree
4. Self-validate (kustomize build, yamllint, bash -n, etc.)
5. Write `01-cursor-implementation.md` — what was done, files changed, validation results

### Step 2 — Claude reviews

1. Read `00-scope.md` and `01-cursor-implementation.md`
2. Review the files listed in the implementation doc
3. Write `02-claude-review-cycle-1.md`:
   - **Minor issues** (typo, formatting): fix directly, note in "Fixes applied"
   - **Major issues** (logic, missing file): describe exact fix in findings
4. Set status: `approved`, `changes-requested`, or `minor-fixes-applied`

### Step 3 — Codex reviews

1. Read all prior artifacts (00, 01, 02, and 03 if it exists)
2. Run validation commands
3. Write `04-codex-review-cycle-1.md`
4. If approved: write `05-final-approval.md`
5. If not approved: describe remaining issues (triggers cycle 2)

### Cycle 2 (if needed)

1. Cursor addresses findings → `07-cursor-response-cycle-2.md`
2. Claude re-reviews → `06-claude-review-cycle-2.md`
3. Codex re-reviews → `08-codex-review-cycle-2.md` → `09-final-approval.md`

If cycle 2 still has unresolved blockers or major issues, **stop**. Do not
start cycle 3. Write a summary of remaining issues and escalate to a human
for resolution.

### After approval

Move the task directory: `mv reviews/active/<task> reviews/archive/<task>`

## Rules

### Agent edit boundaries

| Agent | May edit | Must not edit |
|-------|---------|---------------|
| **Cursor** | Any implementation file + own review artifacts (00, 01, 03, 07) | Other agents' review notes |
| **Claude** | Only files listed in `01-cursor-implementation.md` + own review artifacts (02, 06). Limited to minor/clear fixes (typos, missing fields, formatting). Major issues must be described, not applied. | Unrelated files, broad structural changes, other agents' artifacts |
| **Codex** | Own review artifacts only (04, 05, 08, 09). Read-only review of all other files. | Any implementation file (Codex does not write code) |

### What each agent must review

| Agent | Focus |
|-------|-------|
| Cursor | Self-review before handoff: validation passes, no secrets, scope complete |
| Claude | Correctness, contracts, consistency, secrets, targeted fix opportunities |
| Codex | Completeness, naming, validation results, docs accuracy, approval decision |

### How "done" is declared

A task is done when `05-final-approval.md` (or `09-final-approval.md` for cycle 2)
exists with `Status: approved` and all checklist items are checked.

## Quick-start

```bash
TASK="add-ingress-service"
mkdir -p "reviews/active/${TASK}"
cp reviews/templates/00-scope.md "reviews/active/${TASK}/"
# Fill in scope, implement, create 01, hand off to Claude...
```

## Example

See `reviews/examples/example-v1-platform-refactor/` for a completed reference
example demonstrating every artifact in the sequence.
