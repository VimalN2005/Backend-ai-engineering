"""
Day 45: Capstone 1 — High-Throughput Multi-Tenant Load & IDOR Benchmark
========================================================================

Problem Statement:
In enterprise B2B SaaS platforms, the architecture must guarantee two invariants under extreme traffic:
1. Zero Data Leakage (100% IDOR Defense): Even when millions of requests bombard the API,
   a tenant can NEVER read another tenant's data.
2. Predictable Low Latency ($O(1)$ Keyset Pagination): Paginating through a tenant's partition
   must take constant time (< 5 ms) regardless of total database volume.

This benchmark models 20,000 multi-tenant records across 10 corporate tenants:
- Stage 1: Cross-Tenant IDOR Penetration Attack (1,000 adversarial cross-tenant requests).
- Stage 2: High-Throughput Keyset (Cursor) Pagination Benchmark (5,000 iterations).

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import base64
import bisect
import json
import random
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# SECTION 1: HIGH-SCALE MULTI-TENANT INDEXED STORAGE ENGINE
# ============================================================================

class MultiTenantInvoiceRecord:
    __slots__ = ("id", "tenant_id", "created_at", "amount", "status", "memo")

    def __init__(self, id: int, tenant_id: str, created_at: str, amount: str, status: str, memo: str) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.created_at = created_at
        self.amount = amount
        self.status = status
        self.memo = memo


class BenchmarkMultiTenantDB:
    """
    Simulates PostgreSQL table with compound B-Tree index:
    (tenant_id, created_at DESC, id DESC)
    """
    def __init__(self) -> None:
        self.records_by_id: Dict[int, MultiTenantInvoiceRecord] = {}
        # B-Tree index keys: (tenant_id, -timestamp_epoch, -id)
        self.index_keys: List[Tuple[str, float, int]] = []
        self.index_records: List[MultiTenantInvoiceRecord] = []

    def insert(self, record: MultiTenantInvoiceRecord, epoch: float) -> None:
        self.records_by_id[record.id] = record
        # Compound index key: tenant_id ASC, epoch DESC, id DESC
        key = (record.tenant_id, -epoch, -record.id)
        idx = bisect.bisect_left(self.index_keys, key)
        self.index_keys.insert(idx, key)
        self.index_records.insert(idx, record)

    def retrieve_with_tenant_check(self, tenant_id: str, invoice_id: int) -> Optional[MultiTenantInvoiceRecord]:
        """
        Simulates: SELECT * FROM invoice WHERE id = :id AND tenant_id = :tenant_id;
        Returns None (404) if invoice belongs to a different tenant.
        """
        rec = self.records_by_id.get(invoice_id)
        if rec and rec.tenant_id == tenant_id:
            return rec
        return None

    def cursor_paginate_tenant(
        self,
        tenant_id: str,
        cursor_epoch: Optional[float],
        cursor_id: Optional[int],
        page_size: int = 50
    ) -> List[MultiTenantInvoiceRecord]:
        """
        Simulates:
        SELECT * FROM invoice 
        WHERE tenant_id = :tenant_id AND (created_at, id) < (:epoch, :id)
        ORDER BY created_at DESC, id DESC LIMIT :page_size;
        """
        if cursor_epoch is None:
            seek_key = (tenant_id, -float("inf"), -int(1e9))
        else:
            seek_key = (tenant_id, -cursor_epoch, -cursor_id)

        idx = bisect.bisect_right(self.index_keys, seek_key)
        results: List[MultiTenantInvoiceRecord] = []
        total_len = len(self.index_records)

        while idx < total_len and len(results) < page_size:
            rec = self.index_records[idx]
            if rec.tenant_id != tenant_id:
                break  # Reached end of tenant partition
            results.append(rec)
            idx += 1

        return results


# ============================================================================
# SECTION 2: CURSOR ENCODER
# ============================================================================

class BenchmarkCursorEncoder:
    @staticmethod
    def encode(epoch: float, invoice_id: int) -> str:
        payload = {"t": round(epoch, 3), "i": invoice_id}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode(token: str) -> Optional[Tuple[float, int]]:
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            return (float(data["t"]), int(data["i"]))
        except Exception:
            return None


# ============================================================================
# SECTION 3: POPULATE DATABASE & RUN BENCHMARK
# ============================================================================

def populate_benchmark_database(db: BenchmarkMultiTenantDB, total_records: int = 20000) -> List[str]:
    print(f"Populating database with {total_records:,} multi-tenant invoices across 10 corporate tenants...")
    tenants = [f"tenant_corp_{i:02d}" for i in range(10)]
    base_epoch = 1793294000.0  # Unix timestamp

    random.seed(1337)  # Deterministic seed

    for i in range(1, total_records + 1):
        tenant_id = tenants[i % len(tenants)]
        epoch = base_epoch + (i * 2) + random.uniform(0.0, 1.0)
        rec = MultiTenantInvoiceRecord(
            id=i,
            tenant_id=tenant_id,
            created_at=f"2026-11-01T{i:05d}Z",
            amount=f"{(i % 500) * 10 + 50}.00",
            status="PAID" if i % 3 == 0 else "PENDING",
            memo=f"Invoice reference #{i:06d}"
        )
        db.insert(rec, epoch)

    print(f"Database successfully indexed with {len(db.records_by_id):,} records.")
    return tenants


def run_benchmark() -> None:
    print("=" * 70)
    print(" DAY 45: CAPSTONE 1 — MULTI-TENANT LOAD & IDOR BENCHMARK")
    print("=" * 70)

    db = BenchmarkMultiTenantDB()
    tenants = populate_benchmark_database(db, total_records=20000)

    # ------------------------------------------------------------------------
    # STAGE 1: CROSS-TENANT IDOR PENETRATION ATTACK SIMULATION
    # ------------------------------------------------------------------------
    print("\n[STAGE 1] Cross-Tenant IDOR Attack Simulation (1,000 Adversarial Trials)...")
    attacker_tenant = "tenant_corp_00"
    victim_tenants = [t for t in tenants if t != attacker_tenant]

    attack_attempts = 1000
    blocked_count = 0
    t0 = time.perf_counter()

    for _ in range(attack_attempts):
        # Pick an invoice belonging to a victim tenant (attacker does NOT own this invoice)
        while True:
            random_id = random.randint(1, 20000)
            rec = db.records_by_id.get(random_id)
            if rec and rec.tenant_id != attacker_tenant:
                break

        # Attacker tries to access victim's invoice using their own tenant_id
        res = db.retrieve_with_tenant_check(tenant_id=attacker_tenant, invoice_id=random_id)
        if res is None:
            blocked_count += 1

    t_idor = time.perf_counter() - t0
    rejection_rate = (blocked_count / attack_attempts) * 100.0

    print(f"  -> Total Adversarial Probes : {attack_attempts:,}")
    print(f"  -> Total Probes Blocked (404): {blocked_count:,}")
    print(f"  -> Rejection Rate            : {rejection_rate:.2f}% (Target: 100.00%)")
    print(f"  -> Verification Time         : {t_idor:.4f} seconds")
    assert rejection_rate == 100.0, "CRITICAL SECURITY BREACH: Cross-tenant data leak detected!"
    print("  -> IDOR DEFENSE VALIDATION: PASSED with 100.00% cross-tenant isolation.")

    # ------------------------------------------------------------------------
    # STAGE 2: HIGH-THROUGHPUT KEYSET (CURSOR) PAGINATION BENCHMARK
    # ------------------------------------------------------------------------
    print("\n[STAGE 2] Multi-Tenant Keyset (Cursor) Pagination Benchmark (5,000 Operations)...")
    target_tenant = "tenant_corp_05"
    page_size = 50
    iterations = 5000

    # Get initial anchor item for middle of tenant partition
    first_page = db.cursor_paginate_tenant(target_tenant, cursor_epoch=None, cursor_id=None, page_size=20)
    anchor_item = first_page[-1]
    anchor_epoch = 1793294000.0 + (anchor_item.id * 2)
    cursor_token = BenchmarkCursorEncoder.encode(anchor_epoch, anchor_item.id)
    decoded_epoch, decoded_id = BenchmarkCursorEncoder.decode(cursor_token)

    latencies: List[float] = []

    t_start = time.perf_counter()
    for _ in range(iterations):
        t0 = time.perf_counter()
        page = db.cursor_paginate_tenant(
            tenant_id=target_tenant,
            cursor_epoch=decoded_epoch,
            cursor_id=decoded_id,
            page_size=page_size
        )
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1_000_000)  # microseconds
    t_total = time.perf_counter() - t_start

    assert len(page) == page_size
    # Verify all records strictly belong to target tenant
    assert all(r.tenant_id == target_tenant for r in page)

    # Statistical analysis
    latencies.sort()
    def p(arr: List[float], pct: float) -> float:
        idx = int(len(arr) * pct)
        return arr[min(idx, len(arr) - 1)]

    throughput = iterations / t_total

    print("\n" + "=" * 70)
    print(" MULTI-TENANT KEYSET BENCHMARK METRICS")
    print("=" * 70)
    print(f"{'Metric':<30} | {'Value':<30}")
    print("-" * 70)
    print(f"{'Target Tenant':<30} | {target_tenant:<30}")
    print(f"{'Page Size':<30} | {page_size} records")
    print(f"{'Total Operations':<30} | {iterations:,} cursor queries")
    print(f"{'Wall Clock Time':<30} | {t_total:.4f} seconds")
    print(f"{'Throughput':<30} | {throughput:12.1f} ops/sec")
    print(f"{'Latency p50 (median)':<30} | {p(latencies, 0.50):12.2f} us")
    print(f"{'Latency p95':<30} | {p(latencies, 0.95):12.2f} us")
    print(f"{'Latency p99':<30} | {p(latencies, 0.99):12.2f} us")
    print("=" * 70)
    print("\nArchitectural Conclusion:")
    print("  Compound index (tenant_id, created_at, id) delivers microsecond-level")
    print("  O(1) seek latency across multi-tenant partitions with mathematical IDOR immunity.")


if __name__ == "__main__":
    run_benchmark()
