"""
Day 53: Pydantic V2 Performance & Architectural Benchmark Harness
=================================================================

Comprehensive Performance Benchmarks:
1. Direct Rust JSON Ingestion: model_validate_json() vs json.loads() + model_validate()
2. Batch Collection Validation: TypeAdapter(List[T]) vs Python List Comprehension
3. Serialization Throughput: model_dump_json() vs json.dumps(model_dump())
4. Polymorphic Routing: Discriminated Union (O(1)) vs Bare Union (O(N) Backtracking)

Measures wall-clock time, throughput (ops/sec), and speedup multipliers.
"""

import json
import time
from typing import Annotated, Any, Dict, List, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# ---------------------------------------------------------------------------
# 1. Benchmark Models & Fixtures
# ---------------------------------------------------------------------------

class SensorReading(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    sensor_type: str
    temperature: float
    humidity: float
    battery_level: int
    is_active: bool


# Fixture: 5,000 raw JSON objects
NUM_RECORDS = 5000
RAW_DICTS: List[Dict[str, Any]] = [
    {
        "device_id": f"IOT-SENSOR-{i:05d}",
        "sensor_type": "DHT22" if i % 2 == 0 else "BME280",
        "temperature": 22.5 + (i % 30) * 0.1,
        "humidity": 45.0 + (i % 40) * 0.1,
        "battery_level": 80 + (i % 20),
        "is_active": (i % 5 != 0),
    }
    for i in range(1, NUM_RECORDS + 1)
]

RAW_JSON_BYTES: bytes = json.dumps(RAW_DICTS).encode("utf-8")
SINGLE_JSON_BYTES_LIST: List[bytes] = [json.dumps(d).encode("utf-8") for d in RAW_DICTS]


# Polymorphic Event Models for Union Benchmark
class HeartbeatEvent(BaseModel):
    event_type: Literal["heartbeat"]
    node_id: str
    uptime_seconds: int

class MetricEvent(BaseModel):
    event_type: Literal["metric"]
    node_id: str
    metric_name: str
    metric_value: float

class AlertEvent(BaseModel):
    event_type: Literal["alert"]
    node_id: str
    severity: str
    description: str

# Discriminated Union (Rust O(1) routing)
TaggedEvent = Annotated[
    Union[HeartbeatEvent, MetricEvent, AlertEvent],
    Field(discriminator="event_type"),
]
TaggedAdapter = TypeAdapter(TaggedEvent)

# Bare Union (Legacy O(N) trial-and-error fallback)
BareEvent = Union[HeartbeatEvent, MetricEvent, AlertEvent]
BareAdapter = TypeAdapter(BareEvent)

SAMPLE_EVENTS = [
    {"event_type": "alert", "node_id": f"node-{i}", "severity": "CRITICAL", "description": "Memory threshold exceeded"}
    for i in range(2000)
]


# ---------------------------------------------------------------------------
# 2. Benchmark Executions
# ---------------------------------------------------------------------------

def benchmark_json_ingestion() -> Dict[str, Any]:
    print("\n[PHASE 1] Benchmarking JSON Ingestion: Direct Rust vs json.loads Intermediate...")

    # Method A: json.loads (Python dict intermediate) + model_validate
    t0 = time.perf_counter()
    records_a = [SensorReading.model_validate(json.loads(b)) for b in SINGLE_JSON_BYTES_LIST]
    t_loads = time.perf_counter() - t0

    # Method B: model_validate_json (Direct Rust pydantic-core parser)
    t0 = time.perf_counter()
    records_b = [SensorReading.model_validate_json(b) for b in SINGLE_JSON_BYTES_LIST]
    t_direct = time.perf_counter() - t0

    return {
        "benchmark": "JSON Ingestion (5,000 items)",
        "method_a_name": "json.loads() + model_validate()",
        "method_a_time": t_loads,
        "method_a_ops": NUM_RECORDS / t_loads,
        "method_b_name": "model_validate_json() [Rust]",
        "method_b_time": t_direct,
        "method_b_ops": NUM_RECORDS / t_direct,
        "speedup": t_loads / t_direct,
    }


def benchmark_collection_validation() -> Dict[str, Any]:
    print("\n[PHASE 2] Benchmarking Collection Validation: TypeAdapter vs Python Loop...")

    # Method A: Python list comprehension
    t0 = time.perf_counter()
    res_a = [SensorReading.model_validate(d) for d in RAW_DICTS]
    t_loop = time.perf_counter() - t0

    # Method B: TypeAdapter(List[SensorReading])
    adapter = TypeAdapter(List[SensorReading])
    t0 = time.perf_counter()
    res_b = adapter.validate_python(RAW_DICTS)
    t_adapter = time.perf_counter() - t0

    return {
        "benchmark": "Collection Validation (5,000 items)",
        "method_a_name": "List Comprehension [Python]",
        "method_a_time": t_loop,
        "method_a_ops": NUM_RECORDS / t_loop,
        "method_b_name": "TypeAdapter(List[T]) [Rust]",
        "method_b_time": t_adapter,
        "method_b_ops": NUM_RECORDS / t_adapter,
        "speedup": t_loop / t_adapter,
    }


def benchmark_serialization() -> Dict[str, Any]:
    print("\n[PHASE 3] Benchmarking Serialization: model_dump_json vs json.dumps(model_dump)...")
    models = [SensorReading.model_validate(d) for d in RAW_DICTS]

    # Method A: model_dump() (Python dict) + json.dumps()
    t0 = time.perf_counter()
    json_strs_a = [json.dumps(m.model_dump()) for m in models]
    t_dumps = time.perf_counter() - t0

    # Method B: model_dump_json() (Direct Rust Stringifier)
    t0 = time.perf_counter()
    json_strs_b = [m.model_dump_json() for m in models]
    t_rust_dump = time.perf_counter() - t0

    return {
        "benchmark": "Serialization (5,000 items)",
        "method_a_name": "model_dump() + json.dumps()",
        "method_a_time": t_dumps,
        "method_a_ops": NUM_RECORDS / t_dumps,
        "method_b_name": "model_dump_json() [Rust]",
        "method_b_time": t_rust_dump,
        "method_b_ops": NUM_RECORDS / t_rust_dump,
        "speedup": t_dumps / t_rust_dump,
    }


def benchmark_polymorphic_unions() -> Dict[str, Any]:
    print("\n[PHASE 4] Benchmarking Polymorphic Routing: Discriminated vs Bare Union...")
    event_count = len(SAMPLE_EVENTS)

    # Method A: Bare Union (Tries Heartbeat, then Metric, then Alert -> 3 attempts per item)
    t0 = time.perf_counter()
    res_bare = [BareAdapter.validate_python(e) for e in SAMPLE_EVENTS]
    t_bare = time.perf_counter() - t0

    # Method B: Discriminated Union (Rust routes directly via 'event_type' tag)
    t0 = time.perf_counter()
    res_tagged = [TaggedAdapter.validate_python(e) for e in SAMPLE_EVENTS]
    t_tagged = time.perf_counter() - t0

    return {
        "benchmark": "Polymorphic Unions (2,000 items)",
        "method_a_name": "Bare Union [O(N) Fallback]",
        "method_a_time": t_bare,
        "method_a_ops": event_count / t_bare,
        "method_b_name": "Discriminated Union [O(1)]",
        "method_b_time": t_tagged,
        "method_b_ops": event_count / t_tagged,
        "speedup": t_bare / t_tagged,
    }


# ---------------------------------------------------------------------------
# 3. Presentation & Architectural Takeaways
# ---------------------------------------------------------------------------

def display_results(benchmarks: List[Dict[str, Any]]):
    print("\n" + "=" * 90)
    print("Pydantic V2 Rust Engine Architectural Benchmark Report")
    print("=" * 90)

    for b in benchmarks:
        print(f"\n>>> {b['benchmark']}")
        print(f"  {b['method_a_name']:<35} : {b['method_a_time']*1000:>7.2f} ms | {b['method_a_ops']:>9.1f} ops/sec")
        print(f"  {b['method_b_name']:<35} : {b['method_b_time']*1000:>7.2f} ms | {b['method_b_ops']:>9.1f} ops/sec")
        print(f"  -> Performance Gain: {b['speedup']:.2f}x FASTER ({b['method_b_name']})")

    print("\n" + "=" * 90)
    print("ARCHITECTURAL TAKEAWAYS:")
    print("1. Direct Rust Ingestion (model_validate_json):")
    print("   Bypasses Python dict intermediary allocations completely.")
    print("   Rust parses the JSON buffer directly into model memory, reducing memory pressure.")
    print("2. Batch Collection Validation (TypeAdapter):")
    print("   Validating a list directly in Rust avoids per-element Python function call overhead.")
    print("3. Discriminated Union Fast-Path:")
    print("   Discriminated unions avoid trial-and-error backtracking across union members,")
    print("   achieving dramatic throughput improvements in event-driven systems.")
    print("4. Direct JSON Serialization (model_dump_json):")
    print("   Rust-backed serialization formats strings and dates without allocating intermediate dicts.")
    print("=" * 90)


def main():
    benchmarks = [
        benchmark_json_ingestion(),
        benchmark_collection_validation(),
        benchmark_serialization(),
        benchmark_polymorphic_unions(),
    ]
    display_results(benchmarks)


if __name__ == "__main__":
    main()
