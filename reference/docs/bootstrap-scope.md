# Bootstrap Scope

This document defines the boundary between bootstrap (cloud-init/userdata) and
platform service delivery (ArgoCD from the management cluster).

## Bootstrap contract

The bootstrap layer consists of three userdata templates in `bootstrap/`. The backend
application renders these templates with cluster-specific variables and passes them as
cloud-init userdata when creating OpenStack VMs.

| Template | Node role | What it does |
|----------|-----------|-------------|
| `userdata-cp-init-v1.yaml` | First control plane | Full cluster init: certs, kubeadm init, produces kubeconfig |
| `userdata-cp-join-v1.yaml` | Additional control planes | kubeadm join as control plane, optional etcd disk, PBR |
| `userdata-wk-join-v1.yaml` | Workers (VM + BM) | kubeadm join as worker, BM GPU MAC restore if needed |

## What bootstrap handles

### Infrastructure setup (backend → OpenStack, before bootstrap)

- Network/subnet selection for workload VMs
- Security groups for control plane and worker nodes
- Kube-API load balancer setup (VIP + health checks)
- Anchor LB setup for baremetal worker use case

### VM userdata (rendered by backend, executed by cloud-init)

| Step | Description | Templates |
|------|-------------|-----------|
| OS packages | apt install of base dependencies | All three |
| Container runtime | containerd installation and configuration | All three |
| Kubernetes binaries | kubelet, kubeadm, kubectl at pinned version | All three |
| Kernel modules | `modprobe br_netfilter`, `overlay`, etc. | All three |
| Sysctl tuning | `net.bridge.bridge-nf-call-iptables`, `ip_forward`, etc. | All three |
| External etcd disk | Optional: format and mount dedicated etcd volume | cp-init, cp-join |
| Certificate init | CA/front-proxy/SA/etcd certificate generation | cp-init only |
| kubeadm init | First control plane cluster creation | cp-init only |
| kubeadm join (CP) | Additional control plane join | cp-join only |
| kubeadm join (worker) | Worker join (VM or BM path) | wk-join only |
| Provider network PBR | Policy-based routing for provider network | All three (conditional) |
| GPU MAC restore | BM GPU logical MAC address restore | wk-join only (conditional) |

### What bootstrap produces

- A running Kubernetes control plane (API server, scheduler, controller-manager, etcd)
- Worker nodes joined to the cluster
- Nodes in **NotReady** state (expected — no CNI yet)
- Admin kubeconfig on the first control plane node (backend extracts this)

## What platform services handle

Platform services are delivered by ArgoCD after the backend registers the cluster.
They are defined in `platform/apps/` and configured per-cluster in `platform/clusters/`.

| Service | Role | Deployment phase |
|---------|------|-----------------|
| cloud-controller | OpenStack cloud provider integration (OCCM) | Phase 1 |
| cni | Container networking (kube-ovn) | Phase 2 |
| storage | Ceph RBD persistent storage (ceph-csi) | Phase 3 |
| metrics | Kubernetes resource metrics (metrics-server) | Phase 3 |
| gpu | NVIDIA GPU compute + networking (optional, BM only) | Phase 4 |

## Why the separation

| Concern | Bootstrap | Platform services |
|---------|-----------|-------------------|
| Timing | Must happen at VM creation | Applied after cluster is running |
| Retry | Requires VM rebuild on failure | ArgoCD retries automatically |
| Version control | Embedded in VM userdata | Tracked in git with review |
| Rollback | Requires reprovisioning | Reverts to previous git state |
| Visibility | Logs on individual VMs | Management cluster dashboard |
| Multi-cluster | Per-cluster userdata rendering | Centralized configuration |
| Owner | Backend renders and injects | Backend registers; ArgoCD delivers |

## Baremetal worker considerations

For clusters with baremetal GPU workers:

- The anchor LB setup remains in the infrastructure layer (backend → OpenStack)
- Provider network PBR for baremetal nodes is in bootstrap userdata
- GPU MAC restore (`restore-gpu-logical-mac.sh`) is in wk-join userdata (host-level)
- GPU operator and network operator are delivered as platform services (phase 4)
