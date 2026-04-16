# Agent Roles

## Cursor — Primary Implementer

**Tool**: Cursor IDE (Opus)

### Responsibilities

1. Read the task description and create an implementation plan
2. Implement changes in the target repository
3. Run validation (linting, build checks, syntax checks)
4. Write the implementation handoff artifact
5. Address review feedback in subsequent cycles

### Artifacts produced

- `00-scope.md` — Task objective, acceptance criteria, file scope
- `01-cursor-implementation.md` — What was done, files changed, validation results
- `03-cursor-response-cycle-1.md` — Response to review findings (if changes requested)
- `07-cursor-response-cycle-2.md` — Cycle 2 response (if needed)

### Edit boundaries

- May edit: any implementation file in the target repo + own artifacts
- Must not edit: other agents' review artifacts

---

## Claude — Reviewer + Targeted Fixer

**Tool**: Claude Code / claude.ai

### Responsibilities

1. Read the handoff from Cursor
2. Review all files listed in the handoff
3. Write structured review notes with severity classifications
4. Apply targeted fixes for minor/clear issues (typos, formatting)
5. Describe exact proposed fixes for significant issues

### Artifacts produced

- `02-claude-review-cycle-1.md` — Review findings, fixes applied
- `06-claude-review-cycle-2.md` — Cycle 2 re-review (if needed)

### Review focus

| Area | What to check |
|------|--------------|
| Correctness | Do changes produce valid results? Are values correct? |
| Contracts | Do interfaces match documentation? |
| Consistency | Same naming across docs, code, and scripts? |
| Secrets | No credentials in any file? |
| Completeness | All referenced files exist? |

### Fix policy

| Severity | Action |
|----------|--------|
| Minor (typo, formatting) | Fix directly, note in review |
| Major (wrong value, missing file) | Describe exact fix, do not apply |
| Blocker (structural problem) | Describe issue + proposed resolution |

### Edit boundaries

- May edit: files listed in implementation doc + own artifacts
- Must not edit: unrelated files, other agents' artifacts

---

## Codex — Final Reviewer

**Tool**: OpenAI Codex CLI

### Responsibilities

1. Read all prior artifacts
2. Run validation commands
3. Write final review notes
4. Issue approval or request changes
5. If approved, create final approval document

### Artifacts produced

- `04-codex-review-cycle-1.md` — Final review, validation results
- `05-final-approval.md` — Sign-off document (if approved)
- `08-codex-review-cycle-2.md` — Cycle 2 final review (if needed)
- `09-final-approval.md` — Cycle 2 sign-off (if approved)

### Approval criteria

All must be true:
- [ ] All validation commands pass
- [ ] No blocker or major findings remain
- [ ] Claude's findings are resolved
- [ ] Documentation is consistent with implementation
- [ ] No secrets or production values present
- [ ] Review cycle count <= 2

### Edit boundaries

- May edit: own artifacts only (read-only review of everything else)
- Must not edit: any implementation file (Codex does not write code)

---

## Escalation

If cycle 2 ends with unresolved blockers or major issues, the task
transitions to `escalated` state. No cycle 3 is allowed. A human
must resolve the remaining issues.
