# Day 72: Rapid Revision & Flashcards: Microservices & Saga Pattern

A 5-minute reference sheet, mathematical formulas, critical comparison tables, and 10 self-testing flashcards covering Monolith to Microservices decomposition, Domain-Driven Design, API Gateways, and the Saga Pattern.

---

## 1. High-Density Cheat Sheet

### Core Concepts Summary
- **Bounded Context**: An explicit linguistic and functional boundary in Domain-Driven Design (DDD). The same domain term (e.g., `Order`, `Account`) has strictly scoped definitions inside its context.
- **Anti-Corruption Layer (ACL)**: An adapter service/layer that translates between legacy or external data models and clean internal domain aggregates, protecting microservices from domain rot.
- **Strangler Fig Pattern**: Incrementally replacing legacy monolithic capabilities by intercepting traffic at the edge and routing specific paths to newly carved-out microservices.
- **Database-per-Service**: Microservices must own their datastores exclusively. Shared databases cause severe schema coupling and transaction lock contention.
- **API Gateway**: Reverse proxy handling North-South ingress, authentication token verification, rate limiting, and routing. Keep it thin to avoid the "Smart Gateway" antipattern.
- **Backend-for-Frontend (BFF)**: Specialized gateways per client form factor (Mobile BFF, Web Desktop BFF) performing request aggregation and payload pruning.
- **Saga Pattern**: A sequence of local ACID transactions coordinating a multi-service business workflow without distributed locks.
- **Compensating Transactions**: Semantic undo operations that reverse the business effects of previously committed compensable steps upon workflow failure.
- **Orchestration vs. Choreography**:
  - *Choreography*: Decentralized pub/sub domain events (best for simple $\le 3$-step flows).
  - *Orchestration*: Centralized state machine engine directing worker commands (mandatory for complex $\ge 4$-step enterprise workflows).

---

## 2. Mathematical Reference & Formulas

### 1. Cascading Availability in Synchronous Microservice Chains
If a transaction synchronously calls $N$ independent microservices, each with individual availability $A_i$:
$$\text{System Availability} = \prod_{i=1}^N A_i = A_1 \times A_2 \times \dots \times A_N$$
*Example*: 4 synchronous services with $99.5\%$ availability ($0.995$):
$$\text{Availability} = 0.995^4 \approx 98.01\% \quad (\approx 7.2 \text{ hours downtime/month!})$$

### 2. Cumulative Latency in Synchronous Call Chains
$$\text{Total Latency} = \sum_{i=1}^N L_i + \sum_{i=1}^{N-1} RTT_i$$
Where $L_i$ is service execution time and $RTT_i$ is network round-trip time between services.

### 3. Saga Reliability (Forward Execution vs. Compensation)
$$\mathbb{P}(\text{Saga Success}) = \prod_{k=1}^M p_k$$
Where $p_k$ is the success probability of step $k$. If any step $j$ fails, compensation must guarantee $\mathbb{P}(\text{Compensation Success}) \to 1.0$ through **idempotent retries**.

---

## 3. Comparison Matrix: Monolith vs. Microservices vs. Modular Monolith

| Dimension | Monolith | Modular Monolith | Microservices |
| :--- | :--- | :--- | :--- |
| **Code Structure** | Single codebase, loose boundaries | Single codebase, strict module boundaries | Distributed codebases, independent repos |
| **Datastore** | Single shared database | Single database, schema isolation per module | Strictly Database-per-Service |
| **Inter-Module Comms**| In-memory function calls ($<1$ µs) | In-memory function calls ($<1$ µs) | Network RPC / HTTP / gRPC ($2–20$ ms) |
| **Transactions** | ACID (Local database transaction) | ACID (Local database transaction) | BASE / Sagas (Compensating transactions) |
| **Deployment Unit** | Single monolithic binary | Single modular binary | Independent container per microservice |
| **DevOps Overhead** | Low (Single CI/CD pipeline) | Low (Single CI/CD pipeline) | High (K8s, Service Mesh, Distributed Tracing) |

---

## 4. 10 Rapid Revision Flashcards

### Flashcard 1
**Q: What is the "Strangler Fig Pattern"?**  
**A:** An incremental legacy migration pattern where an edge proxy intercepts incoming traffic and gradually routes specific endpoints away from the monolith to newly built microservices until the monolith is completely replaced.

---

### Flashcard 2
**Q: What is an Anti-Corruption Layer (ACL), and why is it essential during monolith migration?**  
**A:** A translation layer placed between a new microservice and a legacy system that converts legacy schemas, cryptic status codes, and unformatted data into clean domain aggregates, preventing legacy design rot from infecting the new service.

---

### Flashcard 3
**Q: Why do distributed microservices strictly forbid sharing a physical database?**  
**A:** Shared databases cause tight schema coupling (changing a column breaks multiple services), transaction lock contention across unrelated domains, and prevent polyglot database adoption.

---

### Flashcard 4
**Q: What is the primary difference between Choreography and Orchestration in the Saga pattern?**  
**A:** **Choreography** is decentralized: services react to pub/sub domain events independently. **Orchestration** is centralized: a Saga Execution Coordinator (SEC) state machine explicitly commands services and manages compensations.

---

### Flashcard 5
**Q: Why must Compensating Transactions be idempotent?**  
**A:** In distributed systems, network timeouts cause the Saga coordinator to retry compensations. If a compensation (e.g., refund payment or restock inventory) is not idempotent, retries will cause duplicate refunds or phantom inventory.

---

### Flashcard 6
**Q: What is a "Pivot Transaction" in a Saga?**  
**A:** The point of no return in a Saga. Once the pivot transaction commits, the Saga cannot be rolled back via compensation and **must run forward to completion** using retriable transactions.

---

### Flashcard 7
**Q: How does a Saga deal with the lack of Isolation (Dirty Reads and Lost Updates)?**  
**A:** By using **Semantic Locking** (flagging records with a `PENDING` status to block or warn concurrent operations), **Commutative Updates** (mathematical deltas rather than absolute values), or holding resources in temporary reservations.

---

### Flashcard 8
**Q: What is the "Smart Gateway / God Proxy" antipattern?**  
**A:** Leaking business logic, data joins, or pricing calculations into the API Gateway. The gateway should remain thin, focused strictly on cross-cutting concerns (auth, rate limiting, SSL, and routing).

---

### Flashcard 9
**Q: What is the purpose of the Backend-for-Frontend (BFF) pattern?**  
**A:** To provide specialized client-facing gateways tailored to specific form factors (e.g., Mobile BFF vs. Web BFF) that aggregate multiple microservice responses into a single payload and prune unneeded fields to conserve bandwidth.

---

### Flashcard 10
**Q: Why does a chain of synchronous REST calls cause "Availability Collapse"?**  
**A:** In a synchronous chain, overall availability is the product of individual service availabilities ($A_1 \times A_2 \times \dots \times A_N$). If 4 services each have 99.5% availability, the composite availability drops to ~98.0%, resulting in hours of downtime per month.
