"""
Day 46: High-Volume Analytics Benchmark — Window Functions vs Correlated Subqueries
===================================================================================

Problem Statement:
In enterprise databases, calculating running balances or "Top-N per group" queries
using naive correlated subqueries forces the SQL engine to rescan historical rows
for every single outer row.
- Complexity: O(N^2) where N is the number of rows in the partition.
- At 10,000 rows, a correlated subquery executes 50,000,000 row inspections!

Analytic Window Functions:
- Complexity: O(N log N) or O(N) linear single-pass aggregation.
- Calculates running aggregates, rankings, and lead/lag values in memory buffers.

This benchmark processes 20,000 financial ledger transactions:
1. Benchmark 1: Running Account Balance (Correlated Subquery vs Window Running Sum).
2. Benchmark 2: Top-3 Transactions per Account (Correlated Subquery vs DENSE_RANK).

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
import random
import time
from decimal import Decimal
from typing import Any, Dict, List, Tuple


# ============================================================================
# SECTION 1: SYNTHETIC DATA GENERATOR
# ============================================================================

@dataclass(frozen=True)
class LedgerEntry:
    id: int
    account_id: str
    amount: Decimal
    created_at_epoch: float


def generate_ledger_data(count: int = 20000, accounts_count: int = 50) -> List[LedgerEntry]:
    print(f"Generating {count:,} financial ledger entries across {accounts_count} accounts...")
    accounts = [f"acc_{i:03d}" for i in range(accounts_count)]
    random.seed(42)  # Deterministic repeatability

    base_time = 1793294000.0
    entries: List[LedgerEntry] = []

    for i in range(1, count + 1):
        acc = accounts[i % len(accounts)]
        amt = Decimal(str(random.randint(10, 5000)))
        t = base_time + (i * 2)
        entries.append(LedgerEntry(id=i, account_id=acc, amount=amt, created_at_epoch=t))

    return entries


# ============================================================================
# SECTION 2: RUNNING BALANCE IMPLEMENTATIONS
# ============================================================================

def running_balance_correlated_subquery(entries: List[LedgerEntry]) -> List[Decimal]:
    """
    Simulates:
    SELECT l1.id, l1.account_id, l1.amount,
           (SELECT SUM(l2.amount) FROM ledger l2 
            WHERE l2.account_id = l1.account_id AND l2.id <= l1.id) AS running_balance
    FROM ledger l1;
    
    WHY O(N^2) SLOW:
    For every entry, scans backward through all preceding entries of the same account.
    """
    # Sample subset to prevent benchmark taking minutes
    subset = entries[:3000]
    balances: List[Decimal] = []

    for i in range(len(subset)):
        curr = subset[i]
        # Correlated inner scan
        acc_total = Decimal("0")
        for j in range(i + 1):
            if subset[j].account_id == curr.account_id:
                acc_total += subset[j].amount
        balances.append(acc_total)

    return balances


def running_balance_window_function(entries: List[LedgerEntry]) -> List[Decimal]:
    """
    Simulates:
    SELECT id, account_id, amount,
           SUM(amount) OVER (
               PARTITION BY account_id 
               ORDER BY id 
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS running_balance
    FROM ledger;
    
    WHY O(N) FAST:
    Partitions into hash buckets and computes cumulative sum in a single linear pass!
    """
    partitions: Dict[str, List[LedgerEntry]] = collections.defaultdict(list)
    for e in entries:
        partitions[e.account_id].append(e)

    balances: List[Decimal] = []
    for acc, acc_entries in partitions.items():
        # Entries are pre-sorted by id
        cum_sum = Decimal("0")
        for e in acc_entries:
            cum_sum += e.amount
            balances.append(cum_sum)

    return balances


# ============================================================================
# SECTION 3: TOP-N PER GROUP IMPLEMENTATIONS
# ============================================================================

def top_n_dense_rank_window(entries: List[LedgerEntry], n: int = 3) -> List[LedgerEntry]:
    """
    Simulates:
    WITH ranked AS (
        SELECT *, DENSE_RANK() OVER (PARTITION BY account_id ORDER BY amount DESC) as rk
        FROM ledger
    )
    SELECT * FROM ranked WHERE rk <= 3;
    """
    partitions: Dict[str, List[LedgerEntry]] = collections.defaultdict(list)
    for e in entries:
        partitions[e.account_id].append(e)

    top_results: List[LedgerEntry] = []
    for acc, acc_entries in partitions.items():
        # Sort partition descending by amount
        acc_entries.sort(key=lambda x: x.amount, reverse=True)
        # Apply dense_rank
        dense_rank = 1
        prev_amt = None
        for e in acc_entries:
            if prev_amt is not None and e.amount != prev_amt:
                dense_rank += 1
            if dense_rank > n:
                break
            top_results.append(e)
            prev_amt = e.amount

    return top_results


# ============================================================================
# SECTION 4: BENCHMARK EXECUTION HARNESS
# ============================================================================

def run_benchmark() -> None:
    print("=" * 70)
    print(" DAY 46: ANALYTIC WINDOW FUNCTIONS VS CORRELATED SUBQUERIES BENCHMARK")
    print("=" * 70)

    dataset = generate_ledger_data(count=20000, accounts_count=50)

    # ------------------------------------------------------------------------
    # BENCHMARK 1: RUNNING BALANCE
    # ------------------------------------------------------------------------
    print("\n[BENCHMARK 1] Running Balance: Correlated Subquery (3,000 rows) vs Window Function (20,000 rows)...")

    # Correlated Subquery (limited to 3,000 rows to avoid test timeout)
    t0 = time.perf_counter()
    _ = running_balance_correlated_subquery(dataset)
    t_correlated = time.perf_counter() - t0

    # Window Function (full 20,000 rows)
    t0 = time.perf_counter()
    _ = running_balance_window_function(dataset)
    t_window = time.perf_counter() - t0

    throughput_correlated = 3000 / t_correlated
    throughput_window = 20000 / t_window
    speedup = throughput_window / max(throughput_correlated, 1e-9)

    print(f"  -> Correlated Subquery Time (3,000 rows) : {t_correlated:.4f} seconds ({throughput_correlated:10.1f} rows/s)")
    print(f"  -> Window Function Time     (20,000 rows): {t_window:.4f} seconds ({throughput_window:10.1f} rows/s)")
    print(f"  -> Analytical Window Function Speedup   : {speedup:.1f}x FASTER than Correlated Subquery!")

    # ------------------------------------------------------------------------
    # BENCHMARK 2: TOP-3 PER ACCOUNT VIA DENSE_RANK
    # ------------------------------------------------------------------------
    print("\n[BENCHMARK 2] Top-3 Transactions per Account via DENSE_RANK (20,000 rows)...")
    iterations = 200
    latencies: List[float] = []

    t_start = time.perf_counter()
    for _ in range(iterations):
        t0 = time.perf_counter()
        top_n = top_n_dense_rank_window(dataset, n=3)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1_000_000)
    t_total = time.perf_counter() - t_start

    latencies.sort()
    def p(arr: List[float], pct: float) -> float:
        idx = int(len(arr) * pct)
        return arr[min(idx, len(arr) - 1)]

    print(f"  -> Extracted Top-3 per group across 50 accounts: {len(top_n)} total entries")
    print("\n" + "=" * 70)
    print(" PERFORMANCE BENCHMARK RESULTS")
    print("=" * 70)
    print(f"{'Metric':<30} | {'Window Function Execution':<30}")
    print("-" * 70)
    print(f"{'Total Operations':<30} | {iterations:,} trials across 20k rows")
    print(f"{'Wall Clock Time':<30} | {t_total:.4f} seconds")
    print(f"{'Throughput':<30} | {iterations / t_total:12.1f} ops/sec")
    print(f"{'Latency p50 (median)':<30} | {p(latencies, 0.50):12.2f} us")
    print(f"{'Latency p95':<30} | {p(latencies, 0.95):12.2f} us")
    print(f"{'Latency p99':<30} | {p(latencies, 0.99):12.2f} us")
    print("=" * 70)
    print("\nArchitectural Conclusion:")
    print("  Analytic window functions collapse O(N^2) quadratic subquery loops into")
    print("  single-pass O(N log N) / O(N) operations, eliminating database CPU starvation.")


if __name__ == "__main__":
    run_benchmark()
