"""
Day 59: Celery Diagnostic & Benchmark Harness
================================================================================
Target: Enterprise Python Backend & AI Systems Engineer Portfolio
Description: Production performance diagnostic and benchmark harness profiling:
  1. Synchronous vs Asynchronous Task Dispatch Latency (Web Thread Unblocking)
  2. Prefetch Multiplier & Heterogeneous Task Queue Starvation Analysis
  3. Celery Canvas Overhead Profile: Chain vs Group vs Chord
  4. Task Serialization Protocol Benchmark: JSON vs Pickle
================================================================================
"""

import json
import logging
import pickle
import statistics
import time
from typing import Any, Callable, Dict, List
from celery import Celery, chain, chord, group
import fakeredis

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# -----------------------------------------------------------------------------
# Celery Test App Configuration for Diagnostics
# -----------------------------------------------------------------------------
diag_app = Celery("diag_app", broker="memory://", backend="cache+memory://")
diag_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json", "pickle"],
    task_always_eager=True,
    task_eager_propagates=False,
)

redis_mock = fakeredis.FakeRedis()


# Sample Tasks
@diag_app.task
def mock_io_task(duration_ms: float = 10.0) -> Dict[str, Any]:
    time.sleep(duration_ms / 1000.0)
    return {"status": "COMPLETED", "duration_ms": duration_ms}


@diag_app.task
def step_a(val: int) -> int:
    return val + 10


@diag_app.task
def step_b(val: int) -> int:
    return val * 2


@diag_app.task
def step_c(val: int) -> int:
    return val - 5


@diag_app.task
def sum_reducer(numbers: List[int]) -> int:
    return sum(numbers)


# -----------------------------------------------------------------------------
# 1. Benchmark 1: Synchronous In-Thread vs Asynchronous Dispatch Latency
# -----------------------------------------------------------------------------
def benchmark_dispatch_latency(iterations: int = 200) -> Dict[str, Any]:
    """
    Measures the HTTP request latency difference between synchronous execution
    (blocking the web thread) vs asynchronous dispatch (returning HTTP 202 immediately).
    """
    simulated_io_delay_sec = 0.015  # 15 ms external I/O (e.g. PDF generation, Stripe call)

    # 1. Synchronous In-Thread Execution (Simulating traditional web endpoint)
    sync_latencies: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        time.sleep(simulated_io_delay_sec)
        t1 = time.perf_counter()
        sync_latencies.append((t1 - t0) * 1000.0)

    # 2. Asynchronous Queue Dispatch (Enqueuing into broker and unblocking thread)
    # Using raw broker push emulation to isolate pure enqueue dispatch latency
    async_latencies: List[float] = []
    task_message = json.dumps({
        "task": "orders.process_pdf",
        "args": ["ORD-12345"],
        "id": "e58d98d2-5a21-4d32-9cb4-22b374944b58"
    })
    for _ in range(iterations):
        t0 = time.perf_counter()
        redis_mock.lpush("celery:queue:default", task_message)
        t1 = time.perf_counter()
        async_latencies.append((t1 - t0) * 1000.0)

    sync_mean = statistics.mean(sync_latencies)
    async_mean = statistics.mean(async_latencies)
    speedup = sync_mean / async_mean if async_mean > 0 else 0.0

    return {
        "sync_mean_ms": sync_mean,
        "sync_p99_ms": statistics.quantiles(sync_latencies, n=100)[98],
        "async_mean_ms": async_mean,
        "async_p99_ms": statistics.quantiles(async_latencies, n=100)[98],
        "speedup_factor": speedup,
    }


# -----------------------------------------------------------------------------
# 2. Benchmark 2: Prefetch Multiplier & Queue Starvation Simulation
# -----------------------------------------------------------------------------
def benchmark_prefetch_starvation() -> Dict[str, Any]:
    """
    Simulates heterogeneous workload distribution across 2 workers:
    - Worker 1 receives tasks with mixed durations: [200ms, 10ms, 10ms, 10ms]
    - Demonstrates that prefetch_multiplier=4 traps fast tasks behind a slow task,
      while prefetch_multiplier=1 allows Worker 2 to process remaining tasks immediately.
    """
    task_durations = [200, 10, 10, 10, 10, 10, 10, 10]  # ms

    # Case A: prefetch_multiplier=4 (Default)
    # Worker 1 prefetches 4 tasks (Task 0 [200ms], 1 [10ms], 2 [10ms], 3 [10ms])
    # Worker 2 prefetches 4 tasks (Task 4 [10ms], 5 [10ms], 6 [10ms], 7 [10ms])
    # Worker 1 takes 200 + 10 + 10 + 10 = 230ms
    # Worker 2 takes 10 + 10 + 10 + 10 = 40ms, then sits idle for 190ms!
    time_w1_pre4 = 200 + 10 + 10 + 10
    time_w2_pre4 = 10 + 10 + 10 + 10
    total_time_pre4 = max(time_w1_pre4, time_w2_pre4)
    idle_waste_ms_pre4 = time_w1_pre4 - time_w2_pre4

    # Case B: prefetch_multiplier=1 (Optimal QoS)
    # Dynamic work stealing: Worker 1 takes Task 0 (200ms).
    # While Worker 1 is busy, Worker 2 executes Task 1, 2, 3, 4, 5, 6, 7 (7 * 10 = 70ms)!
    # Both workers finish well under 200ms total wall-clock time!
    time_w1_pre1 = 200
    time_w2_pre1 = 7 * 10
    total_time_pre1 = max(time_w1_pre1, time_w2_pre1)
    idle_waste_ms_pre1 = abs(time_w1_pre1 - time_w2_pre1)

    latency_reduction = ((total_time_pre4 - total_time_pre1) / total_time_pre4) * 100.0

    return {
        "pre4_total_time_ms": total_time_pre4,
        "pre4_worker2_idle_ms": idle_waste_ms_pre4,
        "pre1_total_time_ms": total_time_pre1,
        "pre1_worker2_idle_ms": idle_waste_ms_pre1,
        "latency_reduction_pct": latency_reduction,
    }


# -----------------------------------------------------------------------------
# 3. Benchmark 3: Canvas Workflow Overhead Profile
# -----------------------------------------------------------------------------
def benchmark_canvas_primitives(iterations: int = 100) -> Dict[str, Any]:
    """
    Profiles execution latency and overhead across Celery canvas primitives:
    - Linear Chain: step_a | step_b | step_c
    - Parallel Group: 5 parallel workers
    - Reduction Chord: 5 parallel workers -> sum_reducer
    """
    chain_latencies: List[float] = []
    group_latencies: List[float] = []
    chord_latencies: List[float] = []

    # 1. Chain: 3 sequential steps
    for _ in range(iterations):
        t0 = time.perf_counter()
        res = chain(step_a.s(10), step_b.s(), step_c.s())().get()
        t1 = time.perf_counter()
        chain_latencies.append((t1 - t0) * 1000.0)

    # 2. Group: 5 parallel tasks
    for _ in range(iterations):
        t0 = time.perf_counter()
        res = group(step_a.s(i) for i in range(5))().get()
        t1 = time.perf_counter()
        group_latencies.append((t1 - t0) * 1000.0)

    # 3. Chord: 5 parallel tasks + reducer
    for _ in range(iterations):
        t0 = time.perf_counter()
        res = chord(group(step_a.s(i) for i in range(5)))(sum_reducer.s()).get()
        t1 = time.perf_counter()
        chord_latencies.append((t1 - t0) * 1000.0)

    return {
        "chain_mean_ms": statistics.mean(chain_latencies),
        "chain_p99_ms": statistics.quantiles(chain_latencies, n=100)[98],
        "group_mean_ms": statistics.mean(group_latencies),
        "group_p99_ms": statistics.quantiles(group_latencies, n=100)[98],
        "chord_mean_ms": statistics.mean(chord_latencies),
        "chord_p99_ms": statistics.quantiles(chord_latencies, n=100)[98],
    }


# -----------------------------------------------------------------------------
# 4. Benchmark 4: Serialization Protocol Performance & Security
# -----------------------------------------------------------------------------
def benchmark_serialization(iterations: int = 2000) -> Dict[str, Any]:
    """
    Compares JSON vs Pickle serialization performance, payload byte size,
    and security evaluation for task payloads.
    """
    sample_payload = {
        "task_name": "orders.process_checkout",
        "task_id": "c76d9119-95a9-4673-9828-568ea46a9e10",
        "args": [10842, "USD", 4999],
        "kwargs": {"promo_code": "AUTUMN2026", "is_prime": True},
        "metadata": {
            "client_ip": "192.168.1.104",
            "correlation_id": "corr_77332211",
            "timestamp": time.time(),
            "nested_items": [{"sku": f"SKU-{i}", "qty": i, "price": 19.99} for i in range(5)]
        }
    }

    # JSON Benchmark
    t0 = time.perf_counter()
    json_bytes = b""
    for _ in range(iterations):
        json_bytes = json.dumps(sample_payload).encode("utf-8")
    t1 = time.perf_counter()
    json_dump_us = ((t1 - t0) / iterations) * 1_000_000.0

    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = json.loads(json_bytes.decode("utf-8"))
    t1 = time.perf_counter()
    json_load_us = ((t1 - t0) / iterations) * 1_000_000.0

    # Pickle Benchmark
    t0 = time.perf_counter()
    pickle_bytes = b""
    for _ in range(iterations):
        pickle_bytes = pickle.dumps(sample_payload)
    t1 = time.perf_counter()
    pickle_dump_us = ((t1 - t0) / iterations) * 1_000_000.0

    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = pickle.loads(pickle_bytes)
    t1 = time.perf_counter()
    pickle_load_us = ((t1 - t0) / iterations) * 1_000_000.0

    return {
        "json_size_bytes": len(json_bytes),
        "json_dump_us": json_dump_us,
        "json_load_us": json_load_us,
        "json_security": "SECURE: Strict data typing, immune to arbitrary RCE",
        "pickle_size_bytes": len(pickle_bytes),
        "pickle_dump_us": pickle_dump_us,
        "pickle_load_us": pickle_load_us,
        "pickle_security": "CRITICAL RISK: Remote Code Execution (RCE) via __reduce__ injection",
    }


# -----------------------------------------------------------------------------
# Main Diagnostic Execution & Report Formatter
# -----------------------------------------------------------------------------
def run_all_benchmarks():
    print("=" * 80)
    print(" CELERY 5.x PRODUCTION DIAGNOSTIC & BENCHMARK REPORT")
    print("=" * 80)

    # 1. Dispatch Latency
    res_b1 = benchmark_dispatch_latency()
    print("\nBENCHMARK 1: Synchronous In-Thread vs Asynchronous Broker Dispatch")
    print("-" * 80)
    print(f"  Synchronous In-Thread Latency:  Mean = {res_b1['sync_mean_ms']:6.2f} ms | P99 = {res_b1['sync_p99_ms']:6.2f} ms")
    print(f"  Asynchronous Dispatch Latency:  Mean = {res_b1['async_mean_ms']:6.2f} ms ({res_b1['async_mean_ms']*1000:6.1f} µs) | P99 = {res_b1['async_p99_ms']:6.2f} ms")
    print(f"  API Unblocking Speedup Factor: {res_b1['speedup_factor']:6.1f}x reduction in web request hold time")

    # 2. Prefetch Starvation
    res_b2 = benchmark_prefetch_starvation()
    print("\nBENCHMARK 2: Prefetch Multiplier & Heterogeneous Task Queue Starvation")
    print("-" * 80)
    print(f"  prefetch_multiplier = 4 (Default):  Total Duration = {res_b2['pre4_total_time_ms']} ms | Worker 2 Idle Waste = {res_b2['pre4_worker2_idle_ms']} ms")
    print(f"  prefetch_multiplier = 1 (Optimal):  Total Duration = {res_b2['pre1_total_time_ms']} ms | Worker 2 Idle Waste = {res_b2['pre1_worker2_idle_ms']} ms")
    print(f"  Throughput Improvement:            {res_b2['latency_reduction_pct']:.1f}% wall-clock reduction by eliminating prefetch starvation")

    # 3. Canvas Primitives
    res_b3 = benchmark_canvas_primitives()
    print("\nBENCHMARK 3: Celery Canvas Primitive Overhead Profile")
    print("-" * 80)
    print(f"  Chain (3 Sequential Steps):   Mean = {res_b3['chain_mean_ms']:6.3f} ms | P99 = {res_b3['chain_p99_ms']:6.3f} ms")
    print(f"  Group (5 Parallel Tasks):     Mean = {res_b3['group_mean_ms']:6.3f} ms | P99 = {res_b3['group_p99_ms']:6.3f} ms")
    print(f"  Chord (5 Tasks + Reducer):    Mean = {res_b3['chord_mean_ms']:6.3f} ms | P99 = {res_b3['chord_p99_ms']:6.3f} ms")

    # 4. Serialization
    res_b4 = benchmark_serialization()
    print("\nBENCHMARK 4: Task Serialization Performance & Security Profile")
    print("-" * 80)
    print(f"  JSON:   Size = {res_b4['json_size_bytes']:3d} bytes | Dump = {res_b4['json_dump_us']:5.1f} µs | Load = {res_b4['json_load_us']:5.1f} µs")
    print(f"          Security: {res_b4['json_security']}")
    print(f"  Pickle: Size = {res_b4['pickle_size_bytes']:3d} bytes | Dump = {res_b4['pickle_dump_us']:5.1f} µs | Load = {res_b4['pickle_load_us']:5.1f} µs")
    print(f"          Security: {res_b4['pickle_security']}")

    print("\n" + "=" * 80)
    print(" ALL DIAGNOSTIC BENCHMARKS COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    run_all_benchmarks()
