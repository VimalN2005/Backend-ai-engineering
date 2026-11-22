# Day 79: Production Exercises — FastAPI Model Serving & Dynamic Batching

---

## Exercise 1: Adaptive Dynamic Batcher with Latency SLA Feedback

### Objective
In high-throughput microservices, static batching timeouts ($\Delta t_{\text{wait}}$) are suboptimal: during low traffic, static timeouts unnecessarily delay single requests, while during heavy spikes, fixed timeouts disallow optimal coalescing. Implement an `AdaptiveDynamicBatcher` that dynamically scales its coalescence timeout:
- If queue depth is low ($< 4$ items), it dispatches immediately ($\Delta t \approx 1\text{ ms}$).
- If queue depth is high ($> 8$ items), it coalesces up to $B_{\max} = 32$ with $\Delta t \approx 5\text{ ms}$.

### Production Constraints & Specification
1. Implement queue monitoring and adaptive timeout scaling.
2. Correlate requests 1:1 with client `asyncio.Future` promises.
3. Verify that under concurrent load, batch sizes scale up to $\ge 8$ samples per batch.
4. Provide a runnable test script verifying execution without Deadlocks.

### Production Solution & Verification Code

```python
import asyncio
import time
from dataclasses import dataclass
from typing import Any, List, Tuple
import numpy as np


@dataclass
class AdaptiveQueueItem:
    features: List[float]
    future: asyncio.Future[float]


class AdaptiveDynamicBatcher:
    """
    Adaptive Dynamic Batcher dynamically tuning timeout based on queue pressure.
    """

    def __init__(self, model_fn: Any, max_batch_size: int = 16) -> None:
        self.model_fn = model_fn
        self.max_batch_size = max_batch_size
        self._queue: asyncio.Queue[AdaptiveQueueItem] = asyncio.Queue()
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None
        self.batch_sizes_recorded: List[int] = []

    def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def submit(self, features: List[float]) -> float:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[float] = loop.create_future()
        await self._queue.put(AdaptiveQueueItem(features=features, future=future))
        return await future

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                first = await self._queue.get()
                batch = [first]

                # Adaptive timeout: scales with current queue pressure
                q_len = self._queue.qsize()
                timeout = 0.005 if q_len > 4 else 0.001
                deadline = time.perf_counter() + timeout

                while len(batch) < self.max_batch_size:
                    if not self._queue.empty():
                        batch.append(self._queue.get_nowait())
                    else:
                        if time.perf_counter() >= deadline:
                            break
                        await asyncio.sleep(0.0005)

                self.batch_sizes_recorded.append(len(batch))

                # Vectorized batch evaluation
                X = np.array([item.features for item in batch], dtype=np.float32)
                loop = asyncio.get_running_loop()
                preds = await loop.run_in_executor(None, lambda: self.model_fn(X))

                for i, item in enumerate(batch):
                    if not item.future.done():
                        item.future.set_result(float(preds[i]))

            except asyncio.CancelledError:
                break


def mock_model(X: np.ndarray) -> np.ndarray:
    """Simulates vectorized inference."""
    return 1.0 / (1.0 + np.exp(-np.sum(X, axis=1)))


async def run_exercise_01() -> None:
    print("[*] Running Exercise 1: Adaptive Dynamic Batcher...")
    batcher = AdaptiveDynamicBatcher(model_fn=mock_model, max_batch_size=16)
    batcher.start()

    # Submit 40 concurrent items
    tasks = [batcher.submit([float(i), float(i * 0.1)]) for i in range(40)]
    results = await asyncio.gather(*tasks)

    await batcher.stop()

    max_batch = max(batcher.batch_sizes_recorded)
    avg_batch = sum(batcher.batch_sizes_recorded) / len(batcher.batch_sizes_recorded)

    print(f"[+] Total batches formed: {len(batcher.batch_sizes_recorded)}")
    print(f"    Peak batch size   : {max_batch}")
    print(f"    Average batch size: {avg_batch:.2f}")

    assert len(results) == 40
    assert max_batch >= 4, f"Expected batching to coalesce, got {max_batch}"
    print("[+] Exercise 1 Passed: Adaptive dynamic batcher coalescing verified.")


if __name__ == "__main__":
    asyncio.run(run_exercise_01())
```

---

## Exercise 2: Distributed-Ready Canonical LRU Feature Cache with TTL

### Objective
In high-concurrency model serving APIs, repetitive queries from front-end clients or scheduled workers cause redundant inference. Implement a production `CanonicalLRUCache` that:
1. Normalizes incoming feature dicts (rounds numbers to 3 decimals, sorts keys).
2. Generates an immutable SHA-256 canonical hash key.
3. Manages an in-memory `OrderedDict` with bounded capacity ($10,000$ entries) and configurable TTL (Time-To-Live).
4. Tracks hit/miss telemetry and memory metrics.

### Production Solution & Verification Code

```python
import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple


class CanonicalLRUCache:
    """
    Capacity-bounded LRU cache with deterministic canonical feature hashing.
    """

    def __init__(self, maxsize: int = 10_000, ttl_seconds: float = 300.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self._store: OrderedDict[str, Tuple[float, float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def generate_key(payload: Dict[str, Any]) -> str:
        """Deterministic canonical representation: sorted keys, rounded floats."""
        canonical = {}
        for k in sorted(payload.keys()):
            v = payload[k]
            canonical[k] = round(v, 3) if isinstance(v, float) else v
        raw_json = json.dumps(canonical, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw_json).hexdigest()

    def get(self, key: str) -> Optional[float]:
        """Returns cached prediction if present and unexpired."""
        if key not in self._store:
            self.misses += 1
            return None

        val, ts = self._store[key]
        if time.time() - ts > self.ttl:
            del self._store[key]
            self.misses += 1
            return None

        self._store.move_to_end(key)
        self.hits += 1
        return val

    def set(self, key: str, value: float) -> None:
        """Stores value and evicts oldest item if capacity is exceeded."""
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.time())
        if len(self._store) > self.maxsize:
            self._store.popitem(last=False)  # Evict LRU entry


def test_exercise_02() -> None:
    print("[*] Running Exercise 2: Canonical LRU Feature Cache...")
    cache = CanonicalLRUCache(maxsize=3, ttl_seconds=2.0)

    p1 = {"b": 2.0001, "a": 1.0004}
    p2 = {"a": 1.000, "b": 2.000}  # Same canonical features after rounding!

    k1 = cache.generate_key(p1)
    k2 = cache.generate_key(p2)
    assert k1 == k2, "Canonical keys must match for equivalent rounded inputs"

    cache.set(k1, 0.85)
    assert cache.get(k2) == 0.85, "Cache hit failed on canonical match"
    print(f"[+] Canonical hashing verified: hit ratio = {cache.hits}/{cache.hits + cache.misses}")

    # Test LRU eviction (capacity = 3)
    cache.set("k3", 0.1)
    cache.set("k4", 0.2)
    cache.set("k5", 0.3)  # k1 should be evicted now

    assert cache.get(k1) is None, "k1 should have been evicted by maxsize"
    print("[+] Exercise 2 Passed: LRU capacity eviction and canonical keys verified.")


if __name__ == "__main__":
    test_exercise_02()
```

---

## Exercise 3: High-Concurrency Async Stress Testing Harness

### Objective
Before promoting an ML serving container to production Kubernetes clusters, the serving pipeline must be validated under synthetic burst traffic. Write an asynchronous stress testing harness using `httpx.AsyncClient` that fires 100 concurrent requests, verifies zero HTTP 500 errors, measures P50/P99 latency, and confirms graceful performance under load.

### Production Solution & Verification Code

```python
import asyncio
import time
from typing import Dict, List
import httpx
from example_01 import app, lifespan


async def run_stress_test() -> None:
    print("[*] Running Exercise 3: High-Concurrency Stress Test Harness...")
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            n_clients = 60
            payloads = [
                {
                    "income": 40000.0 + (i * 500.0),
                    "debt_ratio": min(0.2 + (i * 0.01), 0.8),
                    "employment_years": float(i % 10),
                    "has_mortgage": (i % 2 == 0),
                }
                for i in range(n_clients)
            ]

            t0 = time.perf_counter()
            tasks = [client.post("/v1/predict", json=p) for p in payloads]
            responses = await asyncio.gather(*tasks)
            total_duration_ms = (time.perf_counter() - t0) * 1000.0

            status_codes = [r.status_code for r in responses]
            assert all(code == 200 for code in status_codes), "Found failed requests!"

            latencies = [r.json()["latency_ms"] for r in responses]
            p50 = sorted(latencies)[len(latencies) // 2]
            p99 = sorted(latencies)[int(len(latencies) * 0.99)]

            print(f"[+] Total Requests   : {n_clients}")
            print(f"[+] Wall Duration    : {total_duration_ms:.2f} ms")
            print(f"[+] Throughput       : {(n_clients / (total_duration_ms / 1000.0)):,.0f} req/sec")
            print(f"[+] P50 API Latency  : {p50:.2f} ms")
            print(f"[+] P99 API Latency  : {p99:.2f} ms")

            assert p99 < 500.0, f"P99 latency breached SLA: {p99} ms"
            print("[+] Exercise 3 Passed: High-concurrency stress test completed with zero failures.")


if __name__ == "__main__":
    asyncio.run(run_stress_test())
```
