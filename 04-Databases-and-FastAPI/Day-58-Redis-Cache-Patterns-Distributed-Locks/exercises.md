# Practical Engineering Exercises: Redis Caching Patterns & Distributed Locks

This document presents 3 enterprise-grade challenges focusing on advanced Redis caching, cache stampede prevention, and distributed locking. Each exercise includes architectural specifications, production-ready reference implementations, and verification harnesses.

---

## Exercise 1: Probabilistic Early Expiration (XFetch Algorithm)

### Objective
In high-throughput platforms, cold key expirations trigger cache stampedes. Mutex single-flight locks prevent database crashes but force waiting requests to experience elevated latency while the leader recomputes the key.

The **XFetch Algorithm** (Vattani et al.) eliminates both the database stampede and the user latency spike. It computes a probabilistic threshold before the key expires:

$$\Delta \cdot \beta \cdot \ln(\text{rand}()) > \text{TTL}_{\text{remaining}}$$

- $\Delta$: Time taken to compute the value (stored in the cache metadata).
- $\beta$: Aggressiveness coefficient ($\beta > 0$, typically 1.0).
- $\text{rand}()$: Uniform random float between 0 and 1.

Build an **XFetch Cache Manager** that:
1. Stores values along with computation metadata: `{"data": ..., "delta": 0.05, "expires_at": ...}`.
2. On read, evaluates the XFetch condition.
3. If true, dispatches a **background asyncio task** to recompute and re-warm the cache, while **immediately returning the current cached value to the client** with zero latency penalty.

### Complete Reference Implementation

```python
"""
Exercise 1 Reference Solution: Probabilistic Early Expiration (XFetch Algorithm)
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import time
from typing import Any, Callable, Dict, Optional
import fakeredis.aioredis


class XFetchCache:
    """
    Implements the XFetch probabilistic early expiration algorithm.
    Pre-warms hot keys in the background before they expire.
    """

    def __init__(self, redis_client: fakeredis.aioredis.FakeRedis, beta: float = 1.0) -> None:
        self.redis = redis_client
        self.beta = beta
        self.background_refreshes = 0

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], Any],
        ttl_seconds: int = 60,
    ) -> Any:
        now = time.time()
        raw = await self.redis.get(key)

        if raw is not None:
            entry = json.loads(raw)
            data = entry["data"]
            delta = entry["delta"]
            expires_at = entry["expires_at"]
            remaining_ttl = expires_at - now

            # XFetch Probabilistic Check
            r = random.uniform(0.0001, 0.9999)
            if (delta * self.beta * -math.log(r)) > remaining_ttl:
                # Probabilistic trigger fired: Recompute in background without blocking caller!
                self.background_refreshes += 1
                asyncio.create_task(self._recompute_and_store(key, compute_fn, ttl_seconds))

            return data

        # Cache Miss: Synchronous compute
        return await self._recompute_and_store(key, compute_fn, ttl_seconds)

    async def _recompute_and_store(
        self,
        key: str,
        compute_fn: Callable[[], Any],
        ttl_seconds: int,
    ) -> Any:
        t0 = time.perf_counter()
        data = await compute_fn()
        delta = time.perf_counter() - t0

        entry = {
            "data": data,
            "delta": delta,
            "expires_at": time.time() + ttl_seconds,
        }
        await self.redis.set(key, json.dumps(entry), ex=ttl_seconds)
        return data


# ==============================================================================
# Verification Harness
# ==============================================================================

async def run_exercise_1_test():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = XFetchCache(redis, beta=2.0)

    db_hits = 0

    async def expensive_query():
        nonlocal db_hits
        db_hits += 1
        await asyncio.sleep(0.02)  # Simulate DB query
        return {"metric": "quarterly_revenue", "value": 4500000}

    # 1. Initial Miss
    val1 = await cache.get_or_compute("metrics:revenue", expensive_query, ttl_seconds=2)
    assert val1["value"] == 4500000
    assert db_hits == 1
    print("[TEST 1 PASSED] Initial cache miss successfully computed and stored.")

    # 2. Immediate Hit (No refresh triggered yet)
    val2 = await cache.get_or_compute("metrics:revenue", expensive_query, ttl_seconds=2)
    assert val2["value"] == 4500000
    assert db_hits == 1

    # 3. Simulate approaching expiration
    await asyncio.sleep(1.8)  # Remaining TTL is now ~0.2s

    # Subsequent read should probabilistically fire background refresh
    val3 = await cache.get_or_compute("metrics:revenue", expensive_query, ttl_seconds=2)
    assert val3["value"] == 4500000

    # Allow background task to complete
    await asyncio.sleep(0.05)
    assert cache.background_refreshes >= 1
    assert db_hits == 2
    print(f"[TEST 2 PASSED] XFetch triggered background refresh without blocking caller.")
    print(f"  Total DB Hits: {db_hits} | Background Refreshes: {cache.background_refreshes}")


if __name__ == "__main__":
    asyncio.run(run_exercise_1_test())
```

---

## Exercise 2: Distributed Lock with Automatic Watchdog Heartbeat Renewal

### Objective
A fundamental flaw of basic distributed leases is choosing a lease duration:
- If the lease is too short, long-running operations risk the lock expiring mid-execution, causing split-brain.
- If the lease is too long, a crashed worker leaves the lock held for minutes, stalling the platform.

Build an **Async Watchdog Distributed Lock** that:
1. Acquires a lock with a short lease (e.g., 2,000 ms).
2. Spawns an asynchronous background **Watchdog Task** that periodically extends the lease (`PEXPIRE`) while the caller's critical section is actively running.
3. Automatically cancels the watchdog and releases the lock atomically via Lua script upon context exit.
4. If the worker process crashes or hangs, the watchdog stops beating, allowing the lock to expire automatically within 2 seconds.

### Complete Reference Implementation

```python
"""
Exercise 2 Reference Solution: Distributed Lock with Automatic Watchdog Heartbeat
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional
import fakeredis.aioredis

LUA_EXTEND_LEASE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""

LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class AsyncWatchdogLock:
    """
    Distributed lock with automatic background lease extension (Watchdog).
    """

    def __init__(
        self,
        redis_client: fakeredis.aioredis.FakeRedis,
        resource_name: str,
        lease_ms: int = 2000,
    ) -> None:
        self.redis = redis_client
        self.key = f"watchdog:lock:{resource_name}"
        self.lease_ms = lease_ms
        self.token = str(uuid.uuid4())
        self._watchdog_task: Optional[asyncio.Task] = None
        self._is_held = False

    async def acquire(self) -> bool:
        acquired = await self.redis.set(self.key, self.token, nx=True, px=self.lease_ms)
        if acquired:
            self._is_held = True
            # Launch background heartbeat watchdog
            self._watchdog_task = asyncio.create_task(self._heartbeat())
            return True
        return False

    async def _heartbeat(self) -> None:
        """Periodically extends the lease to 1/3 of the lease duration."""
        interval = (self.lease_ms / 1000.0) / 3.0
        try:
            while self._is_held:
                await asyncio.sleep(interval)
                res = await self.redis.eval(
                    LUA_EXTEND_LEASE,
                    1,
                    self.key,
                    self.token,
                    str(self.lease_ms),
                )
                if res != 1:
                    # Lock was lost or stolen
                    self._is_held = False
                    break
        except asyncio.CancelledError:
            pass

    async def release(self) -> bool:
        self._is_held = False
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)

        res = await self.redis.eval(LUA_RELEASE_LOCK, 1, self.key, self.token)
        return bool(res == 1)

    async def __aenter__(self) -> AsyncWatchdogLock:
        if not await self.acquire():
            raise TimeoutError(f"Could not acquire lock for {self.key}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.release()


# ==============================================================================
# Verification Harness
# ==============================================================================

async def run_exercise_2_test():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    lock = AsyncWatchdogLock(redis, "long_job", lease_ms=1000)

    # Test: Run job that takes 2.5 seconds (exceeding initial 1.0s lease)
    async with lock:
        print("[TEST] Lock acquired. Executing 2.5s operation with 1.0s base lease...")
        await asyncio.sleep(2.5)
        # Check that lock is still held by caller because watchdog extended it
        current_token = await redis.get(lock.key)
        assert current_token == lock.token
        print("[TEST PASSED] Watchdog successfully kept lock alive past initial lease!")

    # Verify lock was released cleanly upon exit
    assert await redis.get(lock.key) is None
    print("[TEST PASSED] Lock cleanly released on context exit.")


if __name__ == "__main__":
    asyncio.run(run_exercise_2_test())
```

---

## Exercise 3: Two-Tier (L1 Local Memory + L2 Redis) Hybrid Cache with Pub/Sub

### Objective
At ultra-high scale, network round-trips to Redis (~0.5 ms) become the latency bottleneck. Storing data in local in-process memory (`L1`) delivers nanosecond reads (~50 ns). However, if an entity is updated on Node A, Node B's local L1 cache becomes stale.

Build a **Two-Tier (L1/L2) Invalidation Engine** that:
1. Serves reads from **L1 In-Process Memory** first ($O(1)$ RAM, 50ns).
2. On L1 miss, falls back to **L2 Distributed Redis**.
3. On L2 miss, queries the database and populates both L2 and L1.
4. On write/update, commits to the database, deletes L2 Redis, and **publishes an invalidation message to a Redis Pub/Sub channel** (`invalidation:events`).
5. All application nodes subscribe to `invalidation:events` and evict the key from their local L1 caches immediately.

### Complete Reference Implementation

```python
"""
Exercise 3 Reference Solution: Two-Tier (L1/L2) Hybrid Cache with Invalidation Pub/Sub
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional
import fakeredis.aioredis


class TwoTierCacheNode:
    """Simulates a web application instance with local L1 and distributed L2."""

    def __init__(self, node_id: str, redis_client: fakeredis.aioredis.FakeRedis) -> None:
        self.node_id = node_id
        self.redis = redis_client
        self.l1_memory: Dict[str, str] = {}
        self.l1_hits = 0
        self.l2_hits = 0
        self.db_hits = 0

    async def get(self, key: str, db_fn) -> str:
        # 1. Check L1 Local Memory (~50 ns)
        if key in self.l1_memory:
            self.l1_hits += 1
            return self.l1_memory[key]

        # 2. Check L2 Redis (~500 µs)
        val = await self.redis.get(key)
        if val is not None:
            self.l2_hits += 1
            self.l1_memory[key] = val
            return val

        # 3. Query Database
        self.db_hits += 1
        val = await db_fn()
        await self.redis.set(key, val, ex=300)
        self.l1_memory[key] = val
        return val

    async def update(self, key: str, new_val: str, db_update_fn) -> None:
        # 1. Update Database
        await db_update_fn(new_val)

        # 2. Invalidate L2 Redis
        await self.redis.delete(key)

        # 3. Invalidate local L1
        self.l1_memory.pop(key, None)

        # 4. Publish invalidation event across cluster
        await self.redis.publish("cache:invalidation", key)

    def handle_invalidation(self, key: str) -> None:
        """Called when a Pub/Sub message arrives."""
        self.l1_memory.pop(key, None)


# ==============================================================================
# Verification Harness
# ==============================================================================

async def run_exercise_3_test():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    db_store = {"settings:theme": "dark"}

    node_a = TwoTierCacheNode("node_a", redis)
    node_b = TwoTierCacheNode("node_b", redis)

    async def fetch_db():
        return db_store["settings:theme"]

    async def update_db(val: str):
        db_store["settings:theme"] = val

    # Step 1: Node A and Node B both read the key
    val_a = await node_a.get("settings:theme", fetch_db)
    val_b = await node_b.get("settings:theme", fetch_db)
    assert val_a == "dark" and val_b == "dark"
    assert node_a.l1_memory["settings:theme"] == "dark"
    assert node_b.l1_memory["settings:theme"] == "dark"
    print("[TEST 1 PASSED] Both nodes populated local L1 and distributed L2.")

    # Step 2: Node A reads again -> L1 hit (instant)
    _ = await node_a.get("settings:theme", fetch_db)
    assert node_a.l1_hits == 1

    # Step 3: Node A updates setting to "light"
    await node_a.update("settings:theme", "light", update_db)

    # Simulate Pub/Sub propagation to Node B
    node_b.handle_invalidation("settings:theme")
    assert "settings:theme" not in node_b.l1_memory

    # Step 4: Node B reads -> Sees updated "light" value
    val_b_new = await node_b.get("settings:theme", fetch_db)
    assert val_b_new == "light"
    print("[TEST 2 PASSED] Pub/Sub invalidation evicted stale L1 on Node B; fresh data loaded.")


if __name__ == "__main__":
    asyncio.run(run_exercise_3_test())
```
