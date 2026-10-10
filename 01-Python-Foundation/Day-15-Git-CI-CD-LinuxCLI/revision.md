# Day 15: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Advanced Git, GitHub Actions CI/CD, and Linux Systems Engineering for senior backend engineers.

---

## 1. Git DAG Internals at a Glance

| Object Type | What It Stores | Does It Have File Names? |
| :--- | :--- | :--- |
| **Blob** | Raw file contents (compressed with `zlib`). | No |
| **Tree** | Directory listing (POSIX permissions + filename + Blob/Tree SHAs). | Yes |
| **Commit** | Root Tree SHA + Parent Commit SHAs + Author/Committer info + Message. | References Tree |
| **Branch** | 41-byte text file in `.git/refs/heads/<name>` containing a commit SHA. | N/A |
| **HEAD** | Pointer in `.git/HEAD` pointing to active branch ref or commit SHA. | N/A |

### Essential Git Diagnostic Commands
```bash
git cat-file -t <sha>         # Check object type (blob, tree, commit, tag)
git cat-file -p <sha>         # Pretty-print object content
git rev-parse HEAD            # Get full 40-char SHA of current commit
git log --oneline --graph --all # Visualize DAG topology in terminal
```

---

## 2. Git Emergency Disaster Recovery Cheat Sheet

```bash
# 1. Recover lost commits after accidental 'git reset --hard'
git reflog                     # Find the commit SHA before the reset (e.g. HEAD@{1})
git reset --hard HEAD@{1}      # Restore pointer immediately

# 2. Safely undo a pushed commit without rewriting history
git revert <commit-sha>        # Creates an inverse commit that undoes changes

# 3. Safe force-push that protects against overwriting teammates' work
git push --force-with-lease origin <branch-name>

# 4. Clean up private commits before creating PR (Squash / Reword)
git rebase -i HEAD~3           # Interactive rebase of last 3 commits

# 5. Stash untracked and modified files with description
git stash push -u -m "wip: bugfix"
git stash pop                  # Re-apply most recent stash
```

---

## 3. GitHub Actions CI/CD Workflow Cheatsheet

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true    # Kill outdated PR runs automatically

permissions:
  contents: read              # Principle of least privilege

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip"        # Automatic dependency caching
      - run: pip install -r requirements-dev.txt
      - run: pytest --cov=app --cov-fail-under=85
```

---

## 4. Linux Systems Diagnostic Commands Cheat Sheet

| Purpose | Linux Command | What to Look For |
| :--- | :--- | :--- |
| **System Load** | `uptime` | Load average > number of CPU cores indicates CPU saturation. |
| **Memory Usage** | `free -h` | High `used` memory + fluctuating swap (`si`/`so` in `vmstat`) indicates thrashing. |
| **Top Processes** | `ps aux --sort=-%mem \| head -n 10` | Identifies processes with highest memory footprint (RSS). |
| **Listening Ports** | `ss -tlpn` | Confirms backend service PID is bound to expected TCP port. |
| **Socket Stats** | `ss -s` | High `TIME_WAIT` or `CLOSE_WAIT` counts signal connection leaks. |
| **File Descriptors** | `lsof -p <PID> \| wc -l` | If count approaches `ulimit -n`, application is leaking sockets/files. |
| **Log Grepping** | `grep -rn "ERROR" /var/log/app/` | Rapid regex search across logs. |
| **Column Extraction**| `awk '{print $1, $9}' access.log` | Fast stream token processing for status code aggregation. |
| **Stream Edit** | `sed -i 's/foo/bar/g' file.txt` | In-place text replacement across files. |
| **Daemon Status** | `systemctl status <service>` | Verifies active status, restart count, and main PID. |
| **Realtime Logs** | `journalctl -u <service> -f` | Follows systemd unit log streams live. |

---

## 5. Linux Process Signals

- **`kill -15 <PID>` (`SIGTERM`)**: Graceful termination request. Application finishes active work, drains connection pools, and exits cleanly.
- **`kill -2 <PID>` (`SIGINT`)**: Terminal interrupt (`Ctrl+C`).
- **`kill -1 <PID>` (`SIGHUP`)**: Hangup signal; tells web servers (Nginx/Gunicorn) to reload config without dropping traffic.
- **`kill -9 <PID>` (`SIGKILL`)**: Forced kernel destruction. **Uncatchable**. Use strictly as a last resort when a process is frozen.

---

## 6. The Golden Rules for Backend Engineers
1. **Never rebase public, shared branches** (`main`, `staging`).
2. **Never push with raw `--force`**; always use `--force-with-lease`.
3. **Never hardcode secrets** in source code or CI YAML; use pre-commit hooks and OIDC tokens.
4. **Always trap `SIGTERM`** in production Python backends for zero-downtime container deployments.
5. **Never run containers as `root`**; enforce an unprivileged system user (`appuser`).
