# Day 47 Rapid Revision: PostgreSQL Storage & Index Tuning

A 5-minute executive cheat sheet for senior backend engineers.

---

## 1. 8KB Slotted Page Anatomy (At a Glance)

```
Offset 0   +-------------------------------------------------------------+
           | PageHeaderData (24 bytes)                                   |
           | pd_lsn (8B) | pd_checksum (2B) | pd_flags (2B)              |
           | pd_lower (2B) | pd_upper (2B)  | pd_special (2B)            |
           | pd_pagesize_version (2B)       | pd_prune_xid (4B)          |
Offset 24  +-------------------------------------------------------------+
           | LinePointer 1 (4 bytes: lp_offset 15b, lp_flags 2b, len 15b)| | Grows
           | LinePointer 2 (4 bytes: lp_offset 15b, lp_flags 2b, len 15b)| v DOWN
           | ...                                                         | pd_lower
           +-------------------------------------------------------------+
           |                     FREE SPACE HOLE                         |
           +-------------------------------------------------------------+ pd_upper
           | ...                                                         | ^ Grows
           | Tuple 2 Data Payload (Header: t_xmin, t_xmax, t_cid...)     | | UP
           | Tuple 1 Data Payload (Header: t_xmin, t_xmax, t_cid...)     |
Offset 8192+-------------------------------------------------------------+
```
- **TID (`ctid`)**: `(BlockNumber, OffsetNumber)` $\rightarrow$ Points to LinePointer index, NOT physical byte offset!
- **Free Space Available**: `pd_upper - pd_lower - sizeof(ItemIdData)` bytes.

---

## 2. MVCC & HOT (Heap-Only Tuples) Rules

| Event | Physical Disk Action |
| :--- | :--- |
| **`INSERT`** | Appends new tuple with `t_xmin = current_XID`, `t_xmax = 0`. |
| **`DELETE`** | Sets old tuple `t_xmax = current_XID`. |
| **`UPDATE` (Cold)** | Sets old tuple `t_xmax = current_XID`; inserts new tuple on new page; **adds new entry to EVERY index**. |
| **`UPDATE` (HOT)** | Same page + non-indexed column update: Old Line Pointer becomes `LP_REDIRECT` to new tuple; **ZERO index writes!** |

### Snapshot Visibility Logic (`HeapTupleSatisfiesMVCC`)
- If `t_xmin` in-flight or $> \text{snapshot.xmax} \implies$ **Invisible**.
- If `t_xmin` committed before snapshot:
  - If `t_xmax == 0` or aborted $\implies$ **Visible**.
  - If `t_xmax` in-flight $\implies$ **Visible** (deletion not yet finalized).
  - If `t_xmax` committed before snapshot $\implies$ **Invisible** (already dead).

---

## 3. Index Type Decision Matrix

| Index Type | Underlying Structure | Best For | Typical Pitfall |
| :--- | :--- | :--- | :--- |
| **B-Tree** | Lehman-Yao B-Tree | Equality (`=`), range (`<`, `>`), sorting (`ORDER BY`), high cardinality | Bloats under heavy writes; prefix ordering required. |
| **BRIN** | Min/Max per block range (128 pages) | Huge append-only time series, correlated timestamps/IDs | Useless if physical data is not physically ordered on disk. |
| **GIN** | Inverted Index (Keys $\rightarrow$ TID list) | JSONB (`@>`), arrays (`&&`), full-text search (`@@`), trigrams (`pg_trgm`) | Expensive write amplification; slow updates. |
| **GiST** | Generalized Search Tree | Geometric coordinates (PostGIS), IP ranges, nearest neighbor (`<->`) | Higher CPU overhead on lookups. |
| **Partial** | B-Tree with `WHERE` filter | Skewed data (e.g. `WHERE status = 'PENDING'`) | Queries without exact predicate cannot use it. |

---

## 4. Physical Access Path Comparison

```
[High Selectivity: < 1-2%]      --> Index Scan (Traverses B-Tree -> Direct Heap Fetch per TID)
[Medium Selectivity: 2-15%]     --> Bitmap Index Scan + Bitmap Heap Scan (Builds TID bitmap -> Sorts pages physically)
[Low Selectivity: > 15-25%]     --> Sequential Scan (Reads all 8KB pages from 0 to N with kernel read-ahead)
[Covering Index + VM Valid]     --> Index-Only Scan (Reads B-Tree leaf entries directly; 0 heap visits)
```

---

## 5. `EXPLAIN (ANALYZE, BUFFERS)` Reading Cheat Sheet

```
Index Scan using idx_orders on orders (cost=0.42..12.30 rows=5 width=48) (actual time=0.030..0.045 rows=5 loops=1)
  Buffers: shared hit=4 read=1 dirtied=0
```

- `cost=startup..total`: Planner's arbitrary cost units (1.0 = 1 sequential page read).
- `actual time=startup..total`: Wall-clock milliseconds to first row and final row.
- `rows=N loops=L`: Total rows returned is $N \times L$. Multiply actual time by loops!
- `shared hit`: 8KB pages found in RAM cache (`shared_buffers`).
- `shared read`: 8KB pages read from disk / OS filesystem cache.
- `external merge Disk: 15MB`: Indicates sort operation exceeded `work_mem` and spilled to disk.

---

## 6. Critical PostgreSQL Production Parameters

```ini
# Memory Configuration
shared_buffers = '25% of Total RAM'        # Primary database page cache
work_mem = '64MB'                          # Per-sort/hash node memory allocation
maintenance_work_mem = '1GB'               # Used for VACUUM, CREATE INDEX, migrations

# Storage Optimizer Calibration
seq_page_cost = 1.0                        # Baseline sequential I/O cost
random_page_cost = 1.1                     # TUNE TO 1.1 ON NVMe SSDs (Default 4.0 is for spinning HDDs!)

# Autovacuum Tuning
autovacuum_vacuum_scale_factor = 0.05      # Trigger vacuum after 5% row changes (Default 0.20 is too loose)
autovacuum_freeze_max_age = 200000000      # 200M XIDs before emergency wraparound vacuum
idle_in_transaction_session_timeout = '60s'# Kill abandoned sessions holding vacuum locks
```
