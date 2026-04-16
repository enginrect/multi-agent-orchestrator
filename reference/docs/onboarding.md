# Onboarding a New Workload Cluster

## Overview

The backend application orchestrates the full onboarding flow. Each step below
describes what the backend does, what mechanism it uses, and what the expected
outcome is. Human operators do not need to manually edit YAML files or run
registration scripts — the backend handles this end-to-end.

## Backend-driven onboarding flow

### Step 1: Provision infrastructure

The backend creates OpenStack resources for the workload cluster:

- Tenant network and subnets
- Security groups (control plane, worker)
- Kube-API load balancer (VIP + health checks)
- Anchor LB for baremetal workers (if applicable)

**Mechanism**: OpenStack API (Neutron, Nova, Octavia)

### Step 2: Render userdata and create VMs

The backend renders the three userdata templates from `bootstrap/` with
cluster-specific variables and creates VMs:

| Template | Rendered for | Key variables |
|----------|-------------|---------------|
| `userdata-cp-init-v1.yaml` | First control plane | `CLUSTER_NAME`, `K8S_VERSION`, `KUBEAPI_LB_FIP`, `CERT_KEY` |
| `userdata-cp-join-v1.yaml` | Additional control planes | `JOIN_TOKEN`, `JOIN_HASH`, `CERT_KEY` |
| `userdata-wk-join-v1.yaml` | Workers | `NODE_TYPE`, `JOIN_TOKEN`, `JOIN_HASH`, `ENABLE_GPU_SETUP` |

**Mechanism**: OpenStack Nova API with rendered userdata

### Step 3: Wait for bootstrap completion

cloud-init on each VM executes the bootstrap scripts. The backend monitors
completion by polling the control plane API server.

**Outcome**: Kubernetes cluster is running. Nodes are in `NotReady` state
(expected — no CNI yet). Admin kubeconfig exists on the first control plane.

### Step 4: Extract kubeconfig

The backend extracts the admin kubeconfig from the first control plane node.

**Mechanism**: SSH to cp-init node, read `/etc/kubernetes/admin.conf`

### Step 5: Generate cluster configuration

The backend generates the cluster-specific configuration and commits it to
this repository:

```
platform/clusters/<cluster-name>/
├── kustomization.yaml           # Assembly: base + optional GPU + value patches
├── cluster.yaml                 # API endpoint, cluster labels
└── values/
    ├── cloud-controller.yaml    # OCCM: clusterName
    ├── cni.yaml                 # kube-ovn: MASTER_NODES, IFACE
    ├── storage.yaml             # ceph-csi: clusterID, monitors, pool
    ├── metrics.yaml             # metrics-server: replicas
    ├── gpu-operator.yaml        # (if GPU cluster)
    └── network-operator.yaml    # (if GPU cluster)
```

The backend sets:
- `cluster.yaml` → `destination.server` to the kube-API VIP
- `cluster.yaml` → `cluster` label to the cluster name
- `values/cloud-controller.yaml` → `clusterName` from the provisioning context
- `values/cni.yaml` → `MASTER_NODES` from the control plane IPs it just created
- `values/storage.yaml` → `clusterID`, `monitors`, `pool` from the environment config
- `kustomization.yaml` → GPU resources uncommented if the cluster has GPU workers

**Mechanism**: Git commit to this repository (from the backend's service account)

### Step 6: Pre-create secrets on the workload cluster

Before platform services can start, secrets must exist on the workload cluster.
The backend creates them using the extracted kubeconfig:

```bash
# Cloud controller credentials
kubectl create secret generic cloud-config \
  --from-file=cloud.conf=/path/to/rendered-cloud.conf \
  -n kube-system

# Storage credentials
kubectl create namespace ceph-csi
kubectl create secret generic csi-rbd-secret \
  --from-literal=userID=kubernetes \
  --from-literal=userKey=<ceph-user-key> \
  -n ceph-csi
```

**Mechanism**: kubectl against the workload cluster using the extracted kubeconfig

See `platform/secrets/` for full Secret templates.

### Step 7: Register workload cluster to ArgoCD

The backend registers the workload cluster with the management cluster's ArgoCD
and creates the root Application:

1. Register the cluster endpoint and credentials with ArgoCD
2. Apply the app-of-apps Application pointing to `platform/clusters/<cluster-name>/`

**Mechanism**: ArgoCD API or `scripts/register-cluster.sh`

### Step 8: ArgoCD delivers platform services

ArgoCD detects the new cluster configuration in the repository and applies
platform services in phase order:

1. **cloud-controller** (phase 1) — ~1 min — nodes get cloud-provider metadata
2. **cni** (phase 2) — ~3 min — CNI pods start, nodes become Ready
3. **storage + metrics** (phase 3) — ~2 min — persistent volumes and metrics
4. **gpu** (phase 4, if enabled) — ~5 min — GPU driver and toolkit

**Mechanism**: ArgoCD continuous reconciliation (sync waves)

**Total**: approximately 10–15 minutes from registration to fully operational.

### Step 9: Backend confirms readiness

The backend polls the workload cluster until all nodes are `Ready` and
required platform services are healthy, then marks the cluster as operational
in its own state.

## Manual override

For development or emergency scenarios, operators can perform steps 5–7 manually:

```bash
# Copy the example cluster
cp -r platform/clusters/example-cluster platform/clusters/my-cluster

# Edit cluster identity and values
# (In production, the backend generates these)
vi platform/clusters/my-cluster/cluster.yaml
vi platform/clusters/my-cluster/values/*.yaml

# Commit
git add platform/clusters/my-cluster && git commit -m "[feat] Add my-cluster"

# Register
./scripts/register-cluster.sh \
  --cluster-name my-cluster \
  --kubeconfig /path/to/kubeconfig \
  --repo-url https://github.com/<org>/workload-cluster-add-on.git
```

## Verification

```bash
# On the management cluster
argocd app list | grep "<cluster-name>"
argocd app get "<cluster-name>-platform"

# On the workload cluster
kubectl --kubeconfig=/path/to/kubeconfig get nodes
kubectl --kubeconfig=/path/to/kubeconfig get pods -A
```
