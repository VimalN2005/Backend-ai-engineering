"""
Production Pipeline: Redis Cache Patterns, Cache-Aside & Distributed Locks
===========================================================================
Day 58: Redis Cache Patterns: Cache-Aside, Write-Through & Distributed Locks

This module implements a production-grade asynchronous caching and distributed
locking engine featuring:
1. Asynchronous Redis connection with FakeRedis for deterministic local testing.
2. Production Cache-Aside pattern with randomized TTL Jitter (preventing Avalanche).
3. Cache Invalidation on Write (Update DB -> Delete Cache).
4. Atomic Distributed Lock with UUID token and atomic Lua release script.
5. Thundering Herd / Cache Stampede Single-Flight Mutex shield.
6. 6 Automated async integration tests covering hits/misses, locks, and stampedes.

Run via:
    python example_01.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import fakeredis.aioredis
from pydantic import BaseModel, Field

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("redis_cache")

# ==============================================================================
# Lua Scripts for Atomic Operations
# ==============================================================================

# Atomic lock release: Only delete if the token matches the caller's UUID
LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


# ==============================================================================
# 1. Production Distributed Lock Manager
# ==============================================================================

class AsyncDistributedLock:
    """
    Production-grade distributed lock manager.
    Uses atomic SET NX PX and releases via an atomic Lua script to prevent
    split-brain release vulnerabilities.
    """

    def __init__(
        self,
        redis_client: fakeredis.aioredis.FakeRedis,
        resource_name: str,
        lease_ms: int = 5000,
        acquire_timeout_ms: int = 3000,
        retry_delay_ms: int = 50,
    ) -> None:
        self.redis = redis_client
        self.key = f"lock:{resource_name}"
        self.lease_ms = lease_ms
        self.acquire_timeout_ms = acquire_timeout_ms
        self.retry_delay_ms = retry_delay_ms
        self.token = str(uuid.uuid4())
        self._held = False

    async def acquire(self) -> bool:
        """Attempts to acquire the lock with timeout and exponential jittered backoff."""
        start_time = time.monotonic()
        timeout_sec = self.acquire_timeout_ms / 1000.0

        while True:
            # Atomic SET if Not eXists with Millisecond Expiration
            acquired = await self.redis.set(
                self.key,
                self.token,
                nx=True,
                px=self.lease_ms,
            )
            if acquired:
                self._held = True
                return True

            if (time.monotonic() - start_time) >= timeout_sec:
                return False

            # Jittered retry to prevent lock convoy stampedes
            jitter = random.uniform(0.8, 1.2)
            delay = (self.retry_delay_ms / 1000.0) * jitter
            await asyncio.sleep(delay)

    async def release(self) -> bool:
        """Releases the lock atomically using Lua script if token matches."""
        if not self._held:
            return False

        try:
            # Execute atomic Lua script
            result = await self.redis.eval(
                LUA_RELEASE_LOCK,
                1,
                self.key,
                self.token,
            )
            return bool(result == 1)
        finally:
            self._held = False

    async def __aenter__(self) -> AsyncDistributedLock:
        success = await self.acquire()
        if not success:
            raise TimeoutError(f"Could not acquire distributed lock for resource: {self.key}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.release()


# ==============================================================================
# 2. Cache-Aside Engine with TTL Jitter & Stampede Mutex
# ==============================================================================

class CacheEngine:
    """
    Enterprise caching service providing Cache-Aside, TTL Jitter, and Mutex Single-Flight.
    """

    def __init__(self, redis_client: fakeredis.aioredis.FakeRedis) -> None:
        self.redis = redis_client
        self.stats = {"hits": 0, "misses": 0, "db_queries": 0}

    @staticmethod
    def calculate_jittered_ttl(base_ttl: int, jitter_ratio: float = 0.15) -> int:
        """Adds +/- jitter to base TTL to prevent cache avalanche synchronization."""
        delta = int(base_ttl * jitter_ratio)
        return base_ttl + random.randint(-delta, delta)

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], Any],
        base_ttl: int = 60,
    ) -> Any:
        """
        Cache-Aside implementation with Single-Flight Mutex against Thundering Herd.
        """
        # 1. Read from Redis
        cached_data = await self.redis.get(key)
        if cached_data is not None:
            self.stats["hits"] += 1
            return json.loads(cached_data)

        self.stats["misses"] += 1

        # 2. Mutex Single-Flight: Prevent 10,000 concurrent requests from hitting the DB
        lock_resource = f"stampede:{key}"
        lock = AsyncDistributedLock(self.redis, lock_resource, lease_ms=3000, acquire_timeout_ms=2000)

        try:
            acquired = await lock.acquire()
            if acquired:
                # Double-check cache in case another worker just populated it
                double_check = await self.redis.get(key)
                if double_check is not None:
                    self.stats["hits"] += 1
                    return json.loads(double_check)

                # Query Primary Data Source
                self.stats["db_queries"] += 1
                result = await compute_fn()

                # Store in Redis with TTL Jitter
                jittered_ttl = self.calculate_jittered_ttl(base_ttl)
                await self.redis.set(key, json.dumps(result), ex=jittered_ttl)
                return result
            else:
                # Fallback: Lock timed out, fetch from DB directly
                self.stats["db_queries"] += 1
                return await compute_fn()
        finally:
            await lock.release()

    async def invalidate(self, key: str) -> None:
        """Deletes key to ensure fresh reads on subsequent requests."""
        await self.redis.delete(key)


# ==============================================================================
# 3. Product Catalog Service (Cache-Aside + Write-Invalidate)
# ==============================================================================

class Product(BaseModel):
    id: str
    name: str
    price: float
    stock: int
    updated_at: float = Field(default_factory=time.time)


class ProductRepository:
    """Simulates a relational database table with artificially slow queries."""

    def __init__(self) -> None:
        self._db: Dict[str, Product] = {}

    async def seed(self, product: Product) -> None:
        self._db[product.id] = product

    async def get_by_id(self, product_id: str) -> Optional[Product]:
        # Simulate 20ms database disk I/O latency
        await asyncio.sleep(0.02)
        return self._db.get(product_id)

    async def update(self, product_id: str, new_price: float) -> Optional[Product]:
        await asyncio.sleep(0.01)
        prod = self._db.get(product_id)
        if prod:
            prod.price = new_price
            prod.updated_at = time.time()
        return prod


class ProductService:
    """Product service orchestrating relational database and Redis cache."""

    def __init__(self, repo: ProductRepository, cache: CacheEngine) -> None:
        self.repo = repo
        self.cache = cache

    def _cache_key(self, product_id: str) -> str:
        return f"product:{product_id}"

    async def get_product(self, product_id: str) -> Optional[Product]:
        """Cache-Aside read path."""
        key = self._cache_key(product_id)

        async def fetch_from_db():
            prod = await self.repo.get_by_id(product_id)
            return prod.model_dump() if prod else None

        data = await self.cache.get_or_compute(key, fetch_from_db, base_ttl=300)
        return Product(**data) if data else None

    async def update_product_price(self, product_id: str, new_price: float) -> Optional[Product]:
        """
        Cache Invalidation on Write:
        1. Commit update to primary database.
        2. Invalidate (delete) cache key immediately.
        """
        updated = await self.repo.update(product_id, new_price)
        if updated:
            # Immediate invalidation
            await self.cache.invalidate(self._cache_key(product_id))
        return updated


# ==============================================================================
# 4. Automated Integration Test Suite
# ==============================================================================

async def run_integration_tests() -> None:
    print("=" * 80)
    print(" REDIS CACHING PATTERNS & DISTRIBUTED LOCKS INTEGRATION TEST SUITE")
    print("=" * 80)

    # Initialize fake async Redis client
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = CacheEngine(redis_client)
    repo = ProductRepository()
    service = ProductService(repo, cache)

    # Seed product
    prod_initial = Product(id="prod_1001", name="Mechanical Keyboard", price=129.99, stock=45)
    await repo.seed(prod_initial)

    # --------------------------------------------------------------------------
    # Test 1: Cache-Aside Hit and Miss Lifecycle
    # --------------------------------------------------------------------------
    print("\n[TEST 1] Testing Cache-Aside Read Lifecycle (Miss -> Hit)...")
    t0 = time.perf_counter()
    p1 = await service.get_product("prod_1001")
    t_miss_ms = (time.perf_counter() - t0) * 1000.0

    assert p1 is not None
    assert p1.name == "Mechanical Keyboard"
    assert cache.stats["misses"] == 1
    assert cache.stats["hits"] == 0
    assert cache.stats["db_queries"] == 1
    print(f"  -> Cache Miss: Fetched from DB in {t_miss_ms:.2f} ms")

    # Second read: Must be a cache hit
    t1 = time.perf_counter()
    p2 = await service.get_product("prod_1001")
    t_hit_ms = (time.perf_counter() - t1) * 1000.0

    assert p2 is not None
    assert p2.price == 129.99
    assert cache.stats["hits"] == 1
    print(f"  -> Cache Hit : Fetched from Redis in {t_hit_ms:.2f} ms ({t_miss_ms / t_hit_ms:.1f}x speedup)")
    print("  -> PASSED: Cache-Aside pattern verified.")

    # --------------------------------------------------------------------------
    # Test 2: TTL Jitter Range Verification
    # --------------------------------------------------------------------------
    print("\n[TEST 2] Testing Randomized TTL Jitter Range...")
    base_ttl = 300
    ttls = [CacheEngine.calculate_jittered_ttl(base_ttl, 0.15) for _ in range(100)]
    min_ttl, max_ttl = min(ttls), max(ttls)

    assert min_ttl >= int(base_ttl * 0.85)
    assert max_ttl <= int(base_ttl * 1.15)
    assert len(set(ttls)) > 20, "TTL jitter lacks sufficient entropy"
    print(f"  -> Base TTL: {base_ttl}s | Jittered Range observed: [{min_ttl}s - {max_ttl}s]")
    print("  -> PASSED: TTL jitter prevents cache avalanche synchronization.")

    # --------------------------------------------------------------------------
    # Test 3: Cache Invalidation on Update (Delete on Write)
    # --------------------------------------------------------------------------
    print("\n[TEST 3] Testing Cache Invalidation on Write...")
    # Update price in DB
    await service.update_product_price("prod_1001", 149.99)

    # Key must be purged from Redis
    raw_cached = await redis_client.get("product:prod_1001")
    assert raw_cached is None, "Cache was not deleted after update!"

    # Subsequent read triggers fresh DB fetch
    p_updated = await service.get_product("prod_1001")
    assert p_updated.price == 149.99
    assert cache.stats["misses"] == 2
    assert cache.stats["hits"] == 1
    print(f"  -> Cache evicted and re-populated with fresh price: ${p_updated.price}")
    print("  -> PASSED: Invalidation on write prevents stale reads.")

    # --------------------------------------------------------------------------
    # Test 4: Distributed Lock Mutual Exclusion
    # --------------------------------------------------------------------------
    print("\n[TEST 4] Testing Distributed Lock Mutual Exclusion...")
    lock_name = "inventory:checkout:prod_1001"
    critical_section_concurrency = 0
    max_observed_concurrency = 0

    async def worker(worker_id: int):
        nonlocal critical_section_concurrency, max_observed_concurrency
        lock = AsyncDistributedLock(redis_client, lock_name, lease_ms=1000, acquire_timeout_ms=2000)
        async with lock:
            critical_section_concurrency += 1
            max_observed_concurrency = max(max_observed_concurrency, critical_section_concurrency)
            # Simulate work inside critical section
            await asyncio.sleep(0.01)
            critical_section_concurrency -= 1

    # Launch 5 workers competing for the same lock
    await asyncio.gather(*(worker(i) for i in range(5)))

    assert max_observed_concurrency == 1, f"Lock failed mutual exclusion! Max concurrency: {max_observed_concurrency}"
    assert critical_section_concurrency == 0
    print("  -> All 5 workers executed sequentially. Max observed concurrency: exactly 1.")
    print("  -> PASSED: Distributed lock guaranteed strict mutual exclusion.")

    # --------------------------------------------------------------------------
    # Test 5: Atomic Lua Lock Release Safety (Anti-Split-Brain)
    # --------------------------------------------------------------------------
    print("\n[TEST 5] Testing Atomic Lua Lock Release (Split-Brain Immunity)...")
    lock_a = AsyncDistributedLock(redis_client, "invoice:generation", lease_ms=200)
    acquired_a = await lock_a.acquire()
    assert acquired_a

    # Simulate lock expiration by overwriting the key with Client B's token
    rogue_token = str(uuid.uuid4())
    await redis_client.set(lock_a.key, rogue_token, px=1000)

    # Client A attempts to release the lock
    released_a = await lock_a.release()
    assert not released_a, "Client A released Client B's lock! Split-brain occurred!"

    # Verify Client B's lock remains intact
    current_token = await redis_client.get(lock_a.key)
    assert current_token == rogue_token
    print("  -> Client A release safely rejected by Lua script because token did not match.")
    print("  -> PASSED: Atomic Lua release prevents accidental lock theft.")

    # --------------------------------------------------------------------------
    # Test 6: Thundering Herd / Cache Stampede Mutex Protection
    # --------------------------------------------------------------------------
    print("\n[TEST 6] Testing Thundering Herd Mutex Shield...")
    # Flush product key to trigger a cold-cache stampede
    await redis_client.delete("product:prod_1001")
    initial_db_queries = cache.stats["db_queries"]

    # Fire 30 concurrent requests for the missing product
    results = await asyncio.gather(*(service.get_product("prod_1001") for _ in range(30)))

    assert len(results) == 30
    assert all(p.id == "prod_1001" for p in results)

    # Exactly 1 DB query should have been made; remaining 29 hit the single-flight mutex
    db_queries_during_stampede = cache.stats["db_queries"] - initial_db_queries
    assert db_queries_during_stampede == 1, f"Expected 1 DB query during stampede, got {db_queries_during_stampede}"
    print(f"  -> 30 concurrent requests resulted in exactly {db_queries_during_stampede} database query.")
    print(f"  -> Remaining 29 requests safely waited and served from cache.")
    print("  -> PASSED: Thundering herd shield completely neutralized cache stampede.")

    print("\n" + "=" * 80)
    print(" ALL 6 REDIS & DISTRIBUTED LOCK INTEGRATION TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_integration_tests())
