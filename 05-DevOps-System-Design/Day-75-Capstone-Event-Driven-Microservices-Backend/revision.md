# Day 75: Rapid Revision & Flashcards: Enterprise Microservices Capstone

A 5-minute rapid revision reference guide, architectural formulas, and 10 self-testing flashcards synthesizing the entire Phase 5 curriculum into the Capstone Project.

---

## 1. High-Density Cheat Sheet

### Core Capstone Principles
- **Database-per-Service**: Microservices must own their schemas exclusively. Shared databases destroy team autonomy and cause fatal schema lock contention.
- **Transactional Outbox**: Persists domain entities and outbox events in the same local ACID transaction. Eliminates dual-write data loss.
- **Debezium CDC**: Log-based Change Data Capture reading PostgreSQL WAL directly via `pgoutput` with $<10$ ms latency.
- **Saga Orchestrator**: Centralized state machine executing forward compensable steps, managing pivot commitments, and coordinating reverse compensations on failure.
- **Compensating Transactions**: Semantic undo operations that reverse previously committed local transactions. Must be strictly idempotent.
- **Circuit Breaker**: Trips to `OPEN` when failure rate $\ge 50\%$; fast-fails immediately with fallback responses; recovers via `HALF-OPEN` trial probes.
- **Full Jitter**: Spreads retries evenly across the exponential backoff window ($\text{Uniform}(0, \min(M, B \times 2^i))$), eliminating the Thundering Herd.
- **Bulkhead Pattern**: Partitions container concurrency pools per dependency to prevent cascading thread starvation.

---

## 2. Mathematical Reference & Formulas

### 1. Cumulative Cascading Availability
$$\text{Availability} = \prod_{i=1}^N A_i = A_1 \times A_2 \times \dots \times A_N$$

### 2. Retry Storm Amplification Factor
$$\text{Amplification Factor} = (R + 1)^D$$
Where $R$ is retry count and $D$ is service call chain depth.

### 3. AWS Full Jitter Backoff Formula
$$T_i = \text{Uniform}\left(0, \min(T_{\text{max}}, T_{\text{base}} \times 2^i)\right)$$

### 4. Little's Law for Microservice Concurrency
$$L = \lambda \times W$$
Where $L$ is concurrent in-flight requests, $\lambda$ is arrival rate (req/s), and $W$ is average processing latency (seconds).

---

## 3. 10 Rapid Revision Flashcards

### Flashcard 1
**Q: What is the Dual-Write problem in microservices?**  
**A:** Attempting to update a local database and publish a message to a broker in separate steps. If the process crashes between the two steps, state becomes permanently inconsistent across services.

---

### Flashcard 2
**Q: How does the Transactional Outbox Pattern solve the Dual-Write problem?**  
**A:** Both the business entity and the event are saved within the same local ACID database transaction. An independent CDC reader (Debezium) asynchronously relays the outbox table to Kafka with zero data loss.

---

### Flashcard 3
**Q: What is the difference between a Compensable Step and a Pivot Step in a Saga?**  
**A:** A **Compensable Step** can be rolled back via a compensating action (e.g., refund payment). A **Pivot Step** is the point of no return; once it commits, the Saga cannot be aborted and must run forward to completion.

---

### Flashcard 4
**Q: Why must Compensating Transactions be idempotent?**  
**A:** Network timeouts cause the Saga coordinator to retry compensations. If a refund or restock operation is not idempotent, retries will issue multiple refunds or create phantom inventory.

---

### Flashcard 5
**Q: What is Semantic Locking in a Saga?**  
**A:** Flagging a record with a pending state (`status = 'PAYMENT_PENDING'`) to signal to concurrent transactions that a multi-step Saga is in-flight, preventing Dirty Reads and Lost Updates.

---

### Flashcard 6
**Q: Why does Exponential Backoff without Jitter fail during mass outages?**  
**A:** Thousands of clients that fail at the same moment will compute the exact same sleep delay, hitting the recovering server in synchronized, destructive periodic pulses (the Thundering Herd).

---

### Flashcard 7
**Q: What is the purpose of the Bulkhead Pattern?**  
**A:** To isolate concurrency limits (thread pools or semaphores) per downstream dependency, ensuring that a slow or unresponsive external service cannot consume 100% of the host container's worker threads.

---

### Flashcard 8
**Q: How does Distributed Deadline Budget Propagation prevent wasted compute?**  
**A:** An absolute deadline is set at the API Gateway and passed across all service hops. Downstream services check remaining budget before executing queries, immediately aborting work if the client has already timed out.

---

### Flashcard 9
**Q: How does KEDA autoscale event-driven Kafka consumers?**  
**A:** KEDA queries Prometheus for the topic's consumer group lag (`kafka_consumergroup_lag`). When lag exceeds a threshold, KEDA scales consumer pods rapidly to match the topic partition count.

---

### Flashcard 10
**Q: What is the Strangler Fig Pattern in database migrations?**  
**A:** An incremental migration strategy where an edge proxy routes specific endpoints away from the monolith to greenfield microservices, synchronizing data bidirectionally via CDC until the monolith can be safely decommissioned.
