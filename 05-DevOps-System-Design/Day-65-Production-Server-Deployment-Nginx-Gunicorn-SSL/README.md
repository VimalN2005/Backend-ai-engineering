# Day 65: Production Server Deployment: Nginx Reverse Proxy, Gunicorn & SSL (Certbot)

A complete architectural blueprint for deploying production Python web applications on Linux servers: Nginx event-driven reverse proxying, Gunicorn/Uvicorn master-worker process supervisors, Linux systemd unit architecture, UNIX domain socket IPC, automated Let's Encrypt TLS/SSL certificates via Certbot, and HTTP security header hardening.

---

## 1. Production Web Tier Architecture: The Three-Tier Model

In production engineering, **never expose Python application servers (Uvicorn, Gunicorn, Django, FastAPI) directly to the public internet**. 

Although modern ASGI servers like Uvicorn are fast, they are not designed to defend against Slowloris attacks, handle SSL/TLS handshakes at wire-speed, terminate static file requests with zero-copy kernel transfers, or buffer uncooperative client connections.

```
                              PRODUCTION THREE-TIER WEB ARCHITECTURE
+───────────────────────────────────────────────────────────────────────────────────────────+
|                                                                                           |
|  CLIENT BROWSERS & MOBILE APPS (Public Internet)                                          |
|                                       │                                                   |
|                                       ▼ HTTPS (Port 443 - TLS 1.3)                        |
|  ┌─────────────────────────────────────────────────────────────────────────────────────┐  |
|  │ NGINX REVERSE PROXY & LOAD BALANCER (Edge Tier)                                     │  |
|  │ - Terminates TLS/SSL & Enforces HSTS                                                │  |
|  │ - Absorbs Slowloris & HTTP DDoS Attacks                                             │  |
|  │ - Serves Static Assets via Zero-Copy `sendfile`                                     │  |
|  │ - Buffers Slow Client Requests & Compresses Output (Gzip)                           │  |
|  │ - Load Balances Upstream Requests (Least-Conn / Round-Robin)                         │  |
|  └─────────────────────────────────────────────────────────────────────────────────────┘  |
|                                       │                                                   |
|                                       ▼ UNIX Domain Socket (`/run/gunicorn.sock`)         |
|  ┌─────────────────────────────────────────────────────────────────────────────────────┐  |
|  │ GUNICORN PROCESS MANAGER (Application Tier - Systemd Managed)                       │  |
|  │ ┌─────────────────────────────────────────────────────────────────────────────────┐ │  |
|  │ │ Master Process (PID 1204 - Privilege Dropped to `www-data`, SIGHUP Supervisor)  │ │  |
|  │ └───────┬─────────────────────┬─────────────────────┬─────────────────────┬───────┘ │  |
|  │         │ fork()              │ fork()              │ fork()              │ fork()  │  |
|  │         ▼                     ▼                     ▼                     ▼         │  |
|  │   Worker 1 (Uvicorn)    Worker 2 (Uvicorn)    Worker 3 (Uvicorn)    Worker 4 (Uvicorn)│  |
|  │   (FastAPI Async Loop)  (FastAPI Async Loop)  (FastAPI Async Loop)  (FastAPI Async Loop│
|  └─────────────────────────────────────────────────────────────────────────────────────┘  |
|                                       │                                                   |
|                                       ▼ TCP Port 5432 / 6379 (VPC Private Subnet)         |
|  ┌─────────────────────────────────────────────────────────────────────────────────────┐  |
|  │ DATA TIER (PostgreSQL 16 & Redis 7.2)                                               │  |
|  └─────────────────────────────────────────────────────────────────────────────────────┘  |
|                                                                                           |
+───────────────────────────────────────────────────────────────────────────────────────────+
```

---

## 2. Nginx Internals: Event-Driven `epoll` Architecture

Traditional web servers (such as Apache with `prefork` MPM) allocate an independent OS thread or process per concurrent connection. When 10,000 slow clients open HTTP connections, Apache allocates 10,000 threads, causing **RAM exhaustion from thread stack allocations (typically 8MB per thread)** and massive kernel CPU overhead from context switching.

Nginx implements an **Asynchronous, Non-Blocking, Event-Driven Architecture**:

```
                       NGINX WORKER PROCESS ARCHITECTURE
+───────────────────────────────────────────────────────────────────────────+
|                                                                           |
|   Master Process (Root) ──► Reads Configuration, Binds Port 80/443        |
|     │                                                                     |
|     ├── Worker Process 1 (CPU 0) ──► Linux `epoll` Event Loop (10,000 FDs)|
|     ├── Worker Process 2 (CPU 1) ──► Linux `epoll` Event Loop (10,000 FDs)|
|     ├── Worker Process 3 (CPU 2) ──► Linux `epoll` Event Loop (10,000 FDs)|
|     └── Worker Process 4 (CPU 3) ──► Linux `epoll` Event Loop (10,000 FDs)|
|                                                                           |
+───────────────────────────────────────────────────────────────────────────+
```

### The Power of Linux `epoll`
- Instead of polling thousands of sockets sequentially, Nginx registers file descriptors (FDs) with the Linux kernel's `epoll(7)` subsystem (`epoll_create`, `epoll_ctl`, `epoll_wait`).
- When network packets arrive on any socket, the network interface card (NIC) triggers an interrupt, and the kernel notifies the single Nginx worker thread via an $O(1)$ event queue.
- A single Nginx worker process can effortlessly handle **50,000+ concurrent connections consuming less than 50 MB of RAM**!

---

## 3. Reverse Proxying Mechanics & Header Forwarding

When Nginx reverse-proxies an HTTP request to an upstream application server via `proxy_pass`, it acts as a client to the upstream server. By default, Nginx strips or modifies several key HTTP headers.

You must explicitly configure Nginx to forward the client's real identity to your Python application:

```nginx
location / {
    proxy_pass http://unix:/run/gunicorn.sock;
    proxy_http_version 1.1;

    # Preserves the original Host header sent by client (e.g. api.example.com)
    proxy_set_header Host $host;

    # Forwards client IP to prevent all requests appearing as 127.0.0.1
    proxy_set_header X-Real-IP $remote_addr;

    # Appends client IP to proxy chain list
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    # CRITICAL: Forwards original scheme ("https"). Without this, FastAPI generates
    # redirect URLs with "http://", triggering infinite 301 redirect loops!
    proxy_set_header X-Forwarded-Proto $scheme;

    # Enables WebSocket upgrade tunnels
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Proxy Buffer Optimization
    proxy_buffering on;
    proxy_buffer_size 8k;
    proxy_buffers 16 8k;
}
```

---

## 4. Upstream Load Balancing Strategies

When scaling beyond a single application server, Nginx acts as an Layer 7 HTTP load balancer using the `upstream` directive:

```nginx
upstream fastapi_cluster {
    # 1. Least Connections: Routes traffic to worker with fewest active requests
    least_conn;

    # Upstream servers (UNIX domain socket or internal private network IPs)
    server unix:/run/gunicorn_1.sock max_fails=3 fail_timeout=10s;
    server unix:/run/gunicorn_2.sock max_fails=3 fail_timeout=10s;
    server 10.0.11.25:8000           weight=2;
    server 10.0.11.26:8000           backup; # Only receives traffic if all others fail
}
```

### Load Balancing Algorithms
1. **Round-Robin (Default)**: Distributes requests sequentially across servers according to optional weights.
2. **`least_conn` (Production Standard for APIs)**: Directs requests to the worker with the lowest active connection count. Prevents slow, compute-heavy requests (e.g. PDF generation or LLM streaming) from overloading a single worker while other workers sit idle.
3. **`ip_hash`**: Hashes the client's IPv4/IPv6 address to guarantee that requests from a specific client IP consistently reach the exact same backend worker (sticky sessions for legacy stateful architectures).

---

## 5. UNIX Domain Sockets vs. TCP Loopback

When Nginx and Gunicorn run on the **same physical server or EC2 instance**, you can connect them via:
1. **TCP Loopback**: `proxy_pass http://127.0.0.1:8000;`
2. **UNIX Domain Socket (UDS)**: `proxy_pass http://unix:/run/gunicorn.sock;`

```
+───────────────────────────────────────────────────────────────────────────+
|                 TCP LOOPBACK VS. UNIX DOMAIN SOCKET                       |
+───────────────────────────────────────────────────────────────────────────+
|  Dimension          | TCP Loopback (127.0.0.1)    | UNIX Domain Socket    |
├─────────────────────┼─────────────────────────────┼───────────────────────┤
|  Protocol Overhead  | Full TCP/IP stack overhead  | Zero TCP/IP overhead  |
|  Routing Logic      | Checksumming, TCP ACKs, SYN | Direct memory copy via|
|                     | packets, sequence numbers   | kernel ring buffers   |
|  Port Exhaustion    | Risk of TIME_WAIT ephemeral | No ports used!        |
|                     | port exhaustion (65,535)    | (File descriptor based|
|  Access Security    | Any local process can probe | Secured via Linux     |
|                     | port 8000                   | file permissions (660)|
|  Throughput & Latency| Baseline                   | **15-25% lower latency|
|                     |                             | & 20% higher RPS**    |
+───────────────────────────────────────────────────────────────────────────+
```

---

## 6. Gunicorn Process Model & ASGI Uvicorn Workers

FastAPI and modern asynchronous frameworks require an **ASGI (Asynchronous Server Gateway Interface)** server to run coroutines. 

While raw `uvicorn` can run an app, Uvicorn alone does not provide robust process management, master-worker process monitoring, or zero-downtime rolling worker reloads.

**Production Solution: Gunicorn as Process Manager + Uvicorn as Worker Engine**:

```bash
gunicorn main:app \
    --workers 5 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind unix:/run/gunicorn.sock \
    --umask 007 \
    --timeout 30 \
    --keep-alive 5 \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    --access-logfile - \
    --error-logfile -
```

### The Worker Sizing Formula
How many Gunicorn workers should you spawn on an EC2 instance?

$$\text{Workers} = (2 \times \text{CPU Cores}) + 1$$

- On a 2 vCPU EC2 instance (`t4g.medium`): $(2 \times 2) + 1 = \mathbf{5\text{ workers}}$.
- **Why?** When one worker thread yields execution to the operating system waiting on an asynchronous database I/O read, other workers remain active on CPU hardware cores without starving the processor.
- **`--max-requests` and `--max-requests-jitter`**: Periodically recycles workers after processing 10,000 requests. This guards against insidious C-extension memory leaks (`numpy`, `psycopg2`) by smoothly terminating and restarting worker processes in the background without dropping connections.

---

## 7. Linux `systemd` Service Architecture & Process Supervision

In production, your Python backend must start automatically on server boot, restart immediately upon unexpected crashes, and support graceful zero-downtime reloads.

### `gunicorn.service` Systemd Unit Specification

```ini
[Unit]
Description=Gunicorn ASGI Application Server for FastAPI
After=network.target

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/var/www/my-app
RuntimeDirectory=gunicorn

# Path to virtualenv executable
ExecStart=/var/www/my-app/.venv/bin/gunicorn main:app \
    --workers 5 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind unix:/run/gunicorn/gunicorn.sock \
    --access-logfile /var/log/gunicorn/access.log \
    --error-logfile /var/log/gunicorn/error.log

# Graceful rolling reload upon SIGHUP
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=30
PrivateTmp=true

# Automated Resilience & Self-Healing
Restart=always
RestartSec=3

# Security Sandboxing
ProtectSystem=full
ProtectHome=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
# Systemd management commands
sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn
sudo systemctl status gunicorn
sudo journalctl -u gunicorn -f --no-tail
```

---

## 8. Modern TLS/SSL Cryptography & TLS 1.3

Transport Layer Security (TLS) encrypts application traffic between client browsers and Nginx, preventing eavesdropping, session hijacking, and man-in-the-middle (MITM) tampering.

```
                           TLS 1.2 VS TLS 1.3 HANDSHAKE
+───────────────────────────────────────────────────────────────────────────+
|  TLS 1.2 (2 Round-Trips - ~100-200ms latency):                            |
|  Client ─────── ClientHello ────────► Nginx                               |
|  Client ◄────── ServerHello + Cert ── Nginx                               |
|  Client ─────── Key Exchange ───────► Nginx                               |
|  Client ◄────── Finished (Session) ── Nginx                               |
|                                                                           |
|  TLS 1.3 (1 Round-Trip - ~50ms latency - 50% Speedup!):                   |
|  Client ─────── ClientHello + Key Share ─────► Nginx                      |
|  Client ◄────── ServerHello + Finished ────── Nginx                       |
|  [Immediate Encrypted HTTP Data Stream Begins!]                           |
+───────────────────────────────────────────────────────────────────────────+
```

### TLS Invariants
- **Disable Deprecated Protocols**: Completely disable SSLv3, TLSv1.0, and TLSv1.1 (vulnerable to POODLE, BEAST, and SWEET32 attacks).
- **Enforce TLS 1.2 and TLS 1.3 exclusively**.
- **Perfect Forward Secrecy (PFS)**: Ensures that even if the server's private key is compromised in the future, past recorded encrypted traffic cannot be decrypted. Enforced using Ephemeral Diffie-Hellman (`ECDHE`).

---

## 9. Automated SSL with Let's Encrypt & Certbot

**Let's Encrypt** is a free, automated Certificate Authority (CA) operating via the **ACME (Automated Certificate Management Environment)** protocol.

```
                     LET'S ENCRYPT ACME HTTP-01 CHALLENGE FLOW
+───────────────────────────────────────────────────────────────────────────+
|                                                                           |
|  1. Certbot Client (EC2) ──────► Let's Encrypt CA: "Issue cert for domain"|
|                                                                           |
|  2. Let's Encrypt CA    ──────► Certbot: "Prove ownership: host token X   |
|                                            at `/.well-known/acme-challenge`|
|                                                                           |
|  3. Nginx serves Token   ◄───── Let's Encrypt CA fetches file over HTTP 80|
|                                                                           |
|  4. CA validates token   ──────► CA issues signed X.509 Certificate (90d) |
|                                                                           |
+───────────────────────────────────────────────────────────────────────────+
```

### Installation & Execution Command Chain
```bash
# Install Certbot and Nginx plugin
sudo apt update && sudo apt install -y certbot python3-certbot-nginx

# Obtain and automatically install certificate with Nginx auto-configuration
sudo certbot --nginx -d api.mycompany.com --agree-tos --email admin@mycompany.com --redirect

# Test automated certificate renewal timer
sudo certbot renew --dry-run
```

Certbot automatically installs a systemd timer (`/lib/systemd/system/certbot.timer`) that runs twice daily, automatically renewing certificates 30 days before expiration.

---

## 10. Nginx Security Hardening: SSL & Cipher Suites

```nginx
# SSL Configuration
ssl_certificate /etc/letsencrypt/live/api.mycompany.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/api.mycompany.com/privkey.pem;

# Restrict to Modern TLS Protocols Only
ssl_protocols TLSv1.2 TLSv1.3;

# High-Security Modern Cipher Suite (PFS Enabled)
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
ssl_prefer_server_ciphers off;

# SSL Session Caching for Fast Handshake Resumption
ssl_session_cache shared:SSL:10m;
ssl_session_timeout 1d;
ssl_session_tickets off;

# OCSP Stapling (Nginx queries CA and staples signed status, eliminating client DNS latency)
ssl_stapling on;
ssl_stapling_verify on;
ssl_trusted_certificate /etc/letsencrypt/live/api.mycompany.com/chain.pem;
resolver 1.1.1.1 8.8.8.8 valid=300s;
resolver_timeout 5s;
```

---

## 11. Defensive HTTP Security Headers

Protect your application against cross-site scripting (XSS), clickjacking, and MIME-type sniffing by declaring production security headers:

```nginx
# 1. HSTS (HTTP Strict Transport Security): Forces browser to use HTTPS for 2 years
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

# 2. X-Frame-Options: Prevents clickjacking by forbidding embedding in iframes
add_header X-Frame-Options "DENY" always;

# 3. X-Content-Type-Options: Prevents MIME-sniffing exploits
add_header X-Content-Type-Options "nosniff" always;

# 4. Referrer-Policy: Prevents leaking full URLs with sensitive query params to external sites
add_header Referrer-Policy "strict-origin-when-cross-origin" always;

# 5. Content-Security-Policy (CSP): Restricts resource origins
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline';" always;

# 6. Permissions-Policy: Disables dangerous device hardware APIs (mic, camera, geolocation)
add_header Permissions-Policy "geolocation=(), camera=(), microphone=()" always;

# 7. Hide Nginx Version from Attackers
server_tokens off;
```

---

## 12. Traffic Throttling & DDoS Mitigation

Nginx provides high-speed rate limiting implemented using the **Leaky Bucket Algorithm**:

```nginx
# Define shared memory zone: 10MB can track ~160,000 client IPs
# Limit requests to 10 requests per second per client IP
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

# Limit concurrent open connections to 20 per client IP
limit_conn_zone $binary_remote_addr zone=conn_limit:10m;

server {
    # Apply connection limit
    limit_conn conn_limit 20;

    location /api/ {
        # Allow burst of 20 requests with nodelay for bursty API traffic
        limit_req zone=api_limit burst=20 nodelay;
        limit_req_status 429; # Returns HTTP 429 Too Many Requests

        proxy_pass http://unix:/run/gunicorn.sock;
    }
}
```

### Slowloris & Slow HTTP Attack Defense
Slowloris attacks open hundreds of connections and send partial HTTP headers byte-by-byte every 10 seconds, locking up application workers. 

Nginx terminates Slowloris connections automatically via tight timeouts:
```nginx
client_body_timeout 10s;
client_header_timeout 10s;
keepalive_timeout 15s;
send_timeout 10s;
```

---

## 13. Payload Constraints & Zero-Copy Optimization

```nginx
# Restrict file upload sizes to prevent disk exhaustion attacks (Default is only 1MB!)
client_max_body_size 25M;
client_body_buffer_size 128k;

# Linux Kernel Zero-Copy File Transmission
sendfile on;       # Copies directly from disk cache to NIC buffer via sendfile(2)
tcp_nopush on;     # Sends full TCP packets, minimizing packet overhead
tcp_nodelay on;    # Disables Nagle's algorithm for interactive low latency

# Dynamic Gzip Compression
gzip on;
gzip_comp_level 5;
gzip_min_length 256;
gzip_proxied any;
gzip_types application/json application/javascript text/css text/plain application/xml;
```

---

## 14. Linux Host Firewalling (`ufw`)

Configure Ubuntu's **Uncomplicated Firewall (`ufw`)** to enforce default-deny ingress:

```bash
# 1. Default Policies: Deny incoming, Allow outgoing
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 2. Rate-limit SSH on Port 22 (blocks brute-force attackers who attempt >6 connections in 30s)
sudo ufw limit 22/tcp comment 'Rate-limited SSH'

# 3. Allow standard HTTP and HTTPS
sudo ufw allow 80/tcp comment 'Nginx HTTP / Certbot challenge'
sudo ufw allow 443/tcp comment 'Nginx HTTPS'

# 4. Enable Firewall
sudo ufw --force enable
sudo ufw status verbose
```

---

## 15. Production Web Deployment Architecture Matrix

| Architectural Dimension | Naive Development Setup | Production Enterprise Standard |
| :--- | :--- | :--- |
| **Reverse Proxy** | None (Exposes Uvicorn on 0.0.0.0:8000) | **Nginx with `epoll` event loop** |
| **Process Supervision** | Raw python script or terminal shell | **Linux `systemd` service with `Restart=always`** |
| **Worker Engine** | Single Uvicorn process | **Gunicorn Master + $(2N+1)$ `UvicornWorker`s** |
| **IPC Channel** | TCP Loopback (`127.0.0.1:8000`) | **UNIX Domain Socket (`/run/gunicorn.sock`)** |
| **TLS/SSL Encryption** | None (HTTP plaintext) | **Let's Encrypt TLS 1.3 via Certbot Auto-Renew** |
| **Security Headers** | Missing (Vulnerable to XSS/Clickjacking)| **HSTS, CSP, X-Frame-Options, `server_tokens off`** |
| **DoS Defense** | None (Slowloris crashes worker) | **Nginx `limit_req_zone` + tight header timeouts** |
| **Host Firewall** | Open host ports | **`ufw` Default-Deny with SSH Rate-Limiting** |
