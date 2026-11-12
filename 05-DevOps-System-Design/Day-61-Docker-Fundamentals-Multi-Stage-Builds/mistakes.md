# Day 61: Docker Fundamentals — 7 Critical Production Antipatterns

A senior engineering analysis of the 7 most dangerous antipatterns in Docker containerization for Python backends, detailing underlying Linux failure mechanisms, bad vs. good code examples, and production remediations.

---

## 1. Antipattern 1: Running as the Root User (`UID 0`)

### The Failure Mechanism
By default, Docker containers execute under `UID 0` (`root`). Although Linux namespaces isolate the container's process tree and filesystem mount table, the **Linux kernel is shared with the host**. If an application vulnerability (such as remote code execution, SQL injection with `LOAD_FILE`, or a runc/containerd privilege escalation vulnerability like CVE-2024-21626) allows an attacker to escape the container boundary, they arrive on the host operating system with full `root` privileges.

### Bad Code (Antipattern)
```dockerfile
# Anti-pattern: Missing USER directive (defaults to root UID 0)
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Attacker who exploits RCE has root privileges over container filesystem and raw capabilities!
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Good Code (Production Standard)
```dockerfile
FROM python:3.11-slim

# Create deterministic system group and user with unprivileged ID (UID >= 10000)
RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Ensure files are owned by the unprivileged user
COPY --chown=appuser:appgroup requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=appuser:appgroup . .

# Explicitly drop privileges
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 2. Antipattern 2: Inverting Layer Order & Breaking the Build Cache

### The Failure Mechanism
Docker BuildKit evaluates layers sequentially from top to bottom. If a layer's checksum changes, BuildKit invalidates that layer **and every subsequent layer**. 

Application source code changes on virtually every git commit, while `requirements.txt` changes infrequently (e.g. once every few weeks). If you execute `COPY . /app` *before* `RUN pip install`, every single code commit forces `pip install` to re-download and re-install all packages from scratch, turning a 10-second CI/CD build into a 10-minute bottleneck.

### Bad Code (Antipattern)
```dockerfile
FROM python:3.11-slim
WORKDIR /app

# BUSTS CACHE: Every commit modifies a source file, invalidating this COPY!
COPY . /app

# CONSEQUENCE: pip install runs from scratch on every single build!
RUN pip install -r requirements.txt

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Good Code (Production Standard)
```dockerfile
FROM python:3.11-slim
WORKDIR /app

# 1. Copy ONLY dependency manifest first
COPY requirements.txt .

# 2. Install dependencies (Cached until requirements.txt actually changes!)
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy application code in a separate, later layer
COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 3. Antipattern 3: Blindly Choosing Alpine Linux for Python Backends

### The Failure Mechanism
Alpine Linux uses `musl libc` instead of GNU C Library (`glibc`). Most Python binary wheels (including `numpy`, `pandas`, `cryptography`, `psycopg2`, and `grpcio`) are distributed on PyPI targeting the `manylinux` standard, which assumes `glibc`.

When installing on Alpine:
1. `pip` cannot use pre-compiled `manylinux` binary wheels and falls back to compiling from source.
2. The build fails unless compilers (`gcc`, `g++`, `make`, `python3-dev`, `musl-dev`) are installed, ballooning build times.
3. `musl libc`'s memory allocator (`musl-malloc`) lacks `ptmalloc2`'s advanced concurrency tuning, leading to severe memory fragmentation and performance drops under high concurrent load.
4. Alpine's DNS resolver does not handle search domains or EDNS0 identically to glibc, triggering intermittent DNS timeouts in Kubernetes.

### Bad Code (Antipattern)
```dockerfile
# Anti-pattern: Alpine for Python C-extensions
FROM python:3.11-alpine
RUN apk add --no-cache gcc musl-dev libpq-dev  # Slow compilation, fragmentation hazards
COPY requirements.txt .
RUN pip install -r requirements.txt  # Compiles from source; takes minutes!
```

### Good Code (Production Standard)
```dockerfile
# Production Standard: Debian Slim with native glibc and manylinux wheel support
FROM python:3.11-slim-bookworm

# Installs in seconds without needing gcc because binary wheels are utilized directly!
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

---

## 4. Antipattern 4: Using Shell Form for `CMD` and Breaking Signal Handling

### The Failure Mechanism
Docker supports two syntaxes for `CMD` and `ENTRYPOINT`:
- **Shell form**: `CMD uvicorn main:app`
- **Exec form**: `CMD ["uvicorn", "main:app"]`

When you use the shell form, the Docker daemon invokes the command inside `/bin/sh -c "uvicorn main:app"`. Consequently, `/bin/sh` becomes `PID 1` inside the container, and `uvicorn` becomes a child process.

When `docker stop` or Kubernetes sends `SIGTERM` to initiate a graceful shutdown:
1. The Linux kernel delivers `SIGTERM` to `PID 1` (`/bin/sh`).
2. `/bin/sh` **does NOT forward signals to its child processes**!
3. Uvicorn never receives `SIGTERM` and continues processing requests without draining connections.
4. After the grace period (default 10s), the container runtime delivers `SIGKILL` (Exit Code 137), abruptly terminating in-flight HTTP requests and corrupting database transactions.

### Bad Code (Antipattern)
```dockerfile
# Anti-pattern: Shell form spawns /bin/sh as PID 1
CMD uvicorn main:app --host 0.0.0.0 --port 8000
```

### Good Code (Production Standard)
```dockerfile
# Production Standard: Exec JSON array directly spawns application or supervisor as PID 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Antipattern 5: Leaking Secrets via `ENV` and `ARG` Directives

### The Failure Mechanism
Environment variables declared with `ENV` and build arguments passed with `ARG` are permanently recorded inside the image metadata JSON and filesystem layer tarballs. Anyone with access to the Docker image can run `docker history <image>` or `docker inspect <image>` and extract database passwords, private keys, or API tokens.

### Bad Code (Antipattern)
```dockerfile
FROM python:3.11-slim
# CRITICAL VULNERABILITY: Stored permanently in image layers and metadata!
ENV DATABASE_PASSWORD="ProductionSuperSecretPassword123"
ARG GITHUB_TOKEN="ghp_910283419023480129348102394"

RUN git clone https://$GITHUB_TOKEN@github.com/company/private-repo.git
```

### Good Code (Production Standard)
```dockerfile
# Production Standard: Use BuildKit Secret Mounts (secrets never stored in layers)
# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

# Secret mounted temporarily into memory during RUN execution only
RUN --mount=type=secret,id=github_token \
    TOKEN=$(cat /run/secrets/github_token) && \
    git clone https://${TOKEN}@github.com/company/private-repo.git /app/repo
```

---

## 6. Antipattern 6: Separating `apt-get update` and `apt-get install` Across Multiple Layers

### The Failure Mechanism
Docker creates a new read-only filesystem layer for every `RUN` directive. 

If you execute:
1. `RUN apt-get update` (Layer 1)
2. `RUN apt-get install -y curl` (Layer 2)

Two disastrous problems occur:
- **Cache Poisoning**: When you later add another package (e.g., `RUN apt-get install -y curl libpq-dev`), Layer 1 (`apt-get update`) hits cache and does *not* re-run, causing `apt-get install` to fail with outdated package lists.
- **Image Bloat**: If you delete `/var/lib/apt/lists/*` in a separate `RUN` instruction, the temporary package lists still exist in the underlying read-only layer; deleting them later only adds a whiteout file, saving **zero** disk space!

### Bad Code (Antipattern)
```dockerfile
# Layer 1: Cache poisoned on future builds
RUN apt-get update

# Layer 2: Package lists remain trapped in Layer 1
RUN apt-get install -y curl
RUN rm -rf /var/lib/apt/lists/*  # Does not reduce image size!
```

### Good Code (Production Standard)
```dockerfile
# Combine into a single atomic layer and clean cache before layer is committed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*
```

---

## 7. Antipattern 7: Omitting `.dockerignore`

### The Failure Mechanism
When you invoke `docker build .`, the Docker CLI tarballs the entire directory (the **build context**) and sends it over a socket to the Docker daemon. 

Without a comprehensive `.dockerignore`:
1. Gigabytes of host virtual environments (`.venv/`), local database files (`*.db`), `.git/` history, and `node_modules` are sent to the daemon, slowing down builds.
2. Local `.env` files containing production secrets are accidentally copied into the container via `COPY . .`.
3. Pre-compiled local Python bytecode (`__pycache__/*.pyc`) built on the host OS is copied into the container, causing subtle binary incompatibility crashes.

### Remediation Checklist
Always place an explicit `.dockerignore` in the root of every repository containing at minimum:
```gitignore
.git
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
```
