# Scope

**Task**: example-v1-platform-refactor
**Date**: 2026-04-14
**Author**: Cursor

## Objective

Refactor the repository from bootstrap-heavy structure to a platform service
delivery model. The backend application orchestrates cluster lifecycle;
ArgoCD on the management cluster delivers platform services. Bootstrap
templates (cp-init, cp-join, wk-join) remain in the repo as the three-template
contract.

## Acceptance criteria

- [x] Platform service definitions exist under `platform/apps/` with one directory per role
- [x] Per-cluster composition exists under `platform/clusters/example-cluster/`
- [x] Three bootstrap templates present: cp-init, cp-join, wk-join
- [x] Architecture docs describe backend as orchestration owner
- [x] No migration/history language in docs (git handles history)
- [x] Multi-agent workflow set up in `reviews/` and `docs/ai/`
- [x] `kustomize build` passes for apps and example-cluster

## Scope

### Files to create

| File | Purpose |
|------|---------|
| `platform/apps/*/application.yaml` | ArgoCD Application per service role |
| `platform/apps/*/README.md` | Service contract per role |
| `platform/clusters/example-cluster/` | Example cluster overlay |
| `bootstrap/userdata-cp-join-v1.yaml` | Additional CP join template |
| `bootstrap/userdata-wk-join-v1.yaml` | Worker join template |
| `docs/ai/workflow.md` | Multi-agent review process |
| `docs/ai/*-role.md` | Per-agent role definitions |
| `reviews/` | Review workspace with templates |

### Files to modify

| File | Change |
|------|--------|
| `README.md` | Rewrite for platform service model + backend orchestrator |
| `AGENTS.md` | Add multi-agent workflow, backend ownership |
| `CLAUDE.md` | Add review role, backend ownership |
| `docs/architecture.md` | Backend orchestration model |
| `docs/onboarding.md` | Backend-driven onboarding flow |
| `docs/bootstrap-scope.md` | Remove migration narrative |
| `bootstrap/README.md` | Three-template contract, no migration tables |
| `bootstrap/env-vars.md` | Remove migration sections |

### Files to delete

| File | Reason |
|------|--------|
| `docs/ai/cursor-prompt.md` | Replaced by `cursor-role.md` |
| `docs/ai/claude-prompt.md` | Replaced by `claude-role.md` |
| `docs/ai/codex-prompt.md` | Replaced by `codex-role.md` |

## Out of scope

- Backend application code (lives in a separate repo)
- ArgoCD installation on the management cluster
- Real cluster configurations (only example-cluster)
- `.cursor/rules/` updates (tool-specific machine config)

## Risks

- Shared scripts duplicated across three userdata templates (intentional for cloud-init self-containment)
- `restore-gpu-logical-mac.sh` has hardcoded `bond0.2550` NIC — may need parameterization
