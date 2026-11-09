#!/usr/bin/env bash
#
# Demo: Crossplane Composition best practices
#
# Proves, live on a throwaway kind cluster with a mocked AWS (moto):
#
#   1. IN-CLUSTER RENDER  - Helm ships the Composition; function-go-templating renders it
#                           inside the cluster with the OBSERVED state of every child.
#   2. OBSERVE            - provider-kubernetes Object (managementPolicies: Observe) reads
#                           status.atProvider.arn of a bucket MyApp does not own.
#   3. DEPENDENCIES       - function-sequencer holds the attachment until role + policy are Ready.
#   4. READY              - function-auto-ready: MyApp is Ready only when every child is Ready.
#   5. ARGOCD HEALTH      - function-status-transformer lifts a child's AWS error onto MyApp.
#   6. TROUBLESHOOT       - `crossplane render` replays the pipeline on a laptop, no cluster.
#
# Usage:
#   ./demo/demo.sh                  interactive, Enter to continue (default)
#   DEMO_SLEEP=5  ./demo/demo.sh    auto-advance with 5-second pauses
#   DEMO_SLEEP=0  ./demo/demo.sh    fully unattended
#   ./demo/demo.sh --keep           skip teardown at the end
#   ./demo/demo.sh --cleanup        teardown only (cleans up leftover clusters/dirs)
#
# Safety:
#   * KUBECONFIG always points to /tmp/crossplane-bp-demo/kubeconfig - never ~/.kube/config.
#   * Every kubectl/helm call passes --context kind-crossplane-bp.
#   * No real AWS: every provider call goes to moto inside the cluster.
#   * On unexpected failure: prints the cleanup command, does NOT destroy the cluster.
#
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

CLUSTER=crossplane-bp
CTX="kind-$CLUSTER"
DEMO_DIR=/tmp/crossplane-bp-demo
CROSSPLANE_VERSION=2.4.2
APP_NS=team-payments
SLEEP=${DEMO_SLEEP:--1}

KEEP=false
CLEANUP_ONLY=false
for _arg in "$@"; do
  case "$_arg" in
    --keep)    KEEP=true ;;
    --cleanup) CLEANUP_ONLY=true ;;
    *) printf 'Unknown flag: %s\nUsage: %s [--keep|--cleanup]\n' "$_arg" "$0" >&2; exit 1 ;;
  esac
done

# ── colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  B=$(tput bold); R=$(tput setaf 1); G=$(tput setaf 2); Y=$(tput setaf 3)
  C=$(tput setaf 6); N=$(tput sgr0)
else
  B=""; R=""; G=""; Y=""; C=""; N=""
fi

RULE=$(printf '%0.s_' {1..72})
section() { printf '\n%s%s%s\n%s== %s%s\n' "$C" "$RULE" "$N" "$B" "$*" "$N"; }
explain() { printf '%s   %s%s\n' "$Y" "$*" "$N"; }
cmd()     { printf '%s   $ %s%s\n' "$C" "$*" "$N"; }
pause() {
  if   [[ "$SLEEP" == "-1" ]]; then printf '\n%s   [Enter to continue]%s' "$C" "$N"; read -r
  elif [[ "$SLEEP" != "0"  ]]; then sleep "$SLEEP"
  fi
}
die() { printf '%s!! %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# takeaway "line" "line" ... - the plain-language summary of the step the audience
# just watched, in a bold box so it reads from the back of the room. First line is
# the heading. Content must be pure ASCII and <= 69 chars: the padding below counts
# characters, so a stray em dash or arrow would skew the right-hand border.
BOX_RULE=$(printf '%0.s=' {1..70})
takeaway() {
	local _line
	printf '\n%s%s+%s+%s\n' "$B" "$G" "$BOX_RULE" "$N"
	for _line in "$@"; do
		printf '%s%s|%s|%s\n' "$B" "$G" "$(printf ' %-69s' "${_line:0:69}")" "$N"
	done
	printf '%s%s+%s+%s\n' "$B" "$G" "$BOX_RULE" "$N"
}

# ── cleanup ───────────────────────────────────────────────────────────────────
do_cleanup() {
  if kind get clusters 2>/dev/null | grep -q "^${CLUSTER}$"; then
    cmd "kind delete cluster --name $CLUSTER"
    # kind exits non-zero when ~/.kube/config is read-only (e.g. in a container);
    # the nodes ARE deleted either way.
    kind delete cluster --name "$CLUSTER" 2>/dev/null || true
  fi
  if [[ -d "$DEMO_DIR" ]]; then
    cmd "rm -rf $DEMO_DIR"
    rm -rf "$DEMO_DIR" 2>/dev/null || true
  fi
}

# ── trap: print cleanup hint, do NOT auto-destroy ─────────────────────────────
_on_error() {
  printf '\n%s!! Unexpected failure - cluster and %s are intact.\n' "$R" "$DEMO_DIR"
  printf '   Clean up later with:  %s --cleanup%s\n\n' "$0" "$N"
}
trap '_on_error; exit 1' ERR
trap '_on_error; exit 1' INT

export KUBECONFIG="${DEMO_DIR}/kubeconfig"

if [[ "$CLEANUP_ONLY" == "true" ]]; then
  section "Cleanup"
  do_cleanup
  printf '%s   Done.%s\n' "$G" "$N"
  exit 0
fi

# ── reusable helpers ──────────────────────────────────────────────────────────
k() { kubectl --context "$CTX" "$@"; }

# Wait until a jsonpath on a resource equals a value. Prints dots while waiting.
wait_for() {
  local what="$1" jsonpath="$2" want="$3" budget="${4:-300}" ns_args=("${@:5}") elapsed=0 got=""
  printf '   Waiting for %s' "$what"
  while [[ $elapsed -lt $budget ]]; do
    got=$(k get "${ns_args[@]}" -o jsonpath="$jsonpath" 2>/dev/null) || got=""
    [[ "$got" == "$want" ]] && { printf ' ok\n'; return 0; }
    printf '.'
    sleep 3
    elapsed=$((elapsed + 3))
  done
  printf '\n'
  die "Timed out waiting for $what (wanted '$want', got '$got')"
}

# The composed resources of the MyApp, as one table.
print_children() {
  cmd "kubectl -n $APP_NS get buckets,roles,policies,rolepolicyattachments,objects"
  k -n "$APP_NS" get \
    buckets.s3.aws.m.upbound.io,roles.iam.aws.m.upbound.io,policies.iam.aws.m.upbound.io,rolepolicyattachments.iam.aws.m.upbound.io,objects.kubernetes.m.crossplane.io \
    -o custom-columns='KIND:.kind,NAME:.metadata.name,SYNCED:.status.conditions[?(@.type=="Synced")].status,READY:.status.conditions[?(@.type=="Ready")].status,ARN:.status.atProvider.arn' \
    2>/dev/null || true
}

print_myapp() {
  cmd "kubectl -n $APP_NS get myapp checkout"
  k -n "$APP_NS" get myapps.crossplane.best-practice.com checkout 2>/dev/null || true
}

# =============================================================================
# STEP 0: PREFLIGHT
# =============================================================================
section "Step 0 - Preflight"

for _tool in docker kind kubectl helm jq yq crossplane; do
  command -v "$_tool" >/dev/null 2>&1 \
    || die "$_tool not found on PATH - install it and retry"
done
explain "Required tools: docker kind kubectl helm jq yq crossplane - all present."

yq --version 2>&1 | grep -qi mikefarah \
  || die "yq is not mikefarah/yq v4 (found: $(yq --version 2>&1 | head -1)) - install with: brew install yq"

_xpv=$(crossplane version --client 2>/dev/null | grep -o 'v[0-9][0-9.]*' | head -1)
[[ "$_xpv" == v2.* ]] \
  || die "crossplane CLI must be v2.x (found: ${_xpv:-none}) - curl -sL https://raw.githubusercontent.com/crossplane/crossplane/main/install.sh | sh"

cmd "docker info >/dev/null"
docker info >/dev/null 2>&1 \
  || die "Docker daemon is not running or not accessible by the current user"

printf '   docker:     '; docker --version
printf '   kind:       '; kind version 2>/dev/null | head -1
printf '   kubectl:    %s\n' "$(kubectl version --client -o json 2>/dev/null | jq -r '.clientVersion.gitVersion')"
printf '   helm:       '; helm version --short 2>/dev/null
printf '   crossplane: %s\n' "$_xpv"

takeaway \
	"CHECKPOINT 0 - TOOLING IS READY" \
	"" \
	"Every CLI this demo needs is installed, and Docker is running." \
	"Nothing has been created yet. No AWS account is needed at all." \
	"" \
	"Evidence: the version lines above, printed from your machine."

pause

# =============================================================================
# STEP 1: KIND CLUSTER
# =============================================================================
section "Step 1 - Create kind cluster ($CLUSTER)"

mkdir -p "$DEMO_DIR"
explain "KUBECONFIG=$KUBECONFIG - ~/.kube/config is never read or written."

if [[ -f /.dockerenv ]]; then
  explain "Container environment detected - API server on 0.0.0.0, reached via host.docker.internal."
  cmd "kind create cluster --name $CLUSTER --kubeconfig $KUBECONFIG --config - <<'EOF'"
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --config - <<'KINDEOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  apiServerAddress: "0.0.0.0"
kubeadmConfigPatches:
- |
  kind: ClusterConfiguration
  apiServer:
    certSANs:
    - host.docker.internal
    - localhost
    - 127.0.0.1
nodes:
- role: control-plane
KINDEOF
  _kube_port=$(yq '.clusters[0].cluster.server' "$KUBECONFIG"); _kube_port="${_kube_port##*:}"
  kubectl config set-cluster "$CTX" --server "https://host.docker.internal:${_kube_port}" \
    --kubeconfig "$KUBECONFIG" >/dev/null
else
  cmd "kind create cluster --name $CLUSTER --kubeconfig $KUBECONFIG"
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG"
fi

cmd "kubectl --context $CTX get ns"
k get ns || die "Cluster is not reachable after kind create - check the kubeconfig"

takeaway \
	"CHECKPOINT 1 - AN EMPTY KUBERNETES CLUSTER" \
	"" \
	"A throwaway single-node cluster is running inside Docker." \
	"No Crossplane, no providers, no AWS mock yet." \
	"" \
	"Evidence: 'kubectl get ns' above listed only built-in namespaces."

pause

# =============================================================================
# STEP 2: CROSSPLANE
# =============================================================================
section "Step 2 - Install Crossplane v$CROSSPLANE_VERSION"

cmd "helm install crossplane crossplane --repo https://charts.crossplane.io/stable --version $CROSSPLANE_VERSION -n crossplane-system --create-namespace --wait"
helm --kube-context "$CTX" install crossplane crossplane \
  --repo https://charts.crossplane.io/stable --version "$CROSSPLANE_VERSION" \
  -n crossplane-system --create-namespace --wait --timeout 5m

k -n crossplane-system get deploy

takeaway \
	"CHECKPOINT 2 - CROSSPLANE v2 IS RUNNING" \
	"" \
	"The core controllers are up. Crossplane on its own knows nothing" \
	"about AWS - providers and functions are packages we add next." \
	"" \
	"Evidence: crossplane and crossplane-rbac-manager are Available."

pause

# =============================================================================
# STEP 3: PROVIDERS, FUNCTIONS AND THE AWS MOCK
# =============================================================================
section "Step 3 - Providers, functions and a fake AWS (moto)"

explain "AWS mock: moto runs in-cluster and answers IAM / S3 / STS calls."
cmd "kubectl apply -f crossplane/aws-mock.yaml"
k apply -f "$ROOT/crossplane/aws-mock.yaml"

# The S3 Control API (used for bucket tags) prepends the account ID to the host:
# 000000000000.moto.aws-mock.svc... - so resolve *.moto.aws-mock... to moto too.
explain "CoreDNS: resolve <account-id>.moto.aws-mock.svc.cluster.local to moto as well."
cmd "kubectl -n kube-system edit cm coredns   # + rewrite name regex (.+)\\.moto\\.aws-mock... moto.aws-mock..."
k -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' \
  | awk '{print} /^\.:53 \{/{print "    rewrite name regex (.+)\\.moto\\.aws-mock\\.svc\\.cluster\\.local moto.aws-mock.svc.cluster.local answer auto"}' \
  > "$DEMO_DIR/Corefile"
k -n kube-system create cm coredns --from-file=Corefile="$DEMO_DIR/Corefile" --dry-run=client -o yaml \
  | k replace -f - >/dev/null
k -n kube-system rollout restart deploy/coredns >/dev/null
k -n kube-system rollout status deploy/coredns --timeout=120s

explain "Providers: provider-aws-iam, provider-aws-s3 (+ family) and provider-kubernetes."
cmd "kubectl apply -f crossplane/providers.yaml"
k apply -f "$ROOT/crossplane/providers.yaml"

explain "Functions: go-templating, auto-ready, sequencer, status-transformer."
cmd "kubectl apply -f crossplane/functions.yaml"
k apply -f "$ROOT/crossplane/functions.yaml"

cmd "kubectl wait providers.pkg.crossplane.io,functions.pkg.crossplane.io --all --for=condition=Healthy --timeout=600s"
k wait providers.pkg.crossplane.io --all --for=condition=Healthy --timeout=600s
k wait functions.pkg.crossplane.io --all --for=condition=Healthy --timeout=600s
k -n aws-mock rollout status deploy/moto --timeout=300s

explain "Point the AWS provider at moto, give provider-kubernetes read-only access to Buckets."
cmd "kubectl apply -f crossplane/provider-configs.yaml"
k apply -f "$ROOT/crossplane/provider-configs.yaml"

cmd "kubectl get providers,functions"
k get providers.pkg.crossplane.io,functions.pkg.crossplane.io

takeaway \
	"CHECKPOINT 3 - THE KITCHEN IS EQUIPPED" \
	"" \
	"  providers   talk to AWS (here: moto) and to Kubernetes itself" \
	"  functions   the 4 pipeline steps our Composition will chain" \
	"" \
	"ClusterProviderConfig 'default' sends every SDK call to moto, so" \
	"resources really get created and really report ARNs back." \
	"" \
	"Evidence: every provider and function above is HEALTHY=True."

pause

# =============================================================================
# STEP 4: THE PLATFORM API (XRD + COMPOSITION) - HELM DOES NOT RENDER IT
# =============================================================================
section "Step 4 - Install the platform API with Helm (XRD + Composition)"

cmd "helm install myapp-composition charts/myapp-composition"
helm --kube-context "$CTX" install myapp-composition "$ROOT/charts/myapp-composition"

wait_for "XRD to be Established" '{.status.conditions[?(@.type=="Established")].status}' True 120 \
  compositeresourcedefinitions.apiextensions.crossplane.io myapps.crossplane.best-practice.com

explain "What Helm put in the cluster still contains the RAW template. Helm did not render it -"
explain "the template uses [[ ]] delimiters, which Helm ignores:"
cmd "kubectl get composition myapp -o yaml | yq '.spec.pipeline[0].input | (.delims, .inline.template)' | head -14"
k get compositions.apiextensions.crossplane.io myapp -o yaml \
  | yq '.spec.pipeline[0].input | (.delims, .inline.template)' | sed -n '1,14p'

explain "The XRD is a typed API - developers get kubectl explain, enums and defaults:"
cmd "kubectl explain myapp.spec"
k explain myapps.spec --api-version=crossplane.best-practice.com/v1alpha1 2>/dev/null | sed -n '1,30p' || true

takeaway \
	"CHECKPOINT 4 - IN-CLUSTER RENDER" \
	"" \
	"Helm shipped the Composition as-is. The [[ ]] placeholders are" \
	"still there: Crossplane renders them INSIDE the cluster on every" \
	"reconcile, with the live XR AND the observed state of each child." \
	"" \
	"  helm template   renders once, on a laptop, blind to the cloud" \
	"  go-templating   renders every reconcile, sees ARNs/status/IDs" \
	"" \
	"Evidence: '.observed.resources' in the template printed above."

pause

# =============================================================================
# STEP 5: AN EXISTING RESOURCE WE DO NOT OWN
# =============================================================================
section "Step 5 - A pre-existing platform bucket (owned by someone else)"

cmd "kubectl apply -f crossplane/shared-logs-bucket.yaml"
k apply -f "$ROOT/crossplane/shared-logs-bucket.yaml"
wait_for "platform/platform-logs to be Ready" '{.status.conditions[?(@.type=="Ready")].status}' True 300 \
  -n platform buckets.s3.aws.m.upbound.io platform-logs

cmd "kubectl -n platform get bucket platform-logs -o jsonpath='{.status.atProvider.arn}'"
LOGS_ARN=$(k -n platform get buckets.s3.aws.m.upbound.io platform-logs -o jsonpath='{.status.atProvider.arn}')
printf '   %s%s%s\n' "$G" "$LOGS_ARN" "$N"

takeaway \
	"CHECKPOINT 5 - SOMEONE ELSE'S RESOURCE EXISTS" \
	"" \
	"The platform team owns a shared logs bucket in namespace 'platform'." \
	"Every app must be allowed to write to it - so every app needs its" \
	"ARN. Nobody should copy that ARN into values files by hand." \
	"" \
	"Evidence: status.atProvider.arn above, reported by the provider."

pause

# =============================================================================
# STEP 6: THE DEVELOPER ORDERS A MyApp
# =============================================================================
section "Step 6 - Developer deploys MyApp (namespaced, Crossplane v2)"

cmd "helm template checkout charts/myapp-claim -n $APP_NS"
helm template checkout "$ROOT/charts/myapp-claim" -n "$APP_NS" | sed '1,2d'

cmd "helm install checkout charts/myapp-claim -n $APP_NS --create-namespace"
helm --kube-context "$CTX" install checkout "$ROOT/charts/myapp-claim" -n "$APP_NS" --create-namespace

explain "First reconcile: role, bucket and the Observe-only Object only. The policy needs"
explain "two ARNs that do not exist until AWS answers; the attachment waits for both."
explain "moto answers in milliseconds, so the ordering shows up in the events, not a table:"
wait_for "MyApp to be Ready" '{.status.conditions[?(@.type=="Ready")].status}' True 600 \
  -n "$APP_NS" myapps.crossplane.best-practice.com checkout

cmd "kubectl -n $APP_NS events --for myapp/checkout"
k -n "$APP_NS" events --for myapps.crossplane.best-practice.com/checkout 2>/dev/null \
  | grep -E 'Delaying|not yet ready' | cut -c1-160 || true

takeaway \
	"CHECKPOINT 6 - ONE ORDER, FIVE RESOURCES, IN THE RIGHT ORDER" \
	"" \
	"The developer wrote 4 lines. Crossplane built, over several" \
	"reconciles: Bucket, Role, Observe Object, Policy, Attachment." \
	"" \
	"  function-sequencer   attachment waited for role + policy" \
	"  function-auto-ready  MyApp turned Ready only after ALL children" \
	"" \
	"Evidence: the 'Delaying creation' events, then MyApp READY=True."

pause

# =============================================================================
# STEP 7: THE PAYOFF - LIVE DATA FLOWED BETWEEN RESOURCES
# =============================================================================
section "Step 7 - The payoff: observed status flowed into the policy"

print_children
print_myapp

cmd "kubectl -n $APP_NS get object checkout-shared-logs -o jsonpath='{.status.atProvider.manifest.status.atProvider.arn}'"
_observed=$(k -n "$APP_NS" get objects.kubernetes.m.crossplane.io checkout-shared-logs \
  -o jsonpath='{.status.atProvider.manifest.status.atProvider.arn}')
printf '   %s%s%s\n' "$G" "$_observed" "$N"

cmd "kubectl -n $APP_NS get policy checkout -o jsonpath='{.spec.forProvider.policy}' | jq"
POLICY=$(k -n "$APP_NS" get policies.iam.aws.m.upbound.io checkout -o jsonpath='{.spec.forProvider.policy}')
printf '%s\n' "$POLICY" | jq .
printf '%s' "$POLICY" | grep -q "$LOGS_ARN/$APP_NS/checkout/\*" \
  || die "Policy does not contain the observed shared bucket ARN"
printf '%s   Assertion passed: policy grants %s/%s/checkout/*%s\n' "$G" "$LOGS_ARN" "$APP_NS" "$N"

cmd "crossplane resource trace myapp.crossplane.best-practice.com/checkout -n $APP_NS"
crossplane resource trace myapps.crossplane.best-practice.com/checkout -n "$APP_NS" --context "$CTX" 2>/dev/null \
  || crossplane beta trace myapps.crossplane.best-practice.com/checkout -n "$APP_NS" --context "$CTX" || true

takeaway \
	"THE PAYOFF - STATUS OF ONE RESOURCE BECAME SPEC OF ANOTHER" \
	"" \
	"  observed  platform-logs  status.atProvider.arn   (not owned)" \
	"  wrote     checkout policy -> Resource: <that arn>/team-payments/.." \
	"" \
	"Nobody copied an ARN. provider-kubernetes OBSERVED a resource we" \
	"must never modify, and the in-cluster render used its status." \
	"" \
	"Evidence: the assertion above and the Ready tree from trace."

pause

# =============================================================================
# STEP 8: BREAK IT - WHAT ARGOCD WOULD SEE
# =============================================================================
section "Step 8 - Break it: a typo in the order, surfaced on the MyApp"

explain "Developer adds an S3 action without the 's3:' prefix. The XRD allows any string;"
explain "AWS (moto) rejects the policy document."
cmd "helm upgrade checkout charts/myapp-claim -n $APP_NS --set 'extraPolicyActions={s3GetObjectTagging}'"
helm --kube-context "$CTX" upgrade checkout "$ROOT/charts/myapp-claim" -n "$APP_NS" \
  --set 'extraPolicyActions={s3GetObjectTagging}'

wait_for "MyApp ResourcesHealthy=False" '{.status.conditions[?(@.type=="ResourcesHealthy")].status}' False 300 \
  -n "$APP_NS" myapps.crossplane.best-practice.com checkout

print_myapp
cmd "kubectl -n $APP_NS get myapp checkout -o jsonpath='{.status.conditions[?(@.type==\"ResourcesHealthy\")].message}'"
k -n "$APP_NS" get myapps.crossplane.best-practice.com checkout \
  -o jsonpath='{.status.conditions[?(@.type=="ResourcesHealthy")].message}' | fold -w 100
printf '\n'

explain "Fix the typo - the condition flips back on the next reconcile:"
cmd "helm upgrade checkout charts/myapp-claim -n $APP_NS --set 'extraPolicyActions={s3:GetObjectTagging}'"
helm --kube-context "$CTX" upgrade checkout "$ROOT/charts/myapp-claim" -n "$APP_NS" \
  --set 'extraPolicyActions={s3:GetObjectTagging}'
wait_for "MyApp ResourcesHealthy=True" '{.status.conditions[?(@.type=="ResourcesHealthy")].status}' True 300 \
  -n "$APP_NS" myapps.crossplane.best-practice.com checkout
print_myapp

takeaway \
	"CHECKPOINT 8 - THE ERROR IS ON THE THING YOU DEPLOYED" \
	"" \
	"Without status-transformer the AWS error lives on a child Policy." \
	"ArgoCD watches the MyApp, sees Synced+Ready and says Healthy." \
	"" \
	"With it, MyApp gets ResourcesHealthy=False + the real AWS message," \
	"so an ArgoCD health check can turn the app Degraded right away." \
	"Note READY stayed True the whole time - Ready alone hid the error." \
	"" \
	"Evidence: HEALTHY=False with MalformedPolicyDocument, then True."

pause

# =============================================================================
# STEP 9: TROUBLESHOOT OFFLINE WITH crossplane render
# =============================================================================
section "Step 9 - Troubleshoot without a cluster: crossplane render"

RD="$DEMO_DIR/render"; mkdir -p "$RD"
helm template p "$ROOT/charts/myapp-composition" --show-only templates/composition.yaml > "$RD/composition.yaml"
helm template p "$ROOT/charts/myapp-composition" --show-only templates/xrd.yaml > "$RD/xrd.yaml"
helm template checkout "$ROOT/charts/myapp-claim" -n "$APP_NS" > "$RD/xr.yaml"

explain "Reconcile #1 - nothing exists yet. What would Crossplane create?"
cmd "crossplane render xr.yaml composition.yaml crossplane/functions.yaml --xrd xrd.yaml -r"
crossplane render "$RD/xr.yaml" "$RD/composition.yaml" "$ROOT/crossplane/functions.yaml" \
  --xrd "$RD/xrd.yaml" -r | yq -N '[.kind, (.metadata.name // ""), (.message // "")] | join("  ")' \
  | cut -c1-150

explain "Reconcile #2 - feed fake observed state (render/observed-ready.yaml). Policy appears:"
cmd "crossplane render ... -o render/observed-ready.yaml"
crossplane render "$RD/xr.yaml" "$RD/composition.yaml" "$ROOT/crossplane/functions.yaml" \
  --xrd "$RD/xrd.yaml" -o "$ROOT/render/observed-ready.yaml" \
  | yq -N 'select(.kind == "Policy") | .spec.forProvider.policy' | jq .

explain "Replay the failure (render/observed-broken.yaml) - does status-transformer catch it?"
cmd "crossplane render ... -o render/observed-broken.yaml | yq '.status.conditions'"
crossplane render "$RD/xr.yaml" "$RD/composition.yaml" "$ROOT/crossplane/functions.yaml" \
  --xrd "$RD/xrd.yaml" -o "$ROOT/render/observed-broken.yaml" \
  | yq -N 'select(.kind == "MyApp") | .status.conditions[] | select(.type == "ResourcesHealthy") | [.status, .reason] | join(" ")'

takeaway \
	"CHECKPOINT 9 - DEBUG THE PIPELINE ON A LAPTOP" \
	"" \
	"crossplane render runs the SAME functions in Docker, against an XR" \
	"and an optional snapshot of observed state. No cluster, no AWS." \
	"" \
	"  -o observed.yaml   replay any reconcile: 'what if role is Ready?'" \
	"  -r                 see every function result / sequencer message" \
	"  --xrd              apply XRD defaults exactly like the API server" \
	"" \
	"Evidence: three renders above - create, wire, and fail - offline."

pause

# =============================================================================
# STEP 10: CLEANUP
# =============================================================================
section "Step 10 - Cleanup"

if [[ "$KEEP" == "true" ]]; then
  printf '   --keep passed: leaving cluster and %s intact.\n' "$DEMO_DIR"
  printf '   export KUBECONFIG=%s\n' "$KUBECONFIG"
  printf '   Clean up later with:  %s --cleanup\n' "$0"
else
  if [[ "$SLEEP" == "-1" ]]; then
    printf '%s   Tear down the cluster and %s? [Y/n] %s' "$C" "$DEMO_DIR" "$N"
    read -r _confirm
    if [[ "${_confirm:-y}" =~ ^[Nn] ]]; then
      printf '   Skipping. Clean up later with:  %s --cleanup\n' "$0"
    else
      do_cleanup
    fi
  else
    do_cleanup
  fi
fi

# =============================================================================
# SUMMARY
# =============================================================================
section "What this demo proved"
explain "1. IN-CLUSTER RENDER: Helm shipped a template; Crossplane rendered it with live state."
explain "2. OBSERVE: provider-kubernetes read status.atProvider.arn of a bucket we do not own."
explain "3. DEPENDENCIES: function-sequencer created the attachment only after role + policy."
explain "4. READY: function-auto-ready kept MyApp un-Ready until every child was Ready."
explain "5. ARGOCD HEALTH: function-status-transformer put the AWS error on the MyApp itself"
explain "   (Ready stayed True - without it ArgoCD would have called the app Healthy)."
explain "6. TROUBLESHOOT: crossplane render replayed create / wire / fail with no cluster."
