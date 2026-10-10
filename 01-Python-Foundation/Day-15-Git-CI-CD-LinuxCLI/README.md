# Day 15: Advanced Git, CI/CD with GitHub Actions & Linux CLI for Backend

In enterprise software engineering and production AI infrastructure, writing clean Python code is only half the battle. How that code is versioned across distributed teams, verified automatically through continuous integration pipelines, and executed reliably on Linux-based production servers determines whether your backend survives real-world scale or collapses under deployment failures.

---

## 1. The Backend Engineer's Operational Triad: Git, CI/CD, and Linux

Modern backend and AI engineering operates on an interconnected foundation:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    THE BACKEND OPERATIONAL TRIAD                        │
├─────────────────────────┬───────────────────────┬───────────────────────┤
│       1. GIT DAG        │      2. CI/CD GATES   │     3. LINUX SERVERS  │
│  Version Control Core   │ Automated Verification│ Production Execution  │
├─────────────────────────┼───────────────────────┼───────────────────────┤
│ • Cryptographic SHA     │ • Linting & Typecheck │ • Process Lifecycles  │
│ • Branching & Merging   │ • Automated Testing   │ • Signals (SIGTERM)   │
│ • History Rewriting     │ • Build Artifacts     │ • Systemd Daemons     │
│ • Disaster Recovery     │ • Deployment Rollouts │ • Socket Observability│
└─────────────────────────┴───────────────────────┴───────────────────────┘
```

A senior engineer must master:
1. **Git Internals**: Treating Git not as a set of magical commands, but as a content-addressable graph database.
2. **CI/CD Pipelines**: Automating testing, security auditing, container builds, and deterministic rollouts via GitHub Actions.
3. **Linux Diagnostics**: Diagnosing high CPU, memory leaks, zombie processes, and socket exhaustion directly on remote production instances using standard CLI utilities.

---

## 2. Git Under the Hood: The Directed Acyclic Graph (DAG)

Git is **not** a delta-based version control system (unlike legacy systems like CVS or SVN that store file diffs). Instead, Git is a **content-addressable filesystem with a VCS user interface**.

### The Git Object Database (`.git/objects`)
Every object in Git is identified by a 40-character SHA-1 (or 64-character SHA-256) hash computed from its type, byte size, and contents:
$$\text{SHA} = \text{hash}(\text{header} + \text{content})$$

Because hash collisions are astronomically improbable, any change to a single character in any file alters its hash, propagating through tree objects up to the commit hash. This makes Git repository history **cryptographically tamper-evident**.

```
    Commit A (3a4f8b) ────────► Commit B (7c19d2) ────────► Commit C (9e84a1) [main, HEAD]
       │                           │                           │
       ▼                           ▼                           ▼
    Root Tree                   Root Tree                   Root Tree
    ├── file1 (Blob 1)          ├── file1 (Blob 1) [Reused] ├── file1 (Blob 1) [Reused]
    └── app/                    └── app/                    └── app/
        └── main.py (Blob 2)        └── main.py (Blob 3)        └── main.py (Blob 3) [Reused]
                                                                └── test.py (Blob 4)
```

Key characteristics:
- If a file does not change between commits, Git **reuses the existing blob pointer**. It does not duplicate storage.
- Git stores objects compressed with `zlib`.

---

## 3. Git Objects Deep Dive: Blobs, Trees, Commits, and Annotated Tags

Git's object store contains four fundamental primitive object types:

| Object Type | Description | Internal Structure |
| :--- | :--- | :--- |
| **Blob** (*Binary Large Object*) | Stores raw file data without metadata (no filename, no permissions, no timestamps). | `blob <size>\0<content>` |
| **Tree** | Represents a directory. Maps filenames, file permissions (e.g., `100644` for normal files, `100755` for executables), and SHA hashes to blobs or other nested trees. | `tree <size>\0<mode> <name>\0<SHA>` |
| **Commit** | Points to a root tree SHA, parent commit SHAs (0 for root, 1 for normal, 2+ for merges), author metadata, committer metadata, and the commit message. | `commit <size>\0tree <SHA>\nparent <SHA>\n...` |
| **Tag** (Annotated) | Permanent named pointer to a specific commit, with tagger metadata, timestamp, and GPG signature. | `tag <size>\0object <SHA>\ntype commit\n...` |

### Inspecting Objects with Git Plumbing Commands
High-level commands (`git add`, `git commit`) are **porcelain**. Low-level internals are inspected via **plumbing** commands:
```bash
# Print object type (blob, tree, commit, tag)
git cat-file -t 7c19d2

# View formatted object content
git cat-file -p 7c19d2

# Calculate the SHA hash Git would assign to a file
git hash-object -w main.py
```

---

## 4. Branching Internals: Pointers, HEAD, and Detached HEAD Mechanics

In Git, a branch is not a copy of files or a heavy directory. **A branch is simply a 41-byte text file** inside `.git/refs/heads/<branch-name>` containing the 40-character SHA-1 hash of its latest commit!

```bash
# Inspect branch reference directly
cat .git/refs/heads/main
# Output: 9e84a1c0d2345e67890abcdef1234567890abcde
```

### The `HEAD` Pointer
`HEAD` is a reference file (`.git/HEAD`) pointing to the currently active branch:
```text
ref: refs/heads/main
```
When you make a new commit:
1. A new commit object is created with its parent set to current `HEAD`'s commit SHA.
2. The branch ref file (`.git/refs/heads/main`) is updated to point to the new commit SHA.
3. `HEAD` remains pointed to `refs/heads/main`.

### Detached `HEAD` State
When you check out a specific commit hash rather than a branch name (`git checkout 7c19d2`):
- `HEAD` points **directly to the commit hash** rather than a named branch ref.
- Any commits made in this state are orphaned as soon as you checkout another branch, leaving them eligible for garbage collection unless saved to a branch reference.

---

## 5. Git Merge vs Rebase Architecture

Integrating code between branches is governed by two fundamental strategies:

```
                     Merge Strategy                          Rebase Strategy
            (Preserves History Graph)                  (Creates Linear History)

       Feature:     B ── C                           Feature:          B' ── C'
                   /      \                                           /
       Main:    ── A ────── D ── M (Merge Commit)    Main:    ── A ── D (Rebased Base)
```

### 1. `git merge feature`
- **Fast-Forward Merge**: If `main` has not diverged (no new commits on `main` since branch point), Git simply slides the `main` pointer forward. No merge commit is generated.
- **3-Way Merge (`--no-ff`)**: If `main` and `feature` have both advanced, Git locates their Common Ancestor commit, calculates diffs from both, and generates a new **Merge Commit** with two parents (`Parent 1 = main`, `Parent 2 = feature`).
- **Advantage**: Accurately preserves the chronological history and non-destructive graph.
- **Disadvantage**: Can result in cluttered "railroad" graph topologies in large teams.

### 2. `git rebase main`
- Finds the common ancestor of `feature` and `main`.
- Saves `feature`'s commits as temporary patches (`.git/rebase-apply`).
- Resets `feature` branch to point directly to `main`'s latest commit.
- Applies each patch in sequence on top of `main`, generating **brand new commit objects with new SHA hashes**.
- **Advantage**: Perfectly linear commit history; simplified `git bisect` debugging.
- **Disadvantage**: **Rewrites commit SHAs**.

> [!WARNING]
> **The Golden Rule of Rebasing**: NEVER rebase commits that have been pushed to a shared public or main branch. Only rebase private feature branches before opening/merging a Pull Request.

---

## 6. Interactive Rebase (`git rebase -i`): Squashing & Clean PR History

Before submitting a Pull Request for code review, enterprise teams clean up messy WIP ("work in progress", "fix typo", "temp") commits using interactive rebasing:

```bash
# Interactively rebase the last 4 commits on current branch
git rebase -i HEAD~4
```

An editor opens with commands:
```text
pick 1a2b3c4 feat: implement redis token bucket rate limiter
squash 2b3c4d5 fix: correct typo in rate limiter docstring
squash 3c4d5e6 test: add unit tests for redis connection pool
reword 4d5e6f7 docs: update api documentation for rate limiting
```

Commands:
- `pick`: Use commit as is.
- `reword`: Keep commit content but rewrite commit message.
- `edit`: Stop rebase at this commit to amend files.
- `squash` (`s`): Melds commit into the previous commit and concatenates messages.
- `fixup` (`f`): Melds commit into previous commit, discarding this commit's message.
- `drop` (`d`): Deletes commit entirely.

---

## 7. Disaster Recovery with `git reflog`

`git reflog` (Reference Logs) is Git's ultimate safety net. While `git log` traverses the DAG backwards from `HEAD`, `git reflog` records **every movement of HEAD** on your local machine, including resets, rebases, checkouts, and deleted branches.

```bash
$ git reflog
9e84a1c (HEAD -> main) HEAD@{0}: reset: moving to HEAD~1
7c19d2a HEAD@{1}: commit: feat: implement stripe webhook handler
3a4f8b2 HEAD@{2}: checkout: moving from feature to main
```

### Rescuing Commits After an Accidental `git reset --hard`
If an accidental `git reset --hard HEAD~1` vaporizes uncommitted or committed work:
1. Run `git reflog` to identify the SHA before the reset (`7c19d2a` at `HEAD@{1}`).
2. Restore the branch pointer immediately:
   ```bash
   git reset --hard HEAD@{1}
   # Or recover to a new recovery branch:
   git branch recovery-branch 7c19d2a
   ```

Git keeps unreferenced commits in `.git/objects` for at least **30 days** before `git gc` (garbage collection) prunes them.

---

## 8. Cherry-Pick, Revert, and Stash Mechanics

### `git cherry-pick <commit-sha>`
Applies the exact changes introduced by a specific commit from any branch onto your current branch, generating a new commit with identical changes. Essential for backporting hotfixes into production release branches.

### `git revert <commit-sha>`
The safe rollback command for shared branches. Instead of rewriting history with `git reset`, `git revert` calculates the exact inverse diff of the target commit and records a **new commit that undoes the changes**.
```bash
# Revert a faulty production commit without altering historical SHAs
git revert 7c19d2a -m 1
```

### `git stash`
Temporarily shelves uncommitted changes (both staged and unstaged) to restore a clean working tree:
```bash
git stash push -m "wip: celery task retries"  # Save with descriptive name
git stash list                               # View stash stack
git stash pop                                # Apply most recent stash and remove it
git stash drop stash@{0}                     # Delete specific stash
```

---

## 9. Git Hooks Architecture: Client-Side vs Server-Side Automation

Git hooks are executable scripts located in `.git/hooks/` triggered automatically by lifecycle events:

```
                    GIT HOOK LIFECYCLE
   Client-Side                                 Server-Side (GitHub/GitLab)
┌─────────────────┐                          ┌───────────────────────────┐
│ pre-commit      │ Linting, secrets scan    │ pre-receive               │ Reject direct push
├─────────────────┤                          ├───────────────────────────┤
│ prepare-commit-msg                         │ update                    │ Per-branch policy
├─────────────────┤                          ├───────────────────────────┤
│ commit-msg      │ Enforce Conventional Msg │ post-receive              │ Trigger CI/CD Webhooks
├─────────────────┤                          └───────────────────────────┘
│ pre-push        │ Run test suite
└─────────────────┘
```

Using the `pre-commit` framework:
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.0
    hooks:
      - id: ruff
        args: [--fix]
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: check-yaml
      - id: check-added-large-files
        args: ['--maxkb=1000']
      - id: detect-private-key
```

---

## 10. CI/CD Architecture for High-Scale Backend & AI Systems

Continuous Integration (CI) and Continuous Deployment (CD) enforce code quality gates automatically:

```
 Developer Push / PR
        │
        ▼
┌────────────────────────────────────────────────────────┐
│               GITHUB ACTIONS CI PIPELINE               │
├────────────────────────────────────────────────────────┤
│  1. LINT & STATIC ANALYSIS                             │
│     • Ruff / Black (Formatting)                        │
│     • Mypy / Pyright (Type Checking)                   │
├────────────────────────────────────────────────────────┤
│  2. SECURITY & DEPENDENCY AUDIT                        │
│     • Bandit (AST Vulnerability Scanner)               │
│     • Pip-audit / Trivy (CVE Dependency Scanner)       │
├────────────────────────────────────────────────────────┤
│  3. AUTOMATED TEST SUITE (Matrix: Python 3.10-3.14)   │
│     • Pytest with Pytest-cov (>= 85% Coverage gate)    │
│     • Integration tests with Testcontainers / Redis   │
├────────────────────────────────────────────────────────┤
│  4. BUILD & CONTAINER ARTIFACT                         │
│     • Docker Multi-Stage Build & Image Signing         │
│     • Push to Container Registry (GHCR / AWS ECR)      │
└────────────────────────────────────────────────────────┘
        │
        ▼ (On Merge to Main)
┌────────────────────────────────────────────────────────┐
│               CONTINUOUS DEPLOYMENT (CD)               │
│  • Blue/Green or Canary Deployment to Kubernetes       │
│  • Database Migration Runner (`alembic upgrade head`)  │
│  • Automated Rollback on Smoke Test Failure            │
└────────────────────────────────────────────────────────┘
```

---

## 11. GitHub Actions Core Primitives

GitHub Actions pipelines are declared in YAML files under `.github/workflows/`:

- **Workflow**: The automated process defined in a single YAML file.
- **Events / Triggers (`on`)**: Activity that triggers the workflow (`push`, `pull_request`, `workflow_dispatch`, `schedule`).
- **Jobs**: Set of steps executed on the same runner virtual machine. Jobs run in **parallel by default** unless coordinated via `needs: [job_name]`.
- **Steps**: Individual tasks running shell commands or reusable actions.
- **Runners**: Virtual machines (`ubuntu-latest`, `windows-latest`, `macos-latest`) or self-hosted servers executing jobs.

---

## 12. GitHub Actions Matrix Builds, Dependency Caching & Secrets

A production-grade CI workflow requires:
1. **Build Matrix**: Running tests across Python versions and OS environments concurrently.
2. **Deterministic Caching**: Caching virtual environments or pip wheels to cut CI runtimes from 8 minutes to 45 seconds.
3. **Secret Isolation**: Injecting credentials strictly via GitHub Secrets or OIDC tokens, never in plain YAML.

```yaml
name: Production Backend CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test-suite:
    name: Test on Python ${{ matrix.python-version }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip" # Automatic cache based on requirements.txt / poetry.lock

      - name: Install Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-dev.txt

      - name: Run Pytest with Coverage Gate
        env:
          DATABASE_URL: ${{ secrets.CI_DATABASE_URL }}
        run: |
          pytest --cov=app --cov-report=term-missing --cov-fail-under=85
```

---

## 13. Linux Process Architecture & Signals for Backend

Production backends run almost exclusively on Linux kernels. Understanding how the kernel manages Python processes is critical for debugging uptime and latency issues.

### Process States in Linux
- `R` (Running / Runnable): Process is actively executing on a CPU core or queued in the OS run queue.
- `S` (Interruptible Sleep): Waiting for an event (I/O completion, socket read, timer).
- `D` (Uninterruptible Sleep): Waiting on direct disk I/O or kernel hardware drivers. Cannot be killed even by `SIGKILL`!
- `Z` (Zombie): Terminated child process whose exit status has not yet been read (`wait()` syscall) by its parent process. Occupies a Process Table entry.

### Process Signals
Processes communicate with the kernel and other processes via POSIX signals:

| Signal | Number | Catchable? | Purpose in Backend Systems |
| :--- | :--- | :--- | :--- |
| **SIGHUP** | 1 | Yes | Reload configuration without terminating process (e.g., Nginx, Gunicorn worker reload). |
| **SIGINT** | 2 | Yes | Interrupt from keyboard (`Ctrl+C`). Standard terminal termination. |
| **SIGTERM** | 15 | Yes | **Graceful Shutdown Request**. Process finishes active requests, closes DB pools, and terminates cleanly. |
| **SIGKILL** | 9 | **No** | **Unconditional Termination**. Kernel immediately vaporizes process. Buffers are discarded, DB connections abruptly drop. |
| **SIGUSR1** | 10 | Yes | Custom user signal; used by logging systems to reopen log files. |

> [!IMPORTANT]
> In Kubernetes and Docker deployments, orchestrators always send `SIGTERM` first, followed by a grace period (default 30 seconds), and only send `SIGKILL` if the container fails to terminate. Production Python apps must trap `SIGTERM` and exit cleanly.

---

## 14. Linux File Descriptors, Pipes, Redirection & Text Processing

In Unix/Linux philosophy: **Everything is a file**, including network sockets, pipes, and hardware devices.

### Standard File Descriptors
- `0`: Standard Input (`stdin`)
- `1`: Standard Output (`stdout`)
- `2`: Standard Error (`stderr`)

Redirection Operators:
```bash
# Redirect stdout to file (overwrite)
python main.py > output.log

# Redirect stdout to file (append)
python main.py >> output.log

# Redirect stderr to stdout, and send both to log
python main.py > app.log 2>&1

# Discard output entirely
python main.py > /dev/null 2>&1
```

### Essential Backend Diagnostics Toolkit
```bash
# 1. Search for error patterns across 10GB of log files
grep -rn "InternalServerError" /var/log/app/

# 2. Extract column 5 (IP) and column 9 (HTTP Status) from Nginx access logs
awk '{print $1, $9}' /var/log/nginx/access.log | sort | uniq -c | sort -nr

# 3. Replace deprecated environment URLs in config files in-place
sed -i 's/api.oldservice.internal/api.newservice.internal/g' config.env

# 4. Find all Python core dump files larger than 100MB and delete them safely
find /var/crash/ -name "core.python.*" -size +100M -exec rm -f {} +
```

---

## 15. Linux System Observability & Daemon Management

When a production Python service degrades in production, you must diagnose the system layer rapidly:

### 1. Network Sockets & Port Conflicts: `ss` and `lsof`
```bash
# Check what process is listening on port 8000
lsof -i :8000

# View all active TCP listening sockets with PID and process name
ss -tlpn
```

### 2. Memory & Process Inspection: `ps`, `top`, `free`
```bash
# Find top 5 memory-consuming processes
ps aux --sort=-%mem | head -n 6

# Check system memory available (distinguishing buffers/cache from free)
free -h

# Check disk space utilization per mount
df -h
```

### 3. Systemd Service Management
Production Linux servers run backend workers (Gunicorn, Uvicorn, Celery) as **Systemd units**:

```ini
# /etc/systemd/system/backend-api.service
[Unit]
Description=Enterprise FastAPI Backend Service
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=appuser
Group=appuser
WorkingDirectory=/opt/backend-api
ExecStart=/opt/backend-api/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=30
EnvironmentFile=/opt/backend-api/.env

[Install]
WantedBy=multi-user.target
```

Commands to manage the unit:
```bash
sudo systemctl daemon-reload        # Reload unit definitions
sudo systemctl start backend-api    # Start service
sudo systemctl status backend-api   # Check process status and PID
sudo systemctl restart backend-api  # Restart service
journalctl -u backend-api -f        # Stream live logs in real time
```

---

## Summary Checklist for Backend Engineers
- [x] Master Git DAG storage: Blobs, Trees, Commits, and annotated references.
- [x] Keep Git branches clean: Interactive rebase private branches; never rebase public shared history.
- [x] Protect repositories with pre-commit hooks to block API keys, secrets, and unformatted code.
- [x] Build multi-stage GitHub Actions workflows with matrices, caching, and strict test coverage gates.
- [x] Trap Linux signals (`SIGTERM`) for zero-downtime graceful shutdown in microservices and containers.
- [x] Master Linux CLI tools (`ss`, `lsof`, `ps`, `awk`, `grep`, `systemd`) to troubleshoot production incidents under pressure.
