# 7 PostgreSQL Storage & Index Tuning Antipatterns

This document dissects the top seven architectural mistakes made in high-throughput PostgreSQL production environments, analyzing the exact low-level failure mechanisms, system blast radius, and enterprise-grade remediations.

---

## 1. Retaining Default `random_page_cost = 4.0` on NVMe SSD Infrastructure

### Failure Mechanism
PostgreSQL’s default cost model sets `seq_page_cost = 1.0` and `random_page_cost = 4.0`. This 4:1 ratio was calibrated in the 1990s for mechanical spinning hard disk drives (HDDs), where random disk seeks required physical actuator arm movement and rotational delay (~10ms per random I/O vs sequential burst reads).

On modern NVMe SSDs and high-IOPS cloud storage (such as AWS EBS `gp3`/`io2` or GCP Hyperdisk), random read latency is virtually identical to sequential read latency (microsecond scale). By retaining `random_page_cost = 4.0`, the cost planner mathematically over-penalizes B-Tree Index Scans by $4\times$. Consequently, when a query selects as little as **1% to 3% of a table**, the optimizer erroneously concludes that an Index Scan is more expensive than reading the entire table sequentially from disk (`Seq Scan`), abandoning indexes completely!

```
HDD Era Cost Assumption:       Random I/O (4.0) >>> Sequential I/O (1.0)
NVMe SSD Reality:              Random I/O (1.1) ~=  Sequential I/O (1.0)
Consequence of Miscalibration: Planner abandons Index Scan at 1% selectivity!
```

### Remediation
Tune `random_page_cost` cluster-wide or per-tablespace to reflect modern storage media:
```sql
-- For NVMe SSDs and provisioned cloud SSD storage:
ALTER SYSTEM SET random_page_cost = 1.1;
SELECT pg_reload_conf();

-- Or per-tablespace if mixing storage tiers:
ALTER TABLESPACE fast_nvme_storage SET (random_page_cost = 1.1);
```

---

## 2. Leftmost Prefix Violations on Composite B-Tree Indexes

### Failure Mechanism
A composite B-Tree index on columns `(A, B, C)` physically sorts data primarily by `A`, secondarily by `B`, and tertiarily by `C`. The B-Tree search algorithm navigates down the tree by comparing keys from left to right.

If an application executes queries filtering on `(B, C)` or `(C)` alone (omitting leading column `A`), the database engine cannot navigate the B-Tree root and internal pages. Unlike relational database engines with built-in Index Skip Scans (like Oracle), PostgreSQL prior to version 13 could never use such an index; and even in modern versions, skip scans are only considered under extreme cardinality skews. In 99% of cases, omitting the leading column causes a catastrophic fallback to a full table `Seq Scan`.

```
Index: (tenant_id, status, created_at)

Query 1: WHERE tenant_id = 'acme'                          --> Efficient Index Scan (Uses Prefix)
Query 2: WHERE tenant_id = 'acme' AND status = 'PENDING'    --> Efficient Index Scan (Uses Prefix)
Query 3: WHERE status = 'PENDING'                          --> FULL SEQ SCAN (Prefix Omitted!)
```

### Remediation
1. **Order Columns by Selectivity and Query Frequency**: Always place the most strictly filtered equality predicate column first (e.g. `tenant_id` or `company_id`).
2. **Audit Query Workloads**: If queries frequently filter on `status` without `tenant_id`, create a separate index or a partial index specifically for that access path.

---

## 3. Function-Wrapped Columns Blinding the B-Tree Index

### Failure Mechanism
B-Tree indexes index the **exact raw scalar values** stored in the table. When a SQL query wraps an indexed column inside a function or operator expression in the `WHERE` clause:
```sql
-- ANTIPATTERN:
SELECT id, email FROM users WHERE LOWER(email) = 'alex@enterprise.com';
SELECT id, amount FROM ledger WHERE DATE(created_at) = '2026-11-01';
```
The query planner cannot apply the B-Tree index on `email` or `created_at` because the index stores un-lowered strings and exact timestamps. The optimizer has no mathematical proof that `LOWER(email)` preserves the ordering of `email`. As a result, the index is ignored, and PostgreSQL scans all 50,000,000 rows, evaluating the function on every single row in memory!

### Remediation
1. **Create Expression / Functional Indexes**:
   ```sql
   CREATE INDEX idx_users_lower_email ON users (LOWER(email));
   CREATE INDEX idx_ledger_date_trunc ON ledger ((created_at::date));
   ```
2. **Rewrite Predicates to SARGable Ranges**:
   ```sql
   -- SARGable (Search Argument Able) range query preserves standard B-Tree index:
   SELECT id, amount FROM ledger 
   WHERE created_at >= '2026-11-01 00:00:00+00' 
     AND created_at <  '2026-11-02 00:00:00+00';
   ```

---

## 4. Over-Indexing High-Write Tables & Thwarting HOT (Heap-Only Tuples)

### Failure Mechanism
Junior engineers often add an index for every column mentioned in any query. However, every additional index imposes severe operational penalties:
1. **Write Amplification**: Every `INSERT` and `DELETE` must write new entries into every single index table on disk, multiplying WAL generation and I/O.
2. **Thwarting Heap-Only Tuple (HOT) Optimization**: If an `UPDATE` modifies a non-indexed column (e.g. `balance`), and the page has room, PostgreSQL updates the tuple in-place via HOT with zero index writes. However, if *any* column modified in the `UPDATE` is indexed, **HOT is impossible**. The engine is forced to insert new index entries into every index on the table, generating massive index bloat and WAL traffic!

### Remediation
- Audit unused indexes using `pg_stat_user_indexes` where `idx_scan = 0`.
- Keep update-heavy columns out of secondary indexes unless strictly necessary.
- Tune the table `fillfactor` (e.g. `fillfactor = 85`) on write-intensive tables to reserve 15% free space on every 8KB page specifically for HOT update chaining:
  ```sql
  ALTER TABLE accounts SET (fillfactor = 85);
  VACUUM FULL accounts; -- or pg_repack to apply online
  ```

---

## 5. Indexing High-Volume Settled States Instead of Partial Indexes

### Failure Mechanism
In enterprise transactional systems (orders, payments, webhook deliveries), statuses follow a massive skew:
- `COMPLETED`: 98% of rows (10,000,000 rows).
- `FAILED`: 1% of rows (100,000 rows).
- `PENDING`: 1% of rows (100,000 rows).

Creating a standard index on `(status)` creates a multi-gigabyte B-Tree where 98% of the leaf nodes point to settled `COMPLETED` records that active background workers never query! This pollutes `shared_buffers`, pushes active working sets out of cache, and slows down updates to `COMPLETED` records.

```
Standard B-Tree: [COMPLETED, COMPLETED, COMPLETED ... 10,000,000 entries (800MB RAM)]
Partial B-Tree:  [PENDING, FAILED ...................... 200,000 entries ( 16MB RAM)]
```

### Remediation
Create **Partial Indexes** filtered by a predicate:
```sql
CREATE INDEX idx_orders_active_queue ON orders (created_at, id)
WHERE status IN ('PENDING', 'PROCESSING');
```
Benefits:
- 98% reduction in disk and memory footprint.
- Zero index maintenance when records are in the terminal `COMPLETED` state.
- Remains 100% cached inside RAM buffers.

---

## 6. Blocking Production Writes via Non-Concurrent Index Creation

### Failure Mechanism
Executing a standard `CREATE INDEX` or `REINDEX` statement acquires an **`AccessShareLock` on reads and a `ShareLock` on the table**, which permits concurrent `SELECT` queries but **strictly blocks all concurrent `INSERT`, `UPDATE`, and `DELETE` operations** until the entire index build finishes.

On a 100GB table, a standard index creation might take 45 minutes. During those 45 minutes, every write transaction against that table queues behind the `ShareLock`. Application connection pools exhaust within seconds, cascading into system-wide HTTP 504 timeouts and an operational outage!

### Remediation
Always use `CREATE INDEX CONCURRENTLY` in production environments:
```sql
CREATE INDEX CONCURRENTLY idx_orders_customer_id ON orders (customer_id);
```
**Operational Caveats**:
- `CONCURRENTLY` performs two table scans and waits for running transactions to finish; it cannot run inside a `BEGIN ... COMMIT` transaction block.
- If an error occurs (such as a unique constraint violation), the index build fails but leaves behind an **`INVALID`** index marked in `pg_class`. Invalid indexes consume disk space and degrade writes without serving reads!
- Monitor for invalid indexes:
  ```sql
  SELECT schemaname, relname, indexrelname 
  FROM pg_index i 
  JOIN pg_stat_user_indexes ui ON ui.indexrelid = i.indexrelid 
  WHERE NOT i.indisvalid;
  ```

---

## 7. Autovacuum Starvation by Long-Running Transactions & XID Wraparound

### Failure Mechanism
PostgreSQL’s MVCC model leaves dead tuple versions behind when rows are updated or deleted. Autovacuum is responsible for sweeping pages, reclaiming space, and freezing transaction IDs.

However, Autovacuum **cannot clean any dead tuple whose deleting transaction is newer than the oldest active transaction in the cluster**. If an analytics query, an abandoned client connection, or an `idle in transaction` session runs for hours or days, Autovacuum is starved!
1. **Catastrophic Table Bloat**: 8KB pages fill with millions of dead tuples that cannot be pruned. Table and index sizes balloon by $5\times$ to $20\times$.
2. **Transaction ID (XID) Wraparound Catastrophe**: If the database approaches $2^{31}$ un-frozen transactions (`autovacuum_freeze_max_age`), PostgreSQL enters read-only panic mode and forcibly shuts down to prevent data corruption!

### Remediation
1. **Enforce Statement & Transaction Timeouts**:
   ```sql
   -- Terminate idle transactions held open longer than 60 seconds
   ALTER SYSTEM SET idle_in_transaction_session_timeout = '60s';
   -- Terminate any backend query running longer than 5 minutes
   ALTER SYSTEM SET statement_timeout = '300s';
   SELECT pg_reload_conf();
   ```
2. **Aggressively Scale Autovacuum Settings**:
   Default settings (`autovacuum_vacuum_scale_factor = 0.2`) require 20% of a table to change before vacuuming triggers. On a 100M row table, that requires 20,000,000 dead tuples before autovacuum wakes up!
   ```sql
   -- For high-volume write tables:
   ALTER TABLE orders SET (
       autovacuum_vacuum_scale_factor = 0.05,
       autovacuum_vacuum_cost_limit = 2000
   );
   ```
