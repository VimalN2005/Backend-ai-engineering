# Day 72: Monolith to Microservices: Service Boundaries, API Gateways & Saga Pattern

A comprehensive architectural blueprint covering enterprise system decomposition from monolithic architectures to microservices, Domain-Driven Design (DDD) bounded contexts, Anti-Corruption Layers (ACL), API Gateway & BFF patterns, distributed data consistency, and the Saga Pattern (Orchestration vs. Choreography) with compensating transactions.

---

## 1. Monolith vs. Microservices Architecture: The Strategic Trade-Off

```
MONOLITHIC ARCHITECTURE                    MICROSERVICES ARCHITECTURE
+-------------------------------+          +-------------+  +-------------+
|        Single Process         |          | Auth Service|  |Order Service|
|  [Auth] [Orders] [Payments]   |          |  (Postgres) |  |   (Mongo)   |
|  [Inventory] [Notifications]  |          +------+------+  +------+------+
+---------------+---------------+                 ^                ^
                |                                 |    API GATEWAY |
         +------+------+                          +-------+--------+
         | Shared ACID |                                  |
         |  Database   |                         [Client Applications]
         +-------------+
```

### Strategic Trade-Off Matrix

| Dimension | Monolithic Architecture | Modular Monolith | Distributed Microservices |
| :--- | :--- | :--- | :--- |
| **Deployment Model** | Single deployable artifact (`.war`, binary) | Single binary, strict code boundaries | Dozens/hundreds of independent containers |
| **Transaction Boundary**| Local ACID database transactions | Local ACID database transactions | Distributed transactions (Sagas, BASE) |
| **Network Overhead** | In-memory function calls ($< 1$ µs) | In-memory function calls ($< 1$ µs) | Network RPC / HTTP / gRPC ($2–20$ ms) |
| **Failure Domain** | Single crash brings down entire app | Single crash brings down entire app | Isolated: failure of 1 service doesn't crash all |
| **Team Autonomy** | High merge contention, release train | Low merge contention, single deploy | Maximum team autonomy, independent CI/CD |
| **Operational Cost** | Low: 1 DB, 1 deployment pipeline | Low: 1 DB, modular packaging | High: K8s, service meshes, distributed tracing |

> [!IMPORTANT]
> **Martin Fowler's Monolith First Rule**: Never start with microservices unless you have a crystal-clear understanding of the domain boundaries. Decomposing a domain before understanding it produces a **Distributed Monolith**—inheriting all the complexity of distributed systems with none of the benefits of microservices.

---

## 2. Domain-Driven Design (DDD): Establishing Service Boundaries

The only sustainable way to define microservice boundaries is via **Domain-Driven Design (DDD)**:

```
+------------------------------------------------------------------------+
|                          ECOMMERCE SYSTEM                              |
|                                                                        |
|  +------------------------------+     +-----------------------------+  |
|  |     ORDER BOUNDED CONTEXT    |     |  BILLING BOUNDED CONTEXT    |  |
|  |  Ubiquitous Language:        |     |  Ubiquitous Language:       |  |
|  |  - Order (Aggregate Root)    |     |  - Invoice (Aggregate Root) |  |
|  |  - LineItem (Entity)         |     |  - PaymentMethod (Value)    |  |
|  |  - OrderStatus (Value Obj)   |     |  - LedgerEntry (Entity)     |  |
|  +--------------+---------------+     +--------------+--------------+  |
|                 |                                    |                 |
|                 +============[ ACL / Events ]========+                 |
+------------------------------------------------------------------------+
```

### Core DDD Concepts
1. **Bounded Context**: An explicit linguistic and model boundary within which a domain model applies strictly. The word *"Customer"* means something completely different to the Support Team (tickets, sentiment), Sales Team (leads, ARR), and Shipping Team (delivery address, gate code). Forcing a single global `Customer` entity across all contexts causes architectural sclerosis.
2. **Aggregate Root**: The primary entity that guards consistency within the boundary (e.g., `Order` controls `OrderLineItems`). Outside services can only reference an aggregate by its unique ID.
3. **Context Mapping**: Defines how different bounded contexts integrate:
   - **Shared Kernel**: Tightly coupled shared library or schema (use sparingly).
   - **Customer-Supplier**: Upstream service dictates API; downstream consumes.
   - **Anti-Corruption Layer (ACL)**: A translation adapter ensuring that legacy or external schemas do not contaminate the internal clean domain model.

---

## 3. Migration Patterns: The Strangler Fig Pattern

Never attempt a "Big Bang" rewrite. Instead, use the **Strangler Fig Pattern** to incrementally carve out capabilities from the legacy monolith:

```
Phase 1: Intercept                 Phase 2: Carve Out               Phase 3: Strangled
+---------------------+           +---------------------+           +---------------------+
|     API GATEWAY     |           |     API GATEWAY     |           |     API GATEWAY     |
+----------+----------+           +-----+---------+-----+           +-----+---------+-----+
           |                            |         |                       |         |
           v                            v         v                       v         v
     [Legacy Monolith]            [Monolith]  [New Service]          [Service A] [Service B]
     (Handles 100%)               (Orders)    (Payments)
```

### Implementation Mechanics
1. **Edge Interception**: Place an API Gateway (or reverse proxy like Nginx/Envoy) in front of the monolith.
2. **Carve Out Non-Critical Path**: Extract a standalone domain (e.g., Notifications or Reviews) into a new microservice.
3. **Change Data Capture (CDC)**: Use Debezium or PostgreSQL logical decoding to mirror database updates bidirectionally until the new service becomes the authoritative source of truth.
4. **Reroute Traffic**: Update gateway routing rules to direct traffic for that path to the new microservice.
5. **Decommission**: Remove legacy code from the monolith. Repeat until the monolith is eliminated.

---

## 4. The Database-per-Service Principle

In microservices, **services must NEVER share a physical database**:

```
ANTI-PATTERN: Shared Database               PRODUCTION PATTERN: Database-per-Service
+-----------+    +-----------+             +-----------+             +-----------+
|   Order   |    |  Billing  |             |   Order   |             |  Billing  |
|  Service  |    |  Service  |             |  Service  |             |  Service  |
+-----+-----+    +-----+-----+             +-----+-----+             +-----+-----+
      |                |                         |                         |
      +-------+--------+                         v                         v
              |                             [(Orders DB)]             [(Billing DB)]
              v                             (PostgreSQL)                 (MySQL)
       [(Shared DB)]
 (Schema coupling & lock contention)
```

### Why Shared Databases Are Fatal in Microservices
1. **Tight Schema Coupling**: Altering a column in `users` breaks 5 independent services without warning.
2. **Transaction Lock Contention**: A long-running analytics query in the billing domain locks tables needed by real-time order processing.
3. **Technology Lock-In**: Precludes choosing the optimal datastore for each workload (e.g., Neo4j for social graphs, Redis for sessions, PostgreSQL for ledger data).

---

## 5. API Gateway & Backend for Frontend (BFF) Pattern

An **API Gateway** acts as the single ingress entry point, shielding internal microservice topologies from external clients:

```
[Mobile App]          [Web Browser]         [Public API Partner]
     |                      |                         |
     v                      v                         v
[Mobile BFF]           [Web BFF]             [Partner Gateway]
     |                      |                         |
     +----------------------+-------------------------+
                            |
           +----------------+----------------+
           |                |                |
           v                v                v
     [Auth Service]   [Order Service]  [Catalog Service]
```

### Cross-Cutting Responsibilities
- **Authentication & Authorization**: Validates JWTs / OAuth tokens; forwards clean claims via internal headers (`X-User-ID`, `X-User-Roles`).
- **Rate Limiting & Throttling**: Protects downstream services using Token Bucket or Leaky Bucket algorithms.
- **Request Routing & Path Rewriting**: Maps public endpoints (`/v1/orders`) to internal service discovery endpoints.
- **Circuit Breaking & Fallback**: Short-circuits failing upstream services and returns cached or degraded responses.
- **Backend for Frontend (BFF)**: Specialized gateways tailored to specific client form factors (e.g., Mobile BFF strips heavy JSON payloads to conserve cellular bandwidth; Web BFF aggregates multiple calls into one).

---

## 6. Distributed Transactions & The Dual-Write Dilemma

In a monolithic database, multi-table consistency is maintained via local ACID transactions:
```sql
BEGIN TRANSACTION;
  UPDATE inventory SET stock = stock - 1 WHERE item_id = 99;
  INSERT INTO orders (id, status) VALUES (101, 'CONFIRMED');
  INSERT INTO payments (id, amount) VALUES (505, 120.00);
COMMIT;
```

In microservices, Inventory, Orders, and Payments live in **different processes on different servers with different databases**.
- Traditional distributed transactions (**Two-Phase Commit / 2PC / XA**) require a central coordinator holding locks across all nodes.
- **Why 2PC Fails in Cloud Microservices**:
  1. **Lock Stalling**: If a network partition occurs between Phase 1 (Prepare) and Phase 2 (Commit), resources remain locked indefinitely.
  2. **Latency Explosion**: Transaction latency is bounded by the *slowest network round-trip* across all services.
  3. **Availability Collapse**: If any single service is unreachable, the entire transaction aborts ($A = a_1 \times a_2 \times a_3$).

---

## 7. The Saga Pattern: Orchestration vs. Choreography

A **Saga** is a sequence of local transactions where each step updates data within a single service and publishes an event/message triggering the next step. If a step fails, the Saga executes **Compensating Transactions** to undo preceding steps.

```
SUCCESSFUL SAGA EXECUTION:
[Create Order] ===> [Authorize Payment] ===> [Reserve Stock] ===> [Confirm Order]
    (Step 1)              (Step 2)                 (Step 3)            (Completed)

FAILED SAGA EXECUTION (Compensating Sequence):
[Create Order] ===> [Authorize Payment] ===> [Reserve Stock FAILS!]
       |                       |
       v (Undo)                v (Undo)
[Cancel Order] <==== [Refund Payment] <================================(Trigger)
```

### Choreography vs. Orchestration

```
CHOREOGRAPHY (Event-Driven Decentralized)
Order Service ===(OrderCreated)===> Payment Service ===(PaymentAuthorized)===> Inventory Service
      ^                                    |                                          |
      |                                    +==============(PaymentFailed)=============+
      +===============================================================================+

ORCHESTRATION (Centralized State Coordinator)
                         +-----------------------------+
                         |    SAGA ORCHESTRATOR        |
                         |  (State Machine Engine)     |
                         +--+--------+--------------+--+
                            |        |              |
                            v        v              v
                     [Order Svc] [Payment Svc] [Inventory Svc]
```

| Dimension | Choreography-based Saga | Orchestration-based Saga |
| :--- | :--- | :--- |
| **Coordination** | Decentralized: services react to pub/sub events | Centralized: Orchestrator directs services |
| **Coupling** | Loosely coupled services | Services coupled to Orchestrator commands |
| **Complexity** | Difficult to visualize as steps grow ($> 4$ steps) | Centralized, explicit, easy to visualize |
| **Failure Handling**| High risk of cyclic dependencies and deadlocks | Clear step-by-step state machine with compensation |
| **Recommended For**| Simple workflows (2–3 services) | Complex multi-step enterprise workflows (4+ steps)|

---

## 8. Compensating Transactions & Step Classifications

In a distributed Saga, you cannot simply `ROLLBACK` because earlier local transactions have already committed. You must execute **Semantic Undos**:

| Step Type | Description | Example |
| :--- | :--- | :--- |
| **Compensable Transactions** | Transactions that can potentially be rolled back using a compensating transaction | `AuthorizePayment` $\to$ `VoidAuthorization` |
| **Pivot Transaction** | The point of no return. If it commits, the Saga **must run forward to completion** | `CapturePayment` (Money officially changes hands) |
| **Retriable Transactions** | Transactions that follow the pivot and are **guaranteed to eventually succeed** | `SendReceiptEmail`, `UpdateSearchIndex` |

> [!CAUTION]
> **Compensating Transactions MUST Be Idempotent**: Due to network retries, a compensating transaction (e.g., `refund_payment(order_id)`) may execute multiple times. If it is not idempotent, a customer could receive 3 refunds for a single cancelled order!

---

## 9. Concurrency & Isolation Anomalies in Sagas (ACID without the "I")

Sagas guarantee **Atomicity**, **Consistency**, and **Durability** (eventually), but **lack Isolation (I)**. Local transactions commit their changes immediately, exposing intermediate, uncommitted states to concurrent users.

### Common Concurrency Anomalies
1. **Lost Updates**: Saga A updates a record; Saga B overwrites it without knowing Saga A's intermediate state.
2. **Dirty Reads**: Saga A reserves the last laptop in stock. Customer B sees "Out of Stock". Saga A later fails and rolls back, but Customer B has already bounced.

### Senior Engineering Countermeasures
- **Semantic Locking (Pending State)**: Records are marked with a pending flag (e.g., `status = 'PAYMENT_PENDING'`). Other transactions seeing this status either block, reject, or read with degraded permissions.
- **Commutative Updates**: Design operations so ordering does not matter (e.g., `account_balance += 50` is commutative; `account_balance = 500` is not).
- **Pessimistic View / Quorum**: For high-value transactions, verify state across replicas or lock entity rows using distributed leases before proceeding.

---

## 10. Architectural Decision Framework

```
                          Do you have a clear, mature domain model?
                                      /              \
                                    NO                YES
                                   /                    \
                         Build a Modular Monolith        Do you require independent deployment
                                                         and scaling across large autonomous teams?
                                                                 /                \
                                                                NO                YES
                                                               /                    \
                                                    Keep Modular Monolith     Adopt Microservices
                                                                               + API Gateway
                                                                               + Saga Orchestration
```
