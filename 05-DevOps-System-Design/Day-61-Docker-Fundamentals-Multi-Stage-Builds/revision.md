# Day 61: Docker Fundamentals — 5-Minute Rapid Revision

A rapid revision reference, cheat sheet, and flashcard collection for Docker containerization, multi-stage builds, and container security.

---

## 1. Production Multi-Stage Dockerfile Boilerplate

Save this production skeleton as the gold standard for containerizing FastAPI, Django, or Celery backends:

```dockerfile
# ==============================================================================
# STAGE 1: Compiler & Dependency Builder
# ==============================================================================
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies and compilers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build isolated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# STAGE 2: Hardened Minimal Production Runner
# ==============================================================================
FROM python:3.11-slim-bookworm AS runner

# Install only runtime shared libraries and init supervisor
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged system user (UID >= 10000)
RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Copy pre-compiled virtualenv from builder stage
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv

# Copy application source code
COPY --chown=appuser:appgroup . /app

# Switch to unprivileged user
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# tini forwards SIGTERM and reaps zombie processes
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 2. Production `.dockerignore` Template

```gitignore
# VCS Metadata
.git/
.gitignore
.gitattributes

# Python Bytecode & Cache
__pycache__/
*.py[cod]
*$py.class
*.so
build/
dist/
wheels/
*.egg-info/

# Environments
.venv/
venv/
ENV/
env/

# Secrets & Credentials
.env
.env.*
*.pem
*.key
*.cert
id_rsa
id_ed25519

# IDEs
.vscode/
.idea/

# Testing
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/

# Logs & Temporary Files
*.log
tmp/
```

---

## 3. Essential Docker CLI Cheat Sheet

```bash
# Build an image using BuildKit with tag
DOCKER_BUILDKIT=1 docker build -t my-api:v1.0.0 .

# Run container in background with port mapping, resource limits, and non-root security
docker run -d \
  --name api-server \
  -p 8000:8000 \
  --memory="512m" \
  --cpus="1.5" \
  --read-only \
  --cap-drop=ALL \
  --tmpfs /tmp:rw,noexec,nosuid \
  my-api:v1.0.0

# View real-time container log stream
docker logs -f --tail 100 api-server

# Inspect live container healthcheck status
docker inspect --format='{{json .State.Health}}' api-server | jq

# Execute an interactive bash session inside running container
docker exec -it api-server /bin/sh

# View container resource utilization (CPU %, MEM %, I/O)
docker stats api-server

# Clean dangling images, stopped containers, and unused build cache
docker system prune -af --volumes
```

---

## 4. 10 Rapid Recall Flashcards

| # | Question | Answer |
| :-- | :--- | :--- |
| **1** | What three Linux kernel primitives provide container isolation? | **Namespaces** (visibility), **cgroups** (resource limits), and **OverlayFS** (layered union filesystem). |
| **2** | What happens when a container exceeds its cgroup memory limit? | The Linux kernel invokes the **OOM-Killer**, terminating the process immediately with **`SIGKILL` (Exit Code 137)**. |
| **3** | Why should you use Debian Slim over Alpine for Python backends? | Alpine uses `musl libc` which cannot run standard `manylinux` wheels, causing slow compilation from source and memory fragmentation. |
| **4** | Why must `COPY requirements.txt` precede `COPY . .`? | To leverage Docker layer caching: dependencies are cached until `requirements.txt` changes, avoiding re-installing packages on every code commit. |
| **5** | Why should `CMD` use the exec form `["uvicorn", ...]` rather than shell form? | Shell form executes `/bin/sh -c` as PID 1, which **swallows `SIGTERM` signals**, preventing graceful shutdown. |
| **6** | What is the PID 1 problem in Docker containers? | PID 1 disables default signal handling and must reap orphaned zombie processes (`<defunct>`); solved using **`tini`** or **`dumb-init`**. |
| **7** | What is the purpose of `ENV PYTHONUNBUFFERED=1`? | Disables Python stdout/stderr stream buffering, ensuring log lines appear immediately in `docker logs` and monitoring pipelines. |
| **8** | How do Multi-Stage builds enhance container security? | Compilers (`gcc`, `make`) and development header files remain in the builder stage and are **not shipped in the final runtime image**. |
| **9** | What security flag enforces that application files cannot be modified at runtime? | The **`--read-only`** flag, which mounts the container root filesystem as immutable read-only. |
| **10** | How do BuildKit cache mounts (`--mount=type=cache`) improve CI builds? | They persist `/root/.cache/pip` between builds on the host machine, reusing cached wheels even when `requirements.txt` changes. |
