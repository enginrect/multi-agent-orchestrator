# Cursor Implementation

**Task**: example-v1-platform-refactor
**Date**: 2026-04-14
**Author**: Cursor

## Summary

Refactored the repository to the v1.0 platform delivery model. Created
platform service definitions under `platform/apps/` (cloud-controller, cni,
storage, metrics, gpu), per-cluster composition under `platform/clusters/`,
three bootstrap templates (cp-init, cp-join, wk-join), and multi-agent
workflow infrastructure. Removed all migration/history language from docs.
Rewrote architecture and onboarding docs to describe backend as orchestration
owner and ArgoCD as delivery mechanism.

## Files changed

| Action | File | Description |
|--------|------|-------------|
| Created | `platform/apps/cloud-controller/application.yaml` | OCCM ArgoCD Application |
| Created | `platform/apps/cloud-controller/README.md` | Service contract |
| Created | `platform/apps/cni/application.yaml` | kube-ovn ArgoCD Application |
| Created | `platform/apps/cni/README.md` | Service contract |
| Created | `platform/apps/storage/application.yaml` | ceph-csi ArgoCD Application |
| Created | `platform/apps/storage/README.md` | Service contract |
| Created | `platform/apps/metrics/application.yaml` | metrics-server ArgoCD Application |
| Created | `platform/apps/metrics/README.md` | Service contract |
| Created | `platform/apps/gpu/gpu-operator.yaml` | GPU operator ArgoCD Application |
| Created | `platform/apps/gpu/network-operator.yaml` | Network operator ArgoCD Application |
| Created | `platform/apps/gpu/README.md` | Service contract |
| Created | `platform/apps/kustomization.yaml` | Base kustomization |
| Created | `platform/clusters/example-cluster/` | Full example cluster overlay |
| Created | `platform/secrets/*.yaml.example` | Secret templates |
| Created | `platform/app-of-apps.yaml` | Root Application template |
| Created | `bootstrap/userdata-cp-join-v1.yaml` | CP join template |
| Created | `bootstrap/userdata-wk-join-v1.yaml` | Worker join template |
| Rewritten | `README.md` | Backend orchestrator + platform services |
| Rewritten | `AGENTS.md` | Multi-agent workflow + backend ownership |
| Rewritten | `CLAUDE.md` | Claude review role + backend ownership |
| Rewritten | `docs/architecture.md` | Orchestration model, no migration language |
| Rewritten | `docs/onboarding.md` | Backend-driven 9-step flow |
| Rewritten | `docs/platform-services.md` | Backend-generated cluster values |
| Rewritten | `docs/bootstrap-scope.md` | Present-state only, no v0.7 comparisons |
| Rewritten | `bootstrap/README.md` | Three-template contract |
| Rewritten | `bootstrap/env-vars.md` | Clean variable reference |
| Updated | `bootstrap/userdata-cp-init-v1.yaml` | Stripped v0.7 comments |
| Created | `docs/ai/workflow.md` | Multi-agent review process |
| Created | `docs/ai/cursor-role.md` | Cursor role definition |
| Created | `docs/ai/claude-role.md` | Claude role definition |
| Created | `docs/ai/codex-role.md` | Codex role definition |
| Created | `reviews/` | Full review workspace with templates |
| Deleted | `docs/ai/cursor-prompt.md` | Replaced by role file |
| Deleted | `docs/ai/claude-prompt.md` | Replaced by role file |
| Deleted | `docs/ai/codex-prompt.md` | Replaced by role file |

## Validation results

- [x] `kustomize build platform/apps/` — passes
- [x] `kustomize build platform/clusters/example-cluster/` — passes
- [x] `bash -n scripts/register-cluster.sh` — passes
- [x] `rg` scan for migration language — no matches in docs/bootstrap/root files

## Self-review notes

- Shared scripts are duplicated across three userdata templates. This is
  intentional — cloud-init requires self-contained userdata per VM.
- `restore-gpu-logical-mac.sh` has `bond0.2550` hardcoded from the Notion spec.
  May need parameterization for different BM NIC configurations.
- Codex review checklist in `docs/ai/codex-role.md` references "v0.7" in the
  context of "verify no v0.7 comparisons exist" — this is correct usage as a
  review instruction, not migration narrative.

## Handoff to Claude

This task is ready for review. Please read `00-scope.md` and the files
listed above, then write `02-claude-review-cycle-1.md`.
