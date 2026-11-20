# Day 75: Capstone Project 3: Enterprise Event-Driven Microservices Backend

A complete production-grade distributed system blueprint synthesizing the entire **Phase 5: DevOps, Infrastructure & System Design** curriculum into an enterprise **Event-Driven E-Commerce Microservices Platform**.

---

## 1. Executive Summary & System Architecture

This capstone project implements an end-to-end, highly resilient, horizontally scalable **Event-Driven Microservices Backend** designed to process millions of transactions per day with zero data loss.

```
+-----------------------------------------------------------------------------------------------------------------+
|                                            ENTERPRISE INGRESS TIER                                              |
|                                                                                                                 |
|   [ Mobile Clients ]               [ Single Page Web Apps ]                 [ Third-Party Webhook Partners ]    |
|            |                                  |                                            |                    |
|            +----------------------------------+--------------------------------------------+                    |
|                                               |                                                                 |
|                                               v (HTTPS / TLS 1.3 Termination)                                   |
|                        +---------------------------------------------+                                          |
|                        |             KONG / ENVOY API GATEWAY        |                                          |
|                        |  - JWT Bearer Token Validation & Claims     |                                          |
|                        |  - Token Bucket Distributed Rate Limiting   |                                          |
|                        |  - Circuit Breaker Fast-Fail (Resilience4j) |                                          |
|                        |  - Dynamic Path Routing & Header Injection  |                                          |
|                        +----------------------+----------------------+                                          |
+-----------------------------------------------|-----------------------------------------------------------------+
                                                |
                                                v (Internal VPC Network / mTLS)
+-----------------------------------------------------------------------------------------------------------------+
|                                       CORE DOMAIN MICROSERVICES (DDD)                                           |
|                                                                                                                 |
|    +----------------------+       +----------------------+       +----------------------+       +--------------+|
|    |    ORDER SERVICE     |       |   PAYMENT SERVICE    |       |  INVENTORY SERVICE   |       | NOTIFICATION ||
|    |                      |       |                      |       |                      |       |   SERVICE    ||
|    |  +----------------+  |       |  +----------------+  |       |  +----------------+  |       |  +---------+ ||
|    |  | Orders DB      |  |       |  | Payments DB    |  |       |  | Inventory DB   |  |       |  | Audit DB| ||
|    |  | (PostgreSQL)   |  |       |  | (PostgreSQL)   |  |       |  | (PostgreSQL)   |  |       |  | (Mongo) | ||
|    |  +----------------+  |       |  +----------------+  |       |  +----------------+  |       |  +---------+ ||
|    |  | Outbox Events  |  |       |  | Outbox Events  |  |       |  | Outbox Events  |  |       +-------+------+|
|    |  +----------------+  |       |  +----------------+  |       |  +----------------+  |               ^       |
|    +-----------+----------+       +-----------+----------+       +-----------+----------+               |       |
|                |                              |                              |                          |       |
+----------------|------------------------------|------------------------------|--------------------------|-------+
                 v                              v                              v                          |
+---------------------------------------------------------------------------------------------------------|-------+
|                                    EVENT-DRIVEN BACKBONE (APACHE KAFKA)                                 |       |
|                                                                                                         |       |
|  +---------------------------------------------------------------------------------------------------+  |       |
|  |                     DEBEZIUM CHANGE DATA CAPTURE (CDC) REPLICATION ENGINE                         |  |       |
|  +---------------------------------------------------------------------------------------------------+  |       |
|                |                              |                              |                          |       |
|                v                              v                              v                          |       |
|  [ Topic: orders.events ]       [ Topic: payments.events ]     [ Topic: inventory.events ]              |       |
|    - 6 Partitions (Key: order_id) - 6 Partitions (Key: tx_id)    - 6 Partitions (Key: sku)              |       |
|    - Retention: 7 Days Compacted   - Retention: 30 Days Delete    - Retention: Compacted                |       |
|                |                              |                              |                          |       |
|                +------------------------------+------------------------------+--------------------------+       |
|                                               |                                                                 |
|                                               v                                                                 |
|                             +-----------------------------------+                                               |
|                             |      SAGA ORCHESTRATION ENGINE    |                                               |
|                             |  - State Machine Coordinator      |                                               |
|                             |  - Compensating Action Trigger    |                                               |
|                             |  - Dead-Letter Topic (DLT) Router |                                               |
|                             +-----------------------------------+                                               |
+-----------------------------------------------------------------------------------------------------------------+
```

---

## 2. Domain-Driven Design (DDD) & Service Boundaries

The platform strictly respects Domain-Driven Design (DDD) principles. Each service is built around an explicit **Bounded Context** with zero shared database tables:

### 1. Order Bounded Context
- **Aggregate Root**: `OrderAggregate` (guards line items, status state transitions, and price totals).
- **Ubiquitous Language**: `DraftOrder`, `PendingPaymentOrder`, `ConfirmedOrder`, `CancelledOrder`.
- **Database**: Dedicated PostgreSQL database (`orders_db`).

### 2. Payment Bounded Context
- **Aggregate Root**: `PaymentTransaction` (manages payment gateway tokens, authorizations, captures, and refunds).
- **Ubiquitous Language**: `AuthorizeFunds`, `CaptureCharge`, `IssueRefund`.
- **Database**: Dedicated PostgreSQL database (`payments_db`).

### 3. Inventory Bounded Context
- **Aggregate Root**: `InventoryItem` (manages available stock, active reservation leases, and warehouse allocations).
- **Ubiquitous Language**: `ReserveStock`, `ReleaseStockReservation`, `DeductPermanentStock`.
- **Database**: Dedicated PostgreSQL database (`inventory_db`).

### 4. Notification Bounded Context
- **Domain Event Consumer**: Read-only projection consumer listening to domain event topics, formatting transactional emails, SMS notifications, and audit logging.
- **Database**: Dedicated MongoDB / DynamoDB store (`notifications_db`).

---

## 3. Database-per-Service & The Strangler Fig Architecture

```
MIGRATION TOPOLOGY: Strangler Fig Pattern
+-----------------------------------------------------------------------------+
| 1. Ingress Proxy intercepts incoming customer traffic                       |
| 2. Routes new orders directly to Greenfield Order Microservice              |
| 3. Debezium CDC captures order events and synchronizes legacy ERP database  |
| 4. Old monolith continues reading synchronized views without modification   |
| 5. Monolith tables dropped once all domain services are fully decomposed    |
+-----------------------------------------------------------------------------+
```


---

## 4. The Transactional Outbox Pattern & Debezium CDC Pipeline

To eliminate the **Dual-Write Vulnerability** (updating a database and publishing an event to Kafka in separate uncoordinated operations), all mutative state changes are written into an `outbox_events` table **within the exact same local ACID database transaction**:

```sql
-- Atomic Unit of Work inside Order Service
BEGIN TRANSACTION;
  INSERT INTO orders (id, customer_id, total_amount, status)
  VALUES ('ord_101', 'cust_42', 250.00, 'PENDING');

  INSERT INTO outbox_events (event_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (
    'evt_9901',
    'Order',
    'ord_101',
    'OrderCreated',
    '{"order_id": "ord_101", "amount": 250.00, "customer_id": "cust_42"}'
  );
COMMIT;
```

### Debezium Engine Integration
- Debezium connects directly to the PostgreSQL Write-Ahead Log (WAL) via logical replication slots (`pgoutput`).
- It extracts new rows from `outbox_events` with near-zero latency ($< 10$ ms) and streams them to the Kafka topic `orders.events` with an **At-Least-Once** guarantee.
- Downstream consumers employ unique `event_id` checks to guarantee **Idempotent Exactly-Once Processing**.

---

## 5. The Saga Orchestration Engine & Compensating Transactions

Because distributed 2PC locking is fragile in high-throughput cloud environments, the platform implements an **Orchestration-based Saga** to manage distributed checkout consistency:

```
FORWARD EXECUTION PATH:
1. CreateOrder (PENDING) ===> 2. AuthorizePayment ===> 3. ReserveStock ===> 4. ConfirmOrder

BACKWARD COMPENSATION PATH (When ReserveStock fails):
1. ReleaseStock (No-op) <=== 2. RefundPayment <=== 3. CancelOrder (CANCELLED)
```

### Step Classifications
- **Compensable Steps**: Steps 1 & 2 (`CreateOrder`, `AuthorizePayment`). Succeeded steps that can be reversed via explicit compensations (`CancelOrder`, `RefundPayment`).
- **Pivot Step**: Step 3 (`ReserveStock`). Once stock reservation commits, the transaction pivots forward and must not be aborted.
- **Retriable Steps**: Step 4 (`ConfirmOrder`, `SendNotification`). Retried indefinitely using exponential backoff until completion.

---

## 6. API Gateway Ingress, Token Bucket Rate Limiting & Auth

The API Gateway provides a hardened security and ingress boundary:
1. **JWT Authentication**: Validates asymmetric RSA/ECDSA signatures at the edge; extracts user claims and decorates downstream headers (`X-User-ID`, `X-User-Role`, `X-Tenant-ID`).
2. **Token Bucket Rate Limiter**: Caches client token budgets in Redis, allowing traffic bursts up to 20 req/s while throttling sustained load to 10 req/s with `HTTP 429 Too Many Requests`.
3. **Correlation ID Injection**: Generates an OpenTelemetry-compatible `X-Correlation-ID` (UUIDv4) and propagates it across all service invocations for end-to-end distributed tracing.

---

## 7. Distributed Resilience: Circuit Breakers & Dead-Letter Topics (DLT)

1. **Circuit Breakers (Resilience4j Model)**:
   - Wrapped around all inter-service REST/gRPC calls.
   - 10-call sliding window.
   - Trips to `OPEN` if failure rate $\ge 50\%$.
   - Executes stale in-memory cache fallbacks with $< 1$ ms latency.
2. **Dead-Letter Topics (DLT)**:
   - Unparseable or poison-pill event payloads exceeding 3 retries are published to `orders.DLT`.
   - Alerting triggers in PagerDuty, allowing engineers to inspect the quarantine topic without stalling the main commit log.


---

## 8. Deployment Architecture & Kubernetes HPA Autoscaling

```
PRODUCTION KUBERNETES DEPLOYMENT TOPOLOGY
Namespace: ecom-prod
  ├── Deployments:
  │     ├── api-gateway (Replicas: 3-10, HPA: CPU > 70%)
  │     ├── order-service (Replicas: 4-16, HPA: CPU > 65% + Kafka Lag)
  │     ├── payment-service (Replicas: 3-8, HPA: Memory > 75%)
  │     ├── inventory-service (Replicas: 4-12, HPA: CPU > 70%)
  │     └── notification-worker (Replicas: 2-6, KEDA ScaledObject: Topic Lag > 500)
  ├── StatefulSets:
  │     ├── kafka-cluster (3 Brokers, KRaft Mode, NVMe SSD storage)
  │     ├── postgres-orders (Primary + Read Replica)
  │     ├── postgres-payments (Primary + Read Replica)
  │     └── postgres-inventory (Primary + Read Replica)
  └── Ingress:
        └── Nginx Ingress Controller with TLS 1.3 & AWS NLB L4 load balancer
```

### Autoscaling Policy (KEDA + HPA v2)
- **HTTP Services**: Scaled using Kubernetes Horizontal Pod Autoscaler based on CPU utilization and incoming HTTP request rate.
- **Asynchronous Event Consumers**: Scaled using **KEDA (Kubernetes Event-driven Autoscaling)** querying Kafka consumer lag from Prometheus (`kafka_consumergroup_lag`). When topic lag spikes above 500 messages, KEDA scales consumer pods rapidly to match topic partition count.

---

## 9. Distributed Observability (OpenTelemetry & Prometheus)

1. **Distributed Tracing (OpenTelemetry)**:
   - Ingress Gateway initializes trace context: `traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`.
   - Propagated across HTTP headers and Kafka record headers (`KafkaRecord.headers["traceparent"]`).
   - Visualized in Jaeger/Tempo to identify exact bottleneck spans.
2. **Prometheus Metrics**:
   - `http_requests_total{status, service, path}`
   - `saga_execution_duration_seconds{status="SUCCESS|COMPENSATED"}`
   - `circuit_breaker_state{name, state="CLOSED|OPEN|HALF_OPEN"}`
   - `kafka_consumer_lag{topic, partition, consumer_group}`

---

## 10. Performance Benchmarks & Capacity Sizing Matrix

| Metric | Target Specification | Production Sizing / Schedulers |
| :--- | :--- | :--- |
| **Peak Ingress Throughput** | 20,000 req/sec | Nginx L7 Ingress + 6 Gateway Replicas |
| **Kafka Event Throughput** | 100,000 events/sec | 3-node KRaft cluster, 6 partitions per topic |
| **P50 Checkout Latency** | $\le 18$ ms | Asynchronous Outbox + In-Memory Fast Path |
| **P99 Checkout Latency** | $\le 65$ ms | Full Jitter Backoff + Circuit Breaker Fast-Fail |
| **Data Durability Guarantee** | Zero Message Loss (100%) | `acks=all`, `min.insync.replicas=2`, Outbox CDC |
| **Failure Recovery Time** | $\le 300$ ms | Automatic Backward Saga Compensations |
