# Day 75: Principal/Staff System Design Interview Q&A: Event-Driven Microservices Capstone

This guide covers the **Top 5 Principal and Staff-level system design interview questions** synthesized from the complete Phase 5 DevOps, Infrastructure, and Distributed Architecture curriculum.

---

### Question 1: How do you design an enterprise payment and checkout system with zero data loss under network partitions and container restarts? Compare the Transactional Outbox Pattern + Debezium with Two-Phase Commit (2PC).

#### What the Interviewer Is Looking For
- Technical mastery of the Dual-Write problem.
- Why 2PC/XA distributed transactions are discarded in modern cloud native microservices (coordinator single point of failure, in-doubt lock holding).
- End-to-end reliability guarantees of Log-based Change Data Capture (CDC) via Debezium + Kafka.

#### Exemplary Response
In modern microservices, updating an internal database and publishing an event to a message broker cannot be accomplished atomically via simple application-level logic without risking data loss.

1. **Why Two-Phase Commit (2PC) Fails**:
   - 2PC requires synchronous coordination and holds distributed locks across disparate systems (e.g. Postgres and Kafka).
   - If the coordinator crashes during Phase 2, participants are trapped in an **In-Doubt state**, holding row locks indefinitely and collapsing system availability.
   - 2PC is fundamentally incompatible with high-throughput cloud microservices ($>1,000$ tx/s).

2. **The Production Standard: Transactional Outbox + Debezium CDC**:
   - **Local ACID Boundary**: The service inserts the business entity (`orders`) and an outbox event (`outbox_events`) within the **same local database transaction**. Either both persist to disk or neither does.
   - **Log-Based CDC (Debezium)**: Debezium reads the PostgreSQL Write-Ahead Log (WAL) directly using logical decoding (`pgoutput`). It bypasses the SQL execution engine, avoiding index overhead and table polling.
   - **Kafka Reliability**: Debezium streams events to Kafka with `acks=all` and `min.insync.replicas=2`.
   - **Idempotent Consumers**: Downstream services use consumer-side deduplication tables (`processed_events`) with unique event IDs to convert At-Least-Once delivery into **Exactly-Once execution semantics**.

---

### Question 2: In an Orchestration-based Saga, how do you handle concurrency anomalies like "Dirty Reads" and "Lost Updates" that occur because Sagas lack distributed isolation?

#### What the Interviewer Is Looking For
- Clear understanding that Sagas provide Atomicity and Durability (BASE) but lack Isolation.
- Concrete countermeasures: Semantic Locks, Commutative Updates, and Pessimistic View patterns.

#### Exemplary Response
Because Sagas commit local database transactions at each step, intermediate uncommitted states are visible to concurrent transactions:

1. **Semantic Locking**:
   - An entity undergoing a multi-step Saga must be flagged with a pending status: `status = 'PAYMENT_PENDING'`.
   - Concurrent read requests can either display the item as "Temporarily On Hold" or read the previous committed snapshot.
   - Concurrent write requests attempting to modify the same entity are rejected with `HTTP 409 Conflict`.

2. **Commutative Update Design**:
   - Structure domain update operations so that order of execution does not alter the mathematical outcome.
   - *Example*: In an account balance service, instead of setting absolute values (`balance = 500`), express operations as commutative deltas (`balance += 50` or `balance -= 50`). If Saga A's debit and Saga B's credit interleave concurrently, the net balance remains mathematically correct regardless of whether Saga A later compensates (`balance += 50`).

3. **Pivot Point Segregation**:
   - Ensure all compensable steps precede the **Pivot Transaction** (the point of no return). After the pivot transaction commits, the Saga must only execute retriable steps that run forward to completion.

---

### Question 3: How do you design an ingress architecture capable of absorbing a 50x Black Friday traffic surge without crashing internal microservices?

#### What the Interviewer Is Looking For
- Multi-tier ingress design: DNS, Layer 4 vs. Layer 7 load balancing, API Gateway token buckets, and KEDA autoscaling.
- Defense-in-depth: rate limiting, load shedding, and circuit breaking.

#### Exemplary Response
A resilient ingress pipeline uses a defense-in-depth hierarchy:

1. **Edge Tier (L4 & CDN)**:
   - AWS Route 53 with latency-based routing.
   - Cloudflare / CloudFront caching static assets and absorbing DDoS attacks.
   - AWS Network Load Balancer (NLB) operating at Layer 4 with Direct Server Return (DSR), forwarding millions of raw TCP packets/sec without TLS termination overhead.

2. **Ingress & Gateway Tier (L7)**:
   - Nginx / Envoy API Gateway terminating TLS 1.3 and multiplexing HTTP/2 connections.
   - **Distributed Token Bucket Rate Limiting** backed by Redis Cluster, throttling abusive IPs while allowing legitimate bursts.
   - **Adaptive Concurrency Limiting (Netflix gradient algorithm)**: measures latency inflation (RTT) and automatically sheds non-essential traffic before internal queues saturate.

3. **Asynchronous Decoupling & KEDA Autoscaling**:
   - Convert mutative checkout commands into Kafka events immediately at ingress.
   - Worker pods scale automatically using **KEDA (Kubernetes Event-driven Autoscaling)** based on real-time Kafka consumer lag metrics rather than reactive CPU utilization.

---

### Question 4: How do you achieve true Zero-Downtime database migrations when decomposing a monolithic schema into microservice databases?

#### What the Interviewer Is Looking For
- The 5-phase Strangler Fig migration methodology.
- Expand-Contract (Parallel Run) database schema migrations.
- Bi-directional synchronization and data reconciliation.

#### Exemplary Response
Decomposing a live database without downtime requires the **Expand-Contract Pattern** combined with **Shadow CDC**:

1. **Phase 1 (Expand)**: Create the new microservice and its dedicated database. Deploy an Anti-Corruption Layer (ACL) that translates legacy data formats into clean domain models.
2. **Phase 2 (Shadow CDC Replicate)**: Attach Debezium to the monolithic WAL. Stream all legacy writes to the new microservice database in real-time.
3. **Phase 3 (Dark Launch & Verification)**: The API Gateway duplicates incoming write traffic. Asynchronous verification workers compare outputs and log divergences without impacting users.
4. **Phase 4 (Invert Authority)**: Switch the primary write path at the API Gateway to the new microservice. Reverse the CDC pipeline (stream writes from the microservice back to the monolith) so legacy services that haven't migrated yet stay up-to-date.
5. **Phase 5 (Contract)**: Once all dependent monolith modules are refactored to call the new service via gRPC/REST, decommission the reverse CDC pipeline and drop the legacy tables.

---

### Question 5: When a critical downstream dependency slows down from 20ms to 15 seconds, what specific failure cascade occurs, and how do Circuit Breakers, Bulkheads, and Distributed Deadlines prevent system collapse?

#### What the Interviewer Is Looking For
- Understanding thread pool exhaustion, latency amplification, and queue dogpiling.
- The distinct roles of Circuit Breakers (fast-fail), Bulkheads (concurrency capping), and Deadlines (wasted work cancellation).

#### Exemplary Response
When a dependency slows down by $750\times$, incoming requests arrive faster than they can be cleared:
- **The Cascade**: Worker threads remain blocked on socket reads. Within 3 seconds, all 200 container threads are exhausted. Health check endpoints (`/healthz`) stop responding. Kubernetes assumes the container is dead and restarts it. Traffic redirects to surviving pods, crashing them sequentially.

**The Three-Tier Defense**:
1. **Bulkhead Pattern (Fault Isolation)**: Allocates a dedicated semaphore or thread pool (e.g. max 15 concurrent calls) for the slow dependency. Even if that service completely freezes, it can never consume more than 15 threads, leaving the remaining 185 threads available for healthy endpoints.
2. **Circuit Breaker (Fast-Fail Protection)**: Detects that the failure rate or slow call rate exceeds $50\%$. The breaker trips to `OPEN`, immediately short-circuiting calls in $0.00$ ms and returning a cached fallback without touching network sockets.
3. **Distributed Deadline Budget Propagation**: The API Gateway injects an absolute deadline (`X-Request-Deadline: Now + 500ms`). When the budget is exhausted, downstream database drivers cancel queries in-flight, preventing wasted CPU on abandoned requests.
