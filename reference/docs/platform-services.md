# Platform Services

## Service composition model

Each platform service is defined as a self-contained unit under `platform/apps/<role>/`:

```
platform/apps/
├── kustomization.yaml           # Composes standard services
├── cloud-controller/
│   ├── application.yaml         # ArgoCD Application (Helm chart ref + defaults)
│   └── README.md                # Service contract: purpose, deps, secrets, config
├── cni/
│   ├── application.yaml
│   └── README.md
├── storage/
│   ├── application.yaml
│   └── README.md
├── metrics/
│   ├── application.yaml
│   └── README.md
└── gpu/                         # Optional (not in base kustomization)
    ├── gpu-operator.yaml
    ├── network-operator.yaml
    └── README.md
```

Each service directory is a responsibility boundary:
- **One directory per role**, not per implementation
- **application.yaml** defines the ArgoCD Application with upstream Helm chart source and defaults
- **README.md** is the service contract: what it does, what it depends on, what config it needs

## Cluster composition

Each cluster assembles services from the shared definitions and adds its own values.
The backend application generates these files during the onboarding flow — they are
not manually edited in production.

```
platform/clusters/<name>/
├── kustomization.yaml           # Assembly: base + optional GPU + value patches
├── cluster.yaml                 # Cluster identity: API endpoint, labels
└── values/                      # Per-service configuration (backend-generated)
    ├── cloud-controller.yaml    # OCCM-specific overrides
    ├── cni.yaml                 # kube-ovn-specific overrides
    ├── storage.yaml             # ceph-csi-specific overrides
    ├── metrics.yaml             # metrics-server-specific overrides
    ├── gpu-operator.yaml        # GPU overrides (only if GPU enabled)
    └── network-operator.yaml    # Network operator overrides (only if GPU enabled)
```

The `kustomization.yaml` uses kustomize patches to merge cluster-specific values
into the base service definitions. The backend populates values from its
provisioning context (OpenStack tenant, Ceph config, network CIDRs, etc.).

## How services are delivered

The management cluster's ArgoCD uses an app-of-apps pattern:

1. A root Application points to `platform/clusters/<name>/`
2. Kustomize renders the overlay, producing child Application resources
3. Each child Application deploys its Helm chart to the workload cluster
4. Deployment phases (sync waves) enforce dependency ordering

## Why this pattern

| Alternative | Why not |
|-------------|---------|
| ApplicationSet | Powerful for homogeneous clusters; our clusters vary (GPU, network modes) |
| Helm umbrella chart | Wrapping layer obscures actual upstream charts |
| Plain manifests | Upstream charts are complex; raw manifests drift from releases |
| Single values file | Hard to review when one file owns all cluster config |

## Adding a new service

1. Create `platform/apps/<role>/` with `application.yaml` and `README.md`
2. Set the sync-wave annotation appropriate for its dependencies
3. Add it to `platform/apps/kustomization.yaml` (or leave out for optional services)
4. Create a default value override in `platform/clusters/example-cluster/values/`
5. Reference the patch in the example cluster's `kustomization.yaml`
6. Document the service in this file and `docs/architecture.md`

## Removing a service

1. Remove it from `platform/apps/kustomization.yaml`
2. Remove the patch reference from each cluster's `kustomization.yaml`
3. ArgoCD will prune resources from workload clusters (automated sync + prune enabled)

## Secrets handling

Secrets are NOT stored in this repo. Two approaches:

1. **Pre-created secrets**: Create Kubernetes Secrets on the workload cluster before
   services are applied. Templates are in `platform/secrets/`.
2. **External Secrets Operator**: Deploy ESO on the workload cluster with
   ExternalSecret resources that pull from Vault, KMS, etc.

## Version pinning

Helm chart versions use wildcard patch ranges (e.g., `2.33.*`) in the base.
For production clusters, pin to exact versions in the cluster value override:

```yaml
spec:
  source:
    targetRevision: "2.33.1"
```
