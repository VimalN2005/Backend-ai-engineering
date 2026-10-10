# Day 15: Practical Hands-On Engineering Exercises

Solidify your mastery of Advanced Git, CI/CD with GitHub Actions, and Linux Systems Engineering with these 3 production-level challenges.

---

## Challenge 1: Custom Git Pre-Commit Hook & AST Syntax Gate

### Objective
Build a standalone Python script designed to be placed in `.git/hooks/pre-commit` (or configured via the `pre-commit` framework). It must intercept `git commit` execution locally, inspect all staged files, and prevent invalid or hazardous code from entering the repository.

### Requirements
1. **Staged Files Extraction**:
   - Use `subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"])` to retrieve all staged files that were added, copied, or modified.
2. **Python AST Syntax Validation**:
   - For all `.py` files staged, read their staged contents (using `git show :<file_path>`) and run `ast.parse(code, filename=file_path)`.
   - If syntax errors exist (e.g. invalid indentation, unclosed parentheses), display the exact line and column, and abort the commit.
3. **Secret Pattern Scanning**:
   - Inspect staged content against known credential signatures (AWS IAM access keys, OpenAI API token prefixes, RSA private keys).
   - Abort commit immediately if any high-entropy key is detected.
4. **Exit Codes**:
   - Exit with code `0` if all validations pass.
   - Exit with code `1` if any check fails, displaying clean error diagnostics and preventing Git from recording the commit.

---

## Challenge 2: Production Multi-Stage Matrix GitHub Actions Workflow

### Objective
Create an enterprise-grade GitHub Actions workflow file (`.github/workflows/backend-ci.yml`) for a high-concurrency FastAPI & Redis microservice.

### Requirements
1. **Triggers & Permissions**:
   - Trigger on `push` to `main` and all `pull_request` events targeting `main`.
   - Configure strict least-privilege permissions: `permissions: { contents: read, pull-requests: write }`.
   - Include `concurrency` configuration to automatically cancel out-of-date in-progress CI runs when a developer pushes new commits to an open PR.
2. **Job 1: Static Code Quality & Security Gate**:
   - Runs on `ubuntu-latest`.
   - Executes `ruff check .` (formatting/linting) and `bandit -r src/ -ll` (AST security analysis).
3. **Job 2: Test Matrix with Redis Service Container**:
   - Runs on a matrix of Python versions: `["3.11", "3.12", "3.13"]`.
   - Spins up a background Redis container service (`redis:7-alpine`) on port `6379`.
   - Implements dependency caching with `actions/setup-python` (`cache: 'pip'`).
   - Executes `pytest --cov=src --cov-report=term --cov-fail-under=85`.
4. **Job 3: Docker Build & Vulnerability Scan**:
   - `needs: [quality-gate, test-matrix]`.
   - Builds a container image tagged with `sha-${{ github.sha }}`.
   - Verifies the image runs as a non-root user.

---

## Challenge 3: Linux Backend Health Watchdog & Memory Monitor

### Objective
Create a Linux systems monitoring daemon script (`watchdog.py` or `watchdog.sh`) designed to monitor a long-running Python backend worker (e.g. Uvicorn or Celery worker) running on a production Linux server.

### Requirements
1. **PID & Metric Tracking via `/proc` Filesystem**:
   - Accept the target service process name or PID as a command-line argument.
   - Read memory metrics directly from `/proc/<pid>/status` or `ps -o rss= -p <pid>`.
   - Read open file descriptor counts by inspecting `/proc/<pid>/fd/` or via `lsof -p <pid> | wc -l`.
2. **Threshold Alerts & Leaked Descriptor Detection**:
   - Define a memory threshold (e.g. 512 MB RSS) and maximum file descriptor threshold (e.g. 1024 open descriptors).
   - If RSS exceeds 512 MB or open FDs exceed 1024 (indicating socket/connection pool leaks), log a `CRITICAL` alert with timestamp and current system load.
3. **Graceful Worker Restart via POSIX Signals**:
   - Issue a `SIGTERM` (`kill -15 <pid>`) signal to the worker process to allow connection draining.
   - Wait up to 15 seconds for the process to terminate cleanly.
   - If the process remains hung in uninterruptible state after 15 seconds, issue `SIGKILL` (`kill -9 <pid>`) as an emergency fallback.
   - Automatically re-launch the worker process and log the event in structured JSON format.

---

## Solution Guidelines & Architecture Verification
- Verify pre-commit hooks locally by testing them on deliberate syntax errors and mock credentials.
- Use GitHub Actions workflow validators (such as `actionlint`) or the simulator from `example_02.py` to verify workflow syntax.
- Ensure all process management scripts respect POSIX signal semantics to avoid database pool corruption.
