# Crossplane Composition Best Practices

> Companion repo for the talk **From Terraform and Terragrunt to Crossplane: A Gradual Migration at Scale** - the Composition best-practices part.

## Overview

One developer-facing API, `MyApp` (`crossplane.best-practice.com/v1alpha1`, Crossplane v2, **namespaced**), backed by a Composition that shows six practices:

1. **In-cluster render** - Helm ships the Composition; `function-go-templating` renders it *inside* the cluster on every reconcile, with the observed state of every child.
2. **Observe what you don't own** - a `provider-kubernetes` `Object` with `managementPolicies: [Observe]` reads `status.atProvider.arn` from an existing bucket and feeds it into the IAM policy.
3. **Dependencies** - `function-sequencer` holds the `RolePolicyAttachment` until the Role and the Policy are Ready, and the Policy until the observed shared bucket is Ready.
4. **Ready** - `function-auto-ready` turns MyApp Ready only when every composed resource is Ready.
5. **ArgoCD health** - `function-status-transformer` copies a child's AWS error onto MyApp as `ResourcesHealthy=False`.
6. **Troubleshooting** - `crossplane render` replays the pipeline on a laptop: no cluster, no AWS.

## Repository Structure

```
├── charts/
│   ├── myapp-composition/              # Platform team: XRD + Composition
│   │   ├── templates/xrd.yaml          #   MyApp API, scope: Namespaced
│   │   └── templates/composition.yaml  #   4-step pipeline; [[ ]] template rendered IN-CLUSTER
│   └── myapp-claim/                    # Developer: one MyApp (the "claim")
│
├── crossplane/
│   ├── providers.yaml                  # provider-aws-iam, provider-aws-s3, provider-kubernetes
│   ├── functions.yaml                  # the 4 functions (cluster + crossplane render)
│   ├── provider-configs.yaml           # AWS -> moto, provider-kubernetes RBAC
│   ├── aws-mock.yaml                   # moto: in-cluster fake AWS
│   └── shared-logs-bucket.yaml         # pre-existing platform resource (observed, not owned)
│
├── render/
│   ├── observed-ready.yaml             # fake observed state: role/bucket Ready
│   └── observed-broken.yaml            # fake observed state: AWS rejected the policy
│
└── demo/
    └── demo.sh                         # end-to-end demo (kind + Crossplane + moto)
```

## What MyApp composes

| Resource | Kind | Notes |
|---|---|---|
| `shared-logs` | `kubernetes.m.crossplane.io` Object | **Observe only** - reads the platform bucket's ARN |
| `bucket` | `s3.aws.m.upbound.io` Bucket | app data bucket |
| `role` | `iam.aws.m.upbound.io` Role | EKS Pod Identity trust |
| `policy` | `iam.aws.m.upbound.io` Policy | rendered once the app bucket's ARN is known; sequenced after the observed shared bucket |
| `role-policy-attachment` | `iam.aws.m.upbound.io` RolePolicyAttachment | sequenced after role + policy |

A developer writes:

```yaml
apiVersion: crossplane.best-practice.com/v1alpha1
kind: MyApp
metadata:
  name: checkout
  namespace: team-payments
spec:
  team: payments
  region: us-east-1
```

## How It Works

### In-cluster render vs `helm template`

`helm template` renders once, on a laptop or in ArgoCD, before anything exists in AWS. So it can't know an ARN or an ID that AWS only assigns at create time. The Composition's go-template is inline, with custom delimiters, so Helm (which only touches `{{ }}`) leaves it alone. Crossplane renders it on every reconcile and passes in `.observed.resources`:

```yaml
input:
  apiVersion: gotemplating.fn.crossplane.io/v1beta1
  kind: GoTemplate
  delims:
    left: '[['
    right: ']]'
  source: Inline
  inline:
    template: |
      [[- $bucketArn := dig "bucket" "resource" "status" "atProvider" "arn" "" $observed ]]
      [[- $logsArn   := dig "shared-logs" "resource" "status" "atProvider" "manifest" "status" "atProvider" "arn" "" $observed ]]
      [[- if and $bucketArn $logsArn ]]
      # ... emit the Policy
      [[- end ]]
```

### Name the provider config `default`

If an MR has no `providerConfigRef`, its CRD fills one in. Namespaced v2 MRs get `{kind: ClusterProviderConfig, name: default}`, and legacy cluster-scoped MRs get `{name: default}`. So both ClusterProviderConfigs in [`crossplane/provider-configs.yaml`](./crossplane/provider-configs.yaml) are named `default`.

The XRD exposes `spec.providerConfigRef.kind` / `.name` with the same defaults (`ClusterProviderConfig` / `default`), and the Composition passes it to every AWS resource. Most apps never set it; an app in another account sets just `name`:

```yaml
spec:
  providerConfigRef:
    name: cross-account-prod   # kind defaults to ClusterProviderConfig
```

- **Less boilerplate.** Developers never write `providerConfigRef`; the default flows XRD → Composition → MR.
- **Portable.** The same Composition works on every cluster. Each cluster's `default` points at its own account and credentials (IRSA / Pod Identity / here: moto).
- **No "ProviderConfig not found".** You can't misspell a name you never write. Hand-written MRs, imported or observed resources, and `crossplane render` output all work without edits.
- **Each provider family has its own `default`.** `aws.m.upbound.io` and `kubernetes.m.crossplane.io` are separate kinds, so both can be named `default`.

Name it explicitly only when you really need more than one, for example a cross-account config, or a namespaced `ProviderConfig` that gives a team its own credentials.

### Observing an existing resource

```yaml
apiVersion: kubernetes.m.crossplane.io/v1alpha1
kind: Object
spec:
  managementPolicies: ["Observe"]   # never create / update / delete
  readiness:
    policy: DeriveFromObject        # default SuccessfulCreate never fires for Observe
  forProvider:
    manifest:
      apiVersion: s3.aws.m.upbound.io/v1beta1
      kind: Bucket
      metadata: {name: platform-logs, namespace: platform}
```

The whole observed object, including its `status`, lands in `status.atProvider.manifest`. Gotchas we hit:

- **Readiness.** Without `DeriveFromObject` the Object never becomes Ready, so the XR doesn't either.
- **RBAC.** With server-side apply on (the provider's default), even Observe runs a dry-run SSA patch. The provider's ServiceAccount needs `patch` on the observed kind, not just `get/list/watch`.

### ArgoCD health

MyApp's `Ready` stays `True` when an *update* fails, so ArgoCD reports Healthy while AWS rejects every change. The status-transformer hook sets `ResourcesHealthy=False` with the AWS message. This Lua health check turns that into `Degraded` (`argocd-cm`):

```yaml
resource.customizations.health.crossplane.best-practice.com_MyApp: |
  if obj.status ~= nil and obj.status.conditions ~= nil then
    for _, c in ipairs(obj.status.conditions) do
      if (c.type == "ResourcesHealthy" or c.type == "Synced") and c.status == "False" then
        return {status = "Degraded", message = c.message}
      end
    end
    for _, c in ipairs(obj.status.conditions) do
      if c.type == "Ready" and c.status == "True" then
        return {status = "Healthy", message = "All composed resources are Ready"}
      end
    end
  end
  return {status = "Progressing", message = "Waiting for composed resources"}
```

> The demo doesn't install ArgoCD. It checks the condition the Lua reads; the Lua itself hasn't been run against ArgoCD here.

### Troubleshooting with `crossplane render`

```bash
helm template p charts/myapp-composition --show-only templates/composition.yaml > composition.yaml
helm template p charts/myapp-composition --show-only templates/xrd.yaml         > xrd.yaml
helm template checkout charts/myapp-claim -n team-payments                         > xr.yaml

# Reconcile #1 - what gets created first? (-r prints sequencer / readiness messages)
crossplane render xr.yaml composition.yaml crossplane/functions.yaml --xrd xrd.yaml -r

# Reconcile #2 - pretend role + buckets are Ready: the Policy appears
crossplane render xr.yaml composition.yaml crossplane/functions.yaml --xrd xrd.yaml \
  -o render/observed-ready.yaml

# Replay a failure: does the status-transformer catch it?
crossplane render xr.yaml composition.yaml crossplane/functions.yaml --xrd xrd.yaml \
  -o render/observed-broken.yaml | yq 'select(.kind == "MyApp") | .status.conditions'

# Live cluster: the whole tree at a glance
crossplane resource trace myapps.crossplane.best-practice.com/checkout -n team-payments
```

## Live Demo

[`demo/demo.sh`](./demo/demo.sh) brings everything up on a throwaway [kind](https://kind.sigs.k8s.io/) cluster. AWS is replaced by [moto](https://github.com/getmoto/moto) running in the cluster: the real Upbound/contrib AWS providers create real (fake) IAM and S3 resources and report real ARNs back. No AWS account needed.

### Prerequisites

`docker`, `kind`, `kubectl`, `helm`, `jq`, `yq` ([mikefarah/yq](https://github.com/mikefarah/yq)), and the `crossplane` CLI **v2.x**:

```bash
curl -sL https://raw.githubusercontent.com/crossplane/crossplane/main/install.sh | sh
```

You need internet access to pull Crossplane, the providers, the functions and moto. The first run takes a few minutes, so rehearse once to warm the Docker cache.

### Run it

```bash
./demo/demo.sh                    # interactive: Enter to advance between beats (default)
DEMO_SLEEP=0 ./demo/demo.sh       # unattended, no pauses (CI / rehearsal)
DEMO_SLEEP=5 ./demo/demo.sh       # auto-advance every 5 seconds
./demo/demo.sh --keep             # leave the cluster up at the end
./demo/demo.sh --cleanup          # tear down leftovers from a previous run
```

### The beats

| # | Beat | What it does |
|---|------|--------------|
| 0 | Preflight | Checks every CLI (crossplane CLI must be v2) and Docker |
| 1 | kind cluster | Single node, kubeconfig under `/tmp` |
| 2 | Crossplane | Helm-installs Crossplane **v2.4.2** |
| 3 | Providers + functions + moto | provider-aws-iam/s3 **v2.9.0**, provider-kubernetes **v1.4.1**, the 4 functions, moto, and `ClusterProviderConfig default` pointing at moto |
| 4 | Platform API | Helm-installs the XRD + Composition, then shows the **raw** go-template in the cluster: Helm didn't render it |
| 5 | Someone else's resource | Creates `platform/platform-logs`, a bucket MyApp will only observe |
| 6 | Developer orders MyApp | `helm install checkout charts/myapp-claim`; sequencer/readiness events, then MyApp Ready |
| 7 | **The payoff** | The observed ARN shows up inside the Policy JSON (asserted), plus `crossplane resource trace` |
| 8 | Break it | A typo in `extraPolicyActions` makes AWS reject the policy. MyApp shows `HEALTHY=False` with the AWS message while `READY` stays `True`. Fix it and it heals |
| 9 | Troubleshoot offline | Three `crossplane render` runs: create, wire, fail |
| 10 | Cleanup | Deletes the cluster and `/tmp/crossplane-bp-demo` (skipped with `--keep`) |

### Safety

- `KUBECONFIG` points at `/tmp/crossplane-bp-demo/kubeconfig` for the whole run. Your `~/.kube/config` is never read or written.
- Every `kubectl`/`helm` call passes `--context kind-crossplane-bp`.
- No real AWS. Every IAM / S3 / S3 Control / STS call goes to moto, using fake credentials.
- On unexpected failure the script prints the cleanup command and leaves the cluster intact for inspection.

### Mock plumbing (why it looks the way it does)

- `enableServiceLinks: false` on moto. The Service is named `moto`, so Kubernetes injects `MOTO_PORT=tcp://...`, which moto reads as its port and then crashes.
- **CoreDNS rewrite.** Bucket tags go through the S3 Control API, which prefixes the account ID to the host (`000000000000.moto.aws-mock...`). The demo rewrites `*.moto.aws-mock.svc.cluster.local` to the moto Service.

## Versions

| Component | Version |
|---|---|
| Crossplane | v2.4.2 |
| provider-aws-iam / provider-aws-s3 (crossplane-contrib) | v2.9.0 |
| provider-kubernetes | v1.4.1 |
| function-go-templating | v0.13.0 |
| function-auto-ready | v0.6.9 |
| function-sequencer | v0.6.0 |
| function-status-transformer | v0.5.2 |
| moto | 5.2.4 |

## License

MIT
