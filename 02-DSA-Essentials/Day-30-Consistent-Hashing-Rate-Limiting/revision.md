# Day 30 Rapid Revision: Consistent Hashing & Rate Limiting

A condensed 5-minute reference sheet for distributed hash rings, virtual node sizing, and traffic shaping rate limiters.

---

## 1. Algorithmic Decision Matrix

| Problem Goal | Optimal Algorithm | Primary Invariant | Key Advantage |
|---|---|---|---|
| **Elastic Cache / Sharded Storage** | **Consistent Hashing (Vnodes)** | Binary search on circular ring $[0, 2^{32}-1]$ | Remaps only $1/N$ keys on scale up/down; zero cache avalanche |
| **Heterogeneous Server Clustering** | **Weighted Consistent Hashing** | Number of virtual nodes proportional to server RAM/CPU | Heavy servers handle proportionally more traffic without skew |
| **Public REST API Rate Limiting** | **Token Bucket** | Lazy refill $\Delta t \times R$; capped at capacity $C$ | Accommodates legitimate human bursts while enforcing average rate |
| **Outbound Webhook / Disk Traffic** | **Leaky Bucket** | FIFO buffer draining at constant outflow rate | Smooths erratic traffic spikes into a flat, predictable flow |
| **DDoS / Edge Protection** | **Sliding Window Counter** | Interpolates count between current and previous window | $O(1)$ memory; eliminates 2x burst across boundary resets |
| **Multi-Pod Distributed Limiter** | **Redis Lua Token Bucket** | Single-threaded atomic evaluation in Redis | Zero multi-pod race conditions or quota bypass |

---

## 2. Core Reusable Code Templates

### Template 1: Consistent Hash Ring (Vnodes + Binary Search)
```python
from bisect import bisect_right
import hashlib

class ConsistentHashRing:
    def __init__(self, vnodes: int = 150):
        self.vnodes = vnodes
        self.ring = []
        self.token_to_node = {}

    def _hash(self, key: str) -> int:
        # Deterministic 32-bit integer hash across all processes
        return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)

    def add_node(self, node: str) -> None:
        for i in range(self.vnodes):
            token = self._hash(f"{node}#v_{i}")
            self.token_to_node[token] = node
            self.ring.insert(bisect_right(self.ring, token), token)

    def get_node(self, key: str) -> str | None:
        if not self.ring:
            return None
        h = self._hash(key)
        idx = bisect_right(self.ring, h)
        if idx == len(self.ring):
            idx = 0  # Wrap around
        return self.token_to_node[self.ring[idx]]
```

### Template 2: Token Bucket Rate Limiter (Lazy On-Demand Refill)
```python
import time

class TokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.buckets = {}  # client_id -> (tokens, last_refill_timestamp)

    def allow_request(self, client_id: str, tokens: int = 1) -> bool:
        now = time.time()
        if client_id not in self.buckets:
            self.buckets[client_id] = [float(self.capacity), now]

        curr_tokens, last_refill = self.buckets[client_id]
        # Lazy refill on request arrival (Zero background timers!)
        elapsed = now - last_refill
        new_tokens = min(float(self.capacity), curr_tokens + elapsed * self.refill_rate)

        if new_tokens >= tokens:
            self.buckets[client_id] = [new_tokens - tokens, now]
            return True

        self.buckets[client_id] = [new_tokens, now]
        return False
```

### Template 3: Sliding Window Counter (Memory-Efficient O(1))
```python
import time

class SlidingWindowCounter:
    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.limit = max_requests
        self.window = window_seconds
        self.counters = {}  # client -> (prev_win, prev_cnt, curr_win, curr_cnt)

    def allow(self, client_id: str) -> bool:
        now = time.time()
        curr_win = int(now // self.window)
        sec_into_win = now % self.window

        if client_id not in self.counters:
            self.counters[client_id] = [curr_win - 1, 0, curr_win, 0]

        p_win, p_cnt, c_win, c_cnt = self.counters[client_id]
        if curr_win > c_win:
            p_win, p_cnt = (c_win, c_cnt) if curr_win == c_win + 1 else (curr_win - 1, 0)
            c_win, c_cnt = curr_win, 0
            self.counters[client_id] = [p_win, p_cnt, c_win, c_cnt]

        # Weight previous window inversely by time progressed
        weight = 1.0 - (sec_into_win / self.window)
        if int(c_cnt + p_cnt * weight) < self.limit:
            self.counters[client_id][3] += 1
            return True
        return False
```

---

## 3. Non-Negotiable Invariants & Rules

1. **Virtual Nodes are Non-Negotiable**: Without virtual nodes, consistent hashing creates severe data skew ($>70\%$ of keys on one server). Size $V \ge 150$.
2. **Never Use Python's Built-In `hash()`**: Native `hash()` is randomized on each process startup. Always use `MD5` or `MurmurHash3`.
3. **Lazy Timestamp Replenishment**: Never use background timer loops to add tokens. Compute tokens on-demand via elapsed time $\Delta t \times R$.
4. **Always Return Standard HTTP 429 Headers**: Include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `Retry-After` on throttle responses.
5. **Atomic Evaluation in Distributed Systems**: Multiple API gateway pods must check and deduct rate limits atomically inside a single-threaded Redis Lua script.

---

## 4. Complexity Quick Reference Table

| Operation | Modulo Hashing | Consistent Hash Ring | Token Bucket | Sliding Window Counter |
|---|---|---|---|---|
| **Lookup / Evaluation** | $O(1)$ | $O(\log(N \cdot V))$ | $O(1)$ | $O(1)$ |
| **Node Addition Rehash**| $\frac{N-1}{N}$ (~99%) | $\frac{1}{N}$ (~1%) | N/A | N/A |
| **Memory Footprint** | $O(1)$ | $O(N \cdot V)$ | $O(1)$ per client | $O(1)$ per client |
| **Burst Tolerance** | N/A | N/A | High (up to $C$) | Moderate (smoothed) |
