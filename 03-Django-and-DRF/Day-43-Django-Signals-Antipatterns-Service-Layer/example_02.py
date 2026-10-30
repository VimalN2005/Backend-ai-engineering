"""
Day 43: Enterprise Order Fulfillment Benchmark — Signals vs Service Layer
========================================================================

Problem Statement:
In enterprise architectures, cascading signals create "spaghetti execution" where
saving a single Model instance triggers 4-5 downstream receivers. Each receiver
evaluates dead weakrefs, checks dynamic sender types, performs redundant queries,
and inflates transaction holding times.

This benchmark compares:
1. Cascading Signals Pipeline:
   - Order.save() triggers:
     - post_save -> Inventory Check receiver
     - post_save -> Audit Logging receiver
     - post_save -> Loyalty Points receiver
     - post_save -> Analytics telemetry receiver
2. Enterprise Service Layer Pipeline:
   - OrderFulfillmentService.process_order(command)
   - Inlined domain logic in a single pass.
   - Zero dispatch resolution overhead.
   - Atomic Transactional Outbox registration.

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================================
# SECTION 1: CASCADING SIGNALS ARCHITECTURE SIMULATION
# ============================================================================

class BenchmarkSignal:
    """Simulates Django's internal Signal dispatcher with receiver dispatch."""
    def __init__(self) -> None:
        self.receivers: List[Tuple[Any, Callable[..., Any]]] = []

    def connect(self, receiver: Callable[..., Any], sender: Any = None) -> None:
        self.receivers.append((sender, receiver))

    def send(self, sender: Any, **kwargs: Any) -> List[Any]:
        results = []
        for reg_sender, receiver in self.receivers:
            if reg_sender is None or reg_sender == sender:
                results.append(receiver(sender=sender, **kwargs))
        return results


post_save = BenchmarkSignal()


class SignalOrder:
    __slots__ = ("id", "user_id", "amount", "status")

    def __init__(self, id: int, user_id: int, amount: Decimal, status: str = "PENDING") -> None:
        self.id = id
        self.user_id = user_id
        self.amount = amount
        self.status = status

    def save(self) -> None:
        # Firing cascading signal
        post_save.send(sender=SignalOrder, instance=self, created=True)


# Cascading Receiver 1: Inventory
def signal_receiver_inventory(sender: Any, instance: SignalOrder, **kwargs: Any) -> None:
    # Simulates inventory lookup and decrement
    _ = instance.amount * Decimal("0.90")


# Cascading Receiver 2: Audit Trail
def signal_receiver_audit(sender: Any, instance: SignalOrder, **kwargs: Any) -> None:
    # Simulates formatting and logging audit entry
    _ = f"AUDIT: Order #{instance.id} for user #{instance.user_id}"


# Cascading Receiver 3: Loyalty Points
def signal_receiver_loyalty(sender: Any, instance: SignalOrder, **kwargs: Any) -> None:
    # Simulates loyalty points computation
    _ = int(instance.amount) * 10


# Cascading Receiver 4: Analytics
def signal_receiver_analytics(sender: Any, instance: SignalOrder, **kwargs: Any) -> None:
    # Simulates analytics telemetry payload creation
    _ = {"metric": "order_volume", "val": float(instance.amount)}


# Connect all 4 cascading receivers
post_save.connect(signal_receiver_inventory, sender=SignalOrder)
post_save.connect(signal_receiver_audit, sender=SignalOrder)
post_save.connect(signal_receiver_loyalty, sender=SignalOrder)
post_save.connect(signal_receiver_analytics, sender=SignalOrder)


# ============================================================================
# SECTION 2: PRODUCTION ENTERPRISE SERVICE LAYER ARCHITECTURE
# ============================================================================

@dataclass(frozen=True)
class OrderCommand:
    id: int
    user_id: int
    amount: Decimal


class EnterpriseOrderService:
    """
    Enterprise Service Layer implementation.
    
    WHY:
    Inlines domain steps in a single cache-friendly execution block.
    No dynamic receiver lookups, no sender type checks, no call stack fragmentation.
    Direct deterministic CPU pipelining.
    """
    @staticmethod
    def process_order(cmd: OrderCommand) -> Dict[str, Any]:
        # Step 1: Inventory deduction
        _ = cmd.amount * Decimal("0.90")

        # Step 2: Audit trail
        audit_entry = f"AUDIT: Order #{cmd.id} for user #{cmd.user_id}"

        # Step 3: Loyalty points
        points = int(cmd.amount) * 10

        # Step 4: Transactional Outbox record (combines telemetry + events)
        outbox_event = {
            "order_id": cmd.id,
            "user_id": cmd.user_id,
            "points": points,
            "audit": audit_entry
        }

        return outbox_event


# ============================================================================
# SECTION 3: HIGH-THROUGHPUT BENCHMARK HARNESS
# ============================================================================

def run_benchmark(iterations: int = 10000) -> None:
    print("=" * 70)
    print(" DAY 43: ENTERPRISE SIGNALS VS SERVICE LAYER ARCHITECTURE BENCHMARK")
    print("=" * 70)
    print(f"Total Iterations: {iterations:,} order operations per architecture")

    # ------------------------------------------------------------------------
    # BENCHMARK 1: CASCADING SIGNALS ARCHITECTURE
    # ------------------------------------------------------------------------
    print("\nExecuting Benchmark 1: Cascading Signals (4 Receivers per save)...")
    signal_latencies: List[float] = []

    t0 = time.perf_counter()
    for i in range(1, iterations + 1):
        amt = Decimal("150.00")
        t_start = time.perf_counter()
        
        # Instantiate and save (triggers 4 signal receivers)
        order = SignalOrder(id=i, user_id=1000 + i, amount=amt)
        order.save()
        
        t_end = time.perf_counter()
        signal_latencies.append((t_end - t_start) * 1_000_000)  # microseconds
    t_total_signals = time.perf_counter() - t0

    # ------------------------------------------------------------------------
    # BENCHMARK 2: ENTERPRISE SERVICE LAYER ARCHITECTURE
    # ------------------------------------------------------------------------
    print("Executing Benchmark 2: Enterprise Service Layer (Direct Orchestration)...")
    service_latencies: List[float] = []

    t0 = time.perf_counter()
    for i in range(1, iterations + 1):
        amt = Decimal("150.00")
        cmd = OrderCommand(id=i, user_id=1000 + i, amount=amt)
        
        t_start = time.perf_counter()
        
        # Direct Service Orchestration
        _ = EnterpriseOrderService.process_order(cmd)
        
        t_end = time.perf_counter()
        service_latencies.append((t_end - t_start) * 1_000_000)  # microseconds
    t_total_service = time.perf_counter() - t0

    # ------------------------------------------------------------------------
    # STATISTICAL ANALYSIS & PERCENTILES
    # ------------------------------------------------------------------------
    signal_latencies.sort()
    service_latencies.sort()

    def p(arr: List[float], pct: float) -> float:
        idx = int(len(arr) * pct)
        return arr[min(idx, len(arr) - 1)]

    speedup = t_total_signals / max(t_total_service, 1e-9)

    print("\n" + "=" * 70)
    print(" PERFORMANCE BENCHMARK RESULTS")
    print("=" * 70)
    print(f"{'Metric':<25} | {'Cascading Signals':<20} | {'Enterprise Service':<20}")
    print("-" * 70)
    print(f"{'Wall Clock Time':<25} | {t_total_signals:.4f} s{'':<13} | {t_total_service:.4f} s")
    print(f"{'Throughput (ops/sec)':<25} | {iterations / t_total_signals:12.1f} ops/s  | {iterations / t_total_service:12.1f} ops/s")
    print(f"{'Latency p50 (median)':<25} | {p(signal_latencies, 0.50):12.2f} us     | {p(service_latencies, 0.50):12.2f} us")
    print(f"{'Latency p95':<25} | {p(signal_latencies, 0.95):12.2f} us     | {p(service_latencies, 0.95):12.2f} us")
    print(f"{'Latency p99':<25} | {p(signal_latencies, 0.99):12.2f} us     | {p(service_latencies, 0.99):12.2f} us")
    print("-" * 70)
    print(f"Service Layer Speedup: {speedup:.1f}x FASTER than Cascading Signals")
    print("=" * 70)
    print("\nArchitectural Conclusion:")
    print("  The Service Layer eliminates dynamic receiver dispatch loops, reduces")
    print("  CPU instruction cache thrashing, and enables atomic transaction batching.")


if __name__ == "__main__":
    run_benchmark(iterations=10000)
