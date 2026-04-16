# Reviews

File-based multi-agent review workspace for the Cursor → Claude → Codex workflow.

This directory is the working area where review artifacts are created, reviewed,
and archived for each task. It is not documentation — it is operational.

## Directory structure

```
reviews/
├── README.md
├── templates/                       # Reusable artifact templates
│   ├── 00-scope.md
│   ├── 01-cursor-implementation.md
│   ├── 02-claude-review.md
│   ├── 03-cursor-response.md
│   ├── 04-codex-review.md
│   └── 05-final-approval.md
├── active/                          # In-progress tasks ONLY
│   └── <task-name>/
│       ├── 00-scope.md
│       ├── 01-cursor-implementation.md
│       ├── 02-claude-review-cycle-1.md
│       ├── 03-cursor-response-cycle-1.md   (if changes requested)
│       ├── 04-codex-review-cycle-1.md
│       └── 05-final-approval.md
├── archive/                         # Completed tasks (moved from active/)
│   └── <task-name>/
└── examples/                        # Reference examples (not real tasks)
    └── example-v1-platform-refactor/
```

## Rules

1. Every task gets a directory under `reviews/active/<task-name>/`.
2. One task directory = one PR-sized logical change set. Do not combine unrelated changes.
3. Cursor creates the directory and `00-scope.md` before implementing.
4. Files are numbered — agents create them in order, never skip numbers.
5. Maximum 2 review cycles. If cycle 2 still has unresolved issues, **stop and escalate to human**.
6. When `05-final-approval.md` is created, move the task from `active/` to `archive/`.
7. `active/` contains only real in-progress work. Examples live in `examples/`.
8. All state must be recoverable from files. No shared session state.

## Agent edit boundaries

| Agent | May edit | Must not edit |
|-------|---------|---------------|
| **Cursor** | Implementation files + own artifacts (00, 01, 03, 07) | Other agents' review notes |
| **Claude** | Files listed in `01-cursor-implementation.md` + own artifacts (02, 06). Fixes limited to minor/clear issues only. | Unrelated files, structural changes, other agents' artifacts |
| **Codex** | Own artifacts only (04, 05, 08, 09) | All implementation files (read-only review) |

## Mandatory artifact sequence

| File | Author | Required | Purpose |
|------|--------|----------|---------|
| `00-scope.md` | Cursor | Always | Task objective, scope, acceptance criteria |
| `01-cursor-implementation.md` | Cursor | Always | What was done, files changed, validation results |
| `02-claude-review-cycle-1.md` | Claude | Always | Review findings, fixes applied |
| `03-cursor-response-cycle-1.md` | Cursor | If changes requested | How review findings were addressed |
| `04-codex-review-cycle-1.md` | Codex | Always | Final review, validation results |
| `05-final-approval.md` | Codex | When approved | Sign-off, approved for commit |

Cycle 2 files (if needed): `06-claude-review-cycle-2.md`, `07-cursor-response-cycle-2.md`,
`08-codex-review-cycle-2.md`, `09-final-approval.md`.

## Quick start

```bash
TASK="add-ingress-service"
mkdir -p "reviews/active/${TASK}"
cp reviews/templates/00-scope.md "reviews/active/${TASK}/"
# Cursor: fill in scope, implement, then create 01-cursor-implementation.md
# Claude: read 00 + 01, write 02-claude-review-cycle-1.md
# Codex: read all, write 04-codex-review-cycle-1.md + 05-final-approval.md
# Done: mv "reviews/active/${TASK}" "reviews/archive/${TASK}"
```

## Example

See `reviews/examples/example-v1-platform-refactor/` for a completed reference
example demonstrating the full artifact flow.

See `docs/ai/workflow.md` for the full process and role definitions.
