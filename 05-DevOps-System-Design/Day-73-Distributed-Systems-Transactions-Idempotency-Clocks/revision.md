# Day 73: Rapid Revision & Flashcards: Distributed Transactions, Idempotency & Clocks

A 5-minute reference sheet, mathematical formulas, critical comparison tables, and 10 self-testing flashcards covering distributed time, consensus, idempotency, and locking.

---

## 1. High-Density Cheat Sheet

### Core Concepts Summary
- **Physical Clock Drift**: Quartz oscillator drift ($1–17$ ms/day) and NTP jitter make physical wall clocks unreliable for causal ordering.
- **Lamport Timestamps**: Logical scalar clocks guaranteeing $A \to B \implies C(A) < C(B)$. Cannot differentiate concurrent events from causally related events.
- **Vector Clocks**: Vector of size $N$ capturing full causal history. Detects concurrent conflicting writes ($V_A \parallel V_B$) for sibling reconciliation.
- **Hybrid Logical Clocks (HLC)**: Combines physical wall-clock ($pt$) with logical counter ($c$). Retains $O(1)$ scalar size (12 bytes) while guaranteeing monotonicity during backward NTP drift.
- **Google TrueTime**: Bounded uncertainty interval $[t_{\text{earliest}}, t_{\text{latest}}]$ with width $2\epsilon$ ($\le 7$ ms) powered by GPS + atomic clocks. Commit Wait ensures external linearizability globally.
- **Two-Phase Commit (2PC)**: Atomic commitment protocol. Vulnerable to **In-Doubt coordinator crashes** that lock resources indefinitely.
- **Distributed Idempotency**: Using unique `Idempotency-Key` headers with atomic check-and-set locks (`SET ... NX`) and response caching.
- **Fencing Tokens**: Monotonically increasing integers ($T_1 < T_2$) validating writes at the storage layer to prevent split-brain overwrites after JVM GC pauses.

---

## 2. Mathematical Reference & Formulas

### 1. Vector Clock Dominance & Concurrency Rules
For vector clocks $V_A$ and $V_B$ across $N$ nodes:
- **$V_A$ happens-before $V_B$ ($V_A < V_B$)**:
  $$\forall k \in [1, N], V_A[k] \le V_B[k] \quad \land \quad \exists k \text{ such that } V_A[k] < V_B[k]$$
- **$V_A$ and $V_B$ are Concurrent ($V_A \parallel V_B$)**:
  $$\neg(V_A \le V_B) \quad \land \quad \neg(V_B \le V_A)$$

### 2. Google TrueTime Commit Wait Duration
To ensure linearizability across Spanner transactions:
$$\text{Wait Duration} \ge 2\epsilon$$
Where $\epsilon$ is the maximum clock uncertainty bound ($\approx 1–7$ ms).

### 3. Two-Phase Commit Message Complexity
For a coordinator and $N$ cohort participants:
$$\text{Total Network Messages} = 4N \quad (2N \text{ in Prepare} + 2N \text{ in Commit})$$

### 4. Snowflake ID Throughput Capacity
With 41-bit timestamp, 10-bit machine ID, and 12-bit sequence counter:
$$\text{Max Throughput per Node} = 2^{12} = 4,096 \text{ IDs/millisecond} = 4,096,000 \text{ IDs/second}$$
$$\text{Max Unique Nodes} = 2^{10} = 1,024 \text{ machines}$$
$$\text{Epoch Lifespan} = \frac{2^{41} \text{ ms}}{1000 \times 60 \times 60 \times 24 \times 365.25} \approx 69.7 \text{ years}$$

---

## 3. Comparison Matrix: Distributed Clocks & Consensus

| Technology | Data Footprint | Hardware Required | Guarantees | Failure Mode |
| :--- | :--- | :--- | :--- | :--- |
| **Physical Clock (NTP)**| 8 bytes | Standard crystal | Wall-clock time estimate | Silent data loss with LWW |
| **Lamport Clock** | 8 bytes (scalar) | None | Partial causal ordering | Cannot detect concurrency |
| **Vector Clock** | $O(N)$ integers | None | Full causal ordering + conflict detection | Vector explosion on large $N$ |
| **Hybrid Logical Clock** | 12 bytes | Standard NTP | Monotonic + physical bound | Read restarts on high skew |
| **TrueTime (Spanner)** | 16 bytes (interval)| Atomic clocks + GPS | Strict Serializability globally | Degrades latency if $\epsilon$ spikes |
| **Two-Phase Commit** | In-memory locks | None | All-or-nothing ACID | Blocks on coordinator crash |
| **Raft Consensus** | Commit log index | None | Linearizable log agreement | Requires majority ($\lfloor N/2 \rfloor + 1$) |

---

## 4. 10 Rapid Revision Flashcards

### Flashcard 1
**Q: Why is physical clock time dangerous for ordering writes in distributed databases?**  
**A:** Quartz crystals drift ($1–17$ ms/day) and NTP adjustments can step the clock backwards, causing newer writes to be stamped with older times and silently overwritten in Last-Write-Wins (LWW) datastores.

---

### Flashcard 2
**Q: If Lamport timestamp $C(A) < C(B)$, did event $A$ definitely happen before event $B$?**  
**A:** No. Lamport clocks guarantee that if $A \to B$ then $C(A) < C(B)$, but the converse is not true; $C(A) < C(B)$ can occur even if $A$ and $B$ were completely independent concurrent events.

---

### Flashcard 3
**Q: How does a Vector Clock detect that two concurrent updates conflict?**  
**A:** If neither vector clock dominates the other ($\neg(V_A \le V_B)$ and $\neg(V_B \le V_A)$), the events are concurrent ($V_A \parallel V_B$), signaling a conflict that requires sibling reconciliation.

---

### Flashcard 4
**Q: What is a Hybrid Logical Clock (HLC), and why is it preferred over Vector Clocks in CockroachDB?**  
**A:** HLC combines physical wall time with a logical tick in a fixed 12-byte scalar tuple $(l, c)$, eliminating the $O(N)$ storage explosion of Vector Clocks while guaranteeing monotonicity during backward NTP drift.

---

### Flashcard 5
**Q: What is the "Commit Wait" protocol in Google Spanner?**  
**A:** The coordinator picks a commit timestamp $s = \text{latest time}$ and intentionally waits $2\epsilon$ time before releasing locks, guaranteeing that $s$ has definitely passed across all global nodes before any subsequent read begins.

---

### Flashcard 6
**Q: What is the "In-Doubt" participant state in Two-Phase Commit (2PC)?**  
**A:** When a participant votes `COMMIT` during Phase 1, but the coordinator crashes before broadcasting Phase 2. The participant cannot abort or commit unilaterally and must hold resource locks indefinitely until the coordinator recovers.

---

### Flashcard 7
**Q: Why does Martin Kleppmann state that distributed locks require Fencing Tokens?**  
**A:** A client paused by a long JVM GC pause or network stall may believe it still holds a lock after its TTL has expired. A monotonic Fencing Token validated by the storage layer ensures delayed writes with stale tokens are rejected.

---

### Flashcard 8
**Q: How do you prevent race conditions when checking an Idempotency-Key?**  
**A:** Use an atomic check-and-set command (e.g., Redis `SET idemp:<key> 'PROCESSING' NX EX 120`). Never use separate `EXISTS` then `SET` calls, which creates a check-then-act vulnerability.

---

### Flashcard 9
**Q: Why does Raft consensus avoid the blocking problem of Two-Phase Commit?**  
**A:** 2PC requires 100% unanimity across all participants. Raft requires only a **Majority Quorum** ($\lfloor N/2 \rfloor + 1$); if a leader or minority node crashes, the remaining nodes elect a new leader and continue processing immediately.

---

### Flashcard 10
**Q: In Twitter Snowflake, what is the purpose of the 12-bit sequence counter?**  
**A:** It allows a single machine to generate up to $2^{12} = 4,096$ unique, strictly ordered IDs per millisecond during high-concurrency traffic bursts.
