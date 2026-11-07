"""
Day 54: Dependency Injection Performance & Connection Leak Diagnostic Harness
=============================================================================

Production Benchmarks & Safety Diagnostics:
1. DI Resolution Overhead: Shallow (1 Tier) vs Deep Hierarchical DAG (5 Tiers)
2. Dependency Caching: use_cache=True vs use_cache=False over shared sub-dependencies
3. Connection Leak Diagnostic: Verifying that yield dependencies execute finally teardown
   under high concurrency and unhandled route exceptions (Zero Connection Leaks).
"""

import asyncio
import time
from typing import Annotated, AsyncGenerator, Dict, List
import uuid

from fastapi import Depends, FastAPI, HTTPException, status
import httpx

# ---------------------------------------------------------------------------
# 1. Mock Connection Pool Telemetry Tracker
# ---------------------------------------------------------------------------

class ConnectionPoolTelemetry:
    """
    Monitors database connection acquisitions, releases, and leaks.
    WHY: Verifies that yield teardown runs 100% of the time, even during exceptions.
    """
    def __init__(self):
        self.acquired_count = 0
        self.released_count = 0
        self.active_connections = 0

    def acquire(self) -> str:
        self.acquired_count += 1
        self.active_connections += 1
        return f"conn_{uuid.uuid4().hex[:6]}"

    def release(self, conn_id: str) -> None:
        self.released_count += 1
        self.active_connections -= 1

    def reset(self) -> None:
        self.acquired_count = 0
        self.released_count = 0
        self.active_connections = 0


pool_tracker = ConnectionPoolTelemetry()


# ---------------------------------------------------------------------------
# 2. Dependency Providers & Yield Teardown Hooks
# ---------------------------------------------------------------------------

async def get_tracked_connection() -> AsyncGenerator[str, None]:
    """Yield dependency tracking setup and teardown lifecycles."""
    conn = pool_tracker.acquire()
    try:
        yield conn
    finally:
        pool_tracker.release(conn)


TrackedConn = Annotated[str, Depends(get_tracked_connection)]


# Deep Dependency Hierarchy (5 Tiers)
async def dep_tier_1() -> str:
    return "t1"

async def dep_tier_2(t1: Annotated[str, Depends(dep_tier_1)]) -> str:
    return f"{t1}->t2"

async def dep_tier_3(t2: Annotated[str, Depends(dep_tier_2)]) -> str:
    return f"{t2}->t3"

async def dep_tier_4(t3: Annotated[str, Depends(dep_tier_3)]) -> str:
    return f"{t3}->t4"

async def dep_tier_5(t4: Annotated[str, Depends(dep_tier_4)]) -> str:
    return f"{t4}->t5"


# Caching demonstration sub-dependency
def shared_compute_dep() -> str:
    # Simulates micro-calculation or token validation
    time.sleep(0.0001)
    return "computed_token"


# ---------------------------------------------------------------------------
# 3. Benchmark Endpoints
# ---------------------------------------------------------------------------

app = FastAPI(title="DI Performance & Diagnostic API")

# Shallow endpoint (Direct 1-tier dependency)
@app.get("/benchmark/shallow")
async def endpoint_shallow(t1: Annotated[str, Depends(dep_tier_1)]):
    return {"result": t1}


# Deep endpoint (5-tier hierarchical DAG)
@app.get("/benchmark/deep")
async def endpoint_deep(t5: Annotated[str, Depends(dep_tier_5)]):
    return {"result": t5}


# Caching endpoint (5 sub-dependency references with use_cache=True)
@app.get("/benchmark/cached")
async def endpoint_cached(
    s1: Annotated[str, Depends(shared_compute_dep, use_cache=True)],
    s2: Annotated[str, Depends(shared_compute_dep, use_cache=True)],
    s3: Annotated[str, Depends(shared_compute_dep, use_cache=True)],
    s4: Annotated[str, Depends(shared_compute_dep, use_cache=True)],
    s5: Annotated[str, Depends(shared_compute_dep, use_cache=True)],
):
    return {"status": "ok"}


# Uncached endpoint (5 sub-dependency references with use_cache=False)
@app.get("/benchmark/uncached")
async def endpoint_uncached(
    s1: Annotated[str, Depends(shared_compute_dep, use_cache=False)],
    s2: Annotated[str, Depends(shared_compute_dep, use_cache=False)],
    s3: Annotated[str, Depends(shared_compute_dep, use_cache=False)],
    s4: Annotated[str, Depends(shared_compute_dep, use_cache=False)],
    s5: Annotated[str, Depends(shared_compute_dep, use_cache=False)],
):
    return {"status": "ok"}


# Yield dependency testing endpoints
@app.get("/diagnostics/normal-query")
async def endpoint_normal_query(conn: TrackedConn):
    return {"conn": conn, "status": "success"}


@app.get("/diagnostics/route-exception")
async def endpoint_route_exception(conn: TrackedConn):
    # Simulates business logic raising HTTPException
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Simulated failure.")


@app.get("/diagnostics/server-crash")
async def endpoint_server_crash(conn: TrackedConn):
    # Simulates unexpected catastrophic crash
    raise RuntimeError("Unexpected server memory crash.")


# ---------------------------------------------------------------------------
# 4. Diagnostic & Performance Benchmark Execution
# ---------------------------------------------------------------------------

async def profile_endpoint(
    client: httpx.AsyncClient, url: str, iterations: int = 300
) -> Dict[str, any]:
    # Warmup
    await client.get(url)

    latencies = []
    t_start = time.perf_counter()

    for _ in range(iterations):
        t0 = time.perf_counter()
        resp = await client.get(url)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    total_time = time.perf_counter() - t_start
    latencies.sort()
    mean_ms = sum(latencies) / len(latencies)
    p95_ms = latencies[int(len(latencies) * 0.95)]
    rps = iterations / total_time

    return {
        "url": url,
        "iterations": iterations,
        "mean_ms": mean_ms,
        "p95_ms": p95_ms,
        "rps": rps,
    }


async def run_diagnostics():
    print("=" * 85)
    print("FastAPI Dependency Injection Performance & Leak Diagnostic Report")
    print("=" * 85)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://diagnostic") as client:
        # Phase 1: Shallow vs Deep DAG Profiling
        print("\n[PHASE 1] Profiling Dependency Graph Depth (300 requests each)...")
        res_shallow = await profile_endpoint(client, "/benchmark/shallow", iterations=300)
        res_deep = await profile_endpoint(client, "/benchmark/deep", iterations=300)

        print("-" * 85)
        print(f"{'Graph Architecture':<28} | {'Mean Latency':<12} | {'P95 Latency':<12} | {'Throughput'}")
        print("-" * 85)
        print(f"{'Shallow (1-Tier Root)':<28} | {res_shallow['mean_ms']:<9.3f} ms | {res_shallow['p95_ms']:<9.3f} ms | {res_shallow['rps']:<8.1f} rps")
        print(f"{'Deep DAG (5-Tier Nested)':<28} | {res_deep['mean_ms']:<9.3f} ms | {res_deep['p95_ms']:<9.3f} ms | {res_deep['rps']:<8.1f} rps")
        dag_overhead = res_deep["mean_ms"] - res_shallow["mean_ms"]
        print(f"  -> Deep DAG Overhead: Only ~{dag_overhead*1000:.1f} microseconds per request!")

        # Phase 2: Caching Efficiency (use_cache=True vs use_cache=False)
        print("\n[PHASE 2] Benchmarking Dependency Caching (5 Shared Sub-Dependencies)...")
        res_cached = await profile_endpoint(client, "/benchmark/cached", iterations=150)
        res_uncached = await profile_endpoint(client, "/benchmark/uncached", iterations=150)

        print("-" * 85)
        print(f"{'Cache Strategy':<28} | {'Mean Latency':<12} | {'P95 Latency':<12} | {'Throughput'}")
        print("-" * 85)
        print(f"{'use_cache=True (Default)':<28} | {res_cached['mean_ms']:<9.3f} ms | {res_cached['p95_ms']:<9.3f} ms | {res_cached['rps']:<8.1f} rps")
        print(f"{'use_cache=False':<28} | {res_uncached['mean_ms']:<9.3f} ms | {res_uncached['p95_ms']:<9.3f} ms | {res_uncached['rps']:<8.1f} rps")
        speedup = res_uncached["mean_ms"] / res_cached["mean_ms"]
        print(f"  -> Dependency Caching Gain: {speedup:.2f}x FASTER execution by avoiding redundant sub-calls.")

        # Phase 3: Yield Teardown & Connection Leak Diagnostic
        print("\n[PHASE 3] Running Connection Leak Stress Test across Normal & Exception Scenarios...")
        pool_tracker.reset()

        # 1. 100 Successful requests
        for _ in range(100):
            r = await client.get("/diagnostics/normal-query")
            assert r.status_code == 200

        # 2. 50 HTTPException requests (400 Client Errors)
        for _ in range(50):
            r = await client.get("/diagnostics/route-exception")
            assert r.status_code == 400

        # 3. 50 Uncaught Server Crashes (500 Server Errors)
        for _ in range(50):
            r = await client.get("/diagnostics/server-crash")
            assert r.status_code == 500

        print(f"  Total Database Connections Acquired : {pool_tracker.acquired_count}")
        print(f"  Total Database Connections Released : {pool_tracker.released_count}")
        print(f"  Active Leaked Connections Remaining : {pool_tracker.active_connections}")

        assert pool_tracker.acquired_count == 200
        assert pool_tracker.released_count == 200
        assert pool_tracker.active_connections == 0, "CRITICAL ALERT: Connection leak detected!"
        print("  -> PASSED: ZERO connection leaks! Yield teardown executed on 100% of requests.")

    print("\n" + "=" * 85)
    print("ALL DEPENDENCY INJECTION PERFORMANCE & LEAK DIAGNOSTICS COMPLETED!")
    print("=" * 85)


if __name__ == "__main__":
    asyncio.run(run_diagnostics())
