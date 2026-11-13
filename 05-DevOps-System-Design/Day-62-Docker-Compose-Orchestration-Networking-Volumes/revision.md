# Day 62: Docker Compose — 5-Minute Rapid Revision

A rapid revision reference, cheat sheet, and flashcard collection for Docker Compose orchestration, multi-container networking, and named volumes.

---

## 1. Production Docker Compose Boilerplate

Save this complete, production-hardened template for enterprise FastAPI + PostgreSQL + Redis + Celery stacks:

```yaml
version: "3.8"

services:
  # 1. Reverse Proxy Gateway
  gateway:
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

  # 2. FastAPI Application
  web:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    expose:
      - "8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://app_user:${DB_PASSWORD}@postgres:5432/app_db
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
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"

  # 3. Celery Background Worker
  worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["celery", "-A", "tasks.celery_app", "worker", "--loglevel=info", "-c", "4"]
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://app_user:${DB_PASSWORD}@postgres:5432/app_db
      CELERY_BROKER_URL: redis://redis:6379/1
    networks:
      - backend_tier
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  # 4. PostgreSQL Database
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: app_db
      POSTGRES_USER: app_user
      POSTGRES_PASSWORD: ${DB_PASSWORD:?Error: DB_PASSWORD must be provided}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app_user -d app_db"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - backend_tier

  # 5. Redis Cache & Broker
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
      - backend_tier

networks:
  frontend_tier:
    driver: bridge
  backend_tier:
    driver: bridge
    internal: true

volumes:
  postgres_data:
  redis_data:
```

---

## 2. Docker Compose CLI Command Arsenal

```bash
# 1. Start all containers in background and recreate if config changed
docker compose up -d

# 2. View unified real-time logs across all services
docker compose logs -f --tail=100

# 3. View logs for a specific service
docker compose logs -f web

# 4. List status of all managed containers including health status
docker compose ps

# 5. Execute an interactive shell inside a running service container
docker compose exec web /bin/sh

# 6. Validate and display rendered Compose file with variable substitutions
docker compose config

# 7. Scale a stateless service horizontally to 3 replicas
docker compose up -d --scale web=3

# 8. Stop and remove all containers, networks (preserves volumes)
docker compose down

# 9. Stop, remove containers, and WIPE all named volumes (destructive!)
docker compose down -v
```

---

## 3. 10 Rapid Recall Flashcards

| # | Question | Answer |
| :-- | :--- | :--- |
| **1** | Why does naive `depends_on: ["postgres"]` fail? | It only checks process creation, not TCP socket readiness; causes application **`ConnectionRefusedError`** race condition. |
| **2** | What resolves the container startup race condition? | Using **`condition: service_healthy`** inside `depends_on`, combined with a valid container `healthcheck`. |
| **3** | What IP address does Docker provide for container DNS? | **`127.0.0.11`** (Docker Embedded DNS daemon on user-defined bridge networks). |
| **4** | Why should database ports never be declared with `ports:` in production? | `ports:` binds the port to `0.0.0.0` on the host, **publicly exposing the database to the internet**; use `expose:` instead. |
| **5** | What is the advantage of Named Volumes over Host Bind Mounts for databases? | Named volumes reside natively in `/var/lib/docker/volumes/` with **native ext4/xfs POSIX locking and I/O performance**, avoiding VM bridge file-locking bugs. |
| **6** | What does setting `internal: true` on a Compose network do? | Disables external routing and NAT masquerading, **completely blocking inbound and outbound public internet access**. |
| **7** | How does Docker handle DNS when a service has multiple replicas? | Performs **Round-Robin DNS load balancing**, rotating returned IP addresses across active healthy replicas. |
| **8** | What happens if you omit log limits (`max-size`, `max-file`) in Compose? | Unbounded JSON log files will grow continuously until they **exhaust the host node's root disk**, freezing the system. |
| **9** | What is the highest precedence for environment variable resolution in Compose? | **CLI-passed environment variables** override shell exports, which override `.env`, which overrides `docker-compose.yml`. |
| **10** | How do Compose Profiles (`profiles: ["backup"]`) work? | Services marked with a profile do not launch during standard `docker compose up` and run only when explicitly invoked. |
