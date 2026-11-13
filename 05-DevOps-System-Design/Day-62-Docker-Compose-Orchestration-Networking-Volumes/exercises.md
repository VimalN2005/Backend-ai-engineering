# Day 62: Docker Compose — Enterprise Practical Exercises

Three production-level enterprise orchestration challenges with architectural requirements, verification criteria, and complete, verified solutions.

---

## Exercise 1: High-Availability Microservices Stack with Synchronized Healthchecks

### Problem Statement
A fintech backend requires an orchestrated multi-container deployment consisting of:
1. `postgres`: PostgreSQL 16 database storing financial ledgers.
2. `redis`: Redis 7.2 in-memory cache and Celery broker.
3. `api`: FastAPI application serving REST endpoints.
4. `worker`: Celery background worker consuming asynchronous ledger settlement tasks.
5. `scheduler`: Celery Beat cron daemon dispatching periodic tasks.

### Requirements
1. The `api` and `worker` services must **never start** until both `postgres` and `redis` have passed their respective health probes.
2. PostgreSQL healthcheck must verify database readiness using `pg_isready -U postgres_user -d app_db`.
3. Redis healthcheck must verify ping response using `redis-cli ping`.
4. The `api` container must implement an HTTP healthcheck at `/health`.
5. All services must be configured with automatic restarts (`unless-stopped`) and constrained log rotation (`max-size: 10m`, `max-file: 3`).

### Solution (`docker-compose.yml`)

```yaml
version: "3.8"

services:
  # ----------------------------------------------------------------------------
  # 1. PostgreSQL Ledger Database
  # ----------------------------------------------------------------------------
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: app_db
      POSTGRES_USER: postgres_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Error: POSTGRES_PASSWORD required}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres_user -d app_db"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - backend_net
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # ----------------------------------------------------------------------------
  # 2. Redis Cache & Broker
  # ----------------------------------------------------------------------------
  redis:
    image: redis:7.2-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes", "--maxmemory", "256mb", "--maxmemory-policy", "volatile-lru"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
    volumes:
      - redis_data:/data
    networks:
      - backend_net
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # ----------------------------------------------------------------------------
  # 3. FastAPI Web Application
  # ----------------------------------------------------------------------------
  api:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres_user:${POSTGRES_PASSWORD}@postgres:5432/app_db
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s
    ports:
      - "8000:8000"
    networks:
      - backend_net
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # ----------------------------------------------------------------------------
  # 4. Celery Task Worker
  # ----------------------------------------------------------------------------
  worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["celery", "-A", "tasks.celery_app", "worker", "--loglevel=info", "-c", "4"]
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres_user:${POSTGRES_PASSWORD}@postgres:5432/app_db
      CELERY_BROKER_URL: redis://redis:6379/1
    networks:
      - backend_net
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # ----------------------------------------------------------------------------
  # 5. Celery Beat Scheduler
  # ----------------------------------------------------------------------------
  scheduler:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["celery", "-A", "tasks.celery_app", "beat", "--loglevel=info"]
    restart: unless-stopped
    environment:
      CELERY_BROKER_URL: redis://redis:6379/1
    networks:
      - backend_net
    depends_on:
      redis:
        condition: service_healthy
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

networks:
  backend_net:
    driver: bridge

volumes:
  postgres_data:
  redis_data:
```

---

## Exercise 2: Dual-Tier Zero-Trust Isolated Network Architecture

### Problem Statement
An enterprise e-commerce platform must comply with strict PCI-DSS network segmentation rules:
1. Public incoming HTTP/HTTPS traffic must terminate exclusively at an Nginx reverse proxy on a `public_dmz` network.
2. The FastAPI `order-api` service connects both to `public_dmz` (to receive proxy requests) and `private_data` (to write transactions).
3. The PostgreSQL database and Redis cluster must exist strictly on an **isolated internal network** (`private_data`) with `internal: true`.
4. Even if the Nginx reverse proxy is compromised by an attacker via a remote buffer overflow, the attacker must be **unable to reach or resolve** the PostgreSQL database.

### Requirements
1. Author the `docker-compose.yml` implementing the dual-tier architecture.
2. Define `public_dmz` and `private_data` with explicit subnets (`172.20.0.0/16` and `172.21.0.0/16`).
3. Set `internal: true` on `private_data` to forbid external ingress and egress.
4. Verify network isolation using `docker inspect` and container ping simulations.

### Solution (`docker-compose.yml`)

```yaml
version: "3.8"

services:
  # ----------------------------------------------------------------------------
  # Public DMZ: Nginx Reverse Proxy
  # ----------------------------------------------------------------------------
  gateway:
    image: nginx:1.25-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    networks:
      - public_dmz
    depends_on:
      order-api:
        condition: service_healthy

  # ----------------------------------------------------------------------------
  # Application Layer: Bridge between DMZ and Data Tier
  # ----------------------------------------------------------------------------
  order-api:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    expose:
      - "8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://app_user:${DB_PASSWORD}@db:5432/orders_db
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 5s
      timeout: 3s
      retries: 3
    networks:
      - public_dmz    # Talk to Nginx Gateway
      - private_data  # Talk to PostgreSQL Database
    depends_on:
      db:
        condition: service_healthy

  # ----------------------------------------------------------------------------
  # Isolated Data Tier: Zero Public Visibility
  # ----------------------------------------------------------------------------
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: orders_db
      POSTGRES_USER: app_user
      POSTGRES_PASSWORD: ${DB_PASSWORD:?Error: DB_PASSWORD required}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app_user -d orders_db"]
      interval: 5s
      timeout: 3s
      retries: 5
    volumes:
      - orders_data:/var/lib/postgresql/data
    networks:
      - private_data  # ONLY accessible by order-api; invisible to gateway!

networks:
  public_dmz:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16

  private_data:
    driver: bridge
    internal: true  # Prevents external routing completely
    ipam:
      config:
        - subnet: 172.21.0.0/16

volumes:
  orders_data:
```

---

## Exercise 3: Production Named Volume Backup & Disaster Recovery Automation

### Problem Statement
In production, named volumes store mission-critical data. If a physical host fails, the named volume must be recoverable from automated backups stored in Amazon S3 or a secondary volume. 

You must design a containerized backup job in `docker-compose.yml` that mounts the existing `postgres_data` volume as **read-only (`:ro`)**, streams a consistent tarball compression, and writes it to an isolated backup destination without taking down the running database.

### Requirements
1. Declare a secondary service `db-backup` in `docker-compose.yml`.
2. Use Docker Compose **Profiles** (`profiles: ["backup"]`) so the backup container does not run during standard `docker compose up` and is only invoked on-demand or by cron (`docker compose --profile backup run --rm db-backup`).
3. Mount the production `postgres_data` volume with the `:ro` flag.
4. Execute `pg_dump` over the internal network to guarantee transactional consistency, followed by `gzip` compression into a named `backup_storage` volume.

### Solution

```yaml
version: "3.8"

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: production_db
      POSTGRES_USER: postgres_admin
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - internal_net

  # ----------------------------------------------------------------------------
  # On-Demand / Scheduled Transactional Backup Worker
  # ----------------------------------------------------------------------------
  db-backup:
    image: postgres:16-alpine
    profiles:
      - backup  # Runs ONLY when explicitly invoked!
    environment:
      PGHOST: db
      PGUSER: postgres_admin
      PGPASSWORD: ${POSTGRES_PASSWORD}
      PGDATABASE: production_db
    volumes:
      - backup_storage:/backups
    networks:
      - internal_net
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        TIMESTAMP=$$(date +%Y%m%d_%H%M%S)
        BACKUP_FILE="/backups/backup_$$TIMESTAMP.sql.gz"
        echo "[BACKUP] Commencing consistent pg_dump to $$BACKUP_FILE..."
        pg_dump -U postgres_admin -d production_db | gzip > "$$BACKUP_FILE"
        echo "[BACKUP] Backup completed successfully. Size: $$(du -h "$$BACKUP_FILE" | cut -f1)"

networks:
  internal_net:
    driver: bridge

volumes:
  postgres_data:
  backup_storage:
```

### Execution Command
```bash
# Execute transactional backup without disturbing running web application
docker compose --profile backup run --rm db-backup
```
