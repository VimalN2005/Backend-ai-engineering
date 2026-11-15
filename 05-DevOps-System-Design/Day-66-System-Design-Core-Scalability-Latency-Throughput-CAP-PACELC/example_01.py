"""Day 66: System Design Core: Latency, Scalability Laws & Capacity Planning.

This production-grade script provides:
1. Tail Latency & Percentile Profiler (p50, p90, p99, p99.9, and microservice fanout multiplier).
2. Little's Law & Universal Scalability Law (USL) Modeling Engine (contention & coherence penalties).
3. High Availability SLA & Series/Parallel Downtime Calculator.
4. Back-of-the-Envelope Capacity Planning Engine (QPS, IOPS, 5-year storage, RAM cache sizing).
5. Comprehensive standalone automated test suite with zero external dependencies.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ==============================================================================
# 1. TAIL LATENCY & PERCENTILE PROFILER
# ==============================================================================

class LatencyProfiler:
    """Profiles latency distributions and evaluates fanout tail-at-scale amplification."""

    @staticmethod
    def calculate_percentiles(latencies_ms: List[float]) -> Dict[str, float]:
        """Calculates arithmetic mean and exact percentiles (p50, p90, p95, p99, p99.9)."""
        if not latencies_ms:
            raise ValueError("latencies_ms cannot be empty")

        sorted_lat = sorted(latencies_ms)
        n = len(sorted_lat)

        def get_p(p: float) -> float:
            k = (n - 1) * (p / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_lat[int(k)]
            d0 = sorted_lat[int(f)] * (c - k)
            d1 = sorted_lat[int(c)] * (k - f)
            return round(d0 + d1, 3)

        return {
            "count": n,
            "mean": round(sum(sorted_lat) / n, 3),
            "p50": get_p(50.0),
            "p90": get_p(90.0),
            "p95": get_p(95.0),
            "p99": get_p(99.0),
            "p99.9": get_p(99.9),
            "min": round(sorted_lat[0], 3),
            "max": round(sorted_lat[-1], 3),
        }

    @staticmethod
    def simulate_fanout_latency(
        base_service_p99_prob: float = 0.01,
        fast_latency_ms: float = 8.0,
        slow_latency_ms: float = 250.0,
        fanout_sizes: Optional[List[int]] = None,
        iterations: int = 10_000,
        seed: int = 42
    ) -> Dict[int, Dict[str, float]]:
        """Simulates parallel fanout where an API gateway awaits M backend microservices.

        The total request latency is max(latency_1, latency_2, ..., latency_M).
        """
        if fanout_sizes is None:
            fanout_sizes = [1, 5, 10, 25, 50, 100]

        rng = random.Random(seed)
        results: Dict[int, Dict[str, float]] = {}

        for m in fanout_sizes:
            user_latencies: List[float] = []
            for _ in range(iterations):
                # Each of the M calls has (1 - p) probability of fast, p probability of slow
                call_latencies = [
                    slow_latency_ms if rng.random() < base_service_p99_prob else fast_latency_ms
                    for _ in range(m)
                ]
                user_latencies.append(max(call_latencies))

            theoretical_slow_prob = 1.0 - math.pow(1.0 - base_service_p99_prob, m)
            profile = LatencyProfiler.calculate_percentiles(user_latencies)
            profile["theoretical_tail_probability"] = round(theoretical_slow_prob * 100, 2)
            results[m] = profile

        return results


# ==============================================================================
# 2. LITTLE'S LAW & UNIVERSAL SCALABILITY LAW (USL) ENGINE
# ==============================================================================

class LittlesLawEngine:
    """Queueing theory modeling using Little's Law: L = lambda * W."""

    @staticmethod
    def calculate_concurrency(arrival_rate_qps: float, avg_latency_sec: float) -> float:
        """Returns average concurrent in-flight requests (L)."""
        return arrival_rate_qps * avg_latency_sec

    @staticmethod
    def calculate_max_sustainable_qps(concurrency_limit: int, avg_latency_sec: float) -> float:
        """Returns max QPS before connection pool / thread starvation occurs."""
        if avg_latency_sec <= 0:
            raise ValueError("Latency must be positive")
        return concurrency_limit / avg_latency_sec


class UniversalScalabilityLaw:
    """Neil Gunther's Universal Scalability Law (USL).

    Models speedup and throughput accounting for:
    - Linear scaling (N)
    - Contention penalty sigma (serialization / queueing bottlenecks)
    - Coherence penalty kappa (distributed state synchronization / crosstalk)
    Formula: X(N) = (gamma * N) / (1 + sigma*(N - 1) + kappa*N*(N - 1))
    """

    def __init__(self, sigma: float = 0.02, kappa: float = 0.0005, gamma: float = 1000.0) -> None:
        self.sigma = sigma  # Contention coefficient (e.g. 2% serialized)
        self.kappa = kappa  # Coherence coefficient (e.g. 0.05% pairwise sync)
        self.gamma = gamma  # Single node baseline throughput (QPS)

    def throughput(self, n_nodes: int) -> float:
        """Calculates expected throughput for N nodes."""
        if n_nodes <= 0:
            return 0.0
        denominator = 1.0 + self.sigma * (n_nodes - 1) + self.kappa * n_nodes * (n_nodes - 1)
        return (self.gamma * n_nodes) / denominator

    def speedup(self, n_nodes: int) -> float:
        """Relative speedup compared to a single node."""
        if n_nodes <= 0:
            return 0.0
        denominator = 1.0 + self.sigma * (n_nodes - 1) + self.kappa * n_nodes * (n_nodes - 1)
        return n_nodes / denominator

    def optimal_cluster_size(self) -> Tuple[int, float]:
        """Calculates peak cluster size N* before retrograde degradation occurs.

        N* = sqrt((1 - sigma) / kappa)
        """
        if self.kappa <= 0:
            return 999999, float("inf")  # Pure Amdahl has no retrograde peak
        n_star = math.sqrt((1.0 - self.sigma) / self.kappa)
        peak_n = max(1, round(n_star))
        peak_throughput = self.throughput(peak_n)
        return peak_n, peak_throughput


# ==============================================================================
# 3. HIGH AVAILABILITY SLA & SYSTEM RELIABILITY ENGINE
# ==============================================================================

@dataclass
class AvailabilityReport:
    uptime_percentage: float
    downtime_year_seconds: float
    downtime_month_seconds: float
    downtime_day_seconds: float

    def format_summary(self) -> Dict[str, str]:
        def fmt(secs: float) -> str:
            if secs >= 86400:
                return f"{secs / 86400:.2f} days"
            elif secs >= 3600:
                return f"{secs / 3600:.2f} hours"
            elif secs >= 60:
                return f"{secs / 60:.2f} minutes"
            return f"{secs:.2f} seconds"

        return {
            "SLA": f"{self.uptime_percentage * 100:.4f}%",
            "Max Downtime/Year": fmt(self.downtime_year_seconds),
            "Max Downtime/Month": fmt(self.downtime_month_seconds),
            "Max Downtime/Day": fmt(self.downtime_day_seconds),
        }


class ReliabilityEngine:
    """Calculates component and composite system availability."""

    @staticmethod
    def calculate_availability(mtbf_hours: float, mttr_hours: float) -> float:
        """Availability A = MTBF / (MTBF + MTTR)."""
        return mtbf_hours / (mtbf_hours + mttr_hours)

    @staticmethod
    def downtime_breakdown(availability: float) -> AvailabilityReport:
        """Computes allowable downtime across Year, Month, Day for an availability fraction."""
        unavailability = 1.0 - availability
        year_sec = 365.25 * 86400 * unavailability
        month_sec = (365.25 / 12) * 86400 * unavailability
        day_sec = 86400 * unavailability
        return AvailabilityReport(
            uptime_percentage=availability,
            downtime_year_seconds=year_sec,
            downtime_month_seconds=month_sec,
            downtime_day_seconds=day_sec,
        )

    @staticmethod
    def composite_series(components: List[float]) -> float:
        """Series components: A_total = A1 * A2 * ... * An (System fails if ANY fail)."""
        prod = 1.0
        for c in components:
            prod *= c
        return prod

    @staticmethod
    def composite_parallel(components: List[float]) -> float:
        """Parallel components: A_total = 1 - (1 - A1)*(1 - A2)*...*(1 - An)."""
        unavailability_prod = 1.0
        for c in components:
            unavailability_prod *= (1.0 - c)
        return 1.0 - unavailability_prod


# ==============================================================================
# 4. BACK-OF-THE-ENVELOPE CAPACITY PLANNING CALCULATOR
# ==============================================================================

@dataclass
class WorkloadProfile:
    daily_active_users: int = 100_000_000
    reads_per_user_day: int = 20
    writes_per_user_day: int = 2
    avg_read_payload_bytes: int = 409_600   # 400 KB
    avg_write_payload_bytes: int = 41_960   # 41 KB (metadata + image ref)
    peak_traffic_multiplier: float = 2.0
    storage_replication_factor: int = 3
    cache_hot_read_fraction: float = 0.20   # 80-20 Pareto rule (cache top 20%)


class CapacityPlanner:
    """Executes high-scale back-of-the-envelope capacity estimations."""

    def __init__(self, profile: Optional[WorkloadProfile] = None) -> None:
        self.p = profile or WorkloadProfile()

    def calculate_sizing(self) -> Dict[str, Any]:
        seconds_per_day = 86_400

        # 1. QPS Sizing
        total_daily_writes = self.p.daily_active_users * self.p.writes_per_user_day
        total_daily_reads = self.p.daily_active_users * self.p.reads_per_user_day

        avg_write_qps = total_daily_writes / seconds_per_day
        peak_write_qps = avg_write_qps * self.p.peak_traffic_multiplier

        avg_read_qps = total_daily_reads / seconds_per_day
        peak_read_qps = avg_read_qps * self.p.peak_traffic_multiplier

        # 2. Network Bandwidth
        ingress_bytes_sec = avg_write_qps * self.p.avg_write_payload_bytes
        ingress_gbps = (ingress_bytes_sec * 8) / 1e9

        egress_bytes_sec = avg_read_qps * self.p.avg_read_payload_bytes
        egress_gbps = (egress_bytes_sec * 8) / 1e9

        peak_egress_gbps = egress_gbps * self.p.peak_traffic_multiplier

        # 3. Storage Sizing
        daily_storage_raw_bytes = total_daily_writes * self.p.avg_write_payload_bytes
        daily_storage_raw_tb = daily_storage_raw_bytes / 1e12
        daily_storage_replicated_tb = daily_storage_raw_tb * self.p.storage_replication_factor

        five_year_storage_tb = daily_storage_replicated_tb * 365.25 * 5
        five_year_storage_pb = five_year_storage_tb / 1000

        # 4. RAM Cache Sizing (Pareto Rule: 20% of daily read data cached in memory)
        daily_read_data_bytes = total_daily_reads * self.p.avg_read_payload_bytes
        hot_cache_bytes = daily_read_data_bytes * self.p.cache_hot_read_fraction
        hot_cache_tb = hot_cache_bytes / 1e12

        return {
            "write_qps_avg": round(avg_write_qps, 1),
            "write_qps_peak": round(peak_write_qps, 1),
            "read_qps_avg": round(avg_read_qps, 1),
            "read_qps_peak": round(peak_read_qps, 1),
            "ingress_bandwidth_gbps": round(ingress_gbps, 2),
            "egress_bandwidth_gbps": round(egress_gbps, 2),
            "peak_egress_bandwidth_gbps": round(peak_egress_gbps, 2),
            "daily_storage_raw_tb": round(daily_storage_raw_tb, 2),
            "daily_storage_replicated_tb": round(daily_storage_replicated_tb, 2),
            "five_year_storage_petabytes": round(five_year_storage_pb, 2),
            "ram_cache_working_set_tb": round(hot_cache_tb, 2),
        }


# ==============================================================================
# 5. DIAGNOSTICS & VERIFICATION RUNNER
# ==============================================================================

def run_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 66: SYSTEM DESIGN CORE: LATENCY, SCALABILITY LAWS & CAPACITY DIAGNOSTICS")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Diagnostic 1: Percentile & Tail Latency Fanout Profiling
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 1] Latency Profiler & Tail-at-Scale Amplification:")
    # 980 fast requests (~10ms) and 20 slow requests (>200ms) -> p99 is in the slow tail
    raw_latencies = [random.gauss(10.0, 2.0) for _ in range(980)] + [random.uniform(200.0, 500.0) for _ in range(20)]
    stats = LatencyProfiler.calculate_percentiles(raw_latencies)
    print(f"  • Raw Service Latency Distribution (1,000 requests):")
    print(f"    - p50: {stats['p50']}ms | p90: {stats['p90']}ms | p99: {stats['p99']}ms | Max: {stats['max']}ms")
    assert stats["p50"] < 15.0
    assert stats["p99"] >= 200.0

    print("\n  • Microservice Fanout Simulation (1% chance single backend takes 250ms):")
    fanouts = [1, 10, 50, 100]
    fanout_results = LatencyProfiler.simulate_fanout_latency(
        base_service_p99_prob=0.01,
        fast_latency_ms=8.0,
        slow_latency_ms=250.0,
        fanout_sizes=fanouts,
        iterations=5000,
        seed=42
    )
    for m in fanouts:
        r = fanout_results[m]
        print(f"    - Fanout {m:3d} calls -> User p50: {r['p50']:5.1f}ms | p99: {r['p99']:5.1f}ms | Tail Prob: {r['theoretical_tail_probability']}%")

    # At fanout=100, 1 - (0.99)^100 = 63.4% > 50%, so user p50 jumps to 250ms!
    assert fanout_results[100]["p50"] == 250.0
    print("  ✓ Demonstrated Tail-at-Scale: At 100 parallel calls, 63.4% of users experience tail latency!")

    # --------------------------------------------------------------------------
    # Diagnostic 2: Little's Law & Concurrency Sizing
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 2] Little's Law Concurrency Modeling:")
    qps = 10_000.0
    latency_sec = 0.035  # 35 ms
    concurrency = LittlesLawEngine.calculate_concurrency(qps, latency_sec)
    print(f"  • With {qps:,.0f} QPS and {latency_sec * 1000:.0f}ms latency -> Average in-flight requests: {concurrency:.0f}")
    assert math.isclose(concurrency, 350.0, rel_tol=1e-5)

    max_qps = LittlesLawEngine.calculate_max_sustainable_qps(concurrency_limit=500, avg_latency_sec=latency_sec)
    print(f"  • With connection pool capacity of 500 connections -> Max sustainable QPS: {max_qps:,.0f}")
    assert max_qps > 14_000.0
    print("  ✓ Little's Law queue depth calculations validated.")

    # --------------------------------------------------------------------------
    # Diagnostic 3: Universal Scalability Law (USL) & Retrograde Peak
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 3] Universal Scalability Law (Contention vs Coherence):")
    # 2% contention (sigma=0.02), 0.05% coherence crosstalk (kappa=0.0005)
    usl = UniversalScalabilityLaw(sigma=0.02, kappa=0.0005, gamma=1000.0)
    peak_nodes, peak_throughput = usl.optimal_cluster_size()
    print(f"  • Evaluated USL parameters: σ={usl.sigma} (contention), κ={usl.kappa} (coherence)")
    print(f"    - Optimal Cluster Size N*: {peak_nodes} nodes")
    print(f"    - Peak Cluster Throughput: {peak_throughput:,.0f} QPS")
    print(f"    - Throughput at 10 nodes:  {usl.throughput(10):,.0f} QPS (Speedup: {usl.speedup(10):.1f}x)")
    print(f"    - Throughput at 44 nodes:  {usl.throughput(44):,.0f} QPS (Peak Speedup: {usl.speedup(44):.1f}x)")
    print(f"    - Throughput at 100 nodes: {usl.throughput(100):,.0f} QPS (Retrograde Degradation!)")

    assert peak_nodes == 44
    assert usl.throughput(100) < usl.throughput(44)
    print("  ✓ Successfully demonstrated USL retrograde throughput degradation.")

    # --------------------------------------------------------------------------
    # Diagnostic 4: High Availability SLA (Series vs Parallel)
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 4] High Availability Reliability Math:")
    three_nines = ReliabilityEngine.downtime_breakdown(0.999)
    print(f"  • 99.9% (Three Nines): Max {three_nines.format_summary()['Max Downtime/Year']} downtime/year.")

    five_nines = ReliabilityEngine.downtime_breakdown(0.99999)
    print(f"  • 99.999% (Five Nines): Max {five_nines.format_summary()['Max Downtime/Year']} downtime/year.")

    # 3 services in series each 99.9%
    series_sla = ReliabilityEngine.composite_series([0.999, 0.999, 0.999])
    print(f"  • 3 services in series (99.9% each) -> Composite Availability: {series_sla * 100:.3f}% (Degrades!)")
    assert round(series_sla, 4) == 0.9970

    # 2 redundant database replicas in parallel (99% each)
    parallel_sla = ReliabilityEngine.composite_parallel([0.99, 0.99])
    print(f"  • 2 replicas in parallel (99.0% each) -> Composite Availability: {parallel_sla * 100:.2f}% (Four Nines!)")
    assert round(parallel_sla, 4) == 0.9999
    print("  ✓ Series vs. parallel reliability equations verified.")

    # --------------------------------------------------------------------------
    # Diagnostic 5: Back-of-the-Envelope Capacity Sizing
    # --------------------------------------------------------------------------
    print("\n[Diagnostic 5] Global Social Platform Capacity Sizing (100M DAU):")
    planner = CapacityPlanner(WorkloadProfile(
        daily_active_users=100_000_000,
        reads_per_user_day=20,
        writes_per_user_day=2,
        avg_read_payload_bytes=400 * 1024,
        avg_write_payload_bytes=40 * 1024,
        peak_traffic_multiplier=2.0
    ))
    plan = planner.calculate_sizing()
    print(f"  • Throughput:  Avg Write QPS: {plan['write_qps_avg']:,} | Peak Read QPS: {plan['read_qps_peak']:,}")
    print(f"  • Network:     Ingress: {plan['ingress_bandwidth_gbps']} Gbps | Peak Egress: {plan['peak_egress_bandwidth_gbps']} Gbps")
    print(f"  • Storage:     Raw Daily: {plan['daily_storage_raw_tb']} TB | 5-Year Replicated: {plan['five_year_storage_petabytes']} PB")
    print(f"  • Cache:       Working Set RAM (20% Pareto): {plan['ram_cache_working_set_tb']} TB")

    assert plan["write_qps_avg"] > 2300.0
    assert plan["read_qps_avg"] > 23000.0
    assert plan["five_year_storage_petabytes"] > 10.0
    print("  ✓ Capacity sizing equations completed and asserted.")

    print("\n" + "=" * 80)
    print(" ALL DAY 66 SYSTEM DESIGN CORE DIAGNOSTICS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_diagnostics()
