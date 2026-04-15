# Smoke Test Prompt

## Objective

Validate the multi-agent orchestrator pipeline by performing a minimal,
safe change on the target repository.

## Task

1. Create a new file named `morch-smoke-test.md` in the repository root
2. The file should contain:
   - A short description confirming the orchestrator pipeline ran successfully
   - The current timestamp
   - The branch name this change was committed on
3. Commit the file on the designated branch
4. Open a pull request with a clear title and description

## Acceptance criteria

- The file `morch-smoke-test.md` exists in the PR
- The PR targets the configured base branch
- The commit message follows conventional format
- No other files are modified
- The PR description explains this is an orchestrator smoke test

## Safety

- Do NOT modify any existing files
- Do NOT push to the base branch directly
- Keep changes minimal and reversible
