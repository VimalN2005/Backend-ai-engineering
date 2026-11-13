# Day 63: GitHub Actions CI/CD — Enterprise Practical Exercises

Three production-level enterprise CI/CD challenges with architectural requirements, verification criteria, and complete, production-ready GitHub Actions workflow specifications.

---

## Exercise 1: Multi-Python Matrix Pipeline with Live Postgres & Redis Service Containers and Coverage Gate

### Problem Statement
A high-throughput financial transactions backend must run its test suite in parallel across Python 3.10, 3.11, and 3.12. 
- The tests involve async database transactions via `asyncpg` and cache-aside key invalidation via `redis.asyncio`.
- The pipeline must spin up live PostgreSQL 16 and Redis 7.2 service containers on the runner.
- The pipeline must enforce an **85% minimum test coverage gate** using `pytest-cov`, failing the pull request check if coverage drops below the threshold.
- The job must execute within a strict 15-minute timeout with automatic pip wheel caching.

### Requirements
1. Author `.github/workflows/test-matrix.yml`.
2. Configure `strategy.matrix` across Python 3.10, 3.11, and 3.12 with `fail-fast: false`.
3. Provision ephemeral `services.postgres` and `services.redis` with valid healthcheck probes (`pg_isready`, `redis-cli ping`).
4. Output coverage XML report and enforce `--cov-fail-under=85`.

### Solution (`.github/workflows/test-matrix.yml`)

```yaml
name: Test Matrix & Coverage Gate

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: test-matrix-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  test:
    name: Pytest (Python ${{ matrix.python-version }})
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
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 3s
          --health-retries 5

      redis:
        image: redis:7.2-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 3

    steps:
      - name: Checkout Code
        uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c # v5.0.0
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip"

      - name: Install Dependencies
        run: |
          python -m pip install --upgrade pip wheel
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov

      - name: Execute Pytest Suite with Coverage Enforcement
        env:
          DATABASE_URL: postgresql+asyncpg://test_user:test_password@localhost:5432/test_db
          REDIS_URL: redis://localhost:6379/0
        run: |
          pytest tests/ \
            --cov=app \
            --cov-report=term-missing \
            --cov-report=xml \
            --cov-fail-under=85

      - name: Upload Coverage Artifact
        if: matrix.python-version == '3.11'
        uses: actions/upload-artifact@5d5d22a31266ced268874388b861e4b58bb5c2f3 # v4.3.1
        with:
          name: coverage-report
          path: coverage.xml
```

---

## Exercise 2: Secure OCI Docker Build & Push to GHCR with BuildKit GHA Caching and Trivy Security Gate

### Problem Statement
An enterprise microservice container must be automatically compiled, security-scanned, and pushed to GitHub Container Registry (`ghcr.io`):
- The pipeline must use **Docker Buildx** and leverage GitHub Actions native cache backend (`type=gha,mode=max`) to avoid re-compiling layers across PR commits.
- Before publishing the image, the pipeline must scan the image using **Trivy** for any `CRITICAL` or `HIGH` OS or Python package vulnerabilities.
- If any `CRITICAL` or `HIGH` CVE is detected, the pipeline must **exit with status code 1** and block image publishing.
- If the scan passes, the image must be tagged with both the git commit SHA and `latest` (only on `main`), and pushed to GHCR.

### Requirements
1. Author `.github/workflows/docker-publish.yml`.
2. Authenticate to `ghcr.io` using the built-in ephemeral `GITHUB_TOKEN` with `packages: write` permissions.
3. Configure BuildKit cache import and export via `type=gha`.
4. Enforce the Trivy vulnerability blocking gate.

### Solution (`.github/workflows/docker-publish.yml`)

```yaml
name: Build, Scan & Publish OCI Container

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: docker-build-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

permissions:
  contents: read
  packages: write  # Needed to publish to ghcr.io

jobs:
  build-scan-publish:
    name: BuildKit Build & DevSecOps Scan
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout Code
        uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@f95db51fddba0c2d1ec667646a06c2ce06100226 # v3.0.0

      - name: Log in to GitHub Container Registry (GHCR)
        if: github.event_name != 'pull_request'
        uses: docker/login-action@343f7c438450a10d2b1d560731cb4c1435e23c3e # v3.0.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract Docker Metadata (Tags & Labels)
        id: meta
        uses: docker/metadata-action@96383f45573cb7f253c731d3b3ab81c87ef81934 # v5.0.0
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=sha,prefix=sha-,format=short
            type=raw,value=latest,enable=${{ github.ref == 'refs/heads/main' }}

      - name: Build Container Image Locally for Scanning
        uses: docker/build-push-action@4a13e500e05cf64b009ae718145f7299195d64a0 # v5.1.0
        with:
          context: .
          load: true  # Load image into local docker daemon for Trivy scanner
          tags: local-test-image:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: DevSecOps Vulnerability Scan (Trivy Gate)
        uses: aquasecurity/trivy-action@8435476e43ece424a938e58c32382184273a5a84 # v0.18.0
        with:
          image-ref: local-test-image:${{ github.sha }}
          format: "table"
          exit-code: "1"  # Blocks pipeline on CVE violation!
          ignore-unfixed: true
          severity: "CRITICAL,HIGH"

      - name: Push Certified Image to GHCR
        if: github.event_name != 'pull_request'
        uses: docker/build-push-action@4a13e500e05cf64b009ae718145f7299195d64a0 # v5.1.0
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
```

---

## Exercise 3: Keyless AWS ECR Deployment Pipeline using GitHub Actions OpenID Connect (OIDC)

### Problem Statement
A production deployment pipeline must push Docker container images to **Amazon Elastic Container Registry (ECR)** and trigger an Amazon ECS rolling service update:
- To comply with Zero-Trust compliance standards, **no static AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) are permitted** inside GitHub Secrets.
- The pipeline must use **GitHub Actions OpenID Connect (OIDC)** to request a short-lived token from GitHub's token provider and assume an AWS IAM role (`GitHubActionsDeploymentRole`) via AWS STS.
- The IAM role trust policy must restrict access strictly to the `main` branch of `VimalN2005/Backend-ai-engineering`.

### Requirements
1. Author the IAM Role Trust Policy for GitHub Actions OIDC.
2. Author the `.github/workflows/deploy-aws.yml` pipeline.
3. Configure `permissions: id-token: write` and execute `aws-actions/configure-aws-credentials` with `role-to-assume`.

### Solution

#### 1. AWS IAM Role Trust Policy (`oidc-trust-policy.json`)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:VimalN2005/Backend-ai-engineering:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

#### 2. Deployment Workflow (`.github/workflows/deploy-aws.yml`)
```yaml
name: Production Keyless Deployment to AWS

on:
  push:
    branches: [main]

concurrency:
  group: production-deploy
  cancel-in-progress: false  # Do not cancel production deployments mid-flight!

permissions:
  contents: read
  id-token: write  # Mandatory for requesting JWT from GitHub OIDC provider

jobs:
  deploy-production:
    name: OIDC Authenticate & ECS Rolling Release
    runs-on: ubuntu-latest
    timeout-minutes: 25

    steps:
      - name: Checkout Code
        uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1

      - name: Configure AWS Credentials via OIDC
        uses: aws-actions/configure-aws-credentials@e3dd14a369e916a610edf257389e8a2c999494cf # v4.0.2
        with:
          role-to-assume: arn:aws:iam::123456789012:role/GitHubActionsDeploymentRole
          role-session-name: GitHubActions-${{ github.sha }}
          aws-region: us-east-1

      - name: Log in to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@062b18b96a7aff071d4dc91bc00c4c1a7945b076 # v2.0.1

      - name: Build, Tag & Push Image to Amazon ECR
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          ECR_REPOSITORY: backend-api
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          echo "IMAGE_URI=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_ENV

      - name: Emit Deployment Summary
        run: |
          echo "### 🚀 Production Deployment Completed" >> $GITHUB_STEP_SUMMARY
          echo "- **Image URI**: \`$IMAGE_URI\`" >> $GITHUB_STEP_SUMMARY
          echo "- **Commit**: \`${{ github.sha }}\`" >> $GITHUB_STEP_SUMMARY
          echo "- **Authentication**: Keyless OIDC via AWS STS ✅" >> $GITHUB_STEP_SUMMARY
```
