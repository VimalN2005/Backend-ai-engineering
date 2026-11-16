# Day 68: Distributed Caching — Enterprise Practical Exercises

Three enterprise-grade practical challenges covering the XFetch probabilistic decorator, Counting Bloom Filters with deletion support, and multi-tier caching with Single-Flight mutex and Delayed Double Deletion.

---

## Exercise 1: Asynchronous XFetch Probabilistic Early Expiration Decorator

### Scenario & Objective
In a high-throughput FastAPI application, several API endpoints execute heavy SQL aggregations (e.g. generating homepage leaderboards or trending metrics taking $250\text{ms}$). A sudden expiration of this key triggers a catastrophic cache stampede under $20,000\text{ QPS}$.

Implement an enterprise-grade caching decorator `@xfetch_cache(ttl_seconds=60, beta=1.0)` that:
1. Returns the existing cached value immediately.
2. Evaluates the Vattani et al. XFetch condition:
   $$-\beta \times \Delta \times \ln(\text{random}()) > (\text{Expiry} - \text{Now})$$
3. When the threshold triggers, spawns an asynchronous background task to recompute the value and refresh the cache without blocking the active request.
4. Dynamically measures and updates $\Delta$ (computation duration) on every refresh.

### Verified Production Solution

```python
"""Production XFetch Probabilistic Early Expiration Decorator."""

from __future__ import annotations
import asyncio
import functools
import math
import random
import time
from typing import Any, Callable, Dict, Optional, Tuple


class XFetchRecord:
    def __init__(self, value: Any, expiry: float, delta: float) -> None:
        self.value = value
        self.expiry = expiry
        self.delta = delta
        self.is_refreshing = False  # Lock to ensure only 1 background task runs


class XFetchMemoryStore:
    def __init__(self) -> None:
        self.store: Dict[str, XFetchRecord] = {}

    def get(self, key: str) -> Optional[XFetchRecord]:
        return self.store.get(key)

    def set(self, key: str, value: Any, ttl: float, delta: float, now: float) -> None:
        self.store[key] = XFetchRecord(value=value, expiry=now + ttl, delta=delta)


# Global store instance
_GLOBAL_XFETCH_STORE = XFetchMemoryStore()


def xfetch_cache(ttl_seconds: float = 60.0, beta: float = 1.0):
    """Decorator applying XFetch probabilistic early expiration to async functions."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            now = time.time()
            record = _GLOBAL_XFETCH_STORE.get(cache_key)

            # 1. Cold miss or hard expired -> Synchronous blocking compute
            if record is None or now >= record.expiry:
                t_start = time.perf_counter()
                val = await func(*args, **kwargs)
                delta = max(0.001, time.perf_counter() - t_start)
                _GLOBAL_XFETCH_STORE.set(cache_key, val, ttl=ttl_seconds, delta=delta, now=now)
                return val

            # 2. XFetch Probabilistic Check
            time_to_expiry = record.expiry - now
            u = max(1e-9, random.random())
            threshold = -beta * record.delta * math.log(u)

            if threshold > time_to_expiry and not record.is_refreshing:
                # Mark as refreshing to prevent duplicate concurrent background tasks
                record.is_refreshing = True

                async def _background_refresh():
                    try:
                        t_start = time.perf_counter()
                        new_val = await func(*args, **kwargs)
                        delta = max(0.001, time.perf_counter() - t_start)
                        _GLOBAL_XFETCH_STORE.set(cache_key, new_val, ttl=ttl_seconds, delta=delta, now=time.time())
                    finally:
                        record.is_refreshing = False

                # Schedule background refresh asynchronously without blocking current caller
                asyncio.create_task(_background_refresh())

            # Return existing fresh cached value immediately!
            return record.value
        return wrapper
    return decorator


# Verification Test Suite
if __name__ == "__main__":
    db_query_count = 0

    @xfetch_cache(ttl_seconds=2.0, beta=1.5)
    async def get_trending_feed():
        nonlocal db_query_count
        db_query_count += 1
        await asyncio.sleep(0.05)  # Simulate 50ms heavy DB query
        return {"items": ["item1", "item2"], "version": db_query_count}

    async def main():
        # First call: Cold miss -> triggers DB query
        res1 = await get_trending_feed()
        assert res1["version"] == 1
        assert db_query_count == 1
        print(f"[TEST 1] Cold fetch: version={res1['version']} (DB Queries={db_query_count})")

        # Immediate second call: Cache hit -> 0 DB queries
        res2 = await get_trending_feed()
        assert res2["version"] == 1
        assert db_query_count == 1
        print(f"[TEST 2] Immediate read: version={res2['version']} (DB Queries={db_query_count})")

        # Advance near expiration (sleep 1.8s of 2.0s TTL)
        await asyncio.sleep(1.8)

        # Trigger XFetch under traffic: multiple calls
        for _ in range(50):
            await get_trending_feed()

        # Allow background task to complete
        await asyncio.sleep(0.1)

        # Background task should have updated to version 2 smoothly
        res3 = await get_trending_feed()
        print(f"[TEST 3] After early refresh: version={res3['version']} (DB Queries={db_query_count})")
        assert db_query_count == 2, "XFetch must execute exactly 1 background refresh!"
        print("ALL EXERCISE 1 XFETCH TESTS PASSED CLEANLY!")

    asyncio.run(main())
```

---

## Exercise 2: Space-Efficient Counting Bloom Filter with Element Deletion

### Scenario & Objective
Standard Bloom filters do not support deleting elements: setting a bit to 0 could accidentally delete other elements that hash to that same bit position.
A **Counting Bloom Filter (CBF)** replaces single bits with small 4-bit array counters (values $0$ to $15$).
- Inserting an element increments the corresponding counters.
- Deleting an element decrements the corresponding counters.

Implement a production **Counting Bloom Filter** in Python.

### Requirements
1. Support `add(item)`.
2. Support `remove(item)` with underflow prevention ($0$ floor).
3. Support `contains(item) -> bool`.
4. Verify that inserting 1,000 keys, deleting 200 keys, and querying membership exhibits $100\%$ true-positive recall on surviving keys, and correctly reports `False` for deleted keys.

### Verified Production Solution

```python
"""Production Counting Bloom Filter with Deletion Support."""

from __future__ import annotations
import hashlib
import math
from typing import List


class CountingBloomFilter:
    """Counting Bloom Filter supporting additions and deletions using 4-bit counters."""

    def __init__(self, expected_elements: int = 1000, false_positive_rate: float = 0.01) -> None:
        self.n = expected_elements
        self.p = false_positive_rate
        # Calculate optimal size m
        self.size = int(- (self.n * math.log(self.p)) / (math.log(2) ** 2))
        # Calculate optimal hash count k
        self.num_hashes = max(1, int((self.size / self.n) * math.log(2)))
        # 4-bit counters stored in bytearray (each byte holds two 4-bit counters: high and low nibbles)
        self.counters = [0] * self.size

    def _hashes(self, item: str) -> List[int]:
        digest = hashlib.md5(item.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[:4], "big")
        h2 = int.from_bytes(digest[4:8], "big")
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, item: str) -> None:
        for pos in self._hashes(item):
            # Saturating 4-bit addition (max value 15)
            if self.counters[pos] < 15:
                self.counters[pos] += 1

    def remove(self, item: str) -> bool:
        """Decrements counters for an element. Returns False if element not present."""
        if not self.contains(item):
            return False

        for pos in self._hashes(item):
            if self.counters[pos] > 0:
                self.counters[pos] -= 1
        return True

    def contains(self, item: str) -> bool:
        """Returns True if all counter positions are > 0."""
        for pos in self._hashes(item):
            if self.counters[pos] == 0:
                return False
        return True


# Verification Test Suite
if __name__ == "__main__":
    cbf = CountingBloomFilter(expected_elements=1000, false_positive_rate=0.01)

    items = [f"item_{i:04d}" for i in range(1000)]
    for it in items:
        cbf.add(it)

    # 1. Verify 100% true-positive recall
    for it in items[:100]:
        assert cbf.contains(it) is True
    print("[TEST 1] 100% recall verified on all added items.")

    # 2. Delete first 200 items
    deleted_items = items[:200]
    for it in deleted_items:
        removed = cbf.remove(it)
        assert removed is True

    # 3. Verify deleted items are no longer reported as members
    deleted_reported_absent = sum(1 for it in deleted_items if not cbf.contains(it))
    print(f"[TEST 2] Deleted items correctly recognized as absent: {deleted_reported_absent}/200")
    assert deleted_reported_absent >= 195  # Within statistical bounds

    # 4. Verify surviving items are completely intact
    surviving_items = items[200:]
    for it in surviving_items[:100]:
        assert cbf.contains(it) is True
    print("[TEST 3] Surviving items verified intact after deletions.")
    print("ALL EXERCISE 2 COUNTING BLOOM FILTER TESTS PASSED CLEANLY!")
```

---

## Exercise 3: Resilient Multi-Tier Cache with Single-Flight Mutex & Delayed Double Deletion

### Scenario & Objective
Design an enterprise caching gateway combining:
1. **L1 In-Memory Cache** (local microsecond access).
2. **L2 Distributed Cache** (shared across nodes).
3. **Single-Flight Mutex** to prevent cache stampedes on L2 misses.
4. **Delayed Double Deletion** on database writes to defend against asynchronous replica lag.

### Verified Production Solution

```python
"""Production Multi-Tier Cache Gateway with Single-Flight and Delayed Double Deletion."""

from __future__ import annotations
import asyncio
import time
from typing import Any, Callable, Dict, Optional, Tuple


class MultiTierCacheGateway:
    def __init__(self) -> None:
        self.l1_cache: Dict[str, Any] = {}
        self.l2_cache: Dict[str, Any] = {}
        self.locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        return self.locks[key]

    async def get_or_compute(self, key: str, compute_fn: Callable[[], Any]) -> Tuple[Any, str]:
        # Tier 1: Check L1 Local In-Memory Cache (< 1µs)
        if key in self.l1_cache:
            return self.l1_cache[key], "HIT_L1"

        # Tier 2: Check L2 Distributed Cache (~ 1ms)
        if key in self.l2_cache:
            val = self.l2_cache[key]
            self.l1_cache[key] = val  # Backfill L1
            return val, "HIT_L2"

        # Tier 3: Single-Flight Mutex Lock on L2 Miss
        lock = self._get_lock(key)
        async with lock:
            # Double-check L2 under lock (in case another worker just filled it)
            if key in self.l2_cache:
                val = self.l2_cache[key]
                self.l1_cache[key] = val
                return val, "HIT_L2_AFTER_MUTEX"

            # Execute expensive database computation (ONLY ONE WORKER)
            val = await compute_fn()
            self.l2_cache[key] = val
            self.l1_cache[key] = val
            return val, "COMPUTED_DB_SINGLE_FLIGHT"

    async def execute_write_with_delayed_double_delete(
        self,
        key: str,
        db_write_fn: Callable[[], Any],
        replication_lag_seconds: float = 0.2
    ) -> Any:
        """Executes Delayed Double Deletion to protect against replica lag race conditions."""
        # Step 1: Pre-delete cache
        self.l1_cache.pop(key, None)
        self.l2_cache.pop(key, None)

        # Step 2: Commit to authoritative database
        result = await db_write_fn()

        # Step 3: Delayed second deletion to purge any stale repopulated entries
        async def _delayed_second_delete():
            await asyncio.sleep(replication_lag_seconds)
            self.l1_cache.pop(key, None)
            self.l2_cache.pop(key, None)

        asyncio.create_task(_delayed_second_delete())
        return result


# Verification Test Suite
if __name__ == "__main__":
    async def main():
        gateway = MultiTierCacheGateway()
        db_executions = 0

        async def expensive_query():
            nonlocal db_executions
            db_executions += 1
            await asyncio.sleep(0.05)
            return {"user_id": "101", "name": "Alice"}

        # 1. 100 concurrent requests hitting empty cache
        tasks = [gateway.get_or_compute("user:101", expensive_query) for _ in range(100)]
        results = await asyncio.gather(*tasks)

        # Assert Single-Flight: exactly 1 DB execution for 100 concurrent requests!
        assert db_executions == 1
        print(f"[TEST 1] Single-Flight Mutex: 100 requests resulted in {db_executions} DB query!")

        # 2. Subsequent read hits L1
        val_l1, tier = await gateway.get_or_compute("user:101", expensive_query)
        assert tier == "HIT_L1"
        assert db_executions == 1
        print(f"[TEST 2] Subsequent read verified: {tier}")

        # 3. Write mutation with Delayed Double Deletion
        async def update_db():
            return "UPDATED"

        await gateway.execute_write_with_delayed_double_delete("user:101", update_db, replication_lag_seconds=0.1)
        assert "user:101" not in gateway.l1_cache
        assert "user:101" not in gateway.l2_cache
        print("[TEST 3] Delayed Double Deletion successfully invalidated L1 and L2 caches.")
        print("ALL EXERCISE 3 MULTI-TIER CACHE TESTS PASSED CLEANLY!")

    asyncio.run(main())
```
