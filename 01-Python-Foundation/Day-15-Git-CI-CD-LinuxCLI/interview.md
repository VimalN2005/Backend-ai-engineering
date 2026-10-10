# Day 15: Senior Backend & DevOps Interview Mastery

Master these 5 senior-level technical interview questions covering Git internals, GitHub Actions CI/CD architecture, and Linux production systems engineering.

---

### Q1: Explain the internal storage architecture of Git (Blobs, Trees, Commits). How does Git guarantee cryptographic immutability, and what happens during `git gc`?

#### Answer:
Git is fundamentally a **content-addressable Directed Acyclic Graph (DAG) object store** stored under `.git/objects/`. Every object is stored compressed with `zlib` and identified by a 40-character SHA-1 (or 64-character SHA-256) hash computed from its header and contents:
$$\text{SHA} = \text{hash}(\text{type} + \text{" "} + \text{size} + \text{"\0"} + \text{content})$$

The 4 core object types are:
1. **Blob**: Stores pure file content without metadata (no filename, no timestamps, no permissions).
2. **Tree**: Represents a directory. Stores a list of entries consisting of POSIX permissions (e.g. `100644`), entry type (blob or subtree), filename, and object SHA hash.
3. **Commit**: Contains the root Tree SHA, parent commit SHAs (0 for root, 1 for normal, 2+ for merges), author metadata, committer metadata, and the commit message.
4. **Annotated Tag**: A permanent pointer referencing a specific commit SHA with tagger metadata and an optional GPG cryptographic signature.

**Cryptographic Immutability**:
Because every commit SHA depends recursively on its root tree SHA, parent commit SHAs, author metadata, and timestamps, changing any character in any historical file changes its blob SHA, which changes the parent tree SHA, which changes every downstream commit SHA. History cannot be silently tampered with.

**Garbage Collection (`git gc`)**:
When branches are rebased or commits reset via `git reset --hard`, old commit objects become unreferenced "orphans" (unreachable by traversing backwards from any named ref in `refs/heads/` or `refs/tags/`).
1. Git maintains reflogs in `.git/logs/` recording all `HEAD` transitions for 30–90 days.
2. During `git gc`, Git prunes unreferenced objects older than `gc.pruneExpire` (default: 14 days for unreferenced loose objects).
3. It packs remaining loose objects into optimized, delta-compressed binary **Packfiles** (`.pack`) and index files (`.idx`), dramatically reducing disk footprint and accelerating network transfer.

---

### Q2: Compare `git rebase` versus `git merge` from an architectural and DAG perspective. When is rebasing catastrophic, and how does `--force-with-lease` prevent silent commit overwrites?

#### Answer:
Both commands integrate changes from one branch into another, but they create completely different DAG topologies:

| Dimension | `git merge` | `git rebase` |
| :--- | :--- | :--- |
| **DAG Structure** | Preserves divergent branches; creates a merge commit with 2 parents. | Creates a strictly linear history; rewrites commits on top of base. |
| **Commit SHAs** | Original commit SHAs are 100% preserved. | **Rewrites commit SHAs**. New commits are generated for all rebased commits. |
| **History Integrity** | Chronologically accurate representation of when work happened. | Artificially simplified linear history; ideal for `git bisect`. |
| **Traceability** | Easy to see where a feature branch began and ended. | Feature boundaries are flattened unless squash-merged. |

**When Rebasing is Catastrophic**:
Rebasing is dangerous when performed on **shared public branches** (`main`, `staging`, or shared team branches). When you rebase a shared branch and force-push, you rewrite the commit SHAs that other teammates have based their local branches on. When they run `git pull`, Git attempts to merge both the old and new versions of the commits, generating messy duplicate commits and widespread merge conflicts.

**The Golden Rule**: Only rebase local, unpushed feature branches before opening a PR.

**Why `--force-with-lease` is Essential**:
A standard `git push --force` unthinkingly overwrites the remote ref pointer with your local pointer. If a coworker pushed a commit to the remote branch while you were rebasing, their commit is obliterated.
`--force-with-lease` instructs Git to verify that the remote repository's current commit SHA matches your local tracking ref (`origin/branch`). If someone else pushed new commits in the interim, the lease check fails and the push is rejected, preventing accidental data loss.

---

### Q3: How would you architect an enterprise CI/CD pipeline in GitHub Actions for a high-concurrency Python/FastAPI microservice handling 10,000 req/s?

#### Answer:
An enterprise pipeline must balance **speed**, **determinism**, **security**, and **zero-downtime reliability**:

```
[Push/PR] ──► [Static Quality & Security Gate] ──► [Matrix Tests + Redis Service] ──► [Docker Build & Sign] ──► [Canary Deploy]
```

1. **Pipeline Concurrency & Cost Optimization**:
   ```yaml
   concurrency:
     group: ${{ github.workflow }}-${{ github.ref }}
     cancel-in-progress: true  # Abort outdated runs when new commits are pushed
   ```
2. **Determinism & Dependency Caching**:
   - Pin exact dependencies using a lockfile (`uv.lock` or `poetry.lock`).
   - Use `actions/setup-python` with `cache: 'pip'` or hash-keyed `actions/cache` on virtual environments to cut runtimes from 7 minutes to under 45 seconds.
3. **Security Boundaries & OIDC**:
   - Least privilege: Set top-level `permissions: { contents: read, id-token: write }`.
   - Never store long-lived cloud credentials (e.g. AWS access keys) in GitHub Secrets. Use **OpenID Connect (OIDC)** to obtain short-lived, cryptographically verifiable IAM session tokens from AWS/GCP.
   - Run static application security testing (SAST) via `bandit` and dependency vulnerability scanning via `pip-audit` or `trivy`.
4. **Automated Matrix Testing with Service Containers**:
   - Execute parallel test jobs across supported Python runtimes (3.11, 3.12, 3.13) against live Redis and PostgreSQL testcontainers.
   - Enforce a strict test coverage gate (`--cov-fail-under=85`).
5. **Continuous Deployment with Blue/Green or Canary Strategy**:
   - On merge to `main`, build a multi-stage, non-root Docker container image.
   - Sign container images using Sigstore/Cosign.
   - Deploy as a canary (5% traffic) to Kubernetes; monitor error rates and latency via Prometheus for 5 minutes before rolling out to 100% of production traffic.

---

### Q4: A production Python backend service on a Linux VM is experiencing latency spikes and dropping TCP connections. Walk through the exact Linux CLI commands and diagnostic sequence you would use.

#### Answer:
Diagnosing production incidents requires a systematic top-down methodology across the four pillars of system performance: **CPU, Memory, Disk I/O, and Network**.

#### Step 1: Overall System Health & CPU/Memory Saturation
```bash
# 1. Check system load averages (1m, 5m, 15m) and core saturation
uptime

# 2. Identify top CPU-consuming and memory-consuming processes
top -b -n 1 | head -n 20
# Or sort processes directly by memory:
ps aux --sort=-%mem | head -n 10

# 3. Check physical memory and swap thrashing
free -h
# If swap 'used' is actively fluctuating with high si/so (swap in/swap out in vmstat),
# the Linux kernel is swapping Python process memory to disk, creating severe latency spikes.
vmstat 1 5
```

#### Step 2: Network Sockets & Port Saturation
```bash
# 4. Check TCP socket states (ESTABLISHED, TIME_WAIT, CLOSE_WAIT)
ss -s

# 5. Check listening queues and socket backlog drops
ss -tlpn
# If Send-Q > 0 or Recv-Q is full, the Python application is failing to accept incoming connections fast enough!

# 6. Check if connections are hung in TIME_WAIT / CLOSE_WAIT
netstat -ant | awk '{print $6}' | sort | uniq -c | sort -n
```

#### Step 3: File Descriptor Exhaustion
In Linux, sockets are file descriptors. If a service leaks sockets or DB connections, it hits `ulimit -n`:
```bash
# 7. Check current file descriptor limit
ulimit -n

# 8. Count open file descriptors held by target Python PID
lsof -p <PID> | wc -l
# Or directly via procfs:
ls -1 /proc/<PID>/fd | wc -l
```

#### Step 4: Live Kernel Syscalls & Disk I/O Bottlenecks
```bash
# 9. Check disk write bottlenecks (waiting on disk flush)
iostat -xz 1 3

# 10. Trace what the hung Python worker threads are doing in real-time
strace -p <PID> -f -c
# Displays a summary of syscalls (futex deadlocks, read/write socket hangs, epoll_wait timeouts).
```

---

### Q5: What is the fundamental difference between Linux signals `SIGTERM` (15), `SIGINT` (2), and `SIGKILL` (9)? How must a production Python backend handle graceful shutdown?

#### Answer:
POSIX signals are asynchronous notifications dispatched by the Linux kernel to processes:

| Signal | Number | Catchable / Trappable? | Default Action | Purpose in Production Systems |
| :--- | :--- | :--- | :--- | :--- |
| **SIGINT** | 2 | Yes | Terminate | Dispatched by terminal keyboard interrupt (`Ctrl+C`). Standard interactive termination. |
| **SIGTERM** | 15 | **Yes** | Terminate | **Polite Termination Request**. The operating system or container orchestrator requests the service to shut down cleanly. |
| **SIGKILL** | 9 | **NO** (Uncatchable) | Kernel vaporizes process | **Forced Immediate Destruction**. Kernel reclaims memory without notifying process; active buffers/sockets are dropped. |

#### Production Graceful Shutdown Lifecycle
In Kubernetes, Docker, and Systemd, service restarts and deployments follow this sequence:
1. Orchestrator removes the pod/container from the Load Balancer endpoint pool (no new ingress traffic).
2. Orchestrator dispatches **`SIGTERM`** to the container's PID 1.
3. A `terminationGracePeriodSeconds` timer (default: 30s) begins counting down.
4. If the process does not terminate within the grace period, the kernel issues **`SIGKILL`**.

#### How Production Python Backends Must Handle `SIGTERM`:
```python
import asyncio
import signal
import sys
import logging

logger = logging.getLogger("uvicorn.server")

async def shutdown(sig: signal.Signals, loop: asyncio.AbstractEventLoop) -> None:
    logger.info(f"Received exit signal {sig.name}. Commencing graceful shutdown...")
    
    # 1. Stop accepting new HTTP requests
    # 2. Wait for in-flight requests to complete (with timeout)
    # 3. Drain and close database connection pools (PostgreSQL/SQLAlchemy)
    # 4. Flush Redis pipelines and background log buffers
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    logger.info(f"Cancelling {len(tasks)} pending asyncio tasks...")
    for t in tasks:
        t.cancel()
    
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

# Register POSIX signal handlers in Linux event loop
loop = asyncio.get_event_loop()
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, loop)))
```
This ensures zero dropped HTTP responses during rolling Kubernetes deployments and prevents database connection pool corruption.
