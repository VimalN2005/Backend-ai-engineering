# Senior Backend & AI Systems Interview: Distributed Hashing & Rate Limiting

This document details the top 5 high-impact interview questions asked by Tier-1 tech companies (Google, Meta, Amazon DynamoDB, Cloudflare, Stripe) regarding consistent hashing, distributed traffic shaping, and cluster partitioning.

---

### Question 1: Why does Modulo Hashing trigger a Cache Avalanche during cluster autoscaling, and how does Consistent Hashing prevent it?

#### The Question
"In a distributed cache cluster with $N$ nodes, why is routing via $\text{server} = \text{hash}(key) \pmod N$ catastrophic when scaling up from 9 to 10 nodes? Provide the mathematical proof of the key remapping ratio under Consistent Hashing."

#### Candidate Answer

#### The Modulo Hashing Catastrophe:
When routing via $\text{hash}(key) \pmod N$:
- With $N = 9$, key $K$ routes to $\text{hash}(K) \pmod 9$.
- When a 10th node is added ($N = 10$), key $K$ routes to $\text{hash}(K) \pmod{10}$.
- For a key to stay on the same node, it requires $\text{hash}(K) \pmod 9 \equiv \text{hash}(K) \pmod{10}$.
- By the Chinese Remainder Theorem and modular arithmetic, the fraction of keys that remain on the same server is only $\frac{1}{N} = \frac{1}{10} = 10\%$.
- **$90\%$ of all cached keys remap to the wrong server instantaneously!**
- The entire cluster suffers a simultaneous 90% cache miss rate (**Cache Avalanche**), directing peak traffic to the underlying primary database and knocking it offline.

#### Consistent Hashing Mathematical Invariant:
Consistent Hashing maps both keys and servers onto an invariant circular ring $[0, 2^{32} - 1]$:
1. A key routes clockwise to the first server whose token is $\ge \text{hash}(key)$.
2. When an $(N+1)$-th node $X$ is inserted between node $A$ and node $B$:
   - Only keys that fall in the segment between $A$ and $X$ are reassigned to $X$.
   - No keys belonging to any other node segment move!
3. Assuming uniform token distribution across the ring:
   $$\text{Fraction of keys remapped} = \frac{1}{N + 1}$$
- When scaling from 9 to 10 nodes, **only 10% of keys move**. 90% of keys remain undisturbed on their existing servers with zero cache churn.

---

### Question 2: Why are Virtual Nodes (Vnodes) mathematically necessary on a Hash Ring, and how do you size $V$?

#### The Question
"If you implement a Consistent Hash Ring by simply placing physical server IP addresses on the ring, what goes wrong in production? What is the standard deviation formula for load distribution with virtual nodes?"

#### Candidate Answer

#### The Non-Uniform Hotspot Trap
When physical servers are placed on a circle using a uniform random hash function:
1. With small $N$ (e.g. $N = 5$ servers), random point placement does **not** distribute points at equal $72^\circ$ intervals.
2. Statistically, servers cluster together. One server will inevitably own a massive arc of the circle (e.g. $180^\circ$), receiving 50% of the entire system's traffic, while an adjacent server owns only $20^\circ$, receiving 5%.
3. This creates **catastrophic hotspotting**: the overloaded server runs out of RAM or CPU, crashes, and dumps its entire massive arc onto its immediate clockwise neighbor, triggering a **cascading domino failure** across the cluster.

#### Virtual Nodes & Standard Deviation:
By assigning each physical server $V$ virtual node tokens (e.g. `Server1#0`, `Server1#1`, ..., `Server1#V-1`):
- The ring is partitioned into $N \times V$ tiny segments intermingled across the circle.
- When server $X$ crashes, its $V$ virtual tokens are scattered across the ring, so its load is distributed **equally across all remaining $N-1$ servers**, rather than hammering a single neighbor.
- **Statistical Invariant**: The standard deviation $\sigma$ of the load handled by each server scales as:
  $$\sigma \approx \frac{1}{\sqrt{V}}$$
- **Sizing Rule**: Setting $V = 150 \text{ to } 256$ virtual nodes per server reduces the load variance across nodes to $< 5\%$, achieving near-ideal load balancing while keeping the binary search array size ($N \times V \approx 5,000 \text{ items}$) comfortably in L1/L2 CPU cache ($< 64 \text{ KB}$).

---

### Question 3: Token Bucket vs Leaky Bucket vs Sliding Window Counter: How do you choose?

#### The Question
"Compare Token Bucket, Leaky Bucket, and Sliding Window Counter. Which one would you choose for an external public REST API (like Stripe), an egress webhook dispatcher, and an edge DDoS mitigation layer?"

#### Candidate Answer

| Algorithm | Burst Handling | Egress Rate | Memory Cost | Best Production Use Case |
|---|---|---|---|---|
| **Token Bucket** | **Allows bursts** up to capacity $C$; enforces long-term average $R$. | Variable (bursty) | $O(1)$ (2 scalars: tokens, last_time) | **Public Web APIs (Stripe, GitHub)**: Accommodates legitimate human/page bursts. |
| **Leaky Bucket** | **Drops bursts**; enforces strictly constant outflow $R$. | Strictly constant | $O(1)$ or $O(B)$ if queue buffered | **Egress Webhooks / Network Traffic**: Prevents overwhelming third-party endpoints. |
| **Sliding Window Counter**| Smooths boundary bursts via weighted interpolation. | Variable | $O(1)$ (2 window counters) | **Edge Gateways / DDoS Mitigation**: Cloudflare edge proxies protecting against sudden surges. |

#### Architectural Selection:
- **Public REST APIs**: Choose **Token Bucket**. When a web page loads, a frontend app might make 8 parallel API calls (burst). Token Bucket allows these 8 calls if tokens are available, providing good user experience while protecting backend averages.
- **Outbound Webhook Dispatcher / Database Writer**: Choose **Leaky Bucket**. Downstream third-party webhooks or legacy databases fail under sudden spikes. Leaky Bucket buffers calls and drains them at an exact, smooth 50 req/sec.

---

### Question 4: How do you implement Distributed Rate Limiting across 50 API Gateway pods without race conditions?

#### The Question
"In a microservice deployment with 50 FastAPI pods behind an AWS ALB, local in-memory rate limiters fail because a client can hit different pods. How do you synchronize rate limits with Redis, and why is an atomic Lua script mandatory?"

#### Candidate Answer

#### The Distributed Concurrency Flaw:
If gateway pods check rate limits using standard Redis commands:
```python
# POD 1 and POD 2 both run:
count = redis.get(client_key)
if count < 100:
    redis.incr(client_key)
```
- When a client sends 20 requests in parallel across different pods:
- All 20 pods execute `redis.get()` simultaneously, reading `count = 99`.
- All 20 pods decide the request is valid and call `redis.incr()`.
- The client successfully executes 119 requests, violating their 100-request quota (**Concurrency Quota Bypass**).

#### The Production Solution: Redis Lua Scripting (`EVALSHA`)
Redis executes Lua scripts **atomically in a single-threaded execution context**:
1. No other Redis command or script can run while a Lua script executes.
2. The entire Token Bucket update (calculate timestamp delta, add tokens, check quota, decrement, and set TTL) runs in a single atomic transaction without multi-pod race conditions.

```lua
-- Atomic Token Bucket in Redis Lua
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1])
local last_refill = tonumber(data[2])

if not tokens then
    tokens = capacity
    last_refill = now
else
    local elapsed = math.max(0, now - last_refill)
    tokens = math.min(capacity, tokens + elapsed * refill_rate)
end

if tokens >= requested then
    tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 3600)
    return 1 -- Allowed
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    return 0 -- Throttled
end
```

---

### Question 5: How does Amazon DynamoDB use Consistent Hashing with Sloppy Quorums and Hinted Handoff?

#### The Question
"Consistent Hashing routes a key to a single node. How does Amazon's Dynamo architecture achieve 99.999% availability and partition tolerance on top of a hash ring when nodes crash?"

#### Candidate Answer

#### 1. Preference List (Replication Clockwise):
Instead of assigning a key to only one node:
- Dynamo determines the primary coordinator node on the hash ring using consistent hashing.
- It continues walking clockwise along the ring to select the **first $N$ distinct physical servers** (the **Preference List**).
- Every write is asynchronously replicated across all $N$ physical nodes.

#### 2. Sloppy Quorum ($N, R, W$):
In strict quorum systems, if one of the primary $N$ nodes is down, writes fail. Dynamo uses a **Sloppy Quorum**:
- If Node 2 in the preference list is unreachable during a network partition, the coordinator does not fail the write.
- It sends the write to a healthy node further down the hash ring (Node $N+1$).
- Reads and writes require only $R$ and $W$ acknowledgments out of $N$ healthy nodes ($R + W > N$ ensures read consistency).

#### 3. Hinted Handoff:
- When Node $N+1$ accepts a write on behalf of partitioned Node 2, it stores the data in a special local buffer with a metadata "hint" indicating that the record belongs to Node 2.
- A background gossip daemon periodically polls Node 2.
- Once the network partition heals and Node 2 comes back online, Node $N+1$ delivers the buffered data back to Node 2 (**Hinted Handoff**) and deletes its local copy, maintaining high availability without data loss.
