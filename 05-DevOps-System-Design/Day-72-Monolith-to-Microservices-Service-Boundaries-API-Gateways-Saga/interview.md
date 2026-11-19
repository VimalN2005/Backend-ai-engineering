# Day 72: Principal/Staff System Design Interview Q&A: Microservices & Saga Pattern

This guide covers the **Top 5 Principal and Staff-level distributed systems interview questions** on Monolith to Microservices decomposition, Domain-Driven Design, API Gateways, and Saga distributed transactions.

---

### Question 1: Compare Choreography-based vs. Orchestration-based Sagas in depth. What are the specific failure modes of Choreography (e.g., cyclic dependency storms, distributed deadlocks, observability blindness), and at what architectural threshold does Orchestration become mandatory?

#### What the Interviewer Is Looking For
- Technical maturity regarding the operational realities of distributed event-driven architectures.
- Awareness that Choreography becomes unmanageable as the number of participating services exceeds 3 or 4.
- Deep comprehension of how centralized state machines provide auditable compensation graphs and deterministic timeout management.

#### Exemplary Response
Both patterns solve distributed data consistency across microservices, but their operational topologies differ fundamentally:

1. **Choreography-based Sagas (Decentralized Pub/Sub)**:
   - **How it works**: Services subscribe to domain events on a message bus (Kafka/RabbitMQ) and emit subsequent events without a central coordinator.
   - **Fatal Failure Modes at Scale**:
     - **Cyclic Dependency Storms**: Service A emits `OrderCreated`, causing Service B to emit `PaymentAuthorized`, which triggers Service C to emit `InventoryReserved`. If an edge-case failure in Service C emits `InventoryFailed`, which causes Service B to refund and emit `PaymentRefunded`, Service A might accidentally interpret this as a customer modification and re-emit `OrderCreated`. The system enters an infinite distributed ping-pong event loop.
     - **Observability Blindness**: Tracking the current global state of Order #4928 requires querying 5 distinct distributed tracing spans across 5 datastores. There is no single place in the codebase that documents the entire workflow.
     - **Distributed Deadlocks**: When multiple concurrent Sagas race for the same two shared resources in opposite order (e.g., Saga 1: reserve Hotel then Car; Saga 2: reserve Car then Hotel), decentralized choreography can deadlock.

2. **Orchestration-based Sagas (Centralized State Coordinator)**:
   - **How it works**: A dedicated **Saga Execution Coordinator (SEC)** (e.g., Temporal, AWS Step Functions, or an internal state engine) explicitly invokes RPC commands on microservices and stores workflow progression in a durable state log.
   - **Advantages**:
     - Deterministic step-by-step state machine with linear forward execution and guaranteed reverse compensation.
     - Centralized visibility: one dashboard query reveals the exact status, failed step, and compensating state for any order.
     - Separation of concerns: business services remain dumb workers focused on their domain logic, without having to understand the global macro-workflow.

3. **The Architectural Threshold**:
   - Use **Choreography** only for simple, linear 2-to-3-step workflows (e.g., User Signs Up $\to$ Send Welcome Email $\to$ Provision Analytics Profile).
   - Once a workflow involves **4 or more microservices**, non-linear branching, conditional compensations, or strict regulatory auditing (e.g., e-commerce checkout, financial settlements, loan processing), **Orchestration is mandatory**.

---

### Question 2: In a distributed Saga, local database transactions commit immediately, meaning Sagas completely lack Isolation (the "I" in ACID). How do you architecturally defend against Dirty Reads and Lost Updates in production?

#### What the Interviewer Is Looking For
- Deep understanding of the BASE model (Basically Available, Soft state, Eventual consistency).
- Practical countermeasures to distributed race conditions.
- Familiarity with Semantic Locks, Commutative Updates, and Pessimistic View patterns.

#### Exemplary Response
Because Sagas execute as a series of independent local ACID transactions, intermediate states are committed and visible to concurrent actors before the entire Saga finishes. This exposes systems to three classic concurrency anomalies: **Lost Updates**, **Dirty Reads**, and **Non-Repeatable Reads**.

To mitigate this without distributed two-phase locks, we employ three senior engineering patterns:

1. **Semantic Locking (The Pending State Pattern)**:
   - Any entity modified during a compensable step must be flagged with a semantic lock:
     ```sql
     UPDATE orders SET status = 'PAYMENT_PENDING' WHERE id = 101;
     UPDATE inventory SET reserved_qty = reserved_qty + 1 WHERE sku = 'MACBOOK_PRO';
     ```
   - Concurrent transactions inspecting the entity inspect the flag:
     - Read-only operations can either display the item as "Temporarily Reserved / Holding" or read the previous committed snapshot.
     - Write operations attempting to modify the same entity are rejected or queued until the semantic lock clears.

2. **Commutative Update Design**:
   - Structure domain update operations so that order of execution does not alter the mathematical outcome.
   - *Example*: In an account balance service, instead of setting absolute values (`balance = 500`), express operations as commutative deltas (`balance += 50` or `balance -= 50`). If Saga A's debit and Saga B's credit interleave concurrently, the net balance remains mathematically correct regardless of whether Saga A later compensates (`balance += 50`).

3. **Pessimistic View / Approval Step**:
   - For high-value transactions (e.g., wire transfers $> \$50,000$), insert a human or automated review gateway between the compensable steps and the pivot transaction, holding the resource in escrow before irreversible money movement occurs.

---

### Question 3: Walk through the enterprise migration runbook for decomposing a core domain (e.g., Billing) out of a 15-year-old monolithic database using the Strangler Fig Pattern and an Anti-Corruption Layer (ACL). How do you ensure zero downtime and zero data divergence?

#### What the Interviewer Is Looking For
- Real-world experience with database migration complexity.
- Understanding of Change Data Capture (CDC), dual-writing vs. log tailing, and bidirectional data reconciliation.
- How an Anti-Corruption Layer prevents greenfield microservices from inheriting legacy database schema antipatterns.

#### Exemplary Response
Decomposing a critical domain without downtime requires a disciplined **5-phase migration runbook**:

```
[Phase 1: Edge Proxy & ACL]  --->  [Phase 2: CDC Replication]  --->  [Phase 3: Dark Launch]
                                                                            |
[Phase 5: Cutover & Decommission] <--- [Phase 4: Dual-Run Verification] <---+
```

1. **Phase 1: Ingress Interception & Anti-Corruption Layer (ACL)**:
   - Deploy an API Gateway in front of the monolith.
   - Build an Anti-Corruption Layer (ACL) in the new Billing microservice. The ACL translates monolithic numeric status codes and delimited strings into clean Domain-Driven Design Value Objects and Aggregate Roots.

2. **Phase 2: Change Data Capture (CDC) Monolith $\to$ Microservice**:
   - Never use application-level dual-writes (prone to partial failures).
   - Attach a CDC engine (e.g., Debezium) directly to the monolithic PostgreSQL/Oracle Write-Ahead Log (WAL).
   - As writes hit the legacy monolith, Debezium streams events into Kafka, and the new Billing microservice consumes them to hydrate its independent database in real-time.

3. **Phase 3: Dark Launch (Shadow Traffic)**:
   - The API Gateway duplicates incoming production billing requests (`POST /billing/charge`).
   - The live request routes to the legacy monolith; an asynchronous shadow copy routes to the new microservice.
   - Compare outputs and database states offline. If the new service errors, customer traffic is unaffected.

4. **Phase 4: Invert Authority with Reverse CDC**:
   - Flip the authoritative write path: API Gateway now routes 100% of live billing traffic to the **new microservice**.
   - Enable **Reverse CDC** (Debezium tailing the new microservice's DB and updating the legacy monolith DB) so legacy monolith code that hasn't been migrated yet still reads fresh data.

5. **Phase 5: Cutover & Decommission**:
   - Once all dependent monolith modules are refactored to call the new service via gRPC/REST, decommission the reverse CDC pipeline and drop the legacy billing tables.

---

### Question 4: How do you design an API Gateway architecture at scale? Compare a Centralized API Gateway vs. Backend-for-Frontend (BFF) vs. Service Mesh (Envoy/Istio). What belongs at the Edge, and what belongs in the Mesh?

#### What the Interviewer Is Looking For
- Clear demarcation of responsibilities between Edge routing, Client adaptation (BFF), and East-West service communication (Service Mesh).
- Avoiding the "Smart Gateway" antipattern.
- Performance implications (TLS termination, connection pooling, multiplexing).

#### Exemplary Response
A scalable architecture divides network responsibilities across three distinct tiers:

```
Tier 1: Edge API Gateway (North-South: Internet to Data Center)
                  |
Tier 2: Backend-for-Frontend (BFF) (Client Optimization)
                  |
Tier 3: Service Mesh / Sidecars (East-West: Microservice to Microservice)
```

1. **Tier 1: Edge API Gateway (e.g., Kong, Envoy, AWS API Gateway)**:
   - **Boundary**: North-South traffic crossing the public internet security perimeter.
   - **Responsibilities**:
     - Global DDoS mitigation, IP geo-blocking, and WAF rules.
     - TLS 1.3 termination and HTTP/2 or HTTP/3 negotiation.
     - Centralized authentication token validation (validating OAuth2 / JWT signatures).
     - Global rate limiting (protecting cluster ingress from traffic spikes).

2. **Tier 2: Backend-for-Frontend (BFF) (e.g., Node.js / Go microservices)**:
   - **Boundary**: Tailored specifically to client form factors (Mobile App BFF vs. Web Desktop BFF vs. Partner Public API BFF).
   - **Responsibilities**:
     - Over-the-wire payload optimization (stripping unnecessary fields for low-bandwidth mobile clients).
     - Request aggregation: combining 4 internal microservice calls into 1 single HTTP payload to eliminate mobile radio wakeup latency.

3. **Tier 3: Service Mesh (e.g., Istio + Envoy Sidecars)**:
   - **Boundary**: East-West traffic between internal microservices.
   - **Responsibilities**:
     - Mutual TLS (mTLS) for zero-trust service-to-service cryptographic identity.
     - Service discovery and localized client-side load balancing (e.g., Weighted Least-Connections).
     - Automatic retries, circuit breaking, and distributed tracing header propagation (`traceparent`, `b3`).

---

### Question 5: What are the 8 Fallacies of Distributed Computing, and how do they directly dictate modern microservice fault-tolerance patterns?

#### What the Interviewer Is Looking For
- Awareness of L. Peter Deutsch's classic distributed computing fallacies.
- How specific microservice architecture patterns directly counter these false assumptions.

#### Exemplary Response
In monolithic development, developers assume in-process method calls are instantaneous and reliable. Moving to microservices without unlearning these assumptions leads to fragile systems.

| Fallacy | Reality in Microservices | Required Architectural Pattern |
| :--- | :--- | :--- |
| **1. The network is reliable** | Packets drop, Wi-Fi fluctuates, cloud nodes crash | **Idempotent APIs**, automated exponential retries with jitter |
| **2. Latency is zero** | In-memory $= <1$ µs; cross-AZ network $= 2–20$ ms | **Batching**, asynchronous messaging (Kafka), caching |
| **3. Bandwidth is infinite** | Saturated network interfaces cause packet queuing | **Payload pruning**, Protobuf/gRPC binary serialization |
| **4. The network is secure** | Internal VPC networks can be compromised | **Zero-Trust architecture**, Mutual TLS (mTLS) via Service Mesh |
| **5. Topology doesn't change** | Kubernetes pods autoscale, die, and reschedule | **Dynamic Service Discovery** (Consul, CoreDNS, Envoy) |
| **6. There is one administrator**| Multiple autonomous teams deploy independently | **API versioning**, contract testing (Pact), semantic versioning |
| **7. Transport cost is zero** | Serializing/deserializing JSON consumes CPU | High-performance serializers, Keep-Alive connection pooling |
| **8. The network is homogeneous** | Polyglot services run on different OSs/runtimes | Standardized open protocols (HTTP/2, gRPC, JSON/Protobuf) |
