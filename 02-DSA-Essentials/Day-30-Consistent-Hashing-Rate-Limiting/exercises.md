# Practice Exercises: Consistent Hashing & Distributed Rate Limiting

Mastering distributed partitioning and rate limiting requires implementing three production patterns:
1. **Exact Precision Traffic Limiting (Sliding Window Log)** — Sliding window rate limiter using monotonic double-ended queues.
2. **Heterogeneous Cluster Balancing (Weighted Consistent Hash Ring)** — Dynamic virtual token scaling proportional to server hardware capacity.
3. **Atomic Multi-Pod Rate Limiter (Redis Lua Simulator)** — Emulating single-threaded atomic Lua scripts with TTL auto-expiration.

---

## Challenge 1: Sliding Window Log Rate Limiter

### Problem Statement
Implement a rate limiter that provides **100% mathematical precision** by tracking the exact timestamps of client requests.

Implement the `SlidingWindowLogRateLimiter` class:
- `__init__(max_requests: int, window_seconds: float)`: Initializes with quota and time window.
- `allow_request(client_id: str) -> bool`: Returns `True` if request is allowed, `False` otherwise.
- The limiter must prune all timestamps older than `now - window_seconds` before checking capacity.

### Optimal Solution (`collections.deque` Timestamp Log)

```python
from collections import defaultdict, deque
import time

class SlidingWindowLogRateLimiter:
    """
    Sliding Window Log Rate Limiter.
    
    ALGORITHM:
    Stores timestamps of requests in a double-ended queue.
    On each request:
    1. Pop all timestamps older than (now - window_seconds) from the left in O(1).
    2. If len(queue) < max_requests, append `now` to the right and allow.
    3. Else, deny request.
    
    Time Complexity: Amortized O(1) per request.
    Space Complexity: O(N) where N is number of requests in the active window.
    """

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # client_id -> deque of timestamps
        self._logs: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow_request(self, client_id: str) -> bool:
        now = time.time()
        log = self._logs[client_id]
        threshold = now - self.window_seconds

        # Prune all timestamps outside the sliding window
        while log and log[0] <= threshold:
            log.popleft()

        # Check quota
        if len(log) < self.max_requests:
            log.append(now)
            return True

        return False


# Verification
if __name__ == "__main__":
    limiter = SlidingWindowLogRateLimiter(max_requests=2, window_seconds=0.5)
    assert limiter.allow_request("user_1") is True
    assert limiter.allow_request("user_1") is True
    assert limiter.allow_request("user_1") is False  # 3rd request rejected
    time.sleep(0.55)
    assert limiter.allow_request("user_1") is True   # Log expired, allowed again
    print("[PASS] Challenge 1: Sliding Window Log Rate Limiter verified.")
```

---

## Challenge 2: Weighted Consistent Hash Ring

### Problem Statement
In real-world data centers, servers are not identical. A heavy database node with 64 CPU cores and 512GB RAM should handle **3x more keys** than a small 8-core server.

Implement `WeightedConsistentHashRing`:
- `add_node(node_name: str, weight: int)`: Allocates `base_vnodes * weight` virtual tokens on the ring.
- `get_node(key: str) -> str`: Routes keys to the appropriate physical node using binary search.

### Optimal Solution (Proportional Token Allocation)

```python
from bisect import bisect_right
import hashlib
from typing import Dict, List, Optional

class WeightedConsistentHashRing:
    """
    Consistent Hash Ring supporting heterogeneous server capacities.
    """

    def __init__(self, base_vnodes: int = 100):
        self.base_vnodes = base_vnodes
        self._ring: List[int] = []
        self._token_to_node: Dict[int, str] = {}
        self._weights: Dict[str, int] = {}

    def _hash(self, key_str: str) -> int:
        digest = hashlib.md5(key_str.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def add_node(self, node_name: str, weight: int = 1) -> None:
        if weight <= 0:
            raise ValueError("Weight must be >= 1")
        self._weights[node_name] = weight
        total_tokens = self.base_vnodes * weight

        for i in range(total_tokens):
            token = self._hash(f"{node_name}#wvnode_{i}")
            self._token_to_node[token] = node_name
            idx = bisect_right(self._ring, token)
            self._ring.insert(idx, token)

    def get_node(self, key: str) -> Optional[str]:
        if not self._ring:
            return None
        h = self._hash(key)
        idx = bisect_right(self._ring, h)
        if idx == len(self._ring):
            idx = 0
        return self._token_to_node[self._ring[idx]]


# Verification
if __name__ == "__main__":
    from collections import Counter
    weighted_ring = WeightedConsistentHashRing(base_vnodes=100)
    # Node A has weight 3, Node B has weight 1. Node A should get ~75% of keys.
    weighted_ring.add_node("node-large-gpu", weight=3)
    weighted_ring.add_node("node-small-cpu", weight=1)

    distribution = Counter(weighted_ring.get_node(f"key_{i}") for i in range(10_000))
    ratio_large = distribution["node-large-gpu"] / 10_000

    assert 0.70 <= ratio_large <= 0.80, f"Expected ~75%, got {ratio_large * 100:.1f}%"
    print(f"[PASS] Challenge 2: Weighted Consistent Hash Ring verified ({ratio_large * 100:.1f}% load on 3x weight node).")
```

---

## Challenge 3: Redis-Style Atomic Rate Limiter Simulator

### Problem Statement
In multi-node production clusters, rate limiters run inside Redis to synchronize limits across all gateway pods.
Implement an atomic Token Bucket simulator mimicking a **Redis Lua script**:
- Ensures atomic token check-and-decrement with zero race conditions.
- Sets an automatic TTL on the bucket key so idle clients do not consume Redis memory indefinitely.

```python
import threading
import time
from typing import Dict, Tuple

class RedisLuaRateLimiterSimulator:
    """
    Simulates Redis atomic EVALSHA execution of Token Bucket algorithm.
    """

    def __init__(self, capacity: int, refill_rate_per_sec: float, ttl_seconds: int = 3600):
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.ttl = ttl_seconds
        # Simulates Redis in-memory storage: key -> (tokens, last_refill, expires_at)
        self._redis_memory: Dict[str, Tuple[float, float, float]] = {}
        # Simulates Redis single-threaded event loop mutex
        self._redis_single_thread_lock = threading.Lock()

    def eval_rate_limit_script(self, client_key: str) -> bool:
        """
        Executes atomic Lua script logic inside Redis event loop.
        """
        with self._redis_single_thread_lock:
            now = time.time()

            if client_key in self._redis_memory:
                tokens, last_refill, expires_at = self._redis_memory[client_key]
                if now >= expires_at:
                    # Key expired in Redis
                    tokens = float(self.capacity)
                    last_refill = now
                else:
                    # Replenish tokens
                    elapsed = now - last_refill
                    tokens = min(float(self.capacity), tokens + elapsed * self.refill_rate)
            else:
                tokens = float(self.capacity)
                last_refill = now

            if tokens >= 1.0:
                tokens -= 1.0
                # Set TTL expiration in Redis
                self._redis_memory[client_key] = (tokens, now, now + self.ttl)
                return True
            else:
                self._redis_memory[client_key] = (tokens, now, now + self.ttl)
                return False


# Verification
if __name__ == "__main__":
    redis_sim = RedisLuaRateLimiterSimulator(capacity=2, refill_rate_per_sec=1.0)
    assert redis_sim.eval_rate_limit_script("api_key_live_xyz") is True
    assert redis_sim.eval_rate_limit_script("api_key_live_xyz") is True
    assert redis_sim.eval_rate_limit_script("api_key_live_xyz") is False
    print("[PASS] Challenge 3: Redis-Style Atomic Rate Limiter verified.")
```
