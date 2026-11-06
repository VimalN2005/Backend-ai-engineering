"""
Day 52: FastAPI Performance & Serialization Benchmark Harness
=============================================================

Production Performance Comparison:
1. Pydantic V2 response_model (Strict filtering & schema enforcement)
2. response_model with response_model_exclude_none & exclude_unset (Payload minimization)
3. Raw JSONResponse / Direct dictionary returns (High-speed raw extraction)
4. Concurrency Profiling: async def vs def (Starlette thread pool offloading)

Includes automated latency, throughput, and payload wire-size telemetry.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Annotated, Dict, List, Optional

from fastapi import FastAPI, Path, Query, status
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 1. Models & Benchmark Schemas
# ---------------------------------------------------------------------------

class DeviceTelemetry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    sensor_type: str
    temperature_c: float
    voltage: float
    is_alert: bool
    firmware_version: str
    calibration_offset: Optional[float] = None
    debug_trace_dump: Optional[str] = None  # Internal diagnostic data


class DeviceTelemetryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    sensor_type: str
    temperature_c: float
    voltage: float
    is_alert: bool
    calibration_offset: Optional[float] = None


# Mock database with 1,000 telemetry objects
SAMPLE_RECORDS: List[DeviceTelemetry] = [
    DeviceTelemetry(
        device_id=f"DEV-{i:05d}",
        sensor_type="THERMAL_INFRARED" if i % 2 == 0 else "VOLTAGE_SHUNT",
        temperature_c=24.5 + (i % 50) * 0.1,
        voltage=3.30 + (i % 10) * 0.01,
        is_alert=(i % 25 == 0),
        firmware_version="v2.4.12",
        calibration_offset=0.05 if i % 3 == 0 else None,
        debug_trace_dump="INTERNAL_HEX_REGISTER_DUMP_DATA_SECRET" if i % 2 == 0 else None,
    )
    for i in range(1, 1001)
]

RAW_DICTS: List[dict] = [rec.model_dump() for rec in SAMPLE_RECORDS]


# ---------------------------------------------------------------------------
# 2. Benchmark Application Endpoints
# ---------------------------------------------------------------------------

app = FastAPI(title="Serialization Benchmark Harness")

@app.get(
    "/benchmark/response-model-standard",
    response_model=List[DeviceTelemetryPublic],
    summary="Standard Pydantic response_model filtering",
)
async def endpoint_standard_response_model(limit: int = 500):
    return SAMPLE_RECORDS[:limit]


@app.get(
    "/benchmark/response-model-pruned",
    response_model=List[DeviceTelemetryPublic],
    response_model_exclude_none=True,
    response_model_exclude_unset=True,
    summary="Optimized response_model with key pruning",
)
async def endpoint_pruned_response_model(limit: int = 500):
    return SAMPLE_RECORDS[:limit]


@app.get(
    "/benchmark/raw-json-response",
    summary="Direct raw dictionary JSONResponse bypassing Pydantic filtering",
)
async def endpoint_raw_json(limit: int = 500):
    # Manually sanitized dictionaries returning directly through Starlette JSONResponse
    pruned_data = [
        {
            "device_id": d["device_id"],
            "sensor_type": d["sensor_type"],
            "temperature_c": d["temperature_c"],
            "voltage": d["voltage"],
            "is_alert": d["is_alert"],
        }
        for d in RAW_DICTS[:limit]
    ]
    return JSONResponse(content=pruned_data)


# Concurrency test endpoints: async def vs def
@app.get("/concurrency/async-def")
async def concurrency_async():
    # Correct async non-blocking sleep
    await asyncio.sleep(0.005)
    return {"status": "async_ok"}


@app.get("/concurrency/sync-def")
def concurrency_sync():
    # Synchronous sleep safely offloaded to Starlette thread pool
    time.sleep(0.005)
    return {"status": "sync_thread_ok"}


# ---------------------------------------------------------------------------
# 3. Benchmark Telemetry Engine
# ---------------------------------------------------------------------------

async def benchmark_endpoint(
    client: httpx.AsyncClient, url: str, iterations: int = 50
) -> Dict[str, any]:
    """
    Profiles an endpoint over multiple warm iterations.
    Records latency (mean, p95), total throughput, and wire payload size.
    """
    # Warmup request
    warmup = await client.get(url)
    payload_size = len(warmup.content)

    latencies = []
    start_total = time.perf_counter()

    for _ in range(iterations):
        t0 = time.perf_counter()
        resp = await client.get(url)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    total_time = time.perf_counter() - start_total
    latencies.sort()
    mean_lat = sum(latencies) / len(latencies)
    p95_lat = latencies[int(len(latencies) * 0.95)]
    req_per_sec = iterations / total_time

    return {
        "url": url,
        "iterations": iterations,
        "payload_bytes": payload_size,
        "mean_ms": mean_lat,
        "p95_ms": p95_lat,
        "req_per_sec": req_per_sec,
    }


# ---------------------------------------------------------------------------
# 4. Orchestration & Presentation
# ---------------------------------------------------------------------------

async def run_benchmark_suite():
    print("=" * 85)
    print("FastAPI Serialization & ASGI Performance Benchmark Harness")
    print(f"Batch Volume: 500 records per request | Iterations: 60 runs per endpoint")
    print("=" * 85)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
        # Phase 1: Serialization Strategy Comparison
        print("\n[PHASE 1] Benchmarking Response Serialization Strategies...")
        res_standard = await benchmark_endpoint(
            client, "/benchmark/response-model-standard?limit=500", iterations=60
        )
        res_pruned = await benchmark_endpoint(
            client, "/benchmark/response-model-pruned?limit=500", iterations=60
        )
        res_raw = await benchmark_endpoint(
            client, "/benchmark/raw-json-response?limit=500", iterations=60
        )

        print("\n" + "-" * 85)
        header = f"{'Strategy':<32} | {'Mean Latency':<13} | {'P95 Latency':<12} | {'Throughput':<11} | {'Payload'}"
        print(header)
        print("-" * 85)

        for label, r in [
            ("Pydantic response_model", res_standard),
            ("response_model (exclude_none)", res_pruned),
            ("Raw JSONResponse (Bypass)", res_raw),
        ]:
            print(
                f"{label:<32} | "
                f"{r['mean_ms']:<10.2f} ms | "
                f"{r['p95_ms']:<9.2f} ms | "
                f"{r['req_per_sec']:<7.1f} rps | "
                f"{r['payload_bytes'] / 1024:.1f} KB"
            )

        print("-" * 85)

        # Phase 2: Concurrency Profiling (async def vs def)
        print("\n[PHASE 2] Profiling Concurrency: async def vs def (100 parallel requests)...")
        t_async_start = time.perf_counter()
        async_tasks = [client.get("/concurrency/async-def") for _ in range(100)]
        await asyncio.gather(*async_tasks)
        async_total_ms = (time.perf_counter() - t_async_start) * 1000.0

        t_sync_start = time.perf_counter()
        sync_tasks = [client.get("/concurrency/sync-def") for _ in range(100)]
        await asyncio.gather(*sync_tasks)
        sync_total_ms = (time.perf_counter() - t_sync_start) * 1000.0

        print(f"  -> 100 Concurrent Requests via async def (Event Loop): {async_total_ms:.2f} ms")
        print(f"  -> 100 Concurrent Requests via def (Thread Pool)     : {sync_total_ms:.2f} ms")

        print("\n" + "=" * 85)
        print("ARCHITECTURAL CONCLUSIONS:")
        print("1. Security vs Raw Speed Trade-Off:")
        print("   response_model guarantees that confidential fields (passwords, debug traces) are never leaked.")
        print(f"   Pydantic V2 Rust core achieves ~{res_standard['req_per_sec']:.0f} rps over 500 complex items,")
        print("   while Raw JSONResponse offers maximum throughput when manual filtering is already verified.")
        print("2. Payload Optimization:")
        print(f"   Setting response_model_exclude_none reduced wire size from {res_standard['payload_bytes']/1024:.1f}KB to {res_pruned['payload_bytes']/1024:.1f}KB,")
        print("   saving network bandwidth and client deserialization cycles.")
        print("3. Event Loop vs Thread Pool:")
        print("   Pure async def multiplexes hundreds of concurrent requests in a single thread with minimal latency.")
        print("   Use def only when interacting with legacy synchronous libraries (requests, psycopg2).")
        print("=" * 85)


if __name__ == "__main__":
    asyncio.run(run_benchmark_suite())
