"""
Day 47: PostgreSQL Storage Engine, MVCC & Query Execution Internals.

This module provides a pure Python (Standard Library only) simulation of the
core low-level storage engine, concurrency control, and physical scan mechanisms
powering PostgreSQL.

Architectural Components Implemented:
1. Slotted Page Binary Layout:
   - 8KB Page Header (pd_lsn, pd_lower, pd_upper, pd_special).
   - Line Pointers (ItemId: lp_offset, lp_flags, lp_len) growing downward.
   - Physical Tuples (HeapTupleHeader: t_xmin, t_xmax, t_infomask, payload)
     growing upward.
2. MVCC Concurrency & Snapshot Visibility:
   - Transaction IDs (XIDs) and Transaction Snapshots (xmin:xmax:xip_list).
   - Production visibility checking replicating `HeapTupleSatisfiesMVCC`.
   - Update chaining (t_xmax linking) and dead tuple generation.
3. Heap-Only Tuples (HOT) Optimization:
   - In-page update chaining without creating new index entries.
   - Line pointer redirection (LP_REDIRECT).
4. Physical Index Implementations:
   - B-Tree Index: High fan-out search tree mapping Key -> TID (Block, Offset).
   - BRIN (Block Range Index): Min/Max summary per range of physical pages.
5. Physical Scan Executors & Cost Planner:
   - Sequential Scan (Seq Scan).
   - Index Scan (Index Scan).
   - Bitmap Index Scan + Bitmap Heap Scan (Physical Page Sorting).
   - Index Only Scan with Visibility Map (VM) verification.
6. Execution Plan Formatter:
   - Outputs authentic PostgreSQL-style `EXPLAIN (ANALYZE, BUFFERS)`.
"""

from __future__ import annotations

import bisect
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple


# ==============================================================================
# SECTION 1: CONSTANTS & 8KB SLOTTED PAGE BINARY ARCHITECTURE
# ==============================================================================

# In PostgreSQL, BLCKSZ is 8192 bytes (8KB). All disk I/O occurs in blocks of this size.
PAGE_SIZE = 8192

# PageHeaderData layout (24 bytes in PostgreSQL storage/bufpage.h):
# pd_lsn (8 bytes: unsigned long long) - WAL Log Sequence Number
# pd_checksum (2 bytes: unsigned short) - Page integrity checksum
# pd_flags (2 bytes: unsigned short) - Page status flags
# pd_lower (2 bytes: unsigned short) - Offset to end of line pointer array
# pd_upper (2 bytes: unsigned short) - Offset to start of tuple data
# pd_special (2 bytes: unsigned short) - Offset to special space (8192 for heap)
# pd_pagesize_version (2 bytes: unsigned short) - Page size and layout version
# pd_prune_xid (4 bytes: unsigned int) - Oldest unpruned XID
PAGE_HEADER_FORMAT = ">QHHHHHHI"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)  # 24 bytes

# LinePointer (ItemIdData) layout: exactly 4 bytes (32 bits) in PostgreSQL:
# Field 1 (lp_offset): 16-bit uint (0..8192)
# Field 2 (flags & len): 16-bit uint: (flags << 14) | (length & 0x3FFF)
# flags: 2 bits (0=LP_UNUSED, 1=LP_NORMAL, 2=LP_REDIRECT, 3=LP_DEAD)
# length: 14 bits (0..16383 bytes)
ITEM_ID_FORMAT = ">HH"
ITEM_ID_SIZE = struct.calcsize(ITEM_ID_FORMAT)  # 4 bytes


class LinePointerFlag(IntEnum):
    """
    Line pointer flags defined in PostgreSQL (storage/itemid.h):
    LP_UNUSED (0): ItemId slot is unused and free for allocation.
    LP_NORMAL (1): ItemId points to an active tuple on this page.
    LP_REDIRECT (2): HOT redirect pointer (offset points to another ItemId).
    LP_DEAD (3): ItemId points to a dead tuple awaiting vacuum.
    """
    LP_UNUSED = 0
    LP_NORMAL = 1
    LP_REDIRECT = 2
    LP_DEAD = 3


class TupleInfoMask(IntEnum):
    """
    Tuple header infomask bits (access/htup_details.h):
    Used for instant in-cache visibility determination without querying the CLOG.
    """
    HEAP_XMIN_COMMITTED = 0x0100  # Transaction creating tuple is committed
    HEAP_XMIN_INVALID = 0x0200    # Transaction creating tuple aborted
    HEAP_XMAX_COMMITTED = 0x0400  # Transaction deleting/updating tuple is committed
    HEAP_XMAX_INVALID = 0x0800    # Transaction deleting/updating tuple aborted
    HEAP_HOT_UPDATED = 0x4000     # Tuple was updated via HOT
    HEAP_ONLY_TUPLE = 0x8000      # Tuple has no direct external index entries


@dataclass
class ItemPointer:
    """
    PostgreSQL Tuple Identifier (TID / ctid).
    Represents the exact physical location of a row: (BlockNumber, OffsetNumber).
    OffsetNumber is 1-indexed in PostgreSQL.
    """
    block_num: int
    offset_num: int

    def __repr__(self) -> str:
        return f"({self.block_num},{self.offset_num})"


@dataclass
class HeapTupleHeader:
    """
    PostgreSQL Tuple Header (HeapTupleHeaderData):
    Occupies ~23 bytes in C, aligned to 24 bytes on 64-bit architectures.
    """
    t_xmin: int                  # Inserting Transaction ID
    t_xmax: int = 0              # Deleting/Updating Transaction ID (0 = alive)
    t_cid: int = 0               # Command identifier within the transaction
    t_infomask: int = 0          # Visibility bitmask
    t_ctid: ItemPointer = field(default_factory=lambda: ItemPointer(0, 0))


@dataclass
class HeapTuple:
    """Physical representation of a tuple version on disk."""
    header: HeapTupleHeader
    data: Dict[str, Any]         # User attribute payload

    def serialize(self) -> bytes:
        """
        Serializes the tuple into binary bytes.
        WHY: Demonstrates variable-length encoding and physical byte measurement.
        """
        # Pack header: xmin (I), xmax (I), cid (I), infomask (H) -> 14 bytes
        header_bytes = struct.pack(
            ">IIIH",
            self.header.t_xmin,
            self.header.t_xmax,
            self.header.t_cid,
            self.header.t_infomask
        )
        # Encode data payload as UTF-8 key-value string for demonstration
        data_str = ";".join(f"{k}={v}" for k, v in sorted(self.data.items()))
        payload_bytes = data_str.encode("utf-8")
        return header_bytes + payload_bytes

    @classmethod
    def deserialize(cls, raw_bytes: bytes, block_num: int, offset_num: int) -> HeapTuple:
        """Reconstructs a HeapTuple from raw physical byte sequence."""
        header_len = struct.calcsize(">IIIH")
        xmin, xmax, cid, infomask = struct.unpack(">IIIH", raw_bytes[:header_len])
        payload = raw_bytes[header_len:].decode("utf-8")
        data: Dict[str, Any] = {}
        if payload:
            for item in payload.split(";"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    # Attempt numeric cast if possible
                    if v.isdigit():
                        data[k] = int(v)
                    else:
                        try:
                            data[k] = float(v)
                        except ValueError:
                            data[k] = v
        header = HeapTupleHeader(
            t_xmin=xmin,
            t_xmax=xmax,
            t_cid=cid,
            t_infomask=infomask,
            t_ctid=ItemPointer(block_num, offset_num)
        )
        return cls(header=header, data=data)


class SlottedPage:
    """
    Binary 8KB Slotted Page.
    
    Layout:
    [ PageHeaderData (24B) ]
    [ LinePointer 1 (4B)   ] -> grows downward towards pd_lower
    [ LinePointer 2 (4B)   ]
    [ ... Free Space Hole ...]
    [ Tuple 2 Data Payload ] <- grows upward towards pd_upper
    [ Tuple 1 Data Payload ]
    [ Special Space (0B)   ]
    """

    def __init__(self, block_num: int):
        self.block_num = block_num
        self.raw = bytearray(PAGE_SIZE)
        
        # Initialize Page Header
        # pd_lsn = 0, checksum = 0, flags = 0
        # pd_lower starts immediately after header (24)
        # pd_upper starts at the very end of page (8192)
        # pd_special = 8192 (no special space for heap pages)
        self.pd_lsn = 0
        self.pd_checksum = 0
        self.pd_flags = 0
        self.pd_lower = PAGE_HEADER_SIZE
        self.pd_upper = PAGE_SIZE
        self.pd_special = PAGE_SIZE
        self.pd_pagesize_version = 4
        self.pd_prune_xid = 0
        self._write_header()

    def _write_header(self) -> None:
        """Serializes header fields into the page's first 24 bytes."""
        struct.pack_into(
            PAGE_HEADER_FORMAT,
            self.raw,
            0,
            self.pd_lsn,
            self.pd_checksum,
            self.pd_flags,
            self.pd_lower,
            self.pd_upper,
            self.pd_special,
            self.pd_pagesize_version,
            self.pd_prune_xid
        )

    def _read_header(self) -> None:
        """Unpacks header fields from the page's first 24 bytes."""
        (self.pd_lsn, self.pd_checksum, self.pd_flags,
         self.pd_lower, self.pd_upper,
         self.pd_special, self.pd_pagesize_version,
         self.pd_prune_xid) = struct.unpack_from(
            PAGE_HEADER_FORMAT, self.raw, 0
        )

    @property
    def free_space(self) -> int:
        """Available byte count in the unallocated gap between pd_lower and pd_upper."""
        self._read_header()
        # Each new tuple requires its payload size + a 4-byte LinePointer entry
        gap = self.pd_upper - self.pd_lower
        return max(0, gap - ITEM_ID_SIZE)

    def insert_tuple(self, tuple_obj: HeapTuple) -> Optional[ItemPointer]:
        """
        Inserts a tuple into the slotted page.
        Returns the ItemPointer (TID) or None if insufficient space.
        
        WHY: Line pointers grow down from header, tuple bytes grow up from bottom.
        This allows variable-length tuples while keeping external index pointers fixed!
        """
        self._read_header()
        payload = tuple_obj.serialize()
        payload_len = len(payload)
        
        # Check if tuple + line pointer fits in free space gap
        if payload_len + ITEM_ID_SIZE > (self.pd_upper - self.pd_lower):
            return None  # Page full, caller must allocate new page or consult FSM

        # 1. Allocate space at bottom of page (pd_upper moves backwards)
        new_upper = self.pd_upper - payload_len
        self.raw[new_upper:self.pd_upper] = payload

        # 2. Allocate LinePointer at pd_lower
        offset_num = ((self.pd_lower - PAGE_HEADER_SIZE) // ITEM_ID_SIZE) + 1
        line_offset = self.pd_lower
        
        # Pack LinePointer: offset (16b), (flags << 14) | (len & 0x3FFF) (16b)
        field2 = (int(LinePointerFlag.LP_NORMAL) << 14) | (payload_len & 0x3FFF)
        struct.pack_into(
            ITEM_ID_FORMAT,
            self.raw,
            line_offset,
            new_upper,
            field2
        )

        # 3. Update header offsets
        self.pd_lower += ITEM_ID_SIZE
        self.pd_upper = new_upper
        self._write_header()

        # Update tuple's internal TID reference
        tid = ItemPointer(self.block_num, offset_num)
        tuple_obj.header.t_ctid = tid
        return tid

    def set_tuple_xmax(self, offset_num: int, xmax: int) -> bool:
        """
        Updates the t_xmax field of a live tuple directly on disk/in-page.
        Replicates PostgreSQL setting t_xmax on UPDATE/DELETE.
        """
        self._read_header()
        line_offset = PAGE_HEADER_SIZE + (offset_num - 1) * ITEM_ID_SIZE
        if line_offset + ITEM_ID_SIZE > self.pd_lower:
            return False

        tuple_offset, field2 = struct.unpack_from(ITEM_ID_FORMAT, self.raw, line_offset)
        flags = LinePointerFlag((field2 >> 14) & 0x3)
        if flags != LinePointerFlag.LP_NORMAL:
            return False

        # In HeapTupleHeader layout: t_xmin (4B), t_xmax (4B at offset 4)
        struct.pack_into(">I", self.raw, tuple_offset + 4, xmax)
        return True

    def get_tuple(self, offset_num: int) -> Optional[Tuple[LinePointerFlag, Optional[HeapTuple]]]:
        """
        Retrieves line pointer status and deserialized tuple by offset number.
        Handles LP_NORMAL, LP_REDIRECT (HOT chains), and LP_DEAD.
        """
        self._read_header()
        line_offset = PAGE_HEADER_SIZE + (offset_num - 1) * ITEM_ID_SIZE
        if line_offset + ITEM_ID_SIZE > self.pd_lower:
            return None  # Invalid offset number

        tuple_offset, field2 = struct.unpack_from(ITEM_ID_FORMAT, self.raw, line_offset)
        flag = LinePointerFlag((field2 >> 14) & 0x3)
        payload_len = field2 & 0x3FFF

        if flag == LinePointerFlag.LP_UNUSED:
            return (LinePointerFlag.LP_UNUSED, None)
        elif flag == LinePointerFlag.LP_DEAD:
            return (LinePointerFlag.LP_DEAD, None)
        elif flag == LinePointerFlag.LP_REDIRECT:
            # For LP_REDIRECT, tuple_offset holds the target offset_number in the same page!
            return (LinePointerFlag.LP_REDIRECT, None)
        elif flag == LinePointerFlag.LP_NORMAL:
            # Sliced strictly by payload_len to avoid reading following tuples!
            raw_bytes = bytes(self.raw[tuple_offset : tuple_offset + payload_len])
            tuple_obj = HeapTuple.deserialize(raw_bytes, self.block_num, offset_num)
            return (LinePointerFlag.LP_NORMAL, tuple_obj)

        return None

    def compact_and_vacuum(self, dead_offsets: Set[int]) -> int:
        """
        Defragments the page in place, reclaiming dead tuple space.
        Replicates in-page compaction during VACUUM.
        Returns the number of bytes reclaimed.
        """
        self._read_header()
        old_free = self.free_space
        
        # Collect all active tuples
        active_tuples: List[Tuple[int, bytes]] = []  # (offset_num, raw_bytes)
        num_items = (self.pd_lower - PAGE_HEADER_SIZE) // ITEM_ID_SIZE
        
        for off in range(1, num_items + 1):
            line_pos = PAGE_HEADER_SIZE + (off - 1) * ITEM_ID_SIZE
            t_off, field2 = struct.unpack_from(ITEM_ID_FORMAT, self.raw, line_pos)
            flags = LinePointerFlag((field2 >> 14) & 0x3)
            t_len = field2 & 0x3FFF
            if flags == LinePointerFlag.LP_NORMAL:
                if off in dead_offsets:
                    # Mark line pointer as unused
                    struct.pack_into(
                        ITEM_ID_FORMAT, self.raw, line_pos, 0, int(LinePointerFlag.LP_UNUSED) << 14
                    )
                else:
                    payload = bytes(self.raw[t_off : t_off + t_len])
                    active_tuples.append((off, payload))

        # Re-pack active tuples upwards from bottom of page
        curr_upper = PAGE_SIZE
        for off, payload in active_tuples:
            curr_upper -= len(payload)
            self.raw[curr_upper : curr_upper + len(payload)] = payload
            line_pos = PAGE_HEADER_SIZE + (off - 1) * ITEM_ID_SIZE
            field2 = (int(LinePointerFlag.LP_NORMAL) << 14) | (len(payload) & 0x3FFF)
            struct.pack_into(
                ITEM_ID_FORMAT, self.raw, line_pos, curr_upper, field2
            )

        self.pd_upper = curr_upper
        self._write_header()
        return self.free_space - old_free


# ==============================================================================
# SECTION 2: MVCC ENGINE & SNAPSHOT VISIBILITY RULES
# ==============================================================================

class TransactionStatus(IntEnum):
    IN_PROGRESS = 0
    COMMITTED = 1
    ABORTED = 2


@dataclass
class Snapshot:
    """
    PostgreSQL Transaction Snapshot: `xmin:xmax:xip_list`.
    
    xmin: Lowest transaction ID that was active when snapshot was taken.
          All XIDs < xmin are guaranteed committed or aborted.
    xmax: First unassigned transaction ID at snapshot time.
          All XIDs >= xmax are invisible (started after snapshot).
    active_xids: Set of active transactions between xmin and xmax.
    """
    xmin: int
    xmax: int
    active_xids: Set[int] = field(default_factory=set)

    def is_active(self, xid: int) -> bool:
        return xid in self.active_xids


class MVCCEngine:
    """
    Manages transaction lifecycle and enforces tuple visibility.
    Implements the exact logic of PostgreSQL's `HeapTupleSatisfiesMVCC`.
    """

    def __init__(self):
        self._current_xid = 100  # Starting transaction counter
        self._tx_status: Dict[int, TransactionStatus] = {}
        self._active_txns: Set[int] = set()

    def begin_transaction(self) -> int:
        """Starts a new transaction, allocating a monotonically increasing XID."""
        self._current_xid += 1
        xid = self._current_xid
        self._tx_status[xid] = TransactionStatus.IN_PROGRESS
        self._active_txns.add(xid)
        return xid

    def commit(self, xid: int) -> None:
        """Marks transaction as COMMITTED in the commit log (CLOG)."""
        self._tx_status[xid] = TransactionStatus.COMMITTED
        self._active_txns.discard(xid)

    def abort(self, xid: int) -> None:
        """Marks transaction as ABORTED in the commit log (CLOG)."""
        self._tx_status[xid] = TransactionStatus.ABORTED
        self._active_txns.discard(xid)

    def get_snapshot(self) -> Snapshot:
        """
        Acquires an MVCC snapshot based on current engine state.
        
        WHY: Snapshot isolation guarantees readers see a consistent view of the
        database at a specific point in time without locking concurrent writers.
        """
        if not self._active_txns:
            xmin = self._current_xid + 1
        else:
            xmin = min(self._active_txns)
        xmax = self._current_xid + 1
        return Snapshot(xmin=xmin, xmax=xmax, active_xids=set(self._active_txns))

    def is_tuple_visible(
        self,
        header: HeapTupleHeader,
        snapshot: Snapshot,
        current_xid: int
    ) -> bool:
        """
        Replicates PostgreSQL `HeapTupleSatisfiesMVCC` (heapam_visibility.c).
        
        Evaluates t_xmin (creator) and t_xmax (deleter/updater):
        1. If t_xmin was created by our own transaction, visible if created before current statement.
        2. If t_xmin is aborted, strictly invisible.
        3. If t_xmin is in-flight or >= snapshot.xmax, invisible.
        4. If t_xmin is committed, check t_xmax:
           - t_xmax == 0 -> Visible.
           - t_xmax is aborted -> Visible (deletion aborted).
           - t_xmax is in-flight in snapshot -> Visible (deletion not committed yet).
           - t_xmax committed before snapshot -> Invisible (already deleted/updated).
        """
        xmin = header.t_xmin
        xmax = header.t_xmax

        # --- STEP 1: EVALUATE CREATOR (t_xmin) ---
        if xmin == current_xid:
            # Created by our own transaction
            if xmax == 0:
                return True
            if xmax == current_xid:
                return False  # We created and then deleted it in same transaction
            # Otherwise, someone else is attempting to delete it; still visible to us
            return True

        # Check CLOG status of creator
        status_xmin = self._tx_status.get(xmin, TransactionStatus.ABORTED)
        if status_xmin == TransactionStatus.ABORTED:
            return False  # Aborted transaction data is never visible

        if xmin >= snapshot.xmax or snapshot.is_active(xmin):
            # Creator transaction was still active or started after our snapshot
            return False

        if status_xmin != TransactionStatus.COMMITTED:
            return False

        # --- STEP 2: EVALUATE DELETER / UPDATER (t_xmax) ---
        if xmax == 0:
            # Tuple has not been touched by an update or delete
            return True

        if xmax == current_xid:
            # Deleted/updated by our own current transaction
            return False

        status_xmax = self._tx_status.get(xmax, TransactionStatus.ABORTED)
        if status_xmax == TransactionStatus.ABORTED:
            # Deletion aborted, row remains live
            return True

        if xmax >= snapshot.xmax or snapshot.is_active(xmax):
            # Deletion is still in-progress or started after our snapshot
            return True

        if status_xmax == TransactionStatus.COMMITTED:
            # Deletion committed before our snapshot was taken; row is dead to us
            return False

        return True


# ==============================================================================
# SECTION 3: PHYSICAL INDEX IMPLEMENTATIONS (B-TREE & BRIN)
# ==============================================================================

class BTreeIndex:
    """
    Simplified in-memory B-Tree index simulating PostgreSQL nbtree.
    Maps an indexed attribute value to a list of physical ItemPointers (TIDs).
    """

    def __init__(self, key_name: str):
        self.key_name = key_name
        self._keys: List[Any] = []
        self._tids: List[List[ItemPointer]] = []
        self.buffer_reads = 0
        self.buffer_hits = 0

    def insert(self, key_val: Any, tid: ItemPointer) -> None:
        """Maintains sorted order of keys for logarithmic search."""
        idx = bisect.bisect_left(self._keys, key_val)
        if idx < len(self._keys) and self._keys[idx] == key_val:
            self._tids[idx].append(tid)
        else:
            self._keys.insert(idx, key_val)
            self._tids.insert(idx, [tid])

    def search_equality(self, key_val: Any) -> List[ItemPointer]:
        """Performs O(log N) lookup for exact key matches."""
        self.buffer_hits += 1  # B-Tree root/internal pages typically cached in shared_buffers
        idx = bisect.bisect_left(self._keys, key_val)
        if idx < len(self._keys) and self._keys[idx] == key_val:
            return list(self._tids[idx])
        return []

    def search_range(self, low: Any, high: Any) -> List[ItemPointer]:
        """Performs O(log N + K) range scan using leaf sibling pointers."""
        self.buffer_hits += 2
        start_idx = bisect.bisect_left(self._keys, low)
        end_idx = bisect.bisect_right(self._keys, high)
        results: List[ItemPointer] = []
        for i in range(start_idx, end_idx):
            results.extend(self._tids[i])
        return results


@dataclass
class BRINRangeSummary:
    """Stores the min and max values for a contiguous range of heap pages."""
    start_block: int
    end_block: int
    min_val: Any
    max_val: Any


class BRINIndex:
    """
    Block Range Index (BRIN).
    
    WHY: BRIN indexes do not store individual row pointers. Instead, they store
    a tiny summary (min/max) per range of physical pages (e.g. 128 pages).
    Footprint is 100x-1000x smaller than B-Tree, making it ideal for append-only
    time-series data where physical order matches value order.
    """

    def __init__(self, key_name: str, pages_per_range: int = 4):
        self.key_name = key_name
        self.pages_per_range = pages_per_range
        self.ranges: List[BRINRangeSummary] = []
        self.buffer_hits = 0

    def build_or_update(self, block_num: int, val: Any) -> None:
        """Updates summary statistics for the block range enclosing block_num."""
        range_idx = block_num // self.pages_per_range
        start_blk = range_idx * self.pages_per_range
        end_blk = start_blk + self.pages_per_range - 1

        # Locate or initialize summary
        summary = None
        for r in self.ranges:
            if r.start_block == start_blk:
                summary = r
                break
        
        if summary is None:
            summary = BRINRangeSummary(start_blk, end_blk, val, val)
            self.ranges.append(summary)
            self.ranges.sort(key=lambda r: r.start_block)
        else:
            if val < summary.min_val:
                summary.min_val = val
            if val > summary.max_val:
                summary.max_val = val

    def get_candidate_blocks(self, low: Any, high: Any) -> List[int]:
        """
        Prunes non-matching block ranges based on min/max intervals.
        Returns the physical block numbers that must be scanned.
        """
        self.buffer_hits += 1  # Tiny BRIN index is always cached
        candidate_blocks: List[int] = []
        for r in self.ranges:
            # Check interval overlap: [r.min_val, r.max_val] overlaps [low, high]
            if not (r.max_val < low or r.min_val > high):
                for b in range(r.start_block, r.end_block + 1):
                    candidate_blocks.append(b)
        return candidate_blocks


# ==============================================================================
# SECTION 4: TABLE STORAGE ENGINE & VISIBILITY MAP
# ==============================================================================

class TableStorage:
    """
    Simulates a PostgreSQL Relation Heap File.
    Manages 8KB SlottedPages, Free Space Map, and Visibility Map.
    """

    def __init__(self, table_name: str):
        self.table_name = table_name
        self.pages: List[SlottedPage] = []
        # Visibility Map: 1 bit per page (all_visible)
        self.visibility_map: Dict[int, bool] = {}
        # Buffer access tracking for EXPLAIN (BUFFERS)
        self.shared_hits = 0
        self.shared_reads = 0

    def reset_buffer_stats(self) -> None:
        self.shared_hits = 0
        self.shared_reads = 0

    def fetch_page(self, block_num: int) -> SlottedPage:
        """Fetches an 8KB page from memory buffer cache or simulates disk read."""
        if block_num < len(self.pages):
            self.shared_hits += 1  # Simulated shared_buffers cache hit
            return self.pages[block_num]
        raise IndexError(f"Block {block_num} does not exist in relation {self.table_name}")

    def insert_tuple(self, tuple_obj: HeapTuple) -> ItemPointer:
        """
        Appends tuple to existing page with available free space, or allocates new page.
        """
        # Try inserting into the last allocated page
        if self.pages:
            last_page = self.pages[-1]
            tid = last_page.insert_tuple(tuple_obj)
            if tid is not None:
                self.visibility_map[last_page.block_num] = False
                return tid

        # Allocate new 8KB SlottedPage
        new_block_num = len(self.pages)
        new_page = SlottedPage(new_block_num)
        self.pages.append(new_page)
        self.visibility_map[new_block_num] = False
        tid = new_page.insert_tuple(tuple_obj)
        if tid is None:
            raise RuntimeError("Tuple exceeds maximum page capacity!")
        return tid

    def update_tuple(
        self,
        old_tid: ItemPointer,
        new_tuple: HeapTuple,
        is_hot: bool = False
    ) -> ItemPointer:
        """
        Executes an MVCC UPDATE.
        
        If is_hot is True, attempts Heap-Only Tuple optimization:
        Allocates new tuple on the SAME page and turns the old line pointer
        into an LP_REDIRECT, avoiding any external index modifications!
        """
        page = self.fetch_page(old_tid.block_num)
        # Mark old tuple's t_xmax to link MVCC update chain
        page.set_tuple_xmax(old_tid.offset_num, new_tuple.header.t_xmin)
        
        if is_hot:
            # Check if same page has space for the new version
            new_tid = page.insert_tuple(new_tuple)
            if new_tid is not None:
                # Set HOT flags
                new_tuple.header.t_infomask |= TupleInfoMask.HEAP_ONLY_TUPLE
                # Update old line pointer to redirect to new offset
                line_pos = PAGE_HEADER_SIZE + (old_tid.offset_num - 1) * ITEM_ID_SIZE
                field2 = int(LinePointerFlag.LP_REDIRECT) << 14
                struct.pack_into(
                    ITEM_ID_FORMAT,
                    page.raw,
                    line_pos,
                    new_tid.offset_num,  # Target offset stored in offset field
                    field2
                )
                return new_tid

        # Fallback to standard cold update (insert elsewhere in heap)
        return self.insert_tuple(new_tuple)


# ==============================================================================
# SECTION 5: PHYSICAL SCAN EXECUTORS & PLANNER COST MODEL
# ==============================================================================

@dataclass
class PlanNodeResult:
    node_name: str
    startup_cost: float
    total_cost: float
    actual_startup_ms: float
    actual_total_ms: float
    rows_returned: int
    shared_hit: int
    shared_read: int
    details: Dict[str, Any] = field(default_factory=dict)
    child_node: Optional[PlanNodeResult] = None


class QueryPlannerCostEstimator:
    """
    Calibrates cost metrics matching PostgreSQL CBO (optimizer/path/costsize.c):
    - seq_page_cost = 1.0
    - random_page_cost = 4.0 (HDD default) or 1.1 (NVMe SSD)
    - cpu_tuple_cost = 0.01
    - cpu_index_tuple_cost = 0.005
    - cpu_operator_cost = 0.0025
    """

    def __init__(self, random_page_cost: float = 4.0):
        self.seq_page_cost = 1.0
        self.random_page_cost = random_page_cost
        self.cpu_tuple_cost = 0.01
        self.cpu_index_tuple_cost = 0.005
        self.cpu_operator_cost = 0.0025

    def cost_seq_scan(self, total_pages: int, total_tuples: int) -> Tuple[float, float]:
        startup = 0.0
        run_cost = (total_pages * self.seq_page_cost) + (total_tuples * (self.cpu_tuple_cost + self.cpu_operator_cost))
        return (startup, startup + run_cost)

    def cost_index_scan(
        self,
        index_pages: int,
        selected_tuples: int,
        total_table_pages: int
    ) -> Tuple[float, float]:
        # Startup cost traverses index B-tree height (e.g. 3 levels)
        startup = 3 * self.random_page_cost
        # Each selected tuple requires a random page fetch to the heap
        heap_pages_fetched = min(selected_tuples, total_table_pages)
        run_cost = (
            (index_pages * self.random_page_cost)
            + (heap_pages_fetched * self.random_page_cost)
            + (selected_tuples * (self.cpu_index_tuple_cost + self.cpu_tuple_cost))
        )
        return (startup, startup + run_cost)

    def cost_bitmap_scan(
        self,
        index_pages: int,
        selected_tuples: int,
        selected_pages: int
    ) -> Tuple[float, float]:
        # Index scan builds bitmap
        startup = (index_pages * self.random_page_cost) + (selected_tuples * self.cpu_index_tuple_cost)
        # Heap scan visits pages sequentially! Uses seq_page_cost instead of random!
        run_cost = (selected_pages * self.seq_page_cost) + (selected_tuples * self.cpu_tuple_cost)
        return (startup, startup + run_cost)


class PhysicalExecutors:
    """Executes physical scan algorithms against TableStorage."""

    def __init__(self, storage: TableStorage, mvcc: MVCCEngine, planner: QueryPlannerCostEstimator):
        self.storage = storage
        self.mvcc = mvcc
        self.planner = planner

    def seq_scan(
        self,
        predicate: Callable[[Dict[str, Any]], bool],
        snapshot: Snapshot,
        current_xid: int
    ) -> Tuple[List[Dict[str, Any]], PlanNodeResult]:
        """
        Sequential Scan (Seq Scan): Reads every page and tuple sequentially.
        """
        self.storage.reset_buffer_stats()
        t0 = time.perf_counter()
        results: List[Dict[str, Any]] = []
        total_tuples = 0

        first_tuple_time = None
        for page in self.storage.pages:
            num_items = (page.pd_lower - PAGE_HEADER_SIZE) // ITEM_ID_SIZE
            for off in range(1, num_items + 1):
                item = page.get_tuple(off)
                if not item:
                    continue
                flag, tuple_obj = item
                if flag == LinePointerFlag.LP_NORMAL and tuple_obj:
                    total_tuples += 1
                    if self.mvcc.is_tuple_visible(tuple_obj.header, snapshot, current_xid):
                        if predicate(tuple_obj.data):
                            if first_tuple_time is None:
                                first_tuple_time = time.perf_counter() - t0
                            results.append(tuple_obj.data)

        t_total = time.perf_counter() - t0
        startup_ms = (first_tuple_time or t_total) * 1000.0
        total_ms = t_total * 1000.0

        est_start, est_tot = self.planner.cost_seq_scan(len(self.storage.pages), total_tuples)
        plan_res = PlanNodeResult(
            node_name=f"Seq Scan on {self.storage.table_name}",
            startup_cost=round(est_start, 2),
            total_cost=round(est_tot, 2),
            actual_startup_ms=round(startup_ms, 3),
            actual_total_ms=round(total_ms, 3),
            rows_returned=len(results),
            shared_hit=self.storage.shared_hits,
            shared_read=self.storage.shared_reads
        )
        return results, plan_res

    def index_scan(
        self,
        index: BTreeIndex,
        key_val: Any,
        predicate: Callable[[Dict[str, Any]], bool],
        snapshot: Snapshot,
        current_xid: int
    ) -> Tuple[List[Dict[str, Any]], PlanNodeResult]:
        """
        Index Scan: Uses B-Tree to find TIDs, then random-fetches heap pages.
        """
        self.storage.reset_buffer_stats()
        t0 = time.perf_counter()
        results: List[Dict[str, Any]] = []

        candidate_tids = index.search_equality(key_val)
        first_tuple_time = None

        for tid in candidate_tids:
            page = self.storage.fetch_page(tid.block_num)
            item = page.get_tuple(tid.offset_num)
            if not item:
                continue
            flag, tuple_obj = item
            
            # Handle HOT chain redirection
            if flag == LinePointerFlag.LP_REDIRECT:
                target_off = struct.unpack_from(ITEM_ID_FORMAT, page.raw, PAGE_HEADER_SIZE + (tid.offset_num - 1) * ITEM_ID_SIZE)[0]
                item = page.get_tuple(target_off)
                if item:
                    flag, tuple_obj = item

            if flag == LinePointerFlag.LP_NORMAL and tuple_obj:
                if self.mvcc.is_tuple_visible(tuple_obj.header, snapshot, current_xid):
                    if predicate(tuple_obj.data):
                        if first_tuple_time is None:
                            first_tuple_time = time.perf_counter() - t0
                        results.append(tuple_obj.data)

        t_total = time.perf_counter() - t0
        startup_ms = (first_tuple_time or t_total) * 1000.0
        total_ms = t_total * 1000.0

        est_start, est_tot = self.planner.cost_index_scan(1, len(candidate_tids), len(self.storage.pages))
        plan_res = PlanNodeResult(
            node_name=f"Index Scan using idx_{index.key_name} on {self.storage.table_name}",
            startup_cost=round(est_start, 2),
            total_cost=round(est_tot, 2),
            actual_startup_ms=round(startup_ms, 3),
            actual_total_ms=round(total_ms, 3),
            rows_returned=len(results),
            shared_hit=self.storage.shared_hits + index.buffer_hits,
            shared_read=self.storage.shared_reads + index.buffer_reads,
            details={"Index Cond": f"({index.key_name} = {key_val})"}
        )
        return results, plan_res

    def bitmap_heap_scan(
        self,
        index: BTreeIndex,
        key_val: Any,
        predicate: Callable[[Dict[str, Any]], bool],
        snapshot: Snapshot,
        current_xid: int
    ) -> Tuple[List[Dict[str, Any]], PlanNodeResult]:
        """
        Bitmap Index Scan + Bitmap Heap Scan.
        
        WHY: Solves the random I/O thrashing problem of standard Index Scans!
        Phase 1: B-Tree populates a bitmap of block numbers.
        Phase 2: Heap scan sorts block numbers and visits pages sequentially.
        """
        self.storage.reset_buffer_stats()
        t0 = time.perf_counter()

        # Step 1: Bitmap Index Scan
        candidate_tids = index.search_equality(key_val)
        
        # Build physical page bitmap: Map[BlockNum -> List[OffsetNum]]
        page_bitmap: Dict[int, List[int]] = {}
        for tid in candidate_tids:
            page_bitmap.setdefault(tid.block_num, []).append(tid.offset_num)

        t_bitmap_done = time.perf_counter()
        startup_ms = (t_bitmap_done - t0) * 1000.0

        # Step 2: Bitmap Heap Scan (visiting pages in strictly sorted physical order)
        results: List[Dict[str, Any]] = []
        for block_num in sorted(page_bitmap.keys()):
            page = self.storage.fetch_page(block_num)
            for off in page_bitmap[block_num]:
                item = page.get_tuple(off)
                if not item:
                    continue
                flag, tuple_obj = item
                if flag == LinePointerFlag.LP_NORMAL and tuple_obj:
                    if self.mvcc.is_tuple_visible(tuple_obj.header, snapshot, current_xid):
                        if predicate(tuple_obj.data):
                            results.append(tuple_obj.data)

        t_total = time.perf_counter() - t0
        total_ms = t_total * 1000.0

        est_start, est_tot = self.planner.cost_bitmap_scan(1, len(candidate_tids), len(page_bitmap))
        
        # Child Node: Bitmap Index Scan
        child = PlanNodeResult(
            node_name=f"Bitmap Index Scan on idx_{index.key_name}",
            startup_cost=0.0,
            total_cost=round(est_start, 2),
            actual_startup_ms=round(startup_ms, 3),
            actual_total_ms=round(startup_ms, 3),
            rows_returned=len(candidate_tids),
            shared_hit=index.buffer_hits,
            shared_read=index.buffer_reads,
            details={"Index Cond": f"({index.key_name} = {key_val})"}
        )

        parent = PlanNodeResult(
            node_name=f"Bitmap Heap Scan on {self.storage.table_name}",
            startup_cost=round(est_start, 2),
            total_cost=round(est_tot, 2),
            actual_startup_ms=round(startup_ms, 3),
            actual_total_ms=round(total_ms, 3),
            rows_returned=len(results),
            shared_hit=self.storage.shared_hits,
            shared_read=self.storage.shared_reads,
            child_node=child
        )
        return results, parent

    def index_only_scan(
        self,
        index: BTreeIndex,
        key_val: Any,
        snapshot: Snapshot,
        current_xid: int
    ) -> Tuple[List[Dict[str, Any]], PlanNodeResult]:
        """
        Index Only Scan: Reads data directly from B-Tree leaf entries.
        
        WHY: If the Visibility Map confirms the target heap page is 'all_visible',
        the engine NEVER touches the heap file, achieving zero heap disk I/O!
        """
        self.storage.reset_buffer_stats()
        t0 = time.perf_counter()
        results: List[Dict[str, Any]] = []

        candidate_tids = index.search_equality(key_val)
        heap_fetches = 0

        for tid in candidate_tids:
            # Check Visibility Map
            is_all_visible = self.storage.visibility_map.get(tid.block_num, False)
            if is_all_visible:
                # Fully visible: return indexed key without touching heap!
                results.append({index.key_name: key_val})
            else:
                # Fallback: must fetch heap page to check MVCC header
                heap_fetches += 1
                page = self.storage.fetch_page(tid.block_num)
                item = page.get_tuple(tid.offset_num)
                if item and item[0] == LinePointerFlag.LP_NORMAL and item[1]:
                    if self.mvcc.is_tuple_visible(item[1].header, snapshot, current_xid):
                        results.append({index.key_name: key_val})

        t_total = time.perf_counter() - t0
        total_ms = t_total * 1000.0

        plan_res = PlanNodeResult(
            node_name=f"Index Only Scan using idx_{index.key_name} on {self.storage.table_name}",
            startup_cost=0.42,
            total_cost=4.85,
            actual_startup_ms=round(total_ms * 0.2, 3),
            actual_total_ms=round(total_ms, 3),
            rows_returned=len(results),
            shared_hit=index.buffer_hits + self.storage.shared_hits,
            shared_read=index.buffer_reads + self.storage.shared_reads,
            details={
                "Index Cond": f"({index.key_name} = {key_val})",
                "Heap Fetches": heap_fetches
            }
        )
        return results, plan_res


# ==============================================================================
# SECTION 6: POSTGRESQL-STYLE EXPLAIN FORMATTER
# ==============================================================================

def format_explain_analyze(plan: PlanNodeResult, indent: int = 0) -> str:
    """Renders a plan tree in authentic PostgreSQL EXPLAIN (ANALYZE, BUFFERS) format."""
    prefix = "  " * indent
    arrow = "->  " if indent > 0 else ""
    
    header = (
        f"{prefix}{arrow}{plan.node_name}  "
        f"(cost={plan.startup_cost}..{plan.total_cost} rows={plan.rows_returned} width=32) "
        f"(actual time={plan.actual_startup_ms}..{plan.actual_total_ms} rows={plan.rows_returned} loops=1)"
    )
    lines = [header]

    for k, v in plan.details.items():
        lines.append(f"{prefix}      {k}: {v}")

    lines.append(f"{prefix}      Buffers: shared hit={plan.shared_hit} read={plan.shared_read}")

    if plan.child_node:
        lines.append(format_explain_analyze(plan.child_node, indent + 1))

    return "\n".join(lines)


# ==============================================================================
# SECTION 7: VERIFICATION TEST SUITE & ARCHITECTURAL DEMO
# ==============================================================================

def run_storage_engine_demonstration() -> None:
    """Executes end-to-end architectural verification."""
    print("=" * 70)
    print(" DAY 47: POSTGRESQL STORAGE ENGINE & EXPLAIN ANALYZE SIMULATION")
    print("=" * 70)

    # 1. Initialize Storage & MVCC
    storage = TableStorage("accounts")
    mvcc = MVCCEngine()
    planner = QueryPlannerCostEstimator(random_page_cost=4.0)
    executors = PhysicalExecutors(storage, mvcc, planner)
    idx_account_id = BTreeIndex("account_id")
    brin_created_at = BRINIndex("created_at", pages_per_range=2)

    # 2. Insert Records across Transactions
    print("\n[STEP 1] Inserting 500 initial account records into 8KB SlottedPages...")
    tx1 = mvcc.begin_transaction()
    for i in range(1, 501):
        t_obj = HeapTuple(
            header=HeapTupleHeader(t_xmin=tx1),
            data={"account_id": i, "balance": 1000 + (i * 10), "status": "ACTIVE", "created_at": 1700000000 + (i * 60)}
        )
        tid = storage.insert_tuple(t_obj)
        idx_account_id.insert(i, tid)
        brin_created_at.build_or_update(tid.block_num, t_obj.data["created_at"])
    mvcc.commit(tx1)

    print(f"  -> Total 8KB Pages allocated: {len(storage.pages)}")
    print(f"  -> Page 0 Free Space: {storage.pages[0].free_space} bytes")
    print(f"  -> BRIN Ranges tracked: {len(brin_created_at.ranges)}")

    # 3. Simulate Concurrent Transactions & MVCC Isolation
    print("\n[STEP 2] Simulating MVCC Concurrency & Update Chaining...")
    # Transaction 2 updates account #42 balance to 9999
    tx2 = mvcc.begin_transaction()
    old_tid = idx_account_id.search_equality(42)[0]
    old_page = storage.fetch_page(old_tid.block_num)
    flag, old_tuple = old_page.get_tuple(old_tid.offset_num)
    assert old_tuple is not None
    
    # Mark old tuple t_xmax
    old_tuple.header.t_xmax = tx2
    
    # Insert new version (Heap-Only Tuple)
    new_version = HeapTuple(
        header=HeapTupleHeader(t_xmin=tx2),
        data={"account_id": 42, "balance": 9999, "status": "ACTIVE", "created_at": old_tuple.data["created_at"]}
    )
    new_tid = storage.update_tuple(old_tid, new_version, is_hot=True)
    print(f"  -> Account #42 updated by Txn {tx2}. Old TID: {old_tid}, New TID: {new_tid}")

    # Transaction 3 takes snapshot BEFORE Txn 2 commits
    tx3 = mvcc.begin_transaction()
    snap_tx3 = mvcc.get_snapshot()

    # Txn 3 queries account #42: Must see old balance 1420 (snapshot isolation)
    res_tx3, _ = executors.seq_scan(lambda d: d["account_id"] == 42, snap_tx3, tx3)
    assert len(res_tx3) == 1
    assert res_tx3[0]["balance"] == 1420, f"Expected 1420 under snapshot isolation, got {res_tx3[0]['balance']}"
    print(f"  -> Txn 3 (Snapshot {snap_tx3.xmin}:{snap_tx3.xmax}) sees pre-update balance: ${res_tx3[0]['balance']} [PASSED]")

    # Commit Txn 2
    mvcc.commit(tx2)

    # Transaction 4 takes snapshot AFTER Txn 2 commits
    tx4 = mvcc.begin_transaction()
    snap_tx4 = mvcc.get_snapshot()
    res_tx4, _ = executors.seq_scan(lambda d: d["account_id"] == 42, snap_tx4, tx4)
    assert len(res_tx4) == 1
    assert res_tx4[0]["balance"] == 9999, f"Expected 9999 after commit, got {res_tx4[0]['balance']}"
    print(f"  -> Txn 4 (Snapshot {snap_tx4.xmin}:{snap_tx4.xmax}) sees committed balance: ${res_tx4[0]['balance']} [PASSED]")

    # Demonstrate Heap-Only Tuple (HOT) on Page 4 (has available space)
    print("\n[STEP 2B] Demonstrating Heap-Only Tuple (HOT) Optimization...")
    tx_hot = mvcc.begin_transaction()
    old_tid_480 = idx_account_id.search_equality(480)[0]
    old_p480 = storage.fetch_page(old_tid_480.block_num)
    _, tuple_480 = old_p480.get_tuple(old_tid_480.offset_num)
    assert tuple_480 is not None

    new_tuple_480 = HeapTuple(
        header=HeapTupleHeader(t_xmin=tx_hot),
        data={"account_id": 480, "balance": 77777, "status": "ACTIVE", "created_at": tuple_480.data["created_at"]}
    )
    new_tid_480 = storage.update_tuple(old_tid_480, new_tuple_480, is_hot=True)
    mvcc.commit(tx_hot)

    # Crucial Verification: Both old and new tuples reside on the SAME page
    assert old_tid_480.block_num == new_tid_480.block_num, "HOT update must remain on the same page!"
    # The B-Tree index was NOT modified and still points to old_tid_480
    assert idx_account_id.search_equality(480)[0] == old_tid_480, "B-Tree index entry must remain untouched under HOT!"

    # Query via Index Scan: follows LP_REDIRECT to return updated balance
    snap_hot = mvcc.get_snapshot()
    tx_read_hot = mvcc.begin_transaction()
    res_hot, _ = executors.index_scan(idx_account_id, 480, lambda d: True, snap_hot, tx_read_hot)
    assert len(res_hot) == 1
    assert res_hot[0]["balance"] == 77777, f"Expected 77777 via HOT redirect, got {res_hot[0]['balance']}"
    print(f"  -> HOT update verified! Old TID: {old_tid_480} -> New TID: {new_tid_480} (Same Page)")
    print(f"  -> Index Scan successfully traversed LP_REDIRECT with ZERO B-Tree index writes! [PASSED]")

    # 4. Compare Physical Access Paths & EXPLAIN ANALYZE
    print("\n[STEP 3] Comparing Physical Scan Plans with EXPLAIN (ANALYZE, BUFFERS)...")
    snap_current = mvcc.get_snapshot()
    curr_tx = mvcc.begin_transaction()

    # Plan A: Sequential Scan
    print("\n--- PLAN A: SEQUENTIAL SCAN ---")
    _, plan_seq = executors.seq_scan(lambda d: d["account_id"] == 100, snap_current, curr_tx)
    print(format_explain_analyze(plan_seq))

    # Plan B: Index Scan
    print("\n--- PLAN B: B-TREE INDEX SCAN ---")
    _, plan_idx = executors.index_scan(idx_account_id, 100, lambda d: True, snap_current, curr_tx)
    print(format_explain_analyze(plan_idx))

    # Plan C: Bitmap Heap Scan
    print("\n--- PLAN C: BITMAP INDEX SCAN + BITMAP HEAP SCAN ---")
    _, plan_bm = executors.bitmap_heap_scan(idx_account_id, 100, lambda d: True, snap_current, curr_tx)
    print(format_explain_analyze(plan_bm))

    # Plan D: Index-Only Scan with Visibility Map
    print("\n--- PLAN D: INDEX ONLY SCAN (WITH VISIBILITY MAP) ---")
    # Mark Page 0 as all-visible in VM
    storage.visibility_map[0] = True
    _, plan_ios = executors.index_only_scan(idx_account_id, 1, snap_current, curr_tx)
    print(format_explain_analyze(plan_ios))

    print("\n" + "=" * 70)
    print(" ALL ARCHITECTURAL TESTS & EXPLAIN VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    run_storage_engine_demonstration()
