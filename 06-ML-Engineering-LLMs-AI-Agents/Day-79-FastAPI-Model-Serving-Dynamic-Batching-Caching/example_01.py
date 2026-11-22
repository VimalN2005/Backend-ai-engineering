"""
Day 79: Production FastAPI Model Serving Microservice with Dynamic Batching & LRU Caching
Part 1: High-Throughput ASGI Microservice with Vectorized Batching and In-Memory Caching

This script implements an enterprise-grade model serving architecture:
1. Dynamic Batching Worker coalescing concurrent async requests into vectorized batches.
2. Two-Tier Canonical In-Memory LRU Feature Cache bypassing redundant model inference.
3. FastAPI application with Pydantic V2 validation, health probes, and metrics instrumentation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


# ---------------------------------------------------------------------------
# Pydantic V2 Request & Response Schemas
# ---------------------------------------------------------------------------
class PredictionRequest(BaseModel):
    """Input payload for real-time credit default risk prediction."""
    income: float = Field(..., gt=0.0, description="Annual income in USD")
    debt_ratio: float = Field(..., ge=0.0, le=1.0, description="Debt-to-income ratio")
    employment_years: float = Field(..., ge=0.0, le=60.0, description="Years in current employment")
    has_mortgage: bool = Field(..., description="Whether borrower has an active mortgage")

    def canonical_cache_key(self) -> str:
        """Generates deterministic SHA-256 hash for canonical feature payload."""
        features_repr = (
            round(self.income, 2),
            round(self.debt_ratio, 4),
            round(self.employment_years, 1),
            self.has_mortgage,
        )
        payload_bytes = json.dumps(features_repr).encode("utf-8")
        return hashlib.sha256(payload_bytes).hexdigest()

    def to_feature_vector(self) -> List[float]:
        return [self.income, self.debt_ratio, self.employment_years, 1.0 if self.has_mortgage else 0.0]


class PredictionResponse(BaseModel):
    """Prediction result with operational telemetry."""
    credit_risk_probability: float
    decision: str
    cached: bool
    batch_size: int
    latency_ms: float


class MetricsResponse(BaseModel):
    """Real-time operational serving metrics."""
    total_requests: int
    cache_hits: int
    cache_misses: int
    cache_hit_ratio_pct: float
    batches_processed: int
    total_batched_samples: int
    avg_batch_size: float


# ---------------------------------------------------------------------------
# Two-Tier In-Memory Canonical Feature Cache (LRU + TTL)
# ---------------------------------------------------------------------------
class TwoTierFeatureCache:
    """
    Thread-safe in-memory LRU cache storing recent feature prediction results.
    Bypasses expensive model evaluations for identical or duplicate incoming payloads.
    """

    def __init__(self, maxsize: int = 10_000, ttl_seconds: float = 300.0) -> None:
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[float, float]] = OrderedDict()  # key -> (prob, timestamp)
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[float]:
        """Retrieves cached prediction if present and not expired."""
        if key not in self._cache:
            self.misses += 1
            return None

        prob, timestamp = self._cache[key]
        if time.time() - timestamp > self.ttl_seconds:
            # Expired entry
            del self._cache[key]
            self.misses += 1
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self.hits += 1
        return prob

    def set(self, key: str, prob: float) -> None:
        """Stores prediction in LRU cache, evicting oldest entry if full."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (prob, time.time())
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)  # Evict least recently used

    def get_stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        ratio = (self.hits / total * 100.0) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio_pct": round(ratio, 2),
            "size": len(self._cache),
        }


# ---------------------------------------------------------------------------
# Dynamic Batching Worker Engine
# ---------------------------------------------------------------------------
@dataclass
class QueueItem:
    request: PredictionRequest
    cache_key: str
    future: asyncio.Future[float]
    enqueued_at: float


class DynamicBatchingEngine:
    """
    Asynchronous Dynamic Batching Engine.
    Coalesces individual client requests into vectorized batches up to MaxBatchSize
    or until MaxTimeout is reached, then evaluates the batch in a single pass.
    """

    def __init__(
        self,
        model: Any,
        cache: TwoTierFeatureCache,
        max_batch_size: int = 16,
        timeout_seconds: float = 0.005,  # 5 milliseconds
    ) -> None:
        self.model = model
        self.cache = cache
        self.max_batch_size = max_batch_size
        self.timeout_seconds = timeout_seconds

        self._queue: Optional[asyncio.Queue[QueueItem]] = None
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._running = False

        # Metrics
        self.total_requests = 0
        self.batches_processed = 0
        self.total_batched_samples = 0

    def start(self) -> None:
        """Starts background dynamic batching worker task with loop-bound queue."""
        self._running = True
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._batch_worker_loop())

    async def stop(self) -> None:
        """Stops background worker gracefully."""
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def submit(self, request: PredictionRequest) -> Tuple[float, bool, int]:
        """
        Enqueues request into batcher or returns cached value instantly.
        Returns: (probability, is_cached, batch_size)
        """
        cache_key = request.canonical_cache_key()
        cached_val = self.cache.get(cache_key)
        if cached_val is not None:
            return cached_val, True, 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Tuple[float, int]] = loop.create_future()
        item = QueueItem(
            request=request,
            cache_key=cache_key,
            future=future,
            enqueued_at=time.perf_counter(),
        )

        self.total_requests += 1
        if self._queue is None:
            self._queue = asyncio.Queue()
        await self._queue.put(item)
        prob, batch_size = await future
        return prob, False, batch_size

    async def _batch_worker_loop(self) -> None:
        """Continuously pulls items from queue and forms dynamic micro-batches."""
        while self._running:
            try:
                if self._queue is None:
                    await asyncio.sleep(0.005)
                    continue

                # Wait for the first item
                first_item = await self._queue.get()
                batch: List[QueueItem] = [first_item]
                deadline = time.perf_counter() + self.timeout_seconds

                # Coalesce additional items up to max_batch_size or deadline
                while len(batch) < self.max_batch_size:
                    if not self._queue.empty():
                        batch.append(self._queue.get_nowait())
                    else:
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(0.001)

                # Process coalesced batch
                await self._process_batch(batch)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] Error in dynamic batcher loop: {e}")

    async def _process_batch(self, batch: List[QueueItem]) -> None:
        """Executes vectorized model inference across coalesced batch."""
        if not batch:
            return

        batch_size = len(batch)
        self.batches_processed += 1
        self.total_batched_samples += batch_size

        try:
            # Build 2D NumPy feature matrix
            feature_matrix = np.array([item.request.to_feature_vector() for item in batch], dtype=np.float32)

            # Offload CPU-bound inference to threadpool to keep event loop 100% free
            loop = asyncio.get_running_loop()
            probabilities = await loop.run_in_executor(
                None, lambda: self.model.predict_proba(feature_matrix)[:, 1]
            )

            # Resolve futures and populate cache
            for i, item in enumerate(batch):
                prob = float(probabilities[i])
                self.cache.set(item.cache_key, prob)
                if not item.future.done():
                    item.future.set_result((prob, batch_size))

        except Exception as exc:
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(exc)


# ---------------------------------------------------------------------------
# Model Training & Lifespan Context
# ---------------------------------------------------------------------------
def train_mock_credit_model() -> Pipeline:
    """Trains a realistic credit risk model pipeline for testing."""
    np.random.seed(42)
    n = 2_000
    income = np.random.lognormal(10.5, 0.5, n)
    debt_ratio = np.random.uniform(0.05, 0.70, n)
    employment = np.random.uniform(0.0, 30.0, n)
    mortgage = np.random.choice([0.0, 1.0], size=n, p=[0.6, 0.4])

    X = np.column_stack([income, debt_ratio, employment, mortgage])
    logits = (debt_ratio * 4.0) - (income / 40000.0) - (employment * 0.1) + (mortgage * 0.5)
    probs = 1.0 / (1.0 + np.exp(-logits))
    y = (np.random.rand(n) < probs).astype(int)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", HistGradientBoostingClassifier(max_iter=30, random_state=42)),
    ])
    pipeline.fit(X, y)
    return pipeline


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages model loading, cache initialization, and batching background worker."""
    # 1. Load and warm up model
    model = train_mock_credit_model()
    # Warmup inference
    warmup_data = np.array([[60000.0, 0.35, 5.0, 1.0]], dtype=np.float32)
    for _ in range(5):
        _ = model.predict_proba(warmup_data)

    cache = TwoTierFeatureCache(maxsize=10_000, ttl_seconds=300.0)
    batcher = DynamicBatchingEngine(model=model, cache=cache, max_batch_size=16, timeout_seconds=0.005)
    batcher.start()

    app.state.model = model
    app.state.cache = cache
    app.state.batcher = batcher
    app.state.is_ready = True

    yield

    # Shutdown
    app.state.is_ready = False
    await batcher.stop()


# ---------------------------------------------------------------------------
# FastAPI Application Definition
# ---------------------------------------------------------------------------
app = FastAPI(
    title="High-Throughput ML Model Serving API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    """Liveness probe verifying service is healthy."""
    return {"status": "healthy"}


@app.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_probe() -> Dict[str, Any]:
    """Readiness probe verifying model is loaded and worker is active."""
    if not getattr(app.state, "is_ready", False):
        raise HTTPException(status_code=503, detail="Model is still initializing.")
    return {"status": "ready", "batcher_running": app.state.batcher._running}


@app.post("/v1/predict", response_model=PredictionResponse, status_code=status.HTTP_200_OK)
async def predict_credit_risk(request: PredictionRequest) -> PredictionResponse:
    """Real-time prediction endpoint with dynamic batching and LRU caching."""
    t0 = time.perf_counter()
    batcher: DynamicBatchingEngine = app.state.batcher

    prob, is_cached, batch_size = await batcher.submit(request)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    return PredictionResponse(
        credit_risk_probability=round(prob, 4),
        decision="HIGH_RISK" if prob >= 0.50 else "LOW_RISK",
        cached=is_cached,
        batch_size=batch_size,
        latency_ms=round(latency_ms, 2),
    )


@app.get("/metrics", response_model=MetricsResponse)
async def get_serving_metrics() -> MetricsResponse:
    """Returns serving metrics: cache hits/misses, batch sizes, throughput."""
    batcher: DynamicBatchingEngine = app.state.batcher
    cache: TwoTierFeatureCache = app.state.cache
    stats = cache.get_stats()

    avg_batch = (
        batcher.total_batched_samples / batcher.batches_processed
        if batcher.batches_processed > 0
        else 0.0
    )

    return MetricsResponse(
        total_requests=batcher.total_requests + cache.hits,
        cache_hits=cache.hits,
        cache_misses=cache.misses,
        cache_hit_ratio_pct=stats["hit_ratio_pct"],
        batches_processed=batcher.batches_processed,
        total_batched_samples=batcher.total_batched_samples,
        avg_batch_size=round(avg_batch, 2),
    )


# ---------------------------------------------------------------------------
# Self-Contained Test Client Simulation & Verification
# ---------------------------------------------------------------------------
async def run_simulation() -> None:
    print("================================================================================")
    print("DAY 79: FASTAPI MODEL SERVING WITH DYNAMIC BATCHING & LRU CACHING")
    print("================================================================================")

    # Use httpx.AsyncClient with lifespan support
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Test Health & Readiness
            resp_health = await client.get("/healthz")
            resp_ready = await client.get("/ready")
            assert resp_health.status_code == 200
            assert resp_ready.status_code == 200
            print("[+] Health and Readiness probes verified: HTTP 200 OK.")

            # 2. Test Concurrent Load with Dynamic Batching
            print("\n[*] Firing 50 concurrent client requests into /v1/predict...")
            requests_data = [
                {
                    "income": 50000.0 + (i * 1000.0),
                    "debt_ratio": min(0.10 + (i * 0.015), 0.95),
                    "employment_years": float(i % 15),
                    "has_mortgage": (i % 2 == 0),
                }
                for i in range(50)
            ]

            t_start = time.perf_counter()
            tasks = [client.post("/v1/predict", json=data) for data in requests_data]
            responses = await asyncio.gather(*tasks)
            total_elapsed_ms = (time.perf_counter() - t_start) * 1000.0

            assert all(r.status_code == 200 for r in responses)
            results = [r.json() for r in responses]

            max_batch = max(r["batch_size"] for r in results)
            print(f"[+] All 50 concurrent requests succeeded in {total_elapsed_ms:.2f} ms.")
            print(f"    Peak Dynamic Batch Size Achieved : {max_batch} samples coalesced!")

            # 3. Test In-Memory Feature Cache on Identical Requests
            print("\n[*] Firing duplicate requests to test Two-Tier In-Memory LRU Cache...")
            identical_payload = requests_data[0]
            # Send 5 identical requests
            cache_tasks = [client.post("/v1/predict", json=identical_payload) for _ in range(5)]
            cache_responses = await asyncio.gather(*cache_tasks)

            cached_results = [r.json() for r in cache_responses]
            cache_hits = sum(1 for r in cached_results if r["cached"])
            print(f"[+] Cache Test: {cache_hits} of 5 requests served directly from in-memory cache!")
            assert cache_hits >= 4, "Expected subsequent identical requests to be served from cache"

            # 4. Test Input Validation Error Handling (Pydantic V2)
            print("\n[*] Testing Input Schema Validation on invalid debt_ratio (1.5 > 1.0)...")
            bad_payload = {"income": 40000.0, "debt_ratio": 1.5, "employment_years": 3.0, "has_mortgage": False}
            resp_bad = await client.post("/v1/predict", json=bad_payload)
            assert resp_bad.status_code == 422
            print(f"[+] Validation Guardrail Passed: HTTP 422 Unprocessable Entity returned correctly.")

            # 5. Inspect Metrics
            metrics_resp = await client.get("/metrics")
            metrics = metrics_resp.json()
            print("\n--- Operational Serving Metrics ---")
            print(f"Total Requests Processed : {metrics['total_requests']}")
            print(f"Cache Hits / Misses      : {metrics['cache_hits']} / {metrics['cache_misses']}")
            print(f"Cache Hit Ratio          : {metrics['cache_hit_ratio_pct']}%")
            print(f"Batches Processed        : {metrics['batches_processed']}")
            print(f"Average Batch Size       : {metrics['avg_batch_size']}")

            assert metrics["total_requests"] >= 50
            assert metrics["batches_processed"] > 0
            assert max_batch > 1, "Expected dynamic batching to coalesce multiple requests"
            print("\n[+] Verification Passed: All FastAPI model serving assertions satisfied.")
            print("================================================================================")


def main() -> None:
    asyncio.run(run_simulation())


if __name__ == "__main__":
    main()
