# Day 30: Distributed Hash Rings & Rate Limiter Algorithms

When web services, database clusters, and AI inference engines scale beyond a single physical server, two fundamental distributed systems challenges arise:
1. **Partitioning & Data Routing**: How do we distribute millions of keys across $N$ dynamic cache/database nodes without rehashing the entire dataset when nodes join or fail?
2. **Traffic Shaping & Resource Protection**: How do we throttle client requests at API gateways to prevent downstream service collapse, resource starvation, and cascading outages?

This milestone day masters the two core algorithms solving these challenges: **Consistent Hashing (Distributed Hash Rings with Virtual Nodes)** and **Rate Limiting Algorithms (Token Bucket, Leaky Bucket, and Sliding Window Counter)**.

---

## 1. The Modulo Hashing Flaw ($hash(key) \pmod N$)

In naive distributed systems, incoming keys are partitioned across $N$ servers using modulo hashing:
$$\text{server\_id} = \text{hash}(key) \pmod N$$

```
Initial Cluster (N = 4):
hash("user_101") = 101 % 4 = Node 1
hash("user_102") = 102 % 4 = Node 2
hash("user_103") = 103 % 4 = Node 3
hash("user_104") = 104 % 4 = Node 0

Node 3 crashes! Cluster shrinks to (N = 3):
hash("user_101") = 101 % 3 = Node 2  (CHANGED from Node 1!)
hash("user_102") = 102 % 3 = Node 0  (CHANGED from Node 2!)
hash("user_103") = 103 % 3 = Node 1  (CHANGED from Node 3!)
hash("user_104") = 104 % 3 = Node 2  (CHANGED from Node 0!)
```

### The Cache Avalanche Disaster
When $N$ changes:
$$\text{Fraction of keys remapped} = \frac{N - 1}{N} \approx 75\% \text{ to } 99\%$$
- If you have 100 cache servers and add 1 server, **99% of all cached keys remap to the wrong server!**
- Every client experiences an instantaneous cache miss, bombarding the primary database with hundreds of thousands of queries simultaneously (**Cache Stampede / Avalanche**), knocking down production infrastructure.

---

## 2. Consistent Hashing (Karger et al., 1997)

Consistent Hashing maps both **servers (nodes)** and **data keys** onto the same continuous circular mathematical space: a **Hash Ring** $[0, 2^{32} - 1]$.

```
                             [0 / 2^32]
                            /          \
                     Node A              Key 1 (routes clockwise to Node B)
                   /                        \
           Key 3                             Node B
          /                                    \
       Node D                                 Key 2 (routes to Node C)
          \                                    /
           Key 4                             Node C
                   \                        /
                     \                    /
                            ---------
```

### The Clockwise Routing Rule:
1. Hash each node's identifier (IP address, hostname) onto the ring: $H(\text{node})$.
2. Hash each incoming key onto the ring: $H(\text{key})$.
3. **Route the key to the first node encountered moving clockwise along the ring.**
4. If $H(\text{key}) > \text{all nodes on ring}$, wrap around to the first node at the start of the ring.

---

## 3. Minimal Key Migration Invariant

When a new node $X$ is added to a Consistent Hash Ring between Node $A$ and Node $B$:
- **Only keys residing in the segment between $A$ and $X$ are reassigned to $X$.**
- All other segments remain completely untouched!

$$\text{Fraction of keys remapped on node change} = \frac{1}{N}$$

```
+--------------------+----------------------------+----------------------------+
| Cluster Change     | Naive Modulo Hashing       | Consistent Hashing         |
+--------------------+----------------------------+----------------------------+
| 10 -> 11 Nodes     | 90.9% of keys remapped     | ~9.1% of keys remapped     |
| 100 -> 101 Nodes   | 99.0% of keys remapped     | ~1.0% of keys remapped     |
| Database Impact    | Catastrophic Stampede      | Negligible / Smooth        |
+--------------------+----------------------------+----------------------------+
```

---

## 4. Virtual Nodes (Vnodes / Tokens) & Hotspot Defense

In a basic hash ring, hashing 4 physical servers often places them unevenly along the circle (e.g. three nodes clustered within a $60^\circ$ arc). This creates massive **data skew**, where one node receives 80% of all traffic while others sit idle.

```
Without Virtual Nodes (Non-Uniform Skew):
[0] ----- Node A -- Node B -- Node C --------------------------- Node D ----- [2^32]
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^
                                  Node D handles 70% of keys!
```

### The Virtual Node Solution
Instead of placing each physical server once on the ring, map each server to **$V$ virtual nodes (tokens)** spread pseudorandomly across the ring:
$$\text{Virtual Node ID} = \text{NodeName} + \text{"\#vnode\_"} + i \quad (i = 0, 1, \dots, V-1)$$

```
Physical Node 1 -> [Node1#0, Node1#1, Node1#2, ..., Node1#199]
Physical Node 2 -> [Node2#0, Node2#1, Node2#2, ..., Node2#199]
```

- When a key routes to virtual node `Node2#47`, traffic is directed to physical server `Node2`.
- By setting $V \ge 150$, the standard deviation of load across servers drops below $5\%$, guaranteeing near-perfect uniform distribution.

---

## 5. Ring Lookup Algorithm: Binary Search (`bisect_right`)

To route a key in $O(\log(N \cdot V))$ time:
1. Maintain a sorted array of 32-bit integer ring tokens: `sorted_hashes`.
2. Map each token to its physical node: `token_to_node[token] = physical_node`.
3. To route `key`:
   - Compute `h = hash(key)`.
   - Use binary search `bisect_right(sorted_hashes, h)` to find the smallest token $\ge h$.
   - If index reaches the end of the array, wrap around to index 0.

---

## 6. Rate Limiting Architectures: Protecting Backend Services

A **Rate Limiter** controls the rate of traffic sent by a client or received by a server. It protects APIs against DDoS attacks, brute-force credential stuffing, API credit exhaustion, and cascading microservice failures.

```
+-----------------------------------------------------------------------------------+
|                        CANONICAL RATE LIMITING ALGORITHMS                         |
+-----------------------------------------------------------------------------------+
| 1. Token Bucket        | Burst-tolerant; constant-rate token refill. Industry std |
| 2. Leaky Bucket        | Constant-rate outflow queue; completely smooths bursts   |
| 3. Fixed Window        | Simple counter reset at window boundary; 2x burst flaw   |
| 4. Sliding Window Log  | Stores exact timestamps; accurate but high memory O(N)   |
| 5. Sliding Counter     | Hybrid of fixed window; O(1) memory, high precision      |
+-----------------------------------------------------------------------------------+
```

---

## 7. The Token Bucket Algorithm

The **Token Bucket** algorithm is the gold standard for HTTP API rate limiting (used by Stripe, AWS, and Cloudflare) because it naturally accommodates legitimate bursts while enforcing a strict long-term average rate.

```
         +-------------------------------------+
         | Refill: R tokens added every second |
         +-------------------------------------+
                           |
                           v
                +---------------------+
                |                     |
                |     TOKEN BUCKET    |
                |  Capacity: C tokens |
                |  Current: T tokens  |
                |                     |
                +---------------------+
                           |
               Request requires 1 token
                           |
              +------------+------------+
              |                         |
        T >= 1 (Allowed)          T < 1 (Rejected)
       Tokens = Tokens - 1        HTTP 429 Too Many Requests
```

### Lazy In-Memory Refill (Zero Background Timers)
A naive mistake is running a background thread ticking every second to add tokens to millions of buckets.
**Production Invariant**: Compute token refill dynamically on-demand during request arrival:

$$\Delta t = \text{current\_time} - \text{last\_refill\_time}$$
$$\text{new\_tokens} = \min(\text{capacity}, \text{current\_tokens} + \Delta t \times \text{refill\_rate})$$

This executes in strict $O(1)$ time and $O(1)$ space with zero background thread overhead!

---

## 8. The Leaky Bucket Algorithm

While Token Bucket allows bursts up to capacity $C$, **Leaky Bucket enforces a strictly constant output rate**, smoothing traffic into a uniform flow.

```
Requests arrive in bursts:  |||| |||  |   ||
                             \   \  \  \  /
                        +--------------------+
                        |    LEAKY BUCKET    |
                        | (FIFO Queue of Cap)|
                        +--------------------+
                                  |
                                  v
Continuous constant drain:        |   |   |   |   (Smooth output)
```

- If requests arrive faster than the leak rate, the bucket fills up.
- When the bucket capacity is full, incoming requests overflow and are dropped (HTTP 429).
- **Use Case**: Egress traffic shaping, network packet queues, write throttling to disk.

---

## 9. Sliding Window Counter Algorithm

Fixed window counter has a fatal flaw: if limit is 100 req/min, a client can send 100 requests at 11:59:59 and 100 requests at 12:00:01 (200 requests within 2 seconds—a **2x traffic spike**).

The **Sliding Window Counter** approximates the count by weighting the previous window:

$$\text{Estimated Requests} = \text{CurrentWindowCount} + \text{PreviousWindowCount} \times (1 - \text{OverlapPercentage})$$

```
[ Previous Minute: 80 requests ]  |  [ Current Minute: 30 requests ]
                     ----------------------------
                     Window: Current time is 30s into current minute
                     Weight of prev window = (60 - 30) / 60 = 0.5
                     Estimated count = 30 + (80 * 0.5) = 70 requests <= 100 (Allowed)
```

Takes $O(1)$ memory (stores only 2 integer counters) while preventing boundary bursts.

---

## 10. Distributed Rate Limiting Across Clusters

In multi-instance production environments (e.g. 50 FastAPI pods behind an AWS ALB), local in-memory rate limiters allow clients to multiply their quota by 50x.
- **Solution**: Centralized in-memory store (Redis).

### The Concurrency Race Condition & Lua Solution
A client sending 10 parallel requests will cause a classic Read-Modify-Write race condition in Redis:
1. Thread A reads `tokens = 1`.
2. Thread B reads `tokens = 1`.
3. Both threads decrement and write `tokens = 0`.
4. Both requests are allowed (quota violated!).

**Defense**: Execute the Token Bucket algorithm inside a **Redis Lua Script** (`EVALSHA`). Redis executes Lua scripts **atomically in a single thread**, guaranteeing zero race conditions with sub-millisecond execution.

---

## 11. Replication on the Hash Ring (Dynamo Architecture)

Consistent Hashing also solves distributed data replication (used by Amazon DynamoDB, Apache Cassandra, and Riak):
- Instead of routing to just 1 node, a key is written to the **first $M$ distinct physical nodes** encountered moving clockwise on the ring.
- Guarantees fault tolerance: if Node 1 crashes, replicas on Node 2 and Node 3 continue serving data.

---

## 12. 7 Non-Negotiable Rules of Production Distributed Systems

1. **Always Use Virtual Nodes**: A consistent hash ring without virtual nodes has severe data skew. Always configure at least 100 to 256 virtual nodes per server.
2. **Use Cryptographic or Fast Uniform Hash Functions**: Never use Python's built-in `hash()` for consistent hashing (it has randomized seeds per process). Use `md5`, `sha256`, or non-cryptographic `murmurhash3` for cross-process determinism.
3. **Lazy Token Bucket Refill**: Never tick background timers to increment rate limit tokens. Update tokens on-demand via timestamp delta ($\Delta t \times R$).
4. **Enforce Atomic Decrements**: In distributed rate limiters, always execute quota checks inside Redis Lua scripts to eliminate concurrent race conditions.
5. **Always Return Standard HTTP Headers**: Include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `Retry-After` (in seconds) on HTTP 429 responses.
6. **Weighted Tokens for Heterogeneous Hardware**: Assign virtual node counts proportionally to server hardware specs (e.g. 2x RAM gets 2x virtual nodes).
7. **Handle Clock Skew with NTP**: In distributed rate limiters, ensure all API gateway servers synchronize system clocks via NTP to prevent time drift.

---

## 13. Comprehensive Architectural Matrix

| Component | Modulo Hashing | Consistent Hash Ring | Token Bucket | Leaky Bucket | Sliding Window Counter |
|---|---|---|---|---|---|
| **Lookup Time** | $O(1)$ | $O(\log(N \cdot V))$ | $O(1)$ | $O(1)$ | $O(1)$ |
| **Memory Cost** | $O(1)$ | $O(N \cdot V)$ | $O(1)$ per client | $O(1)$ per client | $O(1)$ per client |
| **Node Scaling Rehash**| $\frac{N-1}{N}$ (~99%) | $\frac{1}{N}$ (~1%) | N/A | N/A | N/A |
| **Burst Capacity** | N/A | N/A | **Yes (up to $C$)** | No (strictly flat)| Partial (interpolated) |
| **Traffic Smoothing** | N/A | N/A | No (allows burst) | **Yes (uniform)** | No |
| **Production Fit** | Monolithic DB | Cassandra / Redis / Envoy | Stripe / AWS APIs | Packet traffic shaper | Cloudflare / DDOS |
