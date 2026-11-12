# Day 61: Docker Fundamentals — Enterprise Practical Exercises

Three production-level enterprise challenges with architectural requirements, verification criteria, and complete, production-ready solutions.

---

## Exercise 1: High-Performance Multi-Stage C-Extension Wheel Builder

### Problem Statement
A high-throughput FastAPI machine learning microservice depends on heavy C-extensions (`numpy`, `cryptography`, `psycopg2-binary`, `scipy`). 
- A standard naive `pip install` inside a single-stage container requires installing `build-essential`, `g++`, `gcc`, `gfortran`, `python3-dev`, and `libpq-dev`, which results in a container image exceeding $1.3\text{ GB}$ in size.
- Furthermore, having compilers (`gcc`, `g++`) present inside the production runtime presents a severe security risk (allowing attackers who exploit remote code execution to compile local exploit payloads on the fly).

### Requirements
1. Design a **Multi-Stage Dockerfile** using `python:3.11-slim-bookworm`.
2. **Stage 1 (Builder)**:
   - Install compilation toolchains.
   - Use `pip wheel --no-cache-dir --wheel-dir=/root/wheels` to compile dependencies into binary `.whl` files.
3. **Stage 2 (Runtime)**:
   - Use a fresh `python:3.11-slim-bookworm` base.
   - Install only required runtime dynamic shared libraries (`libpq5`, `libgfortran5`).
   - Copy the pre-compiled `.whl` files from the builder stage and install with `pip install --no-index --find-links=/root/wheels`.
   - Remove the temporary `.whl` files.
   - Enforce an unprivileged `appuser` (`UID 10001`).
4. Resulting production image size must be under $180\text{ MB}$ (an $86\%$ reduction).

### Solution

```dockerfile
# ==============================================================================
# STAGE 1: Compiler & Wheel Builder
# ==============================================================================
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Install compilation toolchains
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gfortran \
    libpq-dev \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Pre-compile dependencies into standalone binary wheels
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

# ==============================================================================
# STAGE 2: Hardened Minimal Production Runtime
# ==============================================================================
FROM python:3.11-slim-bookworm AS runner

# Install only the runtime dynamic libraries (NO gcc, NO build-essential!)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libopenblas0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create deterministic unprivileged non-root system user
RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy pre-compiled binary wheels from builder stage
COPY --from=builder /wheels /wheels
COPY requirements.txt .

# Install directly from local wheels without network or compilers, then purge wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels requirements.txt

# Copy application source code
COPY --chown=appuser:appgroup . /app

# Drop to unprivileged user
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Exercise 2: Zero-Trust Container Hardening (Read-Only Root Filesystem & Dropped Capabilities)

### Problem Statement
A banking backend processing PCI-DSS financial transactions must run under strict **Zero-Trust Container Security**:
- Attackers must not be able to modify any file inside `/app` or execute unauthorized binaries.
- The root filesystem (`/`) must be mounted as **Read-Only** (`--read-only`).
- The application requires temporary write access for PID locks, session files, and FastAPI file uploads, which must be routed to an in-memory `tmpfs` mount that disallows execution (`noexec`).
- All Linux capabilities must be dropped (`--cap-drop=ALL`).

### Requirements
1. Author the hardened Dockerfile configured to run cleanly under a read-only root filesystem.
2. Provide the production `docker run` command and `docker-compose.yml` specification implementing:
   - `--read-only`
   - `--cap-drop=ALL`
   - `--security-opt=no-new-privileges:true`
   - `--tmpfs /tmp:rw,noexec,nosuid,size=64m`
   - `--tmpfs /run:rw,noexec,nosuid,size=16m`
3. Verify that FastAPI application logs to standard output (`stdout`) without attempting to write log files to read-only directories.

### Solution

#### Hardened `Dockerfile`
```dockerfile
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Ensure /app is owned by root and read-only for appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R root:root /app && chmod -R 555 /app

# Configure Python cache directory to point to writable /tmp
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache

USER appuser

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### Production `docker run` Command
```bash
docker run -d \
  --name secure-banking-api \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /run:rw,noexec,nosuid,size=16m \
  -p 8000:8000 \
  banking-api:v1.0.0
```

#### Production `docker-compose.yml` Security Spec
```yaml
version: "3.8"

services:
  api:
    image: banking-api:v1.0.0
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=67108864
      - /run:rw,noexec,nosuid,size=16777216
    ports:
      - "8000:8000"
    restart: on-failure
```

---

## Exercise 3: Production Graceful Shutdown & Signal Forwarding with `dumb-init`

### Problem Statement
When orchestrators (Kubernetes, AWS ECS, Docker Compose) perform a rolling update or autoscaling scale-down, they send a `SIGTERM` signal to the container, followed by a grace period (typically 30 seconds). If the application does not catch `SIGTERM` or if the signal is swallowed by `/bin/sh`, the orchestrator forcefully sends `SIGKILL` after the timeout, severing active HTTP requests mid-flight.

### Requirements
1. Implement a complete Python FastAPI backend with an asynchronous lifespan context manager that registers signal listeners and simulates connection draining.
2. Integrate `dumb-init` or `tini` as the container `ENTRYPOINT`.
3. Verify that receiving `SIGTERM` triggers an ordered sequence:
   - Stop accepting new HTTP requests (`HTTP 503 Service Unavailable` for incoming traffic).
   - Wait for existing in-flight database transactions and Celery tasks to finish.
   - Close async database connection pools (`await engine.dispose()`) and Redis connections (`await redis.close()`).
   - Exit cleanly with status code `0` in under 3 seconds.

### Solution

#### Python Application (`main.py`)
```python
import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, status

class GracefulShutdownManager:
    def __init__(self):
        self.is_shutting_down = False
        self.active_requests = 0

    def trigger_shutdown(self, signum, frame):
        print(f"[SHUTDOWN] Received signal {signal.Signals(signum).name}. Commencing graceful drain...")
        self.is_shutting_down = True

shutdown_manager = GracefulShutdownManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register POSIX signal handlers on event loop
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: shutdown_manager.trigger_shutdown(s, None))
        except NotImplementedError:
            # Fallback for platforms without loop.add_signal_handler support
            signal.signal(sig, shutdown_manager.trigger_shutdown)

    print("[STARTUP] Application initialized. Database connection pool established.")
    yield

    # Teardown logic upon shutdown completion
    print("[TEARDOWN] Draining remaining active connections...")
    while shutdown_manager.active_requests > 0:
        print(f"[TEARDOWN] Waiting on {shutdown_manager.active_requests} in-flight requests...")
        await asyncio.sleep(0.5)

    print("[TEARDOWN] Disposing SQLAlchemy engine and closing Redis pools.")
    await asyncio.sleep(0.2)  # Simulating async pool disposal
    print("[TEARDOWN] Clean shutdown complete. Exiting with code 0.")

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def connection_drain_middleware(request, call_next):
    if shutdown_manager.is_shutting_down:
        return Response(content="Server is shutting down", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    shutdown_manager.active_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        shutdown_manager.active_requests -= 1

@app.get("/health")
async def healthcheck():
    if shutdown_manager.is_shutting_down:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"status": "healthy"}
```

#### Production `Dockerfile` with `tini`
```dockerfile
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000

# tini acts as PID 1, forwards SIGTERM, and reaps zombie child processes
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```
