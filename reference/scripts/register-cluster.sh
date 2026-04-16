#!/usr/bin/env bash
# register-cluster.sh — Register a workload cluster with the management cluster
# and apply platform services via the app-of-apps pattern.
#
# Prerequisites:
#   - kubectl configured to reach the management cluster
#   - argocd CLI installed and logged in to the management cluster's ArgoCD
#   - workload cluster kubeconfig available at the path specified
#
# Usage:
#   ./scripts/register-cluster.sh \
#     --cluster-name wkld-prod-01 \
#     --kubeconfig /path/to/workload-kubeconfig \
#     --repo-url https://github.com/org/workload-cluster-add-on.git \
#     --revision main

set -euo pipefail

CLUSTER_NAME=""
KUBECONFIG_PATH=""
REPO_URL=""
REVISION="main"

usage() {
  echo "Usage: $0 --cluster-name NAME --kubeconfig PATH --repo-url URL [--revision REV]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --cluster-name) CLUSTER_NAME="$2"; shift 2 ;;
    --kubeconfig)   KUBECONFIG_PATH="$2"; shift 2 ;;
    --repo-url)     REPO_URL="$2"; shift 2 ;;
    --revision)     REVISION="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [[ -z "$CLUSTER_NAME" || -z "$KUBECONFIG_PATH" || -z "$REPO_URL" ]]; then
  echo "Error: --cluster-name, --kubeconfig, and --repo-url are required."
  usage
fi

if [[ ! -f "$KUBECONFIG_PATH" ]]; then
  echo "Error: Kubeconfig not found at $KUBECONFIG_PATH"
  exit 1
fi

echo "==> Step 1: Extract workload cluster API server endpoint"
CLUSTER_SERVER=$(kubectl --kubeconfig="$KUBECONFIG_PATH" config view \
  --minify -o jsonpath='{.clusters[0].cluster.server}')
echo "    Cluster API server: $CLUSTER_SERVER"

echo "==> Step 2: Register workload cluster with ArgoCD"
argocd cluster add \
  --kubeconfig "$KUBECONFIG_PATH" \
  --name "$CLUSTER_NAME" \
  "$(kubectl --kubeconfig="$KUBECONFIG_PATH" config current-context)"
echo "    Cluster registered: $CLUSTER_NAME"

echo "==> Step 3: Verify cluster configuration exists"
CLUSTER_DIR="platform/clusters/$CLUSTER_NAME"
if [[ ! -d "$CLUSTER_DIR" ]]; then
  echo "    WARNING: $CLUSTER_DIR does not exist in the repo."
  echo "    Copy platform/clusters/example-cluster/ to $CLUSTER_DIR and customize before proceeding."
  echo "    Example:"
  echo "      cp -r platform/clusters/example-cluster platform/clusters/$CLUSTER_NAME"
  echo "      # Edit cluster.yaml with: server: $CLUSTER_SERVER"
  exit 1
fi

echo "==> Step 4: Apply platform services to management cluster"
cat <<EOF | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ${CLUSTER_NAME}-platform
  namespace: argocd
  labels:
    cluster: ${CLUSTER_NAME}
    purpose: app-of-apps
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: ${REPO_URL}
    targetRevision: ${REVISION}
    path: platform/clusters/${CLUSTER_NAME}
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
EOF
echo "    Platform services applied: ${CLUSTER_NAME}-platform"

echo ""
echo "==> Done. Platform services will be applied to ${CLUSTER_NAME}."
echo "    Deployment order:"
echo "      Phase 1: cloud-controller (OCCM)"
echo "      Phase 2: cni (kube-ovn)"
echo "      Phase 3: storage (ceph-csi) + metrics (metrics-server)"
echo "      Phase 4: gpu (if enabled)"
echo ""
echo "    Monitor: argocd app list | grep ${CLUSTER_NAME}"
