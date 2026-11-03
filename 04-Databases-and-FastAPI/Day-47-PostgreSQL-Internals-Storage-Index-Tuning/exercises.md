# PostgreSQL Internals & Index Tuning Practical Exercises

This document provides three senior-level database engineering challenges designed to reinforce low-level understanding of page layouts, MVCC visibility engines, and query planner cost models. Complete reference solutions in pure Python standard library are included.

---

## Challenge 1: 8KB Slotted Page In-Place Compactor (VACUUM Page Defragmenter)

### Problem Specification
When tuples are deleted or updated, dead byte gaps remain in the upper section of an 8KB heap page. Over time, free space becomes severely fragmented, preventing new large tuples from fitting even if total free bytes are sufficient.

Write a function `compact_slotted_page(page_bytes: bytearray, dead_line_numbers: set[int]) -> int` that:
1. Parses the 24-byte `PageHeaderData` (`pd_lower`, `pd_upper`).
2. Identifies all line pointers (`ItemIdData`) from offset 24 to `pd_lower`.
3. Marks line pointers in `dead_line_numbers` as `LP_UNUSED` (`flags = 0`).
4. Re-packs all surviving live tuples contiguously upwards from the bottom of the page (offset 8192).
5. Updates surviving line pointers with their new contiguous byte offsets.
6. Updates `pd_upper` to the start of the newly packed live data and recalculates free space.
7. Returns the total contiguous free bytes available between `pd_lower` and `pd_upper`.

### Reference Solution & Verification Test

```python
import struct
from typing import Set, Tuple, List

PAGE_SIZE = 8192
PAGE_HEADER_SIZE = 24  # pd_lsn (8), pd_checksum (2), pd_flags (2), pd_lower (2), pd_upper (2), pd_special (2), pd_ver (2), pd_prune_xid (4)
PAGE_HEADER_FORMAT = ">QHHHHHHI"
ITEM_ID_FORMAT = ">HH"  # offset (16b), (flags << 14) | (len & 0x3FFF)
ITEM_ID_SIZE = 4

def compact_slotted_page(page_raw: bytearray, dead_offsets: Set[int]) -> int:
    """
    Defragments an 8KB page in-place, repacking live tuples towards 8192.
    Returns the new contiguous free space gap (pd_upper - pd_lower).
    """
    # 1. Unpack header
    (pd_lsn, pd_checksum, pd_flags, pd_lower, pd_upper,
     pd_special, pd_pagesize_ver, pd_prune_xid) = struct.unpack_from(PAGE_HEADER_FORMAT, page_raw, 0)
    
    num_items = (pd_lower - PAGE_HEADER_SIZE) // ITEM_ID_SIZE
    live_tuples: List[Tuple[int, bytes]] = []  # (offset_num, tuple_bytes)

    # 2. Extract live tuples and mark dead slots
    for off_num in range(1, num_items + 1):
        line_pos = PAGE_HEADER_SIZE + (off_num - 1) * ITEM_ID_SIZE
        tuple_off, field2 = struct.unpack_from(ITEM_ID_FORMAT, page_raw, line_pos)
        flags = (field2 >> 14) & 0x3
        length = field2 & 0x3FFF

        if flags == 1:  # LP_NORMAL
            if off_num in dead_offsets:
                # Mark as unused
                struct.pack_into(ITEM_ID_FORMAT, page_raw, line_pos, 0, 0)
            else:
                data = bytes(page_raw[tuple_off : tuple_off + length])
                live_tuples.append((off_num, data))

    # 3. Compact live tuples towards end of page (offset 8192)
    curr_upper = PAGE_SIZE
    for off_num, data in live_tuples:
        curr_upper -= len(data)
        page_raw[curr_upper : curr_upper + len(data)] = data
        
        # Update line pointer
        line_pos = PAGE_HEADER_SIZE + (off_num - 1) * ITEM_ID_SIZE
        field2 = (1 << 14) | (len(data) & 0x3FFF)  # LP_NORMAL
        struct.pack_into(ITEM_ID_FORMAT, page_raw, line_pos, curr_upper, field2)

    # 4. Update header
    struct.pack_into(
        PAGE_HEADER_FORMAT, page_raw, 0,
        pd_lsn, pd_checksum, pd_flags, pd_lower, curr_upper,
        pd_special, pd_pagesize_ver, pd_prune_xid
    )

    return curr_upper - pd_lower

# Test Verification
if __name__ == "__main__":
    page = bytearray(PAGE_SIZE)
    # Initialize header
    struct.pack_into(PAGE_HEADER_FORMAT, page, 0, 0, 0, 0, PAGE_HEADER_SIZE + (3 * ITEM_ID_SIZE), 8192 - 300, 8192, 4, 0)
    # Add 3 dummy tuples (100 bytes each)
    for i in range(1, 4):
        t_off = 8192 - (i * 100)
        line_pos = PAGE_HEADER_SIZE + (i - 1) * ITEM_ID_SIZE
        struct.pack_into(ITEM_ID_FORMAT, page, line_pos, t_off, (1 << 14) | 100)
        page[t_off : t_off + 100] = b"X" * 100

    # Compact deleting tuple #2
    reclaimed_gap = compact_slotted_page(page, dead_offsets={2})
    print(f"Compaction Test Passed! New Free Space Gap: {reclaimed_gap} bytes (Expected: {8192 - 200 - 36} bytes)")
    assert reclaimed_gap == (8192 - 200 - 36)
```

---

## Challenge 2: MVCC Snapshot Visibility Evaluator

### Problem Specification
Implement the core visibility evaluation algorithm replicating PostgreSQL's `HeapTupleSatisfiesMVCC`.

Define a class `MVCCVisibilityChecker` that accepts:
- A set of committed transaction IDs (`committed_xids`).
- A set of aborted transaction IDs (`aborted_xids`).

Implement `is_visible(t_xmin: int, t_xmax: int, snapshot: Snapshot, current_xid: int) -> bool` handling:
1. Tuples created by current transaction (`t_xmin == current_xid`).
2. Tuples created by committed transactions prior to snapshot.
3. Tuples deleted by in-flight or uncommitted transactions.
4. Tuples deleted by committed transactions prior to snapshot.

### Reference Solution & Verification Test

```python
from dataclasses import dataclass
from typing import Set

@dataclass(frozen=True)
class Snapshot:
    xmin: int
    xmax: int
    active_xids: Set[int]

class MVCCVisibilityChecker:
    def __init__(self, committed_xids: Set[int], aborted_xids: Set[int]):
        self.committed = committed_xids
        self.aborted = aborted_xids

    def is_visible(self, t_xmin: int, t_xmax: int, snapshot: Snapshot, current_xid: int) -> bool:
        # 1. Creator evaluation
        if t_xmin == current_xid:
            # We created it in this transaction
            if t_xmax == 0:
                return True
            if t_xmax == current_xid:
                return False  # Created and deleted in same transaction
            return True

        if t_xmin in self.aborted:
            return False  # Created by aborted transaction

        if t_xmin >= snapshot.xmax or t_xmin in snapshot.active_xids:
            return False  # In-flight or future transaction

        if t_xmin not in self.committed:
            return False

        # 2. Deleter / Updater evaluation
        if t_xmax == 0:
            return True  # Never deleted/updated

        if t_xmax == current_xid:
            return False  # Deleted by current transaction

        if t_xmax in self.aborted:
            return True  # Deleting transaction aborted; tuple still alive

        if t_xmax >= snapshot.xmax or t_xmax in snapshot.active_xids:
            return True  # Deletion is still concurrent/uncommitted

        if t_xmax in self.committed:
            return False  # Deletion committed before our snapshot; tuple is dead

        return True

# Test Verification
if __name__ == "__main__":
    checker = MVCCVisibilityChecker(committed_xids={100, 101, 102}, aborted_xids={103})
    snap = Snapshot(xmin=102, xmax=105, active_xids={104})
    curr = 105

    # Case A: Created by 101 (committed), not deleted -> Visible
    assert checker.is_visible(t_xmin=101, t_xmax=0, snapshot=snap, current_xid=curr) is True

    # Case B: Created by 103 (aborted) -> Invisible
    assert checker.is_visible(t_xmin=103, t_xmax=0, snapshot=snap, current_xid=curr) is False

    # Case C: Created by 101, deleted by 102 (committed before snapshot) -> Invisible
    assert checker.is_visible(t_xmin=101, t_xmax=102, snapshot=snap, current_xid=curr) is False

    # Case D: Created by 101, deleted by 104 (currently in-flight in snapshot) -> Visible
    assert checker.is_visible(t_xmin=101, t_xmax=104, snapshot=snap, current_xid=curr) is True
    print("MVCC Visibility Engine Verification Passed!")
```

---

## Challenge 3: Automated Index Advisor & Cost Optimizer

### Problem Specification
Write an automated index advisor function `recommend_access_path(total_pages: int, total_rows: int, selectivity: float, is_all_visible: bool, is_ssd: bool) -> dict` that:
1. Sets `random_page_cost = 1.1` if `is_ssd` is True, otherwise `4.0`.
2. Computes the estimated execution costs for:
   - Sequential Scan
   - B-Tree Index Scan
   - Bitmap Index + Heap Scan
   - Index-Only Scan
3. Identifies the optimal access path.
4. Explains whether setting SSD parameters changed the recommended plan.

### Reference Solution & Verification Test

```python
import math
from typing import Dict, Any

def recommend_access_path(
    total_pages: int,
    total_rows: int,
    selectivity: float,
    is_all_visible: bool,
    is_ssd: bool
) -> Dict[str, Any]:
    seq_page_cost = 1.0
    random_page_cost = 1.1 if is_ssd else 4.0
    cpu_tuple_cost = 0.01
    cpu_idx_tuple_cost = 0.005
    cpu_op_cost = 0.0025

    est_rows = max(1.0, total_rows * selectivity)
    idx_pages = math.ceil(total_pages * 0.1 * selectivity)

    # 1. Sequential Scan Cost
    cost_seq = (total_pages * seq_page_cost) + (total_rows * (cpu_tuple_cost + cpu_op_cost))

    # 2. Index Scan Cost
    heap_pages_random = min(total_pages, est_rows)
    cost_idx = (3 * random_page_cost) + (idx_pages * random_page_cost) + (heap_pages_random * random_page_cost) + (est_rows * (cpu_idx_tuple_cost + cpu_tuple_cost))

    # 3. Bitmap Heap Scan Cost
    est_pages_bm = total_pages * (1.0 - math.exp(-est_rows / total_pages)) if total_pages > 0 else 1.0
    cost_bm = (idx_pages * random_page_cost) + (est_rows * cpu_idx_tuple_cost) + (est_pages_bm * seq_page_cost) + (est_rows * cpu_tuple_cost)

    # 4. Index-Only Scan Cost
    if is_all_visible:
        cost_ios = (3 * random_page_cost) + (idx_pages * random_page_cost) + (est_rows * (cpu_idx_tuple_cost + cpu_tuple_cost))
    else:
        cost_ios = cost_idx  # Falls back to heap checks

    candidates = {
        "Index-Only Scan": cost_ios if is_all_visible else float("inf"),
        "B-Tree Index Scan": cost_idx,
        "Bitmap Heap Scan": cost_bm,
        "Sequential Scan": cost_seq
    }

    best_plan = min(candidates.keys(), key=lambda k: candidates[k])
    return {
        "recommended_plan": best_plan,
        "lowest_cost": round(candidates[best_plan], 2),
        "all_candidate_costs": {k: round(v, 2) for k, v in candidates.items()},
        "random_page_cost_used": random_page_cost
    }

# Test Verification
if __name__ == "__main__":
    # Test 1% selectivity on 100,000 rows across 1,600 pages:
    plan_hdd = recommend_access_path(1600, 100_000, 0.01, is_all_visible=False, is_ssd=False)
    plan_ssd = recommend_access_path(1600, 100_000, 0.01, is_all_visible=False, is_ssd=True)
    plan_ios = recommend_access_path(1600, 100_000, 0.01, is_all_visible=True, is_ssd=True)

    print("HDD Recommendation :", plan_hdd["recommended_plan"], plan_hdd["lowest_cost"])
    print("SSD Recommendation :", plan_ssd["recommended_plan"], plan_ssd["lowest_cost"])
    print("IOS Recommendation :", plan_ios["recommended_plan"], plan_ios["lowest_cost"])
    assert plan_ios["recommended_plan"] == "Index-Only Scan"
    print("Cost Optimizer & Index Advisor Tests Passed!")
```
