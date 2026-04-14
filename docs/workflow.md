# Review Workflow Specification

## Overview

The orchestrator manages a three-agent review pipeline:

1. **Cursor** — Primary implementer. Writes code, creates artifacts.
2. **Claude** — First reviewer. Reviews for correctness, applies minor fixes.
3. **Codex** — Final reviewer. Validates builds, issues approval or rejection.

## Design constraint

Agents may run on different accounts and machines. The workflow is
**file-based** — no shared session state. Every agent picks up work
by reading files in the task directory.

## Artifact sequence

### Cycle 1

| # | File | Author | Required | Purpose |
|---|------|--------|----------|---------|
| 0 | `00-scope.md` | Cursor | Yes | Task objective, criteria, file scope |
| 1 | `01-cursor-implementation.md` | Cursor | Yes | Implementation summary, files changed |
| 2 | `02-claude-review-cycle-1.md` | Claude | Yes | Review findings, fixes applied |
| 3 | `03-cursor-response-cycle-1.md` | Cursor | If changes requested | Response to Claude findings |
| 4 | `04-codex-review-cycle-1.md` | Codex | Yes | Final review, validation results |
| 5 | `05-final-approval.md` | Codex | If approved | Sign-off document |

### Cycle 2 (if Codex requests changes)

| # | File | Author | Required | Purpose |
|---|------|--------|----------|---------|
| 6 | `06-claude-review-cycle-2.md` | Claude | Yes | Re-review |
| 7 | `07-cursor-response-cycle-2.md` | Cursor | If changes requested | Response to cycle 2 findings |
| 8 | `08-codex-review-cycle-2.md` | Codex | Yes | Cycle 2 final review |
| 9 | `09-final-approval.md` | Codex | If approved | Cycle 2 sign-off |

**Maximum 2 cycles.** If cycle 2 still has unresolved issues, the task
is escalated to a human.

## Step-by-step flow

### Step 1 — Cursor implements

1. `orchestrator init <task-name> --target-repo <path>`
2. Edit `00-scope.md` — objective, acceptance criteria, file scope, risks
3. Implement changes in the target repository
4. Write `01-cursor-implementation.md` — what was done, files changed, validation
5. `orchestrator advance <task-name>`

### Step 2 — Claude reviews

1. Read `00-scope.md` and `01-cursor-implementation.md`
2. Review all files listed in the implementation doc
3. Write `02-claude-review-cycle-1.md`:
   - Set `**Status**: approved`, `minor-fixes-applied`, or `changes-requested`
   - Minor issues: fix directly, note in "Fixes applied"
   - Major issues: describe exact fix in findings
4. `orchestrator advance <task-name>`

### Step 3 — Cursor reworks (if changes requested)

1. Read Claude's findings
2. Address each finding
3. Write `03-cursor-response-cycle-1.md`
4. `orchestrator advance <task-name>`

### Step 4 — Codex final review

1. Read all prior artifacts
2. Run validation commands
3. Write `04-codex-review-cycle-1.md`
4. If approved: `05-final-approval.md` is generated
5. `orchestrator advance <task-name>`
6. If approved: `orchestrator archive <task-name>`

### Cycle 2 (if needed)

Same flow with artifacts 06-09. If cycle 2 still fails, the task
transitions to `escalated` state.

## Review outcomes

| Outcome | Meaning | Effect |
|---------|---------|--------|
| `approved` | No issues remain | Advance to next phase |
| `minor-fixes-applied` | Claude fixed minor issues | Advance to next phase |
| `changes-requested` | Issues need addressing | Loop back for rework |

## Agent edit boundaries

| Agent | May edit | Must not edit |
|-------|---------|---------------|
| Cursor | Implementation files + own artifacts (00, 01, 03, 07) | Other agents' review notes |
| Claude | Files in implementation doc + own artifacts (02, 06) | Unrelated files, other agents' artifacts |
| Codex | Own artifacts only (04, 05, 08, 09) | Any implementation file |
