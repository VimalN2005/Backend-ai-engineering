# Day 69: Load Balancing & Autoscaling — Enterprise Practical Exercises

Three enterprise-grade practical challenges covering hardened Nginx upstream reverse proxying, production Kubernetes HPA v2 manifests with stabilization windows, and KEDA event-driven autoscaling with Scale-to-Zero.

---

## Exercise 1: Hardened Production Nginx Upstream Configuration

### Scenario & Objective
You are the Lead SRE deploying a high-throughput API gateway on Nginx reverse proxying to a backend microservice pool.
Configure `/etc/nginx/conf.d/gateway.conf` to satisfy the following enterprise requirements:
1. Load balance traffic using **Least Connections** across 3 backend instances with varying hardware weights.
2. Implement **passive circuit breaking**: mark an upstream down for $10\text{ seconds}$ if it fails $3$ consecutive requests (`max_fails=3 fail_timeout=10s`).
3. Maintain an active pool of **keepalive connections** to upstream pods (`keepalive 64`) to eliminate TCP 3-way handshake latency on internal hops.
4. Forward client identity headers: `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto`.
5. Provide a lightweight `/healthz` endpoint that verifies Nginx is operational without forwarding to upstream.

### Verified Production Solution

File: `/etc/nginx/conf.d/gateway.conf`

```nginx
# ==============================================================================
# Enterprise Nginx Layer 7 Load Balancing Gateway Configuration
# ==============================================================================

# 1. Upstream Backend Cluster Definition
upstream order_api_cluster {
    # Least Connections balancing: dynamically favors least-busy workers
    least_conn;

    # Node 1: High-capacity server (Weight 3)
    server 10.0.1.10:8000 weight=3 max_fails=3 fail_timeout=10s;

    # Node 2: Standard server (Weight 2)
    server 10.0.1.11:8000 weight=2 max_fails=3 fail_timeout=10s;

    # Node 3: Low-capacity server (Weight 1)
    server 10.0.1.12:8000 weight=1 max_fails=3 fail_timeout=10s;

    # Hot Standby: Only receives traffic if all three primary nodes fail!
    server 10.0.1.99:8000 backup;

    # Keepalive Connection Pooling: Caches 64 idle TCP sockets per worker process
    # Eliminates SYN/SYN-ACK overhead on high-frequency internal microservice calls
    keepalive 64;
    keepalive_requests 1000;
    keepalive_timeout 60s;
}

# 2. Server Block Configuration
server {
    listen 80;
    listen [::]:80;
    server_name api.enterprise.internal;

    server_tokens off;

    # Buffer & Timeout Protections
    client_max_body_size 25M;
    client_body_buffer_size 128k;
    proxy_connect_timeout 3s;
    proxy_read_timeout 30s;
    proxy_send_timeout 10s;

    # Health Check Endpoint (Probed by AWS ALB / Kubernetes Ingress)
    location = /healthz {
        access_log off;
        default_type text/plain;
        return 200 "OK\n";
    }

    # API Ingress Reverse Proxy
    location / {
        proxy_pass http://order_api_cluster;

        # Mandate HTTP/1.1 for upstream keepalive connection reuse
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # Client Identity Forwarding Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Retry idempotent requests on connection reset or timeout
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 3;
        proxy_next_upstream_timeout 5s;
    }
}
```

---

## Exercise 2: Production Kubernetes HPA v2 Manifest with Hysteresis

### Scenario & Objective
Create a Kubernetes Horizontal Pod Autoscaler manifest (`hpa.yaml`) for an enterprise API service (`api-service`) that adheres to strict reliability standards:
1. Target **$65\%$ average CPU utilization** and **$75\%$ average Memory utilization**.
2. Scale between a minimum of **$4$ Pods** (for high availability across 3 availability zones) and a maximum of **$40$ Pods**.
3. **Scale-Up Policy**: Must react immediately to sudden traffic spikes ($0\text{s}$ stabilization window), allowing scaling by either $100\%$ or $4$ pods every $15\text{ seconds}$ (whichever is larger).
4. **Scale-Down Policy**: Enforce a **$300\text{s}$ (5-minute) stabilization window** to eliminate flapping/thrashing, and cap scale-down rate to a maximum of $10\%$ of pods per minute.

### Verified Production Solution

File: `k8s/hpa.yaml`

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-service-hpa
  namespace: production
  labels:
    app: api-service
    tier: backend
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-service

  minReplicas: 4
  maxReplicas: 40

  metrics:
  # Metric 1: CPU Utilization (Target: 65% with 35% spike headroom)
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 65

  # Metric 2: Memory Utilization (Target: 75% to prevent OOM kills)
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 75

  # Advanced Scaling Behaviors & Hysteresis Defense
  behavior:
    # Scale-Up: Fast and Aggressive to maintain low latency
    scaleUp:
      stabilizationWindowSeconds: 0
      selectPolicy: Max
      policies:
      - type: Percent
        value: 100
        periodSeconds: 15
      - type: Pods
        value: 4
        periodSeconds: 15

    # Scale-Down: Conservative and Stabilized to prevent flapping
    scaleDown:
      stabilizationWindowSeconds: 300  # 5-minute cooldown window
      selectPolicy: Min
      policies:
      - type: Percent
        value: 10                     # Shrink by at most 10% per minute
        periodSeconds: 60
```

---

## Exercise 3: KEDA Event-Driven Autoscaling with Scale-to-Zero

### Scenario & Objective
You are architecting an asynchronous order processing worker fleet that consumes messages from an AWS SQS queue (`https://sqs.us-east-1.amazonaws.com/123456789/order-processing-queue`).
Because message volume is bursty (busy during business hours, completely dead at night):
1. The worker Deployment must support **Scale-to-Zero** ($0$ Pods when the queue is empty) to eliminate cloud infrastructure costs.
2. Scale up to a maximum of **$50$ Pods** during peak sales events.
3. Target **$30$ pending messages per Pod** (`queueLength = 30`).
4. Enforce a **$120\text{s}$ cooldown period** before scaling to zero to avoid cold-start delays from small message trickles.

### Verified Production Solution

File: `k8s/keda-sqs-scaler.yaml`

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: order-processor-scaler
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: order-processor-worker

  # Scale-to-Zero Configuration
  minReplicaCount: 0                  # Scales down to 0 when queue is empty!
  maxReplicaCount: 50                 # Capped at 50 to prevent DB connection saturation

  # Timing Configurations
  pollingInterval: 15                 # Scrape SQS queue depth every 15 seconds
  cooldownPeriod: 120                 # Wait 2 minutes after last message before scaling to 0

  # Advanced HPA Scaling Behaviors
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 180
          policies:
          - type: Percent
            value: 20
            periodSeconds: 60

  # Trigger: AWS SQS Queue Depth
  triggers:
  - type: aws-sqs-queue
    metadata:
      queueURL: https://sqs.us-east-1.amazonaws.com/123456789/order-processing-queue
      queueLength: "30"               # 1 Pod for every 30 messages in queue
      awsRegion: "us-east-1"
      identityOwner: "operator"       # Uses Pod IAM Role via IRSA
```
