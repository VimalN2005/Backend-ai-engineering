# Day 63: GitHub Actions CI/CD — 7 Critical Production Antipatterns

A senior DevOps & DevSecOps engineering analysis of the 7 most dangerous antipatterns in GitHub Actions pipelines for Python backends, detailing underlying failure mechanisms, bad vs. good code examples, and production remediations.

---

## 1. Antipattern 1: Storing Long-Lived Static Cloud Credentials in GitHub Secrets

### The Failure Mechanism
Historically, deploying to AWS or GCP required creating an IAM user with access keys (`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`) and pasting them into GitHub Repository Secrets.
- **Risk 1 (Exfiltration via Malicious PR / Dependency)**: If a dependency is compromised (e.g. typosquatting attack on PyPI) or a workflow prints environment variables, attackers exfiltrate the keys.
- **Risk 2 (Indefinite Lifespan)**: Static access keys do not expire. Attackers can maintain persistent, undetected access to production infrastructure months after an exfiltration event.
- **Risk 3 (Audit Trail Blindspots)**: CloudTrail logs cannot easily attribute actions to specific GitHub runs or commits.

### Bad Code (Antipattern)
```yaml
# ANTI-PATTERN: Static, permanent AWS access keys stored in GitHub Secrets
- name: Configure AWS Credentials
  uses: aws-actions/configure-aws-credentials@v4
  with:
    aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
    aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    aws-region: us-east-1
```

### Good Code (Production Standard)
```yaml
# PRODUCTION STANDARD: Keyless OpenID Connect (OIDC) with 1-hour ephemeral session
permissions:
  id-token: write  # Mandatory for requesting OIDC JWT token
  contents: read

steps:
  - name: Configure AWS Credentials via OIDC
    uses: aws-actions/configure-aws-credentials@e3dd14a369e916a610edf257389e8a2c999494cf
    with:
      role-to-assume: arn:aws:iam::123456789012:role/GitHubActionsDeploymentRole
      aws-region: us-east-1
      # Zero static keys required! Session automatically expires in 1 hour.
```

---

## 2. Antipattern 2: Omitting `timeout-minutes` on Jobs

### The Failure Mechanism
By default, GitHub Actions sets a job execution timeout of **360 minutes (6 hours)**!

If a test process hangs due to a database connection deadlock, an unclosed socket, or an infinite loop:
- The runner remains locked for 6 continuous hours.
- In private repositories, this consumes 360 billable runner minutes in a single run, quickly draining the organization's monthly billing quota and blocking other developers' pipelines.

### Bad Code (Antipattern)
```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    # ANTI-PATTERN: No timeout declared! A hung deadlock will run for 6 hours (360 minutes)!
    steps:
      - uses: actions/checkout@v4
      - run: pytest
```

### Good Code (Production Standard)
```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    # PRODUCTION STANDARD: Explicitly bounded execution time (fails fast if hung)
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
      - run: pytest
```

---

## 3. Antipattern 3: Referencing Third-Party Actions via Mutable Tags (`@v4`, `@main`)

### The Failure Mechanism
Git tags in GitHub are mutable. An attacker who compromises a popular open-source third-party action (or who takes over the developer's GitHub account) can force-push a malicious commit to the `@v4` tag.

When your production CI pipeline runs:
1. GitHub Actions fetches the compromised `@v4` commit.
2. The malicious action executes with full access to the runner's memory, file system, and repository secrets.
3. Your production credentials and source code are silently exfiltrated to an attacker-controlled endpoint.

### Bad Code (Antipattern)
```yaml
# ANTI-PATTERN: Mutable tags can be maliciously modified by upstream maintainers
- uses: actions/checkout@v4
- uses: docker/build-push-action@v5
- uses: some-unverified-org/custom-action@main
```

### Good Code (Production Standard)
```yaml
# PRODUCTION STANDARD: Pinned to immutable, cryptographically verifiable 40-character commit SHA
- uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1
- uses: docker/build-push-action@4a13e500e05cf64b009ae718145f7299195d64a0 # v5.1.0
```

---

## 4. Antipattern 4: Omitting Concurrency Cancellation on Pull Requests

### The Failure Mechanism
When a developer pushes 5 quick commits to their Pull Request (e.g. fixing typos or linting), GitHub Actions triggers 5 independent workflow runs.

Without concurrency controls:
- All 5 pipelines run simultaneously, creating queue congestion and consuming 5x the runner compute.
- Test results from older, obsolete commits may finish *after* newer commits, confusing the pull request status checks.

### Bad Code (Antipattern)
```yaml
# ANTI-PATTERN: Missing concurrency block. All intermediate commits execute to completion.
name: CI
on:
  pull_request:
    branches: [main]
```

### Good Code (Production Standard)
```yaml
# PRODUCTION STANDARD: Cancels in-flight runs when newer commits are pushed to the same PR
name: CI
on:
  pull_request:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

---

## 5. Antipattern 5: Testing Against SQLite in CI While Running PostgreSQL in Production

### The Failure Mechanism
Teams often configure `DATABASE_URL: sqlite+aiosqlite:///:memory:` in CI because SQLite requires zero setup and boots instantly.

However, SQLite and PostgreSQL have fundamentally incompatible behaviors:
- SQLite does not enforce PostgreSQL `JSONB` path queries (`data->>'field'`).
- SQLite lacks true row-level write locks (`SELECT ... FOR UPDATE`), passing concurrency tests in CI that deadlock in production.
- SQLite does not support native PostgreSQL UUID generation, array types, or full-text search vectors.

### Remediation
Always run integration tests against a live **PostgreSQL Service Container** in GitHub Actions, mirroring the exact production database version and dialect:

```yaml
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
```

---

## 6. Antipattern 6: Cache Pollution & Incorrect Key Invalidation in `actions/cache`

### The Failure Mechanism
Using static or loose cache keys (e.g., `key: pip-cache`) causes cache pollution:
- When a package version is bumped in `requirements.txt`, the cache key still matches the old cache!
- `pip install` restores outdated wheels from the cache, masking dependency incompatibilities or failing tests unpredictably.

### Bad Code (Antipattern)
```yaml
# ANTI-PATTERN: Static key never invalidates when dependencies change!
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: pip-dependencies
```

### Good Code (Production Standard)
```yaml
# PRODUCTION STANDARD: Key incorporates cryptographic hash of exact requirements file
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: pip-${{ runner.os }}-${{ matrix.python-version }}-${{ hashFiles('**/requirements.txt') }}
    restore-keys: |
      pip-${{ runner.os }}-${{ matrix.python-version }}-
```

---

## 7. Antipattern 7: Exposing Secrets via `pull_request_target` Triggers

### The Failure Mechanism
The `pull_request` event executes code in the context of the fork and has **no access to repository secrets**. 

To work around this limitation (e.g. to run tests requiring database secrets on community PRs), developers sometimes switch to `on: pull_request_target`.

**CRITICAL SECURITY EXPLOIT**:
`pull_request_target` executes with access to repository secrets AND write permissions!
If the workflow checks out the untrusted fork's pull request code (`ref: ${{ github.event.pull_request.head.sha }}`) and runs `pytest` or `npm test`, an attacker can open a PR containing malicious code inside `tests/` that reads `$AWS_SECRET_ACCESS_KEY` or `$GITHUB_TOKEN` and sends it to their server!

### Remediation
- **Never** check out and execute code from untrusted forks inside a `pull_request_target` workflow.
- Use `pull_request_target` exclusively for labeling, triage, or commenting via the GitHub API, never for running untrusted build or test scripts.
