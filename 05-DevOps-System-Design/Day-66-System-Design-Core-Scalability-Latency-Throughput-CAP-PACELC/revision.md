# Day 66: System Design Core — 5-Minute Revision & Cheat Sheet

A high-density reference sheet covering scalability dimensions, queueing theory, latency laws, the CAP and PACELC theorems, availability mathematics, and capacity planning formulas.

---

## 1. Core Mathematical Formulas Cheat Sheet

### Little's Law
$$L = \lambda \times W$$
- $L$ = Average concurrent in-flight requests (Concurrency).
- $\lambda$ = Arrival rate / Throughput (QPS).
- $W$ = Average latency / response time (Seconds).

### Universal Scalability Law (USL)
$$X(N) = \frac{\gamma N}{1 + \sigma(N - 1) + \kappa N(N - 1)}$$
- $\sigma$ = Contention penalty (serialization bottleneck).
- $\kappa$ = Coherence penalty (crosstalk / distributed consensus).
- $\gamma$ = Single-node baseline throughput.
- **Optimal Peak Cluster Size**: $N^* = \sqrt{\frac{1 - \sigma}{\kappa}}$

### Fanout Tail Latency Multiplication
$$P(\text{User Hits Tail}) = 1 - (1 - p)^M$$
- $p$ = Probability of a single microservice experiencing tail latency.
- $M$ = Number of parallel backend calls per client request.

### Composite System Availability
- **Components in Series**: $A_{\text{series}} = A_1 \times A_2 \times \dots \times A_n$
- **Components in Parallel (Redundant)**: $A_{\text{parallel}} = 1 - (1 - A_1) \times (1 - A_2) \times \dots \times (1 - A_n)$
- **Availability Formula**: $A = \frac{\text{MTBF}}{\text{MTBF} + \text{MTTR}}$

---

## 2. CAP vs. PACELC Quick Reference

### The CAP Trilemma
In any networked system, **Partition Tolerance (P) is mandatory**. You cannot choose "CA".
- **CP**: Sacrifices Availability during partitions to guarantee linearizable consistency (CockroachDB, Spanner, etcd).
- **AP**: Sacrifices Consistency during partitions to guarantee 100% availability (Cassandra, DynamoDB, Couchbase).

### The PACELC Theorem
$$\textbf{If Partition (P)} \implies [\textbf{A} \lor \textbf{C}], \quad \textbf{Else} \implies [\textbf{L} \lor \textbf{C}]$$

| Classification | Partition Behavior | Normal State Behavior | Production Examples |
| :--- | :--- | :--- | :--- |
| **PC/EC** | Consistency (rejects writes on minority) | Consistency (synchronous 2PC/Raft) | Google Spanner, CockroachDB |
| **PA/EL** | Availability (accepts writes everywhere) | Latency (asynchronous replication) | Amazon DynamoDB, Cassandra |
| **PC/EL** | Consistency (primary failover) | Latency (stale reads from secondaries)| MongoDB (secondary reads) |

---

## 3. Latency Numbers Every Systems Architect Must Know

```text
L1 Cache Reference:                   0.5 ns
Branch Mispredict:                    5.0 ns
L2 Cache Reference:                   7.0 ns
Mutex Lock / Unlock:                 25.0 ns
Main Memory (RAM) Access:           100.0 ns  (0.1 µs)
Compress 1KB with Zstd:           2,000.0 ns  (2.0 µs)
Read 1MB sequentially (NVMe):    10,000.0 ns  (10.0 µs)
Read 1MB sequentially (SSD):    250,000.0 ns  (250.0 µs)
Datacenter LAN Round Trip:      500,000.0 ns  (0.5 ms)
Read 1MB sequentially (HDD):  2,000,000.0 ns  (2.0 ms)
Cross-Continent RTT (NYC-SF):  40,000,000.0 ns (40.0 ms)
Transatlantic RTT (NYC-LON):   60,000,000.0 ns (60.0 ms)
```

---

## 4. Availability "Nines" Reference Table

| SLA Level | Percentage | Max Downtime / Year | Max Downtime / Month |
| :--- | :--- | :--- | :--- |
| **Two Nines** | $99.0\%$ | 3.65 days | 7.30 hours |
| **Three Nines** | $99.9\%$ | 8.76 hours | 43.8 minutes |
| **Four Nines** | $99.99\%$ | 52.56 minutes | 4.38 minutes |
| **Five Nines** | $99.999\%$ | **5.26 minutes** | **25.9 seconds** |

---

## 5. Rapid-Fire Flashcards (10 Q&A)

1. **Why is "CA" impossible in distributed systems?**  
   *Answer*: Network partitions are an unavoidable physical reality; when a network partition occurs, a system must either drop availability or drop consistency.

2. **What does Abadi's PACELC Theorem add that CAP misses?**  
   *Answer*: PACELC models system trade-offs during **normal operation (Else state)** between Latency ($L$) and Consistency ($C$).

3. **What is Little's Law, and how do you use it?**  
   *Answer*: $L = \lambda W$. Use it to calculate required concurrency or connection pool capacity from arrival rate ($\lambda$) and latency ($W$).

4. **How does the Universal Scalability Law differ from Amdahl's Law?**  
   *Answer*: USL introduces a quadratic coherence penalty ($\kappa N(N-1)$) for cross-node state synchronization, modeling retrograde throughput collapse.

5. **What is the mathematical consequence of a 100-service fanout on p99 latency?**  
   *Answer*: $1 - (0.99)^{100} = 63.4\%$. Nearly two-thirds of user requests will experience the worst-case tail latency of the slowest service.

6. **What is the difference between Latency and Throughput?**  
   *Answer*: Latency is the time taken to process a single unit of work ($\text{ms}$); Throughput is the rate of completed units of work per second (QPS).

7. **How does request hedging reduce tail latency?**  
   *Answer*: It sends an identical duplicate request to a backup replica if the primary does not respond by its $p95$ time, taking the first response.

8. **Why is wall-clock Last-Write-Wins (LWW) dangerous across servers?**  
   *Answer*: NTP clock drift (1-100ms) causes newer writes to be silently overwritten by older writes with future timestamps.

9. **What three mathematical properties must a CRDT merge operator possess?**  
   *Answer*: Commutativity ($A \sqcup B = B \sqcup A$), Associativity ($(A \sqcup B) \sqcup C = A \sqcup (B \sqcup C)$), and Idempotence ($A \sqcup A = A$).

10. **Why does placing three 99.9% services in series violate a 99.9% SLA?**  
    *Answer*: Series availability is multiplicative: $0.999^3 \approx 99.70\%$, resulting in $26.3\text{ hours}$ of downtime/year instead of $8.76\text{ hours}$.
