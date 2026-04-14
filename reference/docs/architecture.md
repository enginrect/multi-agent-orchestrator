# Architecture

## Overview

This repository manages platform services for Kubernetes workload clusters
running on OpenStack. The backend application orchestrates the end-to-end
lifecycle: infrastructure provisioning, node bootstrap, kubeconfig extraction,
cluster registration, and platform service configuration. ArgoCD on the
management cluster is the delivery mechanism that applies services to
registered workload clusters through continuous reconciliation.

```
┌─────────────────────────────────────────────────────────────────┐
│    OpenStack Environment                                        │
│                                                                 │
│  ┌────────────────────────┐                                     │
│  │   Backend Application  │  ← orchestration owner              │
│  │                        │                                     │
│  │  1. Provision infra    │                                     │
│  │  2. Render userdata    │─── uses templates from bootstrap/   │
│  │  3. Create VMs         │                                     │
│  │  4. Extract kubeconfig │                                     │
│  │  5. Generate values    │─── writes to platform/clusters/     │
│  │  6. Register to ArgoCD │                                     │
│  └────────┬───────────────┘                                     │
│           │                                                     │
│  ┌────────▼───────────────┐                                     │
│  │  Management Cluster    │                                     │
│  │  ┌──────────────────┐  │                                     │
│  │  │     ArgoCD       │──┼── watches this repository           │
│  │  │  (delivery only) │  │                                     │
│  │  └───────┬──────────┘  │                                     │
│  └──────────┼─────────────┘                                     │
│             │ applies services                                  │
│             ▼                                                   │
│  ┌──────────────────────┐                                       │
│  │  Workload Cluster    │                                       │
│  │  ┌────┐┌────┐┌────┐ │                                       │
│  │  │OCCM││kOVN││Ceph│ │                                       │
│  │  └────┘└────┘└────┘ │                                       │
│  └──────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────┘
```

## Orchestration model

The backend application is the single orchestration owner. Every other
component (OpenStack, cloud-init, ArgoCD) is a mechanism it drives.

| Step | Owner | Action | Mechanism |
|------|-------|--------|-----------|
| 1 | Backend | Provision infrastructure | OpenStack API (networks, subnets, SGs, LBs) |
| 2 | Backend | Render userdata from templates | Templates in `bootstrap/` |
| 3 | Backend | Create VMs with rendered userdata | OpenStack Nova API |
| 4 | cloud-init | Execute bootstrap on each VM | Runs setup-kubernetes, kubeadm init/join |
| 5 | Backend | Extract admin kubeconfig from cp-init | SSH or API to first control plane |
| 6 | Backend | Generate cluster-specific values | Writes to `platform/clusters/<name>/` |
| 7 | Backend | Register workload cluster to ArgoCD | ArgoCD API or `scripts/register-cluster.sh` |
| 8 | ArgoCD | Deliver platform services | Watches this repo, syncs to workload cluster |

**Key distinction**: The backend decides *what* happens and *when*. ArgoCD
decides *how* to keep the desired state converged on the workload cluster.

## Responsibility split

| Layer | Owner | What |
|-------|-------|------|
| Infrastructure | Backend → OpenStack | Networks, subnets, security groups, LBs, VMs |
| Node bootstrap | Backend → cloud-init | Userdata rendering, OS prep, kubeadm init/join |
| Kubeconfig extraction | Backend | Extract admin kubeconfig after cp-init |
| Cluster registration | Backend → ArgoCD | Register workload cluster, create app-of-apps |
| Cluster configuration | Backend → this repo | Generate and commit per-cluster values |
| Platform services | ArgoCD → this repo | Deliver services to registered workload clusters |

## What this repo owns

- Userdata templates for node bootstrap (`bootstrap/`)
- Platform service definitions (one per role under `platform/apps/`)
- Per-cluster composition and value overrides (`platform/clusters/`)
- Helm chart references, default values, and deployment ordering
- Secret templates (examples only, never real credentials)
- Cluster registration helper script
- Architecture and operations documentation

## What this repo does NOT own

- The backend application itself
- OpenStack infrastructure lifecycle (create/delete)
- ArgoCD installation on the management cluster
- Secret storage backend (Vault, KMS, etc.)
- Actual secret values

## Directory structure

```
workload-cluster-add-on/
├── platform/                    # Platform service management
│   ├── apps/                    # Service definitions (one dir per role)
│   │   ├── cloud-controller/    # Phase 1: OCCM
│   │   ├── cni/                 # Phase 2: kube-ovn
│   │   ├── storage/             # Phase 3: ceph-csi
│   │   ├── metrics/             # Phase 3: metrics-server
│   │   └── gpu/                 # Phase 4: GPU + network operators
│   ├── clusters/                # Per-cluster composition (backend-generated)
│   └── secrets/                 # Secret templates
├── bootstrap/                   # Userdata templates (cp-init, cp-join, wk-join)
├── scripts/                     # Operational helpers
└── docs/                        # Documentation
```

## Deployment phases

Services are applied in a strict dependency order:

| Phase | Service | Implementation | Reason |
|-------|---------|----------------|--------|
| 1 | cloud-controller | OCCM | Nodes need cloud-provider metadata before CNI |
| 2 | cni | kube-ovn | CNI must be operational before network-dependent services |
| 3 | storage | ceph-csi | Persistent storage, depends on networking |
| 3 | metrics | metrics-server | Resource metrics, depends on networking |
| 4 | gpu-networking | network-operator | Optional, NVIDIA networking for BM GPU clusters |
| 4 | gpu | gpu-operator | Optional, NVIDIA GPU support for BM GPU clusters |

## Service version matrix

Pinned versions validated for production use.
These versions are set in `platform/apps/*/application.yaml` and apply across K8s 1.32–1.34.

| Service | Implementation | Version | Chart source |
|---------|---------------|---------|-------------|
| cloud-controller | OCCM | **v1.33.1** (chart ~2.33.x) | `kubernetes.github.io/cloud-provider-openstack` |
| cni | kube-ovn | **v1.14.13** | `kubeovn.github.io/kube-ovn` |
| storage | ceph-csi-rbd | **v3.16.0** | `ceph.github.io/csi-charts` |
| metrics | metrics-server | **v0.8.0** (chart ~3.12.x) | `kubernetes-sigs.github.io/metrics-server` |
| gpu-networking | network-operator | **v25.10.0** | `helm.ngc.nvidia.com/nvidia` |
| gpu | gpu-operator | **v25.10.1** | `helm.ngc.nvidia.com/nvidia` |

## Bootstrap contract

The `bootstrap/` directory contains three userdata templates that the backend
renders for each node role. See `bootstrap/README.md` for the full three-template
contract and `bootstrap/env-vars.md` for the variable mapping.

| Template | Role | Produces |
|----------|------|----------|
| `userdata-cp-init-v1.yaml` | First control plane | Running cluster + admin kubeconfig |
| `userdata-cp-join-v1.yaml` | Additional control planes | HA control plane |
| `userdata-wk-join-v1.yaml` | Workers (VM + BM) | Joined worker nodes |

## Environments

The management cluster and workload clusters run in the same OpenStack environment
but under separate tenants. Environment-specific values (OpenStack auth URLs, Ceph
monitors, network CIDRs) are set per-cluster in `platform/clusters/<name>/values/`
by the backend when it generates the cluster configuration.
