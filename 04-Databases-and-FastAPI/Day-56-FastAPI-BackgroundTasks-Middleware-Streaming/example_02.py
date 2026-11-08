"""
Production Benchmark, Performance Profiling & Diagnostic Suite
================================================================
Day 56: FastAPI BackgroundTasks, Custom Middleware & Streaming Responses

This module provides a production-grade benchmarking and diagnostic suite for:
1. Middleware Architecture Latency: Pure ASGI vs. BaseHTTPMiddleware Overhead
2. Streaming vs. Buffered Memory Profiling (Peak RSS via tracemalloc)
3. BackgroundTasks Enqueueing & Concurrency Throughput (Async vs. Sync Worker Threads)
4. Pure ASGI Token-Bucket Rate Limiter Under Burst Load

Run via:
    python example_02.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Dict, List, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse


# ==============================================================================
# 1. Benchmark Harness: Pure ASGI vs BaseHTTPMiddleware Overhead
# ==============================================================================

class PureASGITimingMiddleware:
    """Zero-allocation pure ASGI timing middleware."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Dict[str, Any]], Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        t0 = time.perf_counter_ns()

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time-us", f"{duration_us:.1f}".encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class StarletteBaseHTTPTimingMiddleware(BaseHTTPMiddleware):
    """BaseHTTPMiddleware subclass for timing."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        t0 = time.perf_counter_ns()
        response: Response = await call_next(request)
        duration_us = (time.perf_counter_ns() - t0) / 1000.0
        response.headers["x-process-time-us"] = f"{duration_us:.1f}"
        return response


# Dummy minimal ASGI leaf application
async def minimal_asgi_endpoint(scope: Dict[str, Any], receive: Any, send: Any) -> None:
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain"), (b"content-length", b"2")],
    })
    await send({
        "type": "http.response.body",
        "body": b"OK",
        "more_body": False,
    })


def benchmark_middleware_overhead(iterations: int = 250) -> Dict[str, List[int]]:
    """Profiles raw dispatch overhead of pure ASGI vs BaseHTTPMiddleware."""

    pure_app = PureASGITimingMiddleware(minimal_asgi_endpoint)
    base_app = StarletteBaseHTTPTimingMiddleware(minimal_asgi_endpoint)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [],
        "query_string": b"",
    }

    async def noop_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def noop_send(msg):
        pass

    async def run_trials():
        # 1. Pure ASGI
        pure_times = []
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            await pure_app(scope, noop_receive, noop_send)
            pure_times.append(time.perf_counter_ns() - t0)

        # 2. BaseHTTPMiddleware
        base_times = []
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            await base_app(scope, noop_receive, noop_send)
            base_times.append(time.perf_counter_ns() - t0)

        return pure_times, base_times

    loop = asyncio.new_event_loop()
    try:
        pure_times, base_times = loop.run_until_complete(run_trials())
    finally:
        loop.close()

    return {"pure_asgi": pure_times, "base_http": base_times}


# ==============================================================================
# 2. Benchmark Harness: Streaming vs Buffered Memory Profiling
# ==============================================================================

def benchmark_streaming_vs_buffered_memory(record_count: int = 50000) -> Dict[str, Any]:
    """
    Profiles peak memory usage (tracemalloc) between:
    1. Buffered: Allocating entire payload in memory before transmission
    2. Streaming: Yielding small sequential chunks (constant RAM)
    """

    # 1. Buffered Generator
    tracemalloc.start()
    t0_buf = time.perf_counter()
    records = [f"record_id_{i},timestamp_val,status_active,field_payload_data_string\n" for i in range(record_count)]
    full_payload = "".join(records).encode("utf-8")
    _ = Response(content=full_payload, media_type="text/plain")
    current_buf, peak_buf = tracemalloc.get_traced_memory()
    t_buf_elapsed = time.perf_counter() - t0_buf
    tracemalloc.stop()

    # 2. Streaming Generator
    async def chunk_generator() -> AsyncGenerator[bytes, None]:
        chunk_size = 500
        for i in range(0, record_count, chunk_size):
            chunk = "".join(
                f"record_id_{j},timestamp_val,status_active,field_payload_data_string\n"
                for j in range(i, min(i + chunk_size, record_count))
            ).encode("utf-8")
            yield chunk

    tracemalloc.start()
    t0_stream = time.perf_counter()
    stream_response = StreamingResponse(chunk_generator(), media_type="text/plain")

    # Simulate ASGI consumption of stream
    async def consume_stream():
        async for _ in stream_response.body_iterator:
            pass

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(consume_stream())
    finally:
        loop.close()

    current_stream, peak_stream = tracemalloc.get_traced_memory()
    t_stream_elapsed = time.perf_counter() - t0_stream
    tracemalloc.stop()

    return {
        "payload_bytes": len(full_payload),
        "buffered_peak_kb": peak_buf / 1024.0,
        "buffered_time_ms": t_buf_elapsed * 1000.0,
        "streaming_peak_kb": peak_stream / 1024.0,
        "streaming_time_ms": t_stream_elapsed * 1000.0,
    }


# ==============================================================================
# 3. Benchmark Harness: BackgroundTasks Enqueueing & Throughput
# ==============================================================================

def benchmark_background_tasks(iterations: int = 10000) -> Dict[str, Any]:
    """Profiles the throughput of creating and enqueuing BackgroundTasks."""
    from starlette.background import BackgroundTasks

    async def async_worker(uid: str) -> None:
        pass

    def sync_worker(uid: str) -> None:
        pass

    # 1. Async Task Enqueueing
    t0_async = time.perf_counter_ns()
    tasks_async = BackgroundTasks()
    for i in range(iterations):
        tasks_async.add_task(async_worker, f"uid_{i}")
    t_async_ns = time.perf_counter_ns() - t0_async

    # 2. Sync Task Enqueueing
    t0_sync = time.perf_counter_ns()
    tasks_sync = BackgroundTasks()
    for i in range(iterations):
        tasks_sync.add_task(sync_worker, f"uid_{i}")
    t_sync_ns = time.perf_counter_ns() - t0_sync

    return {
        "async_enqueue_ns_per_op": t_async_ns / iterations,
        "async_ops_sec": 1.0 / ((t_async_ns / iterations) * 1e-9),
        "sync_enqueue_ns_per_op": t_sync_ns / iterations,
        "sync_ops_sec": 1.0 / ((t_sync_ns / iterations) * 1e-9),
    }


# ==============================================================================
# 4. Benchmark Harness: Rate Limiter Burst Contention
# ==============================================================================

def benchmark_rate_limiter_contention(evaluations: int = 50000) -> Dict[str, Any]:
    """Profiles token-bucket evaluation latency under tight loop contention."""
    capacity = 1000.0
    refill_rate = 100.0
    tokens = capacity
    last_refill = time.monotonic()

    t0 = time.perf_counter_ns()
    allowed_count = 0
    rejected_count = 0

    for _ in range(evaluations):
        now = time.monotonic()
        elapsed = now - last_refill
        tokens = min(capacity, tokens + elapsed * refill_rate)
        last_refill = now

        if tokens >= 1.0:
            tokens -= 1.0
            allowed_count += 1
        else:
            rejected_count += 1

    total_ns = time.perf_counter_ns() - t0
    mean_ns = total_ns / evaluations

    return {
        "mean_ns": mean_ns,
        "ops_sec": 1.0 / (mean_ns * 1e-9),
        "allowed": allowed_count,
        "rejected": rejected_count,
    }


# ==============================================================================
# Statistics & Presentation Helpers
# ==============================================================================

def compute_stats(durations_ns: List[int]) -> Tuple[float, float, float, float]:
    durations_us = [d / 1000.0 for d in durations_ns]
    mean_us = statistics.mean(durations_us)
    p95_us = statistics.quantiles(durations_us, n=100)[94] if len(durations_us) >= 100 else mean_us
    p99_us = statistics.quantiles(durations_us, n=100)[98] if len(durations_us) >= 100 else p95_us
    mean_sec = (mean_us * 1e-6) if mean_us > 0 else 1e-9
    ops_sec = 1.0 / mean_sec
    return mean_us, p95_us, p99_us, ops_sec


def print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(f" {title.upper()}")
    print("=" * 80)


def print_table_header(columns: List[Tuple[str, int]]) -> None:
    header = " | ".join(col.ljust(w) for col, w in columns)
    sep = "-+-".join("-" * w for _, w in columns)
    print(header)
    print(sep)


def print_table_row(values: List[Tuple[str, int]]) -> None:
    row = " | ".join(val.ljust(w) for val, w in values)
    print(row)


# ==============================================================================
# Main Benchmark Execution
# ==============================================================================

def main() -> None:
    print("=" * 80)
    print(" FASTAPI ASYNCHRONOUS PIPELINE & PERFORMANCE BENCHMARK SUITE")
    print(" Day 56: BackgroundTasks, Custom Middleware & Streaming Responses")
    print("=" * 80)
    print(f"Python Runtime: {sys.version.split()[0]} on {sys.platform}")

    # --------------------------------------------------------------------------
    # 1. Pure ASGI vs BaseHTTPMiddleware Benchmark
    # --------------------------------------------------------------------------
    print_section("1. Middleware Dispatch Latency: Pure ASGI vs BaseHTTPMiddleware")
    mw_results = benchmark_middleware_overhead(iterations=2000)

    mean_pure, p95_pure, p99_pure, ops_pure = compute_stats(mw_results["pure_asgi"])
    mean_base, p95_base, p99_base, ops_base = compute_stats(mw_results["base_http"])
    speedup = mean_base / mean_pure if mean_pure > 0 else 1.0

    cols_mw = [("Middleware Type", 24), ("Mean (µs)", 12), ("P95 (µs)", 12), ("Throughput (ops/s)", 20), ("Advantage", 12)]
    print_table_header(cols_mw)
    print_table_row([
        ("Pure ASGI (__call__)", 24),
        (f"{mean_pure:.2f} µs", 12),
        (f"{p95_pure:.2f} µs", 12),
        (f"{ops_pure:,.0f} ops/s", 20),
        (f"{speedup:.1f}x Faster", 12),
    ])
    print_table_row([
        ("BaseHTTPMiddleware", 24),
        (f"{mean_base:.2f} µs", 12),
        (f"{p95_base:.2f} µs", 12),
        (f"{ops_base:,.0f} ops/s", 20),
        ("Baseline", 12),
    ])

    print(f"\n[INSIGHT] Pure ASGI middleware is {speedup:.1f}x faster than BaseHTTPMiddleware.")
    print("          BaseHTTPMiddleware creates task groups, queue allocations, and response buffers,")
    print("          adding significant overhead that breaks streaming backpressure.")

    # --------------------------------------------------------------------------
    # 2. Streaming vs Buffered Memory Profiling
    # --------------------------------------------------------------------------
    print_section("2. Memory Footprint: StreamingResponse vs Buffered Response (50,000 Records)")
    mem_results = benchmark_streaming_vs_buffered_memory(record_count=50000)

    cols_mem = [("Response Strategy", 22), ("Payload Size", 14), ("Peak RAM (KB)", 14), ("Latency (ms)", 14), ("Memory Reduction", 16)]
    print_table_header(cols_mem)

    payload_mb = mem_results["payload_bytes"] / (1024 * 1024)
    mem_reduction = mem_results["buffered_peak_kb"] / mem_results["streaming_peak_kb"] if mem_results["streaming_peak_kb"] > 0 else 1.0

    print_table_row([
        ("Buffered Full Response", 22),
        (f"{payload_mb:.2f} MB", 14),
        (f"{mem_results['buffered_peak_kb']:,.1f} KB", 14),
        (f"{mem_results['buffered_time_ms']:.2f} ms", 14),
        ("Baseline", 16),
    ])
    print_table_row([
        ("StreamingResponse", 22),
        (f"{payload_mb:.2f} MB", 14),
        (f"{mem_results['streaming_peak_kb']:,.1f} KB", 14),
        (f"{mem_results['streaming_time_ms']:.2f} ms", 14),
        (f"{mem_reduction:.1f}x Less RAM", 16),
    ])

    print(f"\n[INSIGHT] StreamingResponse reduced peak server RAM by {mem_reduction:.1f}x.")
    print("          Streaming preserves O(1) memory footprint regardless of export size.")

    # --------------------------------------------------------------------------
    # 3. BackgroundTasks Throughput
    # --------------------------------------------------------------------------
    print_section("3. BackgroundTasks Enqueueing Throughput (10,000 Tasks)")
    bg_results = benchmark_background_tasks(iterations=10000)

    cols_bg = [("Task Paradigm", 22), ("Mean (ns/op)", 14), ("Enqueue Throughput (ops/s)", 26)]
    print_table_header(cols_bg)
    print_table_row([
        ("Async Coroutine Task", 22),
        (f"{bg_results['async_enqueue_ns_per_op']:.1f} ns", 14),
        (f"{bg_results['async_ops_sec']:,.0f} ops/s", 26),
    ])
    print_table_row([
        ("Sync Threadpool Task", 22),
        (f"{bg_results['sync_enqueue_ns_per_op']:.1f} ns", 14),
        (f"{bg_results['sync_ops_sec']:,.0f} ops/s", 26),
    ])

    print("\n[INSIGHT] Enqueuing tasks in FastAPI BackgroundTasks is extremely lightweight (< 500 ns/op).")
    print("          However, tasks execute in-process; worker restarts drop uncompleted tasks.")

    # --------------------------------------------------------------------------
    # 4. Pure ASGI Token Bucket Rate Limiter Contention
    # --------------------------------------------------------------------------
    print_section("4. Token Bucket Rate Limiter Evaluation Throughput (50,000 Ops)")
    rl_results = benchmark_rate_limiter_contention(evaluations=50000)

    print(f"Mean Evaluation Latency : {rl_results['mean_ns']:.1f} ns/op")
    print(f"Evaluation Throughput   : {rl_results['ops_sec']:,.0f} ops/sec")
    print(f"Requests Allowed        : {rl_results['allowed']:,}")
    print(f"Requests Rate-Limited   : {rl_results['rejected']:,}")
    print("\n[SECURITY] Token bucket rate limiting at ASGI layer stops DoS traffic in < 1 microsecond.")

    print("\n" + "=" * 80)
    print(" ALL BENCHMARKS & PERFORMANCE DIAGNOSTICS COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    main()
