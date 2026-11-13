# Day 62: Docker Compose — Principal/Staff Interview Q&As

Five deep, architectural interview questions with exhaustive answers expected of Senior, Staff, and Principal DevOps & Backend Engineers.

---

## Question 1: Explain how Docker's embedded DNS server (`127.0.0.11`) works under the hood on user-defined bridge networks, and why it is disabled on the default `docker0` bridge.

### Answer
In Docker, container name resolution by hostname (e.g. `postgres`, `redis`) is powered by an internal DNS daemon running inside the Docker engine daemon space, accessible inside every container at loopback address `127.0.0.11:53`.

### 1. Underlying Kernel Mechanics
When a container starts on a user-defined bridge network:
1. The container's `/etc/resolv.conf` is dynamically generated with:
   ```text
   nameserver 127.0.0.11
   options ndots:0
   ```
2. The Docker engine injects an `iptables` DNAT (Destination Network Address Translation) rule into the container's network namespace:
   ```bash
   iptables -t nat -A OUTPUT -d 127.0.0.11 -p udp --dport 53 -j DNAT --to-destination <docker-internal-dns-ip>:<random-port>
   ```
3. When the application issues `getaddrinfo("postgres")`:
   - UDP packet to `127.0.0.11:53` is intercepted by Docker's embedded DNS server.
   - The DNS daemon checks its in-memory table of active containers attached to that specific network.
   - If found, it returns the container's IP (e.g. `172.29.0.2`). If multiple replicas exist (e.g. scaled web instances), it rotates through the IPs in **Round-Robin** order.
   - If the name is not an internal container (e.g., `api.stripe.com`), Docker forwards the query upstream to the host's external DNS resolver (e.g., `8.8.8.8` or systemd-resolved).

### 2. Why is it Disabled on the Default `docker0` Bridge?
On the default bridge network (`docker0`):
- Containers can only communicate by explicit IP addresses or legacy `--link` flags.
- **Reason**: The default bridge is a legacy global shared network. Allowing automatic DNS resolution across all containers sharing `docker0` would violate multitenant isolation and cause naming collisions if two unrelated containers both named themselves `web` or `db`.
- User-defined networks (automatically created by Compose) isolate DNS scopes per network.

---

## Question 2: What are the failure modes of naive `depends_on: [service]` vs. `condition: service_healthy` in production orchestrations?

### Answer

### Naive `depends_on` (Process Level Only)
When you write:
```yaml
web:
  depends_on:
    - postgres
```
Docker Compose checks only that the kernel process for `postgres` has transitioned into the `running` state.
- **Failure Mode (TCP Startup Race Condition)**:
  1. Postgres process launches (`PID 1`).
  2. Postgres begins reading configuration files, performing crash recovery, and checking WAL consistency. During this 5–10 second window, the TCP socket `0.0.0.0:5432` is **closed**.
  3. Meanwhile, Docker Compose marks the dependency satisfied and launches `web`.
  4. `web` executes `asyncio.run(engine.connect())`, encounters `ConnectionRefusedError`, and crashes immediately.
  5. If `restart: on-failure` is configured, `web` restarts, hits rate-limiting exponential backoff, or permanently enters `CrashLoopBackOff`.

### Health-Checked `condition: service_healthy`
```yaml
web:
  depends_on:
    postgres:
      condition: service_healthy
```
- Docker Compose pauses `web` until PostgreSQL's declared `healthcheck` command (e.g. `pg_isready -U app_user -d app_db`) exits with status code `0`.
- This guarantees that PostgreSQL has opened its socket, passed authentication handshakes, and is actively accepting queries before the first application coroutine attempts connection.

---

## Question 3: Compare Named Volumes, Host Bind Mounts, and tmpfs mounts in Docker. What are the performance, locking, and security trade-offs for production databases?

### Answer

```
+────────────────────+─────────────────────────────+─────────────────────────────+
| Dimension          | Named Volumes               | Host Bind Mounts            |
+────────────────────+─────────────────────────────+─────────────────────────────+
| Storage Path       | /var/lib/docker/volumes/... | Arbitrary host directory    |
| Managed By         | Docker daemon               | Host OS user / permissions  |
| I/O Performance    | Native Linux ext4/xfs speed | Slower on macOS/Windows VM  |
| POSIX Direct I/O   | Full kernel POSIX compliance| Broken under FUSE/VirtioFS  |
| Security Boundary  | Isolated from host processes| Vulnerable to host overwrites|
| Production Database| **MANDATORY STANDARD**       | **STRICTLY DISCOURAGED**    |
+────────────────────+─────────────────────────────+─────────────────────────────+
```

### Detailed Trade-Offs

1. **Named Volumes (Production Standard)**:
   - Stored in Docker's managed storage directory (`/var/lib/docker/volumes/<name>/_data`).
   - Completely native Linux filesystem block allocation.
   - Guaranteed support for PostgreSQL's `fdatasync()` and SQLite's POSIX advisory file locking (`fcntl(F_SETLK)`).
   - Prevents host permission conflicts because permissions are initialized by the container's entrypoint.

2. **Host Bind Mounts (`./data:/var/lib/postgresql/data`)**:
   - Maps an arbitrary host directory.
   - On Linux, performance is fine, but on macOS or Windows (Docker Desktop), writes must cross the hypervisor boundary via VirtioFS or 9P, which drops database throughput by $50\text{--}80\%$ and can corrupt Write-Ahead Logs.
   - Ideal solely for mounting local application source code during development to enable hot-reloading (`uvicorn --reload`).

3. **`tmpfs` Mounts**:
   - Resides exclusively in host RAM. Never writes to disk.
   - Zero physical disk I/O; maximum memory-speed access.
   - Destroyed immediately when container stops.
   - Perfect for ephemeral session caches, IPC sockets, and storing decrypted TLS keys.

---

## Question 4: How does network segmentation in Docker Compose map to underlying Linux kernel primitives (`veth` pairs, Linux bridge `br-xxxx`, `iptables`)?

### Answer
When Docker Compose declares two networks (`frontend_tier` and `backend_tier`), the Docker daemon provisions isolated network namespaces and virtual bridges in the Linux kernel:

```
                            LINUX HOST KERNEL
+───────────────────────────────────────────────────────────────────────────+
|                                                                           |
|   BRIDGE 1: br-frontend (172.28.0.1)     BRIDGE 2: br-backend (172.29.0.1) |
|         ▲                   ▲                    ▲                 ▲      |
|         │ vethA1            │ vethB1             │ vethB2          │ vethC1
|         ▼                   ▼                    ▼                 ▼      |
|  ┌─────────────┐     ┌─────────────┐      ┌─────────────┐   ┌────────────┐|
|  │ Nginx Proxy │     │ FastAPI App │      │ FastAPI App │   │ PostgreSQL │|
|  │ (Net NS 1)  │     │ (Net NS 2)  │      │ (Net NS 2)  │   │ (Net NS 3) │|
|  └─────────────┘     └─────────────┘      └─────────────┘   └────────────┘|
|                                                                           |
+───────────────────────────────────────────────────────────────────────────+
```

### Kernel Mechanics Step-by-Step
1. **Linux Virtual Bridges**: The kernel creates two virtual software switches: `br-frontend` and `br-backend` using `brctl` / `ip link add type bridge`.
2. **Virtual Ethernet (`veth`) Pairs**:
   - For container `web` (attached to both networks), the kernel creates two pairs of `veth` cables.
   - One end of `vethB1` plugs into `br-frontend`, the other enters the container's network namespace as `eth0`.
   - One end of `vethB2` plugs into `br-backend`, the other enters the container's namespace as `eth1`.
3. **`iptables` Firewall Rules**:
   - Docker inserts rules into the `FORWARD` filter chain.
   - Packets originating from `br-frontend` are **explicitly blocked** from jumping directly to `br-backend` unless routed through a container with dual interfaces (`web`).
   - If `internal: true` is set on `br-backend`, Docker drops the default outbound NAT masquerade rule (`-o eth0 -j MASQUERADE`), preventing backend containers from initiating any outbound traffic to the public internet!

---

## Question 5: How do you implement Zero-Trust Secret Management in Docker Compose without leaking credentials?

### Answer
Zero-Trust credential management in Docker Compose follows 4 strict principles:

1. **Never Commit Plaintext Secrets**:
   - Use variable interpolation with mandatory enforcement:
     ```yaml
     POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?CRITICAL: POSTGRES_PASSWORD must be provided}
     ```
   - Store real secrets in `.env` or external secret managers (AWS Secrets Manager, HashiCorp Vault), and ensure `.env` is listed in `.gitignore`.

2. **Docker Compose Secrets (In-Memory File Mounts)**:
   - For sensitive credentials, prefer Docker secrets over environment variables.
   - Unlike environment variables (which leak into `docker inspect <container>`, `/proc/<pid>/environ`, and application crash tracebacks), secrets are mounted into an ephemeral in-memory `tmpfs` file at `/run/secrets/<name>`:
     ```yaml
     services:
       postgres:
         image: postgres:16-alpine
         environment:
           POSTGRES_PASSWORD_FILE: /run/secrets/db_password
         secrets:
           - db_password

     secrets:
       db_password:
         file: ./secrets/db_password.txt
     ```

3. **Dual Network Isolation**:
   - Internal databases must reside on networks with `internal: true`, preventing compromised database instances from exfiltrating credentials to external command-and-control servers.

4. **Verify Rendered Config via CI**:
   - In CI/CD pipelines, run `docker compose config` and scan with git-secrets or Trivy to guarantee that no unencrypted tokens are committed.
