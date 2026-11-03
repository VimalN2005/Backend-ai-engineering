# Day 48 Rapid Revision: Transactions, Isolation & Deadlocks

A 5-minute executive cheat sheet for senior backend engineers.

---

## 1. ACID Guarantees & Engine Mechanics

- **Atomicity**: Enforced by Write-Ahead Logging (WAL) and 2-bit commit status in CLOG (`pg_xact`). Aborted transactions simply set status to `ABORTED`; tuples are cleaned up asynchronously by `VACUUM`.
- **Consistency**: Relational integrity, constraints (`CHECK`, `FOREIGN KEY`, `EXCLUSION`), and serializability invariants.
- **Isolation**: MVCC (readers never block writers, writers never block readers) + Shared Memory Lock Manager.
- **Durability**: Synchronous commit flushes WAL buffers to disk via OS `fsync()`. (Disabling via `synchronous_commit = off` boosts write TPS by $10\times$ at the cost of losing up to 30–60ms of committed data upon power cut).

---

## 2. Concurrency Anomalies & Isolation Matrix

```
+--------------------+------------+-----------------+--------------+-------------+------------+
| Isolation Level    | Dirty Read | Non-Repeatable  | Phantom Read | Lost Update | Write Skew |
+--------------------+------------+-----------------+--------------+-------------+------------+
| Read Committed     | NO (MVCC)  | YES             | YES          | YES         | YES        |
| Repeatable Read    | NO (MVCC)  | NO              | NO           | NO (40001)  | YES        |
| Serializable (SSI) | NO (MVCC)  | NO              | NO           | NO (40001)  | NO (40001) |
+--------------------+------------+-----------------+--------------+-------------+------------+
```

### Snapshot Timing
- **`READ COMMITTED`**: New snapshot per **SQL statement**. Uses EvalPlanQual (EPQ) to re-evaluate updated rows.
- **`REPEATABLE READ`**: Fixed snapshot taken at **first statement**. Enforces First-Committer-Wins (aborts concurrent update with `40001`).
- **`SERIALIZABLE`**: Fixed snapshot + **SIREAD locks** tracking $rw$-antidependency graphs. Aborts on cycle detection ($T_1 \xrightarrow{rw} T_2 \xrightarrow{rw} T_1$).

---

## 3. Row-Level Locks & Storage Architecture

```
SELECT * FROM table FOR UPDATE;           -- Exclusive lock (blocks concurrent updates & deletes)
SELECT * FROM table FOR NO KEY UPDATE;    -- Weak exclusive (allows foreign key checks)
SELECT * FROM table FOR SHARE;            -- Shared lock (blocks updates, allows readers)
SELECT * FROM table FOR KEY SHARE;        -- Weak shared (used by foreign keys)
```

- **Lock Storage**: Stored directly in physical tuple headers (`t_xmax` and `HEAP_XMAX_EXCL_LOCK` in `t_infomask`), or MultiXacts in `$PGDATA/pg_multixact/`.
- **Zero Escalation**: PostgreSQL **never escalates locks** to table level, regardless of row count!

---

## 4. `NOWAIT` vs `SKIP LOCKED` Cheat Sheet

```sql
-- Interactive UI query (Fail-fast):
SELECT * FROM tickets WHERE id = 42 FOR UPDATE NOWAIT;
-- Throws SQLSTATE 55P03 immediately if locked!

-- High-throughput Queue Workers (Zero contention):
SELECT * FROM queue 
WHERE status = 'PENDING' 
ORDER BY id ASC 
LIMIT 1 
FOR UPDATE SKIP LOCKED;
-- Bypasses locked rows instantly; enables 100% horizontal scaling across Celery/K8s pods!
```

---

## 5. Deadlock Detection & The Resource Ordering Invariant

- **PostgreSQL Deadlock Detector**: Fires after `deadlock_timeout` (default 1000ms). Builds Wait-For Graph (WFG) and executes DFS cycle search. Aborts youngest transaction with `SQLSTATE 40P01`.
- **The Prevention Invariant**: Always sort primary keys before acquiring multiple locks:
  $$\text{Lock Order: } \min(\text{id}_1, \text{id}_2) \longrightarrow \max(\text{id}_1, \text{id}_2)$$
  Breaks Coffman's "Circular Wait" condition; guarantees **0 deadlocks**!

---

## 6. Emergency Production Lock Diagnostic Queries

```sql
-- 1. Find who is blocking whom:
SELECT 
    blocked.pid AS blocked_pid, 
    blocking.pid AS blocking_pid, 
    blocked.query AS blocked_sql, 
    blocking.query AS blocking_sql
FROM pg_stat_activity blocked
JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
JOIN pg_locks kl ON kl.locktype = bl.locktype 
    AND kl.relation IS NOT DISTINCT FROM bl.relation 
    AND kl.page IS NOT DISTINCT FROM bl.page 
    AND kl.tuple IS NOT DISTINCT FROM bl.tuple 
    AND kl.pid != bl.pid
JOIN pg_stat_activity blocking ON blocking.pid = kl.pid;

-- 2. Safely cancel the blocking query:
SELECT pg_cancel_backend(<blocking_pid>);

-- 3. Forcibly terminate the blocking connection:
SELECT pg_terminate_backend(<blocking_pid>);
```
