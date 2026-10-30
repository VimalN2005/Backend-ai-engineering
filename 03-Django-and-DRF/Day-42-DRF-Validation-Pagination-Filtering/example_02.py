"""
Day 42: Enterprise High-Throughput Keyset (Cursor) Pagination vs Deep Offset Benchmark
======================================================================================

Enterprise Problem Space:
At scale (millions of records), offset-based pagination (`OFFSET N LIMIT M`) degrades
linearly because relational databases must physically scan and discard N index or table
rows before returning M results.

This benchmark models a production FinTech Multi-Tenant Ledger Service containing
50,000+ transactions across enterprise tenants.

It compares:
1. Deep Offset Pagination: Scanning 45,000 rows to fetch 50 items (O(N) degradation).
2. Keyset (Cursor) Pagination: Direct B-Tree index seek using an opaque compound cursor
   `(tenant_id, created_at, id)` to fetch 50 items in O(1) constant time.

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import base64
import bisect
import json
import random
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# SECTION 1: IN-MEMORY DATABASE & COMPOSITE B-TREE INDEX SIMULATION
# ============================================================================

class TransactionRecord:
    __slots__ = ("id", "tenant_id", "created_at", "amount", "currency", "status", "memo")

    def __init__(
        self,
        id: int,
        tenant_id: str,
        created_at: str,
        amount: Decimal,
        currency: str,
        status: str,
        memo: str
    ) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.created_at = created_at
        self.amount = amount
        self.currency = currency
        self.status = status
        self.memo = memo

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "amount": str(self.amount),
            "currency": self.currency,
            "status": self.status,
            "memo": self.memo,
        }


class EnterpriseLedgerDatabase:
    """
    Simulates an indexed PostgreSQL table storing transactions.
    
    Composite Index:
    `(tenant_id, created_at DESC, id DESC)`
    Allows binary search (bisect) directly to the cursor anchor in O(log N) time,
    mirroring PostgreSQL B-Tree index scans.
    """
    def __init__(self) -> None:
        self.records: List[TransactionRecord] = []
        # Index keys: (tenant_id, -timestamp_epoch, -id) -> record reference
        self.index_keys: List[Tuple[str, float, int]] = []
        self.index_records: List[TransactionRecord] = []

    def insert(self, record: TransactionRecord) -> None:
        self.records.append(record)
        dt = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
        epoch = dt.timestamp()
        
        # Sort descending: negate epoch and id for standard bisect
        key = (record.tenant_id, -epoch, -record.id)
        
        idx = bisect.bisect_left(self.index_keys, key)
        self.index_keys.insert(idx, key)
        self.index_records.insert(idx, record)

    def offset_query(
        self,
        tenant_id: str,
        offset: int,
        limit: int
    ) -> List[TransactionRecord]:
        """
        Simulates:
        SELECT * FROM ledger WHERE tenant_id = '...' ORDER BY created_at DESC, id DESC
        OFFSET 45000 LIMIT 50;
        
        WHY O(N) SLOW:
        Even with an index, the database must scan past `offset` rows, checking visibility
        and counting each row before returning the slice.
        """
        # Linear iteration simulating SQL engine scanning and discarding `offset` rows
        scanned = 0
        results: List[TransactionRecord] = []
        
        for rec in self.index_records:
            if rec.tenant_id == tenant_id:
                if scanned >= offset:
                    results.append(rec)
                    if len(results) == limit:
                        break
                scanned += 1
        return results

    def cursor_seek_query(
        self,
        tenant_id: str,
        cursor_epoch: Optional[float],
        cursor_id: Optional[int],
        limit: int
    ) -> List[TransactionRecord]:
        """
        Simulates:
        SELECT * FROM ledger 
        WHERE tenant_id = '...' AND (created_at, id) < (cursor_timestamp, cursor_id)
        ORDER BY created_at DESC, id DESC
        LIMIT 50;
        
        WHY O(1) FAST:
        Uses B-Tree index bisect to jump straight to the cursor position, then scans
        exactly `limit` rows.
        """
        if cursor_epoch is None:
            # First page: seek to start of tenant partition
            seek_key = (tenant_id, -float("inf"), -int(1e9))
        else:
            # Keyset seek: jump directly after the cursor tuple
            seek_key = (tenant_id, -cursor_epoch, -cursor_id)

        # O(log N) binary search into B-tree index
        idx = bisect.bisect_right(self.index_keys, seek_key)

        results: List[TransactionRecord] = []
        total_len = len(self.index_records)

        # Scan only `limit` rows from the indexed seek point
        while idx < total_len and len(results) < limit:
            rec = self.index_records[idx]
            if rec.tenant_id != tenant_id:
                break  # Reached end of tenant partition
            results.append(rec)
            idx += 1

        return results


# ============================================================================
# SECTION 2: PRODUCTION CURSOR ENCODER & RFC 5988 LINK GENERATOR
# ============================================================================

class KeysetCursorEncoder:
    """Opaque Base64URL cursor token encoder/decoder."""
    
    @staticmethod
    def encode(epoch: float, record_id: int) -> str:
        payload = {"t": round(epoch, 3), "i": record_id}
        raw_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")

    @staticmethod
    def decode(token: str) -> Optional[Tuple[float, int]]:
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return (float(data["t"]), int(data["i"]))
        except Exception:
            return None


def generate_rfc_5988_link_header(
    base_url: str,
    next_cursor: Optional[str],
    prev_cursor: Optional[str]
) -> str:
    """
    Format standard RFC 5988 Link headers for HATEOAS client pagination.
    """
    links: List[str] = []
    if next_cursor:
        links.append(f'<{base_url}?cursor={next_cursor}>; rel="next"')
    if prev_cursor:
        links.append(f'<{base_url}?cursor={prev_cursor}>; rel="prev"')
    return ", ".join(links)


# ============================================================================
# SECTION 3: BENCHMARK SUITE (OFFSET VS CURSOR AT SCALE)
# ============================================================================

def populate_database(db: EnterpriseLedgerDatabase, count: int = 50000) -> None:
    print(f"Generating {count:,} synthetic financial ledger records across 5 tenants...")
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    tenants = ["tenant_acme", "tenant_stripe", "tenant_shopify", "tenant_plaid", "tenant_revolut"]
    currencies = ["USD", "EUR", "GBP", "JPY"]
    statuses = ["COMPLETED", "COMPLETED", "COMPLETED", "PENDING", "FAILED"]

    random.seed(42)  # Deterministic repeatability

    for i in range(1, count + 1):
        t_id = tenants[i % len(tenants)]
        # Microsecond spread to test tie-breakers
        timestamp = base_time + timedelta(seconds=i * 5, milliseconds=random.randint(0, 999))
        iso_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        amount = Decimal(f"{random.uniform(5.00, 15000.00):.2f}")
        currency = currencies[i % len(currencies)]
        status = statuses[i % len(statuses)]
        memo = f"Transfer ref #{i:08d} payload settlement"

        rec = TransactionRecord(
            id=i,
            tenant_id=t_id,
            created_at=iso_str,
            amount=amount,
            currency=currency,
            status=status,
            memo=memo
        )
        db.insert(rec)

    print(f"Database populated with {len(db.records):,} indexed transactions.")


def run_benchmark() -> None:
    print("=" * 70)
    print(" DAY 42: ENTERPRISE KEYSET (CURSOR) VS OFFSET PAGINATION BENCHMARK")
    print("=" * 70)

    db = EnterpriseLedgerDatabase()
    populate_database(db, count=50000)

    target_tenant = "tenant_acme"
    page_size = 50
    deep_offset = 8000  # Deep page inside tenant partition (~10,000 records)
    iterations = 5000

    print(f"\nTarget Tenant        : {target_tenant}")
    print(f"Page Size            : {page_size} records")
    print(f"Offset Position      : Row {deep_offset:,} (Deep page)")
    print(f"Iterations           : {iterations:,} trials each")

    # ------------------------------------------------------------------------
    # BENCHMARK 1: DEEP OFFSET PAGINATION
    # ------------------------------------------------------------------------
    print("\nExecuting Benchmark 1: Deep Offset Pagination (OFFSET 8,000 LIMIT 50)...")
    offset_latencies: List[float] = []
    
    t0 = time.perf_counter()
    for _ in range(iterations):
        t_start = time.perf_counter()
        results = db.offset_query(tenant_id=target_tenant, offset=deep_offset, limit=page_size)
        t_end = time.perf_counter()
        offset_latencies.append((t_end - t_start) * 1_000_000)  # microseconds
    t_total_offset = time.perf_counter() - t0

    assert len(results) == page_size
    anchor_item = results[0]
    dt_anchor = datetime.fromisoformat(anchor_item.created_at.replace("Z", "+00:00"))
    cursor_token = KeysetCursorEncoder.encode(dt_anchor.timestamp(), anchor_item.id)

    # ------------------------------------------------------------------------
    # BENCHMARK 2: KEYSET (CURSOR) PAGINATION
    # ------------------------------------------------------------------------
    print(f"Executing Benchmark 2: Keyset Cursor Seek (Anchor ID: {anchor_item.id})...")
    cursor_latencies: List[float] = []
    decoded_epoch, decoded_id = KeysetCursorEncoder.decode(cursor_token)

    t0 = time.perf_counter()
    for _ in range(iterations):
        t_start = time.perf_counter()
        cursor_results = db.cursor_seek_query(
            tenant_id=target_tenant,
            cursor_epoch=decoded_epoch,
            cursor_id=decoded_id,
            limit=page_size
        )
        t_end = time.perf_counter()
        cursor_latencies.append((t_end - t_start) * 1_000_000)  # microseconds
    t_total_cursor = time.perf_counter() - t0

    assert len(cursor_results) == page_size

    # Calculate statistics
    offset_latencies.sort()
    cursor_latencies.sort()

    def p(arr: List[float], pct: float) -> float:
        idx = int(len(arr) * pct)
        return arr[min(idx, len(arr) - 1)]

    speedup = t_total_offset / max(t_total_cursor, 1e-9)

    print("\n" + "=" * 70)
    print(" PERFORMANCE BENCHMARK RESULTS")
    print("=" * 70)
    print(f"{'Metric':<25} | {'Deep Offset (O(N))':<20} | {'Keyset Cursor (O(1))':<20}")
    print("-" * 70)
    print(f"{'Wall Clock Time':<25} | {t_total_offset:.4f} s{'':<13} | {t_total_cursor:.4f} s")
    print(f"{'Throughput (ops/sec)':<25} | {iterations / t_total_offset:12.1f} ops/s  | {iterations / t_total_cursor:12.1f} ops/s")
    print(f"{'Latency p50 (median)':<25} | {p(offset_latencies, 0.50):12.2f} us     | {p(cursor_latencies, 0.50):12.2f} us")
    print(f"{'Latency p95':<25} | {p(offset_latencies, 0.95):12.2f} us     | {p(cursor_latencies, 0.95):12.2f} us")
    print(f"{'Latency p99':<25} | {p(offset_latencies, 0.99):12.2f} us     | {p(cursor_latencies, 0.99):12.2f} us")
    print("-" * 70)
    print(f"Keyset Pagination Speedup: {speedup:.1f}x FASTER than Deep Offset")
    print("=" * 70)

    # ------------------------------------------------------------------------
    # HATEOAS RFC 5988 LINK HEADER VERIFICATION
    # ------------------------------------------------------------------------
    next_anchor = cursor_results[-1]
    next_dt = datetime.fromisoformat(next_anchor.created_at.replace("Z", "+00:00"))
    next_token = KeysetCursorEncoder.encode(next_dt.timestamp(), next_anchor.id)

    link_header = generate_rfc_5988_link_header(
        base_url="https://api.fintech.enterprise/v1/ledger",
        next_cursor=next_token,
        prev_cursor=cursor_token
    )
    print(f"\nGenerated RFC 5988 Link Header:\n  {link_header}\n")


if __name__ == "__main__":
    run_benchmark()
