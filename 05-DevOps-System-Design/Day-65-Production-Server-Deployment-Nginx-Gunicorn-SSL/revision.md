# Day 65: Production Server Deployment — 5-Minute Revision & Cheat Sheet

A concise, high-density reference sheet covering Nginx reverse proxies, Gunicorn process management, systemd service units, Let's Encrypt Certbot, and Linux server hardening.

---

## 1. Core Architecture Diagram

```
[Public Internet: HTTPS/443]
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ NGINX (Edge Proxy & TLS Termination)                   │
│ - epoll event loop (O(1) I/O multiplexing)             │
│ - Rate limiting (Leaky bucket rate=10r/s burst=20)     │
│ - Security headers (HSTS, CSP, X-Frame-Options)        │
│ - Static assets via zero-copy sendfile(2)              │
└────────────────────────────────────────────────────────┘
            │
            ▼ UNIX Domain Socket (/run/gunicorn.sock - Mode 0660)
┌────────────────────────────────────────────────────────┐
│ GUNICORN (Systemd Supervised Master: PID 1)            │
│ - SIGHUP zero-downtime hot reloading                   │
│ - Worker heartbeat timeout monitoring                  │
│ ┌─────────────────┬─────────────────┬────────────────┐ │
│ │ Worker 1 (ASGI) │ Worker 2 (ASGI) │ Worker 3 (ASGI)│ │
│ │ (UvicornWorker) │ (UvicornWorker) │ (UvicornWorker)│ │
│ └─────────────────┴─────────────────┴────────────────┘ │
└────────────────────────────────────────────────────────┘
```

---

## 2. Linux Essential Signals & Process Control

| Signal | Standard Command | Effect on Nginx | Effect on Gunicorn Master |
| :--- | :--- | :--- | :--- |
| **`SIGHUP`** | `kill -HUP $PID` | Re-reads configuration; gracefully spawns new workers and drains old workers. | Re-reads configuration; forks new generation of workers, drains old workers with zero downtime. |
| **`SIGTERM`**| `kill -TERM $PID`| Quick shutdown; terminates active connections immediately. | Graceful shutdown; allows workers to finish in-flight requests within timeout. |
| **`SIGQUIT`**| `kill -QUIT $PID`| Graceful shutdown; drains active requests before exit. | Quick shutdown; immediate process termination. |
| **`SIGUSR1`**| `kill -USR1 $PID`| Re-opens log files (used by `logrotate`). | Re-opens log files. |
| **`SIGUSR2`**| `kill -USR2 $PID`| Upgrades Nginx binary on the fly without dropping connections. | Upgrades Gunicorn binary on the fly. |
| **`SIGWINCH`**| `kill -WINCH $PID`| Gracefully shuts down worker processes (keeps master alive). | Gracefully stops workers (used during live upgrade). |

---

## 3. Production Deployment Commands

### Nginx Management
```bash
# Test configuration syntax before reloading (CRITICAL)
sudo nginx -t

# Gracefully reload configuration with zero dropped connections
sudo systemctl reload nginx

# Full restart (brief downtime)
sudo systemctl restart nginx
```

### Systemd & Gunicorn Management
```bash
# Reload systemd unit definitions after editing .service files
sudo systemctl daemon-reload

# Enable socket and service to start automatically at boot
sudo systemctl enable --now gunicorn.socket
sudo systemctl enable --now gunicorn.service

# View live service logs
sudo journalctl -u gunicorn.service -f -n 100

# View detailed unit status
sudo systemctl status gunicorn.service
```

### Let's Encrypt (Certbot)
```bash
# Obtain and install certificate using Nginx plugin
sudo certbot --nginx -d api.example.com -d example.com

# Test automatic renewal dry run
sudo certbot renew --dry-run

# Force renewal with post-deploy reload hook
sudo certbot renew --deploy-hook "systemctl reload nginx"
```

### Host Firewall (`ufw`)
```bash
# Baseline hardening
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit 22/tcp comment 'Rate-limited SSH'
sudo ufw allow 80/tcp comment 'HTTP / ACME'
sudo ufw allow 443/tcp comment 'HTTPS'
sudo ufw --force enable
sudo ufw status verbose
```

---

## 4. Production Boilerplates

### `/etc/systemd/system/gunicorn.service`
```ini
[Unit]
Description=Gunicorn ASGI Application Server
Requires=gunicorn.socket
After=network.target gunicorn.socket

[Service]
Type=notify
User=deploy
Group=www-data
WorkingDirectory=/home/deploy/app
Environment="PATH=/home/deploy/app/venv/bin:/usr/local/bin:/usr/bin"
EnvironmentFile=/home/deploy/app/.env
ExecStart=/home/deploy/app/venv/bin/gunicorn \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 30 \
    --keep-alive 5 \
    --max-requests 5000 \
    --max-requests-jitter 500 \
    app.main:app
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=3s
LimitNOFILE=65536
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

### `/etc/nginx/sites-available/api.example.com.conf`
```nginx
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=20r/s;

server {
    listen 80;
    server_name api.example.com;
    server_tokens off;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name api.example.com;
    server_tokens off;

    ssl_certificate /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    client_max_body_size 25M;
    client_body_timeout 10s;
    client_header_timeout 10s;

    location / {
        limit_req zone=api_limit burst=10 nodelay;
        proxy_pass http://unix:/run/gunicorn.sock;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 5. Rapid-Fire Flashcards (10 Q&A)

1. **Why should you never expose Uvicorn directly to the public internet?**  
   *Answer*: Uvicorn is susceptible to Slowloris resource exhaustion, lacks fast TLS termination and HTTP security header hardening, and cannot serve static assets via kernel zero-copy `sendfile(2)`.

2. **Why use a UNIX Domain Socket over TCP Loopback (`127.0.0.1:8000`)?**  
   *Answer*: UNIX sockets bypass the TCP network stack (no 3-way handshakes, framing, or checksums) and avoid ephemeral port exhaustion in high-QPS production environments.

3. **What fatal bug occurs if `proxy_set_header X-Forwarded-Proto $scheme;` is missing?**  
   *Answer*: FastAPI/Django sees an incoming HTTP request and issues a 301 redirect to HTTPS, causing an infinite browser redirect loop.

4. **What signal performs a zero-downtime worker reload in Gunicorn and Nginx?**  
   *Answer*: `SIGHUP` (`kill -HUP $PID` or `systemctl reload`).

5. **How does Nginx's `epoll` differ from Apache's thread-per-connection model?**  
   *Answer*: `epoll` provides $O(1)$ event notifications on ready sockets in a single event loop thread, avoiding thread stack memory allocation and kernel context switching.

6. **What is the recommended Gunicorn worker count for asynchronous ASGI applications?**  
   *Answer*: $1 \text{ to } 1.5 \times \text{CPU cores}$ (unlike synchronous WSGI which uses $2N+1$), letting `asyncio` manage concurrency within each core.

7. **How does `burst=20 nodelay;` alter Nginx rate limiting?**  
   *Answer*: It allows immediate wire-speed processing of up to 20 burst requests without artificial delay, while still draining the leaky bucket at the configured rate.

8. **Why does Nginx require `systemctl reload nginx` after Certbot renews a certificate?**  
   *Answer*: Nginx caches TLS certificates in memory at startup; without a reload, it continues serving the expired certificate to clients.

9. **What does `server_tokens off;` prevent?**  
   *Answer*: It stops Nginx from disclosing its exact version and OS build in HTTP response headers and default error pages.

10. **What does `LimitNOFILE=65536` configure in a systemd service?**  
    *Answer*: It increases the maximum number of open file descriptors (sockets) the process can allocate, preventing `Too many open files` errors under high load.
