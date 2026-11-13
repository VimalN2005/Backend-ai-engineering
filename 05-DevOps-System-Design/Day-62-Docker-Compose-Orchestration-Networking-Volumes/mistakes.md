# Day 62: Docker Compose — 7 Critical Production Antipatterns

A deep engineering analysis of the 7 most dangerous architectural antipatterns in Docker Compose multi-container orchestration, detailing failure mechanisms, bad vs. good code examples, and production remediations.

---

## 1. Antipattern 1: Using Naive `depends_on` Without `condition: service_healthy`

### The Failure Mechanism
In Docker Compose, declaring `depends_on: ["postgres"]` merely guarantees that the Docker daemon initiates the `postgres` container process before launching the dependent application container. 

However, starting a PostgreSQL container takes only ~100 milliseconds, while initializing the database engine (reading configuration, allocating shared buffers, replaying Write-Ahead Logs, and binding the TCP listener to `0.0.0.0:5432`) takes 3 to 10 seconds. 

When your FastAPI application starts, its SQLAlchemy connection pool attempts immediate TCP handshakes with `postgres:5432`. Because the port is not yet listening, the app throws an immediate `ConnectionRefusedError`, crashes, and exits with code `1`.

### Bad Code (Antipattern)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: app_db

  web:
    build: .
    # ANTI-PATTERN: Only checks that postgres container process has started!
    # Does NOT guarantee postgres is ready to accept database connections!
    depends_on:
      - postgres
```

### Good Code (Production Standard)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: app_db
      POSTGRES_USER: app_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app_user -d app_db"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    volumes:
      - postgres_data:/var/lib/postgresql/data

  web:
    build: .
    # PRODUCTION STANDARD: Blocks until pg_isready returns exit code 0!
    depends_on:
      postgres:
        condition: service_healthy
```

---

## 2. Antipattern 2: Exposing Internal Database Ports Directly to the Host Network

### The Failure Mechanism
Developers frequently declare `ports: ["5432:5432"]` on PostgreSQL and `ports: ["6379:6379"]` on Redis so they can connect with desktop GUI tools (e.g., DBeaver, TablePlus, Redis Insight).

In production, `ports: ["5432:5432"]` instructs the Docker daemon to modify the host's `iptables` NAT table, binding `0.0.0.0:5432` to the public host interface. This exposes your internal database directly to the public internet, bypassing host firewalls (like UFW, which Docker notoriously overrides by default) and subjecting the database to automated brute-force attacks and zero-day exploits.

### Bad Code (Antipattern)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    # CRITICAL VULNERABILITY: Binds 0.0.0.0:5432 on the host public network!
    ports:
      - "5432:5432"

  redis:
    image: redis:7.2-alpine
    # CRITICAL VULNERABILITY: Publicly exposes unauthenticated Redis!
    ports:
      - "6379:6379"
```

### Good Code (Production Standard)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    # PRODUCTION STANDARD: No ports exposed to host. Containers on the same
    # user-defined bridge network communicate internally via service DNS name: "postgres:5432"
    expose:
      - "5432"
    networks:
      - backend_tier

  # If local developer debugging is strictly necessary, bind EXCLUSIVELY to loopback:
  # ports:
  #   - "127.0.0.1:5432:5432"
```

---

## 3. Antipattern 3: Using Host Bind Mounts for Database Data in Production

### The Failure Mechanism
Mounting a local host directory directly into a database container (`volumes: ["./data/postgres:/var/lib/postgresql/data"]`):
1. **Filesystem Locking Hazards**: Host bind mounts on non-Linux hosts (macOS/Windows via Docker Desktop) use virtualized filesystem bridges (VirtioFS / gRPC-FUSE) which do not support standard Linux POSIX asynchronous I/O (`O_DIRECT`, `fdatasync`). This causes severe Write-Ahead Log (WAL) write corruption and sluggish performance.
2. **Permission Clashes**: The in-container `postgres` user (`UID 70`) conflicts with the host user's UID/GID, causing `permission denied` startup failures.

### Bad Code (Antipattern)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    # ANTI-PATTERN: Host bind mount causes file locking issues and permission collisions
    volumes:
      - ./local_data:/var/lib/postgresql/data
```

### Good Code (Production Standard)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    # PRODUCTION STANDARD: Docker Managed Named Volume with native ext4/xfs POSIX locking
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
    driver: local
```

---

## 4. Antipattern 4: Committing Plaintext Secrets Directly into `docker-compose.yml`

### The Failure Mechanism
Hardcoding database passwords, JWT secrets, and AWS keys directly in `docker-compose.yml`:
- Commits sensitive credentials into Git version control.
- Exposes credentials to any developer or CI runner with repository read access.
- Makes secret rotation impossible without code commits and container rebuilds.

### Bad Code (Antipattern)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      # CRITICAL LEAK: Plaintext production password committed to Git!
      POSTGRES_PASSWORD: "SuperSecretProductionDatabasePassword2026!"
      DATABASE_URL: "postgresql://admin:SuperSecretProductionDatabasePassword2026!@postgres:5432/prod"
```

### Good Code (Production Standard)
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-app_production}
      POSTGRES_USER: ${POSTGRES_USER:-app_user}
      # Variable interpolated from root .env or shell environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Error: POSTGRES_PASSWORD is mandatory!}
```

---

## 5. Antipattern 5: Single Flat Network (No Tier Segmentation)

### The Failure Mechanism
By default, if you do not specify custom networks, Docker Compose places all services into a single default bridge network (`<project>_default`). 

In a flat network:
- Every container can resolve and connect to every other container via DNS.
- If an attacker compromises your public-facing web server or reverse proxy via an application-level vulnerability (e.g. an arbitrary file upload or Server-Side Request Forgery - SSRF), they have **direct, unrestricted network access** to probe your PostgreSQL database, Redis cache, and internal message queues.

### Bad Code (Antipattern)
```yaml
# ANTI-PATTERN: All services share the default flat network.
services:
  proxy:
    image: nginx:alpine
    ports: ["80:80"]

  web:
    build: .

  postgres:
    image: postgres:16-alpine
    # Insecure: proxy can reach postgres:5432 directly!
```

### Good Code (Production Standard)
```yaml
# PRODUCTION STANDARD: Strict Dual-Tier Network Segmentation
services:
  proxy:
    image: nginx:alpine
    ports: ["80:80"]
    networks:
      - frontend_tier  # Cannot communicate with backend_tier!

  web:
    build: .
    networks:
      - frontend_tier  # Reached by proxy
      - backend_tier   # Connects to database and cache

  postgres:
    image: postgres:16-alpine
    networks:
      - backend_tier   # Isolated: proxy cannot resolve or reach postgres!

networks:
  frontend_tier:
    driver: bridge
  backend_tier:
    driver: bridge
    internal: true     # Disallows external ingress & egress
```

---

## 6. Antipattern 6: Omitting Logging Constraints (Host Disk Exhaustion)

### The Failure Mechanism
Docker's default logging driver (`json-file`) stores all container `stdout` and `stderr` logs on the host filesystem under `/var/lib/docker/containers/<id>/<id>-json.log`.

By default, Docker applies **no size limits** to these log files. A high-throughput FastAPI application or Celery worker logging millions of requests can generate 50+ Gigabytes of log files within days, completely exhausting the host's root disk (`/`), causing database crashes, write freezes, and host kernel panic.

### Bad Code (Antipattern)
```yaml
services:
  web:
    build: .
    # ANTI-PATTERN: No logging configuration. Logs grow indefinitely on host disk!
```

### Good Code (Production Standard)
```yaml
services:
  web:
    build: .
    logging:
      driver: "json-file"
      options:
        max-size: "10m"   # Rotate when file reaches 10 Megabytes
        max-file: "5"     # Retain at most 5 rotated files (50MB maximum total)
```

---

## 7. Antipattern 7: Misunderstanding Environment Variable Precedence

### The Failure Mechanism
Docker Compose resolves environment variables according to a strict hierarchy:
1. Shell environment variables (highest)
2. Compose project `.env` file
3. Service `environment:` key
4. Service `env_file:` file inclusions
5. Container image `ENV` (lowest)

A common mistake is editing the service `environment:` block in `docker-compose.yml` and expecting it to override an existing environment variable already exported in the developer's shell or CI runner. The shell variable silently takes precedence, leading to catastrophic configuration drift (e.g. running migrations against staging instead of production).

### Remediation
- Use the `.env` file strictly for **default values**.
- Use explicit interpolation with error checking: `${VARIABLE:?Error message}` to force developers and CI to supply required parameters.
- Verify active configuration with `docker compose config` before executing `docker compose up`.
