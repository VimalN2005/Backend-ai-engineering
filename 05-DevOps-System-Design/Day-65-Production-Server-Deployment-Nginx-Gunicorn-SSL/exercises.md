# Day 65: Production Server Deployment — Enterprise Hands-On Exercises

Three enterprise-grade practical challenges covering systemd socket activation, production Nginx configuration hardening with rate limiting, and zero-downtime deployment scripting with automated SSL validation.

---

## Exercise 1: Dual-Unit Systemd Socket & Service Activation

### Scenario & Objective
In high-reliability Linux environments, application crashes or cold starts can cause dropped TCP connections before the Python application finishes initializing. Systemd **Socket Activation** solves this by having PID 1 open and listen on the UNIX domain socket *before* launching the service. If the Gunicorn process crashes or restarts, incoming requests are buffered in the kernel socket buffer rather than rejected with `502 Bad Gateway`.

Create:
1. `/etc/systemd/system/gunicorn.socket`
2. `/etc/systemd/system/gunicorn.service`

### Requirements
- Socket must bind to `/run/gunicorn.sock` with user `www-data` and mode `0660`.
- Service must run as user `deploy` and group `www-data`.
- Gunicorn must execute inside a Python virtual environment (`/home/deploy/app/venv/bin/gunicorn`) with 4 `UvicornWorker` processes.
- Service must use `Type=notify` (or `Type=exec`), `Restart=always`, `RestartSec=3s`, and resource limit constraints (`LimitNOFILE=65536`).

### Verified Production Solution

#### Step 1: `/etc/systemd/system/gunicorn.socket`
```ini
[Unit]
Description=Gunicorn UNIX Domain Socket for Enterprise FastAPI
PartOf=gunicorn.service

[Socket]
# Bind to root-level run directory
ListenStream=/run/gunicorn.sock

# Socket file permissions: only www-data (Nginx) and deploy can read/write
SocketUser=www-data
SocketGroup=www-data
SocketMode=0660

# Kernel backlog queue depth
Backlog=2048

[Install]
WantedBy=sockets.target
```

#### Step 2: `/etc/systemd/system/gunicorn.service`
```ini
[Unit]
Description=Gunicorn ASGI Production Application Server
Requires=gunicorn.socket
After=network.target gunicorn.socket

[Service]
Type=notify
User=deploy
Group=www-data
WorkingDirectory=/home/deploy/app
Environment="PATH=/home/deploy/app/venv/bin:/usr/local/bin:/usr/bin"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/home/deploy/app/.env

# Gunicorn master process binding to systemd-provided socket descriptor
ExecStart=/home/deploy/app/venv/bin/gunicorn \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 30 \
    --keep-alive 5 \
    --max-requests 5000 \
    --max-requests-jitter 500 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    app.main:app

# Zero-downtime graceful reload on SIGHUP
ExecReload=/bin/kill -s HUP $MAINPID

# Graceful termination timeout
KillMode=mixed
TimeoutStopSec=30

# Systemd Watchdog & Auto-Restart Policies
Restart=always
RestartSec=3s
StartLimitIntervalSec=60s
StartLimitBurst=5

# Linux Kernel Resource Hardening
LimitNOFILE=65536
PrivateTmp=true
ProtectSystem=full
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

#### Step 3: Activation Commands
```bash
# Reload systemd daemon
sudo systemctl daemon-reload

# Enable and start the socket unit
sudo systemctl enable --now gunicorn.socket

# Verify socket existence and permissions
ls -la /run/gunicorn.sock
# Output: srw-rw---- 1 www-data www-data 0 /run/gunicorn.sock
```

---

## Exercise 2: Production Nginx Server Block with SSL, Rate Limiting & ACME Pass-Through

### Scenario & Objective
Write a complete, security-audited Nginx virtual host configuration file for `api.enterprise.com` that fulfills the following enterprise security controls:
1. Port 80 redirects all HTTP traffic to HTTPS, **except** Let's Encrypt ACME verification requests (`/.well-known/acme-challenge/`).
2. TLS 1.2 and TLS 1.3 only, with ECDHE ciphers and OCSP stapling.
3. Leaky bucket rate limiting: maximum 20 requests/second per IP with a burst of 10 requests.
4. HTTP Strict Transport Security (HSTS) with 2-year `max-age` and subdomain inclusion.
5. Reverse proxying to UNIX domain socket `unix:/run/gunicorn.sock`.

### Verified Production Solution

File: `/etc/nginx/sites-available/api.enterprise.com.conf`

```nginx
# 1. Rate Limiting Zones (Allocates 10MB memory zone ~ 160,000 tracked IPs)
limit_req_zone $binary_remote_addr zone=api_gateway_limit:10m rate=20r/s;
limit_req_status 429;

# 2. HTTP Server Block (Port 80) — Redirects to HTTPS with ACME Challenge Exception
server {
    listen 80;
    listen [::]:80;
    server_name api.enterprise.com;
    server_tokens off;

    # Let's Encrypt ACME HTTP-01 Challenge Pass-Through
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files $uri =404;
    }

    # Permanent redirect to secure HTTPS for all other requests
    location / {
        return 301 https://$host$request_uri;
    }
}

# 3. HTTPS Server Block (Port 443) — Hardened Production Reverse Proxy
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name api.enterprise.com;
    server_tokens off;

    # SSL Certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/api.enterprise.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.enterprise.com/privkey.pem;

    # TLS Protocols & Cipher Suites
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;

    # SSL Session Caching & OCSP Stapling
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;
    ssl_trusted_certificate /etc/letsencrypt/live/api.enterprise.com/chain.pem;
    resolver 8.8.8.8 1.1.1.1 valid=300s;
    resolver_timeout 5s;

    # Enterprise HTTP Security Headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'none'; frame-ancestors 'none';" always;

    # Buffer & Timeout Defense
    client_max_body_size 25M;
    client_body_buffer_size 128k;
    client_header_timeout 10s;
    client_body_timeout 10s;
    send_timeout 10s;
    keepalive_timeout 15s;

    # API Gateway Reverse Proxy
    location / {
        # Rate Limiting: 20 req/sec with burst buffer of 10
        limit_req zone=api_gateway_limit burst=10 nodelay;

        proxy_pass http://unix:/run/gunicorn.sock;
        proxy_http_version 1.1;

        # Identity & Protocol Forwarding Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Connection Header Tuning
        proxy_set_header Connection "";
        proxy_buffering on;
        proxy_buffers 16 16k;
        proxy_buffer_size 16k;

        # Upstream Timeouts
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_send_timeout 10s;
    }
}
```

---

## Exercise 3: Automated Zero-Downtime Deployment & SSL Health Check Script

### Scenario & Objective
Develop an enterprise bash deployment script (`deploy.sh`) that automates code deployment, database migration, zero-downtime Gunicorn worker reload via SIGHUP, post-deployment health check probing, and automated rollback upon failure.

### Verified Production Solution

File: `/home/deploy/app/scripts/deploy.sh`

```bash
#!/usr/bin/env bash
# ==============================================================================
# Enterprise Zero-Downtime Deployment & Health Probe Script
# ==============================================================================
set -euo pipefail

APP_DIR="/home/deploy/app"
VENV_BIN="$APP_DIR/venv/bin"
HEALTH_CHECK_URL="http://127.0.0.1/health"
SOCKET_FILE="/run/gunicorn.sock"
MAX_HEALTH_ATTEMPTS=15
HEALTH_INTERVAL=1

echo "=================================================================="
echo " Starting Zero-Downtime Deployment: $(date -u)"
echo "=================================================================="

cd "$APP_DIR"

# 1. Backup current git revision for instant rollback
PREV_COMMIT=$(git rev-parse HEAD)
echo "[INFO] Current Commit: $PREV_COMMIT"

# 2. Fetch latest changes
echo "[STEP 1/6] Pulling latest code from origin/main..."
git fetch origin main
git reset --hard origin/main
NEW_COMMIT=$(git rev-parse HEAD)
echo "[INFO] Deploying Commit: $NEW_COMMIT"

# 3. Install / Sync Python Dependencies
echo "[STEP 2/6] Syncing virtual environment packages..."
"$VENV_BIN/pip" install -q -r requirements.txt

# 4. Run Database Migrations
echo "[STEP 3/6] Running Alembic database migrations..."
"$VENV_BIN/alembic" upgrade head

# 5. Compile Python bytecode (fails fast if syntax errors exist)
echo "[STEP 4/6] Validating syntax and compiling bytecode..."
"$VENV_BIN/python" -m compileall -q app/

# 6. Issue Zero-Downtime SIGHUP Reload to Gunicorn Master
echo "[STEP 5/6] Issuing SIGHUP to Gunicorn Master..."
if systemctl is-active --quiet gunicorn.service; then
    sudo systemctl reload gunicorn.service
else
    echo "[WARN] Gunicorn was not active. Starting service..."
    sudo systemctl start gunicorn.service
fi

# 7. Post-Deployment Smoke Test & Health Probing
echo "[STEP 6/6] Probing application health..."
HEALTH_SUCCESS=false

for ((i=1; i<=MAX_HEALTH_ATTEMPTS; i++)); do
    # Probe UNIX domain socket directly via curl
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --unix-socket "$SOCKET_FILE" "http://localhost/health" || echo "000")
    
    if [ "$HTTP_CODE" -eq 200 ]; then
        echo "[SUCCESS] Health probe passed with HTTP 200 OK (Attempt $i/$MAX_HEALTH_ATTEMPTS)!"
        HEALTH_SUCCESS=true
        break
    fi
    echo "[WAIT] Health probe returned HTTP $HTTP_CODE. Retrying in ${HEALTH_INTERVAL}s..."
    sleep "$HEALTH_INTERVAL"
done

# 8. Rollback Procedure on Health Failure
if [ "$HEALTH_SUCCESS" = false ]; then
    echo "❌ [FATAL] Health check failed after $MAX_HEALTH_ATTEMPTS attempts!"
    echo "[ROLLBACK] Reverting git repository to $PREV_COMMIT..."
    git reset --hard "$PREV_COMMIT"
    sudo systemctl reload gunicorn.service
    echo "❌ Deployment aborted and rolled back to previous stable release."
    exit 1
fi

# 9. Verify SSL Certificate Expiry Warning (< 30 days remaining)
DOMAIN="api.enterprise.com"
CERT_PATH="/etc/letsencrypt/live/$DOMAIN/cert.pem"
if [ -f "$CERT_PATH" ]; then
    EXPIRY_DATE=$(openssl x509 -enddate -noout -in "$CERT_PATH" | cut -d= -f2)
    EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
    echo "[SSL AUDIT] Domain '$DOMAIN' certificate expires in $DAYS_LEFT days ($EXPIRY_DATE)."
    if [ "$DAYS_LEFT" -le 30 ]; then
        echo "⚠️ [WARNING] SSL certificate expires within 30 days! Running certbot renewal..."
        sudo certbot renew --deploy-hook "systemctl reload nginx"
    fi
fi

echo "=================================================================="
echo " Zero-Downtime Deployment Completed Successfully!"
echo "=================================================================="
```
