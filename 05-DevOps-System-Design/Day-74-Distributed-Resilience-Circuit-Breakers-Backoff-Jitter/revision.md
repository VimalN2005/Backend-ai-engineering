# Day 74: Rapid Revision & Flashcards: Distributed Resilience

A 5-minute reference sheet, mathematical formulas, critical comparison tables, and 10 self-testing flashcards covering Circuit Breakers, Exponential Backoff, Jitter, Bulkheads, and Distributed Deadlines.

---

## 1. High-Density Cheat Sheet

### Core Concepts Summary
- **Circuit Breaker**: An electrical fuse for network calls. Three states: `CLOSED` (normal pass-through), `OPEN` (fast-fail without network call; executes fallback), and `HALF-OPEN` (trial probe requests to verify recovery).
- **Retry Storm**: When failed clients retry simultaneously, multiplying load on a degraded service by $(R + 1)$ and turning brief hiccups into catastrophic outages.
- **Exponential Backoff**: Increasing retry delay geometrically ($T_i = \min(M, B \times 2^i)$) to give recovering services breathing room.
- **Jitter**: Injecting random noise into backoff intervals to break client synchronization and eliminate the **Thundering Herd**.
- **Full Jitter**: $\text{Uniform}(0, \min(M, B \times 2^i))$. Disperses retries evenly across the window; reduces peak concurrency by $>60–80\%$.
- **Decorrelated Jitter**: Derives sleep from the *previous sleep duration*, providing optimal smoothing for dense client clusters.
- **Bulkhead Pattern**: Isolating thread pools or semaphores per external dependency to prevent a slow downstream service from starving the entire container.
- **Deadline Budget Propagation**: Propagating absolute deadline timestamps across service hops (`X-Request-Deadline`), cancelling in-flight work immediately when the client budget expires.

---

## 2. Mathematical Reference & Formulas

### 1. Exponential Backoff Formula
$$T_i = \min\left(T_{\text{max}}, T_{\text{base}} \times 2^i\right)$$

### 2. AWS Full Jitter Formula
$$T_i = \text{Uniform}\left(0, \min(T_{\text{max}}, T_{\text{base}} \times 2^i)\right)$$

### 3. AWS Decorrelated Jitter Formula
$$T_0 = T_{\text{base}}, \quad T_i = \min\left(T_{\text{max}}, \text{Uniform}(T_{\text{base}}, T_{i-1} \times 3)\right)$$

### 4. Retry Amplification Factor
In a call chain of depth $D$ where each service retries $R$ times:
$$\text{Amplification Factor} = (R + 1)^D$$
*Example*: Depth $D = 3$, Retries $R = 3$:
$$\text{Amplification} = (3 + 1)^3 = 64\times \text{ traffic surge!}$$

### 5. Netflix Concurrency-Limits Gradient Formula
$$L_{\text{new}} = L_{\text{current}} \times \left(\frac{\text{RTT}_{\text{no\_load}}}{\text{RTT}_{\text{actual}}}\right) + \text{Headroom}$$

---

## 3. Comparison Matrix: Fault Tolerance Patterns

| Pattern | Scope | Primary Benefit | Failure Action |
| :--- | :--- | :--- | :--- |
| **Circuit Breaker** | Caller Client | Protects downstream service and preserves caller threads | Fast-fails immediately; runs local fallback |
| **Exponential Backoff**| Caller Client | Prevents tight-loop spamming on transient failures | Increases delay geometrically |
| **Full Jitter** | Caller Client | Eliminates thundering herd / synchronized traffic pulses | Randomizes wait time across window |
| **Semaphore Bulkhead**| Caller Client | Limits concurrent in-flight connections per dependency | Rejects overflow with `BulkheadFullException` |
| **Deadline Budget** | Distributed Context | Eliminates wasted compute on abandoned requests | Aborts execution when remaining budget $\le 0$ |

---

## 4. 10 Rapid Revision Flashcards

### Flashcard 1
**Q: What are the three states of a Circuit Breaker and their transitions?**  
**A:** `CLOSED` (normal pass-through) trips to `OPEN` when the failure rate exceeds a threshold. `OPEN` (fast-fail mode) transitions to `HALF-OPEN` after a sleep window expires. `HALF-OPEN` resets to `CLOSED` if trial probes succeed, or re-trips to `OPEN` if any probe fails.

---

### Flashcard 2
**Q: Why is Exponential Backoff alone insufficient to stop the Thundering Herd?**  
**A:** Without jitter, thousands of clients that fail at the same time will calculate the exact same backoff delay, hitting the downstream service in synchronized, destructive periodic pulses.

---

### Flashcard 3
**Q: What is the AWS Full Jitter formula?**  
**A:** $\text{Sleep} = \text{Uniform}(0, \min(T_{\text{max}}, T_{\text{base}} \times 2^i))$. It randomizes the sleep duration across the entire exponential window, spreading retries evenly across time.

---

### Flashcard 4
**Q: What is the Bulkhead pattern, and what real-world hazard does it prevent?**  
**A:** It isolates resource pools (thread pools or semaphores) per downstream dependency. It prevents a slow or hanging non-critical service from monopolizing all container threads and starving core flows.

---

### Flashcard 5
**Q: What is the "Slow Call Rate Threshold" in modern circuit breakers?**  
**A:** A metric that tracks latency instead of error status. If $>50\%$ of calls exceed a latency threshold (e.g., 2 seconds), the circuit trips to `OPEN` even if all requests eventually return `HTTP 200 OK`.

---

### Flashcard 6
**Q: What types of HTTP errors should NEVER be retried?**  
**A:** Non-transient client errors (`400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `422 Unprocessable`) because they indicate structural invalidity and will fail 100% of the time.

---

### Flashcard 7
**Q: What is a Client-Side Retry Budget?**  
**A:** A token bucket mechanism (e.g., in Finagle/gRPC) where successful requests deposit small tokens (e.g. 0.1) and retries consume tokens (1.0). It mathematically caps aggregate retry traffic to a fixed percentage (e.g. 10%) of baseline traffic.

---

### Flashcard 8
**Q: How does Distributed Deadline Budget Propagation prevent wasted compute?**  
**A:** The ingress gateway sets an absolute deadline timestamp in request headers (`X-Request-Deadline`). Downstream services check remaining budget before executing queries, immediately cancelling work if the client has already timed out.

---

### Flashcard 9
**Q: What is the danger of setting a Circuit Breaker's minimum request volume too low?**  
**A:** It causes "flapping" during cold starts or canary rollouts; if the first request fails due to a normal socket retry, the failure rate becomes 100%, prematurely tripping the breaker on false positives.

---

### Flashcard 10
**Q: How does Adaptive Concurrency Limiting (TCP Vegas gradient) differ from static rate limiting?**  
**A:** Static rate limiters enforce fixed request-per-second ceilings regardless of backend strain. Adaptive concurrency limiters measure inflation in round-trip latency (RTT) and automatically throttle in-flight concurrency before queues saturate and servers crash.
