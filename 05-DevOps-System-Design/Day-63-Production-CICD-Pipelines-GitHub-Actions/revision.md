# Day 63: GitHub Actions CI/CD — 5-Minute Rapid Revision

A rapid revision reference, cheat sheet, and flashcard collection for GitHub Actions CI/CD, DevSecOps gating, matrix testing, and keyless cloud authentication via OIDC.

---

## 1. Production GitHub Actions Pipeline Template

Save this complete production template as the golden standard for Python backends (FastAPI / Django / Celery):

```yaml
name: Production CI/CD Pipeline

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  # ============================================================================
  # 1. Quality Gate (Fast Linter & Type Checker)
  # ============================================================================
  quality-gate:
    name: Code Quality (Ruff & Mypy)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1
      - uses: actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c # v5.0.0
        with:
          python-version: "3.11"
          cache: "pip"
      - run: |
          pip install ruff mypy
          ruff check .
          ruff format --check .
          mypy app/

  # ============================================================================
  # 2. Test Matrix with Native Service Containers
  # ============================================================================
  test-matrix:
    name: Pytest (Python ${{ matrix.python-version }})
    needs: [quality-gate]
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: test_db
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_password
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 3s
          --health-retries 5

      redis:
        image: redis:7.2-alpine
        ports: ["6379:6379"]
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 3

    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
      - uses: actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip"
      - run: |
          pip install -r requirements.txt pytest pytest-asyncio pytest-cov
          pytest --cov=app --cov-report=xml --cov-fail-under=85
        env:
          DATABASE_URL: postgresql+asyncpg://test_user:test_password@localhost:5432/test_db
          REDIS_URL: redis://localhost:6379/0

  # ============================================================================
  # 3. DevSecOps Container Scanning & BuildKit Image Build
  # ============================================================================
  build-and-deploy:
    name: BuildKit Build & OIDC Deploy
    needs: [test-matrix]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    timeout-minutes: 25
    permissions:
      contents: read
      packages: write
      id-token: write  # Mandatory for OIDC token minting

    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
      - uses: docker/setup-buildx-action@f95db51fddba0c2d1ec667646a06c2ce06100226

      # Build local image for vulnerability scanning
      - uses: docker/build-push-action@4a13e500e05cf64b009ae718145f7299195d64a0
        with:
          context: .
          load: true
          tags: local-image:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      # DevSecOps Gate: Fail build if CRITICAL CVE detected
      - uses: aquasecurity/trivy-action@8435476e43ece424a938e58c32382184273a5a84
        with:
          image-ref: local-image:${{ github.sha }}
          severity: "CRITICAL,HIGH"
          exit-code: "1"

      # Keyless Cloud Authentication via GitHub OIDC
      - uses: aws-actions/configure-aws-credentials@e3dd14a369e916a610edf257389e8a2c999494cf
        with:
          role-to-assume: arn:aws:iam::123456789012:role/GitHubActionsDeploymentRole
          aws-region: us-east-1

      - run: echo "### 🚀 Production Deployment Certified" >> $GITHUB_STEP_SUMMARY
```

---

## 2. GitHub CLI (`gh`) Command Arsenal

```bash
# 1. Trigger a workflow manually on demand
gh workflow run production-pipeline.yml -f environment=staging

# 2. View active and recent workflow runs
gh run list --workflow=production-pipeline.yml

# 3. Watch real-time log stream of an active workflow run
gh run watch

# 4. View detailed failures and error logs for a specific run ID
gh run view <RUN_ID> --log-failed

# 5. Set an encrypted repository secret
gh secret set DATABASE_PASSWORD --body "production_secret_value"

# 6. Re-run only failed jobs from a previous run
gh run rerun <RUN_ID> --failed
```

---

## 3. 10 Rapid Recall Flashcards

| # | Question | Answer |
| :-- | :--- | :--- |
| **1** | How does `concurrency.cancel-in-progress: true` save CI compute? | It automatically **cancels running builds on older commits** when a newer commit is pushed to the same PR. |
| **2** | What does `strategy.fail-fast: false` achieve in a test matrix? | Prevents a single Python version failure from cancelling the other parallel matrix jobs, ensuring **full diagnostic visibility across all versions**. |
| **3** | Why must third-party actions be pinned to a 40-character commit SHA? | Tags (`@v4`) are mutable and vulnerable to **supply chain account takeovers**; commit SHAs are cryptographically immutable. |
| **4** | How does GitHub Actions OpenID Connect (OIDC) eliminate static cloud keys? | The runner requests a short-lived signed JWT from GitHub, which cloud providers (AWS STS) validate to grant **ephemeral 1-hour credentials**. |
| **5** | What permission scope is mandatory for minting OIDC tokens? | **`permissions: id-token: write`**. |
| **6** | What is the total storage cache limit per repository in GitHub Actions? | **10 GB total** across all branches; older entries are evicted via **Least Recently Used (LRU)** policy. |
| **7** | What is the difference between `cache-to: type=gha,mode=min` vs `mode=max` in BuildKit? | `mode=min` caches only the final image stage; **`mode=max` caches all intermediate stages**, saving heavy compiler layers in multi-stage builds. |
| **8** | Why should you avoid testing against SQLite in CI when deploying PostgreSQL in production? | SQLite lacks true row-level write locks (`FOR UPDATE`), PostgreSQL JSONB path queries, and array types, masking concurrency deadlocks. |
| **9** | What is the purpose of `$GITHUB_STEP_SUMMARY`? | Allows workflows to append custom Markdown text/tables that render directly as **visual status cards on the GitHub Actions UI page**. |
| **10** | What is the critical vulnerability in `on: pull_request_target`? | It runs in the context of the base repo with **full access to repository secrets**; checking out untrusted fork code allows malicious PRs to steal credentials. |
