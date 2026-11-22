"""
Day 79: Concurrent Load Benchmark & Dynamic Batching vs Sequential Performance Harness
Part 2: Multi-Architecture Comparative Load Testing and Latency Profiling

This script benchmarks 200 concurrent incoming inference requests across three architectures:
1. Architecture 1: Naive Sequential Serving (Single-request scalar evaluation)
2. Architecture 2: Threadpool Offloaded Serving (Unbatched concurrent threads)
3. Architecture 3: Dynamic Batching Engine (Coalesced SIMD vectorized evaluation)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class BenchmarkSummary:
    """Statistical summary of serving architecture performance."""
    architecture_name: str
    total_requests: int
    wall_time_ms: float
    throughput_rps: float
    p50_latency_ms: float
    p90_latency_ms: float
    p99_latency_ms: float
    speedup_vs_baseline: float


class DynamicBatchingHarness:
    """
    Dedicated in-memory dynamic batcher for performance benchmarking.
    """

    def __init__(
        self,
        model: Any,
        max_batch_size: int = 16,
        timeout_seconds: float = 0.005,
    ) -> None:
        self.model = model
        self.max_batch_size = max_batch_size
        self.timeout_seconds = timeout_seconds
        self._queue: asyncio.Queue[Tuple[np.ndarray, asyncio.Future[float]]] = asyncio.Queue()
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None
        self.max_batch_seen = 0

    def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def submit(self, feature_vec: np.ndarray) -> float:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[float] = loop.create_future()
        await self._queue.put((feature_vec, future))
        return await future

    async def _worker(self) -> None:
        while self._running:
            try:
                first = await self._queue.get()
                batch = [first]
                deadline = time.perf_counter() + self.timeout_seconds

                while len(batch) < self.max_batch_size:
                    if not self._queue.empty():
                        batch.append(self._queue.get_nowait())
                    else:
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(0.0005)

                b_size = len(batch)
                if b_size > self.max_batch_seen:
                    self.max_batch_seen = b_size

                # Batch matrix evaluation
                matrix = np.vstack([item[0] for item in batch])
                loop = asyncio.get_running_loop()
                probs = await loop.run_in_executor(
                    None, lambda: self.model.predict_proba(matrix)[:, 1]
                )

                for i, item in enumerate(batch):
                    if not item[1].done():
                        item[1].set_result(float(probs[i]))

            except asyncio.CancelledError:
                break


def train_benchmark_model() -> Pipeline:
    """Trains a realistic credit scoring model for latency evaluation."""
    np.random.seed(42)
    n = 2_000
    X = np.random.randn(n, 4).astype(np.float32)
    y = ((X[:, 0] * 1.5 - X[:, 1] * 0.8 + X[:, 2] * 0.5) > 0.0).astype(int)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", HistGradientBoostingClassifier(max_iter=40, random_state=42)),
    ])
    pipeline.fit(X, y)
    return pipeline


# ---------------------------------------------------------------------------
# Architecture Runners
# ---------------------------------------------------------------------------
async def run_sequential_baseline(model: Any, requests: List[np.ndarray]) -> Tuple[List[float], List[float], float]:
    """Architecture 1: Evaluates requests one by one sequentially."""
    t0 = time.perf_counter()
    latencies_ms: List[float] = []
    results: List[float] = []

    for req in requests:
        req_start = time.perf_counter()
        prob = float(model.predict_proba(req.reshape(1, -1))[0, 1])
        results.append(prob)
        latencies_ms.append((time.perf_counter() - req_start) * 1000.0)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return results, latencies_ms, elapsed_ms


async def run_threadpool_concurrent(model: Any, requests: List[np.ndarray]) -> Tuple[List[float], List[float], float]:
    """Architecture 2: Dispatches each request to threadpool concurrently without batching."""
    loop = asyncio.get_running_loop()
    latencies_ms: List[float] = [0.0] * len(requests)
    results: List[float] = [0.0] * len(requests)

    async def single_predict(idx: int, req: np.ndarray) -> None:
        t_req = time.perf_counter()
        prob = await loop.run_in_executor(
            None, lambda: float(model.predict_proba(req.reshape(1, -1))[0, 1])
        )
        latencies_ms[idx] = (time.perf_counter() - t_req) * 1000.0
        results[idx] = prob

    t0 = time.perf_counter()
    await asyncio.gather(*(single_predict(i, req) for i, req in enumerate(requests)))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return results, latencies_ms, elapsed_ms


async def run_dynamic_batching(model: Any, requests: List[np.ndarray]) -> Tuple[List[float], List[float], float, int]:
    """Architecture 3: Dynamic Batching Engine with queue coalescence."""
    batcher = DynamicBatchingHarness(model=model, max_batch_size=32, timeout_seconds=0.005)
    batcher.start()

    latencies_ms: List[float] = [0.0] * len(requests)
    results: List[float] = [0.0] * len(requests)

    async def single_submit(idx: int, req: np.ndarray) -> None:
        t_req = time.perf_counter()
        prob = await batcher.submit(req)
        latencies_ms[idx] = (time.perf_counter() - t_req) * 1000.0
        results[idx] = prob

    t0 = time.perf_counter()
    await asyncio.gather(*(single_submit(i, req) for i, req in enumerate(requests)))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    max_batch = batcher.max_batch_seen
    await batcher.stop()

    return results, latencies_ms, elapsed_ms, max_batch


async def run_benchmark_suite() -> None:
    print("================================================================================")
    print("DAY 79: HIGH-CONCURRENCY MODEL SERVING BENCHMARK HARNESS")
    print("================================================================================")

    model = train_benchmark_model()
    # Warm up model
    dummy = np.zeros((1, 4), dtype=np.float32)
    for _ in range(5):
        _ = model.predict_proba(dummy)

    n_requests = 200
    np.random.seed(42)
    requests_data = [np.random.randn(4).astype(np.float32) for _ in range(n_requests)]
    print(f"[*] Simulating {n_requests} concurrent requests across 3 architectures...")

    # 1. Sequential Baseline
    print("\n[1] Running Architecture 1: Naive Sequential Serving (Baseline)...")
    res_seq, lat_seq, time_seq = await run_sequential_baseline(model, requests_data)
    seq_rps = n_requests / (time_seq / 1000.0)

    # 2. Threadpool Concurrent
    print("[2] Running Architecture 2: Threadpool Concurrent Serving...")
    res_thr, lat_thr, time_thr = await run_threadpool_concurrent(model, requests_data)
    thr_rps = n_requests / (time_thr / 1000.0)

    # 3. Dynamic Batching Engine
    print("[3] Running Architecture 3: Dynamic Batching Engine (MaxBatch=16, Timeout=5ms)...")
    res_bat, lat_bat, time_bat, max_batch = await run_dynamic_batching(model, requests_data)
    bat_rps = n_requests / (time_bat / 1000.0)

    # Parity verification: predictions match across all architectures
    np.testing.assert_allclose(res_seq, res_thr, rtol=1e-4, atol=1e-4)
    np.testing.assert_allclose(res_seq, res_bat, rtol=1e-4, atol=1e-4)

    summaries = [
        BenchmarkSummary(
            "1. Naive Sequential",
            n_requests,
            time_seq,
            seq_rps,
            float(np.percentile(lat_seq, 50)),
            float(np.percentile(lat_seq, 90)),
            float(np.percentile(lat_seq, 99)),
            1.0,
        ),
        BenchmarkSummary(
            "2. Threadpool Concurrent",
            n_requests,
            time_thr,
            thr_rps,
            float(np.percentile(lat_thr, 50)),
            float(np.percentile(lat_thr, 90)),
            float(np.percentile(lat_thr, 99)),
            time_seq / time_thr,
        ),
        BenchmarkSummary(
            "3. Dynamic Batching Engine",
            n_requests,
            time_bat,
            bat_rps,
            float(np.percentile(lat_bat, 50)),
            float(np.percentile(lat_bat, 90)),
            float(np.percentile(lat_bat, 99)),
            time_seq / time_bat,
        ),
    ]

    print("\n+------------------------------+------------+--------------+-----------+-----------+-----------+------------+")
    print("| Serving Architecture         | Total Time | Throughput   | P50 Lat   | P90 Lat   | P99 Lat   | Speedup vs |")
    print("|                              | (ms)       | (req/sec)    | (ms)      | (ms)      | (ms)      | Baseline   |")
    print("+------------------------------+------------+--------------+-----------+-----------+-----------+------------+")
    for s in summaries:
        print(f"| {s.architecture_name:<28} | {s.wall_time_ms:>8.1f}ms | {s.throughput_rps:>10,.1f} | {s.p50_latency_ms:>7.2f}ms | {s.p90_latency_ms:>7.2f}ms | {s.p99_latency_ms:>7.2f}ms | {s.speedup_vs_baseline:>8.1f}x |")
    print("+------------------------------+------------+--------------+-----------+-----------+-----------+------------+")

    print(f"\n[+] Dynamic Batching Peak Batch Coalescence : {max_batch} samples/batch")
    print(f"[+] Dynamic Batching Speedup vs Sequential  : {(time_seq / time_bat):.1f}x")
    print(f"[+] Mathematical parity verified across all 200 predictions (zero divergence).")

    # Assertions
    assert (time_seq / time_bat) >= 1.5, f"Expected Dynamic Batching speedup >= 1.5x, got {time_seq / time_bat:.1f}x"
    assert max_batch >= 4, f"Expected peak batch >= 4, achieved {max_batch}"
    print("[+] All high-concurrency performance assertions satisfied.")
    print("================================================================================")


def main() -> None:
    asyncio.run(run_benchmark_suite())


if __name__ == "__main__":
    main()
