# Day 62: Docker Compose Multi-Container Orchestration, Networking & Volumes

An enterprise-grade architectural blueprint for multi-container orchestration with Docker Compose: service dependency graphs, health-checked lifecycles, network segmentation, embedded DNS resolution, and persistent storage management for production Python backends.

---

## 1. The Multi-Container Orchestration Problem

Modern backend applications are rarely monolithic single-process systems. A standard enterprise Python stack consists of multiple cooperating microservices and infrastructure dependencies:

```
                                  INTERNET / CLIENTS
                                          │
                                          ▼
                         ┌─────────────────────────────────┐
                         │   Nginx Reverse Proxy / SSL     │
                         │   Port 80 / 443 (Host Exposed)  │
                         └─────────────────────────────────┘
                                          │
                ┌─────────────────────────┴─────────────────────────┐
                │                                                   │
                ▼                                                   ▼
┌───────────────────────────────┐                   ┌───────────────────────────────┐
│     FastAPI Web Service       │                   │    Flower Celery Monitor      │
│     (Async REST API)          │                   │    (Protected Dashboard)      │
└───────────────────────────────┘                   └───────────────────────────────┘
        │               │                                   │               │
        │ SQL           │ Redis Pub/Sub                     │ Celery Events │
        ▼               ▼                                   ▼               ▼
┌───────────────┐ ┌───────────────┐                 ┌───────────────────────────────┐
│  PostgreSQL   │ │  Redis Cache  │◄────────────────│        Celery Worker          │
│  Database 16  │ │  & Broker 7   │  Task Enqueue   │   (Async Background Tasks)    │
└───────────────┘ └───────────────┘                 └───────────────────────────────┘
        ▲                                                           ▲
        │                                                           │
        └───────────────────────────────────────────────────────────┘
                                 DB Writes / Reads
```

Running this multi-component topology via manual `docker run` commands is fragile, error-prone, and unsustainable:
- Containers must be started in an exact dependency order (e.g. database must be healthy before web API starts).
- Virtual network bridges and port mappings must be created manually.
- IP addresses change dynamically whenever containers restart, breaking hardcoded configuration.
- Storage volumes must be detached and re-attached without corrupting database files.

**Docker Compose** formalizes this topology into an **immutable, declarative Infrastructure-as-Code (IaC) specification** (`docker-compose.yml`).

---

## 2. Docker Compose Specification & Architecture

Docker Compose operates on the **Compose Specification** (standardized across Docker CLI `docker compose` v2 written in Go, superseding the legacy Python `docker-compose` v1).

```
┌────────────────────────────────────────────────────────────────────────┐
│                        COMPOSE FILE ANATOMY                            │
├────────────────────────────────────────────────────────────────────────┤
│ services:        # Container definitions, replicas, builds, and images │
│ networks:        # Virtual software bridges and network isolation      │
│ volumes:         # Persistent stateful disk storage allocations        │
│ configs:         # Static configuration files (nginx.conf, certs)      │
│ secrets:         # Sensitive credentials passed via in-memory mounts   │
└────────────────────────────────────────────────────────────────────────┘
```

When you execute `docker compose up -d`:
1. **DAG Construction**: Compose parses the service definitions and builds a Directed Acyclic Graph (DAG) based on `depends_on` and network links.
2. **Resource Provisioning**: It creates custom user-defined bridge networks and named volumes.
3. **Sequential Execution**: It launches containers in topological sort order, evaluating readiness probes before releasing dependent services.

---

## 3. Service Dependencies & The Health-Checked Startup Contract

A widespread failure in multi-container setups is the **TCP Race Condition**.

### The Naive `depends_on` Pitfall
In Docker Compose v1, `depends_on` only checked whether the underlying container process had started (`status: created/running`).
- When a PostgreSQL container starts, the container process begins running within 100 milliseconds.
- However, PostgreSQL requires 3 to 10 seconds to initialize shared memory, replay Write-Ahead Logs (WAL), run crash recovery, and bind to port `5432`.
- If the FastAPI container starts immediately, its SQLAlchemy connection pool issues connection requests to `postgres:5432`, fails with `ConnectionRefusedError`, and terminates with crash loop status code `1`!

```
NAIVE STARTUP (RACE CONDITION & CRASH):
Postgres Container Starts  ──────► [Initializing WAL / 5s delay] ──────► Port 5432 Open
                                         ▲
                                         │ (Crash: Connection Refused!)
FastAPI Container Starts   ──────────────┴─────────────────────────────► [CRASH: Exit 1]
```

### The Production Solution: `condition: service_healthy`
Docker Compose v2 introduces active healthcheck integration into `depends_on`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: app_production
      POSTGRES_USER: postgres_admin
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres_admin -d app_production"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    volumes:
      - postgres_data:/var/lib/postgresql/data

  web:
    build:
      context: .
      dockerfile: Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
```

```
HEALTH-CHECKED STARTUP (SYNCHRONIZED):
Postgres Container Starts  ──► [pg_isready Probe Fails] ──► [Probe 200 OK: HEALTHY]
                                                                  │
                                                                  ▼ (Unlocks dependency)
FastAPI Container Starts   ──────────────────────────────────────► [Connects to DB cleanly!]
```

---

## 4. Multi-Tier Network Segmentation & Isolation

In a secure cloud deployment, **services should only be granted access to the minimal network boundaries required to fulfill their business purpose**.

### Network Security Invariants
- **Public / DMZ Tier**: Web proxy (Nginx) and API service. Open to incoming host traffic.
- **Internal / Data Tier**: PostgreSQL database, Redis cache, and Celery workers. **Never exposed to public host ports!**
- If an attacker compromises the web proxy via an HTTP vulnerability, they cannot directly probe database network packets or issue raw TCP exploits to internal services.

```
+─────────────────────────────────────────────────────────────────────────────+
|                         MULTI-TIER DOCKER NETWORKS                          |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  FRONTEND NETWORK (bridge: 172.28.0.0/16)                                   |
|  ┌──────────────────┐           ┌──────────────────┐                        |
|  │   Nginx Proxy    │ ────────► │   FastAPI Web    │                        |
|  │   (Port 80/443)  │           │   (Port 8000)    │                        |
|  └──────────────────┘           └──────────────────┘                        |
|                                           │                                 |
+───────────────────────────────────────────┼─────────────────────────────────+
|                                           │                                 |
|  BACKEND NETWORK (bridge: 172.29.0.0/16)  │                                 |
|  ┌──────────────────┐           ┌─────────┴────────┐    ┌─────────────────┐ |
|  │  Celery Worker   │ ────────► │  PostgreSQL 16   │ ◄──│ Celery Beat     │ |
|  │  (Async tasks)   │           │  (NO host ports!)│    │ (Scheduler)     │ |
|  └─────────┬────────┘           └──────────────────┘    └────────┬────────┘ |
|            │                                                     │          |
|            └──────────────────► ┌──────────────────┐ ◄───────────┘          |
|                                 │   Redis 7        │                        |
|                                 │  (NO host ports!)│                        |
|                                 └──────────────────┘                        |
+─────────────────────────────────────────────────────────────────────────────+
```

### Network Topology Implementation in Compose
```yaml
networks:
  frontend_tier:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16
  backend_tier:
    driver: bridge
    internal: true  # Prevents external egress/ingress traffic entirely!
    ipam:
      config:
        - subnet: 172.29.0.0/16
```

---

## 5. Embedded Docker DNS Resolver (`127.0.0.11`)

When containers run inside a user-defined bridge network, the Docker engine automatically provisions an **embedded DNS server** at IP address `127.0.0.11`.

```
CONTAINER: web-api (172.29.0.4)
   │
   │  gethostbyname("postgres")
   ▼
Docker Embedded DNS Daemon (127.0.0.11:53)
   │
   ├─► Checks service name alias table in memory
   ├─► Resolves "postgres" ──► 172.29.0.2
   └─► Returns A Record to web-api
```

### DNS Resolution Mechanics
- In the **Default Bridge Network** (`docker0`), container-to-container DNS resolution by service name is **disabled** (containers can only communicate via explicit IP addresses or legacy `--link` flags).
- In **User-Defined Bridge Networks** (automatically provisioned by Compose), DNS resolution is enabled out-of-the-box.
- If multiple container replicas exist (e.g. `docker compose up --scale web=3`), the Docker DNS daemon automatically performs **Round-Robin DNS load balancing**, returning all 3 container IP addresses in rotating order.

---

## 6. Storage Architecture: Named Volumes vs. Bind Mounts vs. tmpfs

Container storage is ephemeral by default. When a container terminates, its read-write `upperdir` layer is permanently destroyed. Persistent data requires explicit volume mounts.

```
+─────────────────────────────────────────────────────────────────────────────+
|                         CONTAINER STORAGE OPTIONS                           |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  1. NAMED VOLUMES:                                                          |
|     Host: /var/lib/docker/volumes/postgres_data/_data                       |
|     Container: /var/lib/postgresql/data                                     |
|     -> Managed by Docker engine. High performance, native POSIX locks.      |
|     -> Production standard for databases (PostgreSQL, MySQL, Redis).        |
|                                                                             |
|  2. BIND MOUNTS:                                                            |
|     Host: /home/user/project/src                                            |
|     Container: /app/src                                                     |
|     -> Direct mapping of host filesystem paths.                             |
|     -> Ideal for local hot-reloading development; dangerous in production!  |
|                                                                             |
|  3. TMPFS MOUNTS:                                                           |
|     Host: RAM only (never written to physical disk)                         |
|     Container: /tmp or /run                                                 |
|     -> Ephemeral, zero disk I/O, secure for tokens & temporary PID files.    |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### Comparison Matrix

| Storage Dimension | Named Volumes | Bind Mounts | tmpfs Mounts |
| :--- | :--- | :--- | :--- |
| **Host Storage Location** | Managed by Docker (`/var/lib/docker/volumes/`) | Arbitrary host path (e.g. `./src`) | In-memory RAM only |
| **Lifecycle** | Survives container deletion; requires `docker volume rm` | Survives container deletion; host file | Destroyed when container stops |
| **Filesystem Performance** | Native Linux ext4/xfs speed | OS filesystem driver (Slow on macOS/Windows) | Maximum speed (Direct RAM I/O) |
| **Security & Permissions** | Isolated from host users; managed permissions | Vulnerable to host permission conflicts | Ephemeral; never touches disk |
| **Best Used For** | **PostgreSQL, Redis, MinIO, RabbitMQ** | **Local development code hot-reload** | **Secrets, session tokens, `/tmp`** |

---

## 7. Environment Variable Precedence & Interpolation

Docker Compose provides powerful environment variable substitution, but understanding the **strict resolution precedence** is essential to prevent configuration drift:

```
HIGHEST PRECEDENCE (Overrides all below)
  ▲
  │  1. CLI Environment Variables (e.g. `POSTGRES_DB=prod docker compose up`)
  │  2. Shell Environment Variables export on the host system
  │  3. The `.env` file located in the Compose project directory
  │  4. Service-level `environment:` block in `docker-compose.yml`
  │  5. Service-level `env_file:` file inclusions
  │  6. Default values defined in image `Dockerfile` (`ENV VAR=value`)
  ▼
LOWEST PRECEDENCE
```

### Interpolation Syntax
```yaml
services:
  web:
    image: "my-app:${APP_VERSION:-latest}"  # Fallback to 'latest' if unset
    environment:
      DEBUG: "${DEBUG:?Error: DEBUG variable must be explicitly defined!}"
      DATABASE_URL: "postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}"
```

---

## 8. Log Management & Host Disk Protection

A frequent production failure occurs when containers write gigabytes of standard output logs, silently filling the host node's root disk (`/var/lib/docker/containers/`) and causing total kernel freeze.

### Docker JSON Logging Limits
Always configure log driver constraints across all services in `docker-compose.yml`:

```yaml
services:
  web:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"   # Rotate log file when it reaches 10 Megabytes
        max-file: "5"     # Keep at most 5 rotated files (Max 50MB per container)
```

---

## 9. Production Multi-Container Compose Architecture

```yaml
version: "3.8"

services:
  # ----------------------------------------------------------------------------
  # 1. Reverse Proxy (Gateway)
  # ----------------------------------------------------------------------------
  proxy:
    image: nginx:1.25-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    networks:
      - frontend_tier
    depends_on:
      web:
        condition: service_healthy

  # ----------------------------------------------------------------------------
  # 2. FastAPI Application
  # ----------------------------------------------------------------------------
  web:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    expose:
      - "8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres_user:secure_pwd@postgres:5432/app_db
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s
    networks:
      - frontend_tier
      - backend_tier
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  # ----------------------------------------------------------------------------
  # 3. Celery Asynchronous Task Worker
  # ----------------------------------------------------------------------------
  worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["celery", "-A", "tasks.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres_user:secure_pwd@postgres:5432/app_db
      CELERY_BROKER_URL: redis://redis:6379/1
    networks:
      - backend_tier
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy

  # ----------------------------------------------------------------------------
  # 4. PostgreSQL Database
  # ----------------------------------------------------------------------------
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: app_db
      POSTGRES_USER: postgres_user
      POSTGRES_PASSWORD: secure_pwd
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres_user -d app_db"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - backend_tier

  # ----------------------------------------------------------------------------
  # 5. Redis Cache & Broker
  # ----------------------------------------------------------------------------
  redis:
    image: redis:7.2-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
    volumes:
      - redis_data:/data
    networks:
      - backend_tier

networks:
  frontend_tier:
    driver: bridge
  backend_tier:
    driver: bridge
    internal: true

volumes:
  postgres_data:
    driver: local
  redis_data:
    driver: local
```

---

## 10. Production Decision Matrix

| Architectural Dimension | Naive Development Setup | Production Enterprise Standard |
| :--- | :--- | :--- |
| **Startup Synchronization** | Unchecked `depends_on: [postgres]` | Health-Checked `condition: service_healthy` |
| **Port Exposure** | All ports exposed to host (`5432:5432`, `6379:6379`) | Internal bridge network; database ports **unexposed** |
| **Database Storage** | Host bind mount (`./data:/var/lib/postgresql`) | Docker **Named Volume** (`postgres_data:/var/lib/...`) |
| **Network Topology** | Single flat default network | **Dual-Tier**: Public `frontend_tier` + Private `backend_tier` |
| **Log Management** | Unbounded JSON file (risk of host disk exhaustion) | Rotated logs (`max-size: 10m`, `max-file: 5`) |
| **Credentials** | Plaintext in `docker-compose.yml` | Environment files (`.env`) or Docker secrets |
| **DNS Resolution** | Hardcoded IP addresses or `--link` | Docker **Embedded DNS Resolver (`127.0.0.11`)** |
