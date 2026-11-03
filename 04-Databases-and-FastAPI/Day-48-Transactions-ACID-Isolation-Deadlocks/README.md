# Day 48: Database Transactions: ACID, Isolation Levels & Deadlock Resolution

## Executive Overview & Architectural Scope

In distributed web services and high-scale enterprise architectures, data integrity hinges on database transaction semantics. While application-level abstractions often reduce transactions to a simple `@transaction.atomic` decorator or an asynchronous context manager (`async with session.begin():`), failing to understand the underlying concurrency engine leads to severe production outages: silent financial balances drifting, double-spending vulnerabilities, queue worker lock convoys, catastrophic deadlock cascades, and system-wide connection pool starvation.

This deep dive deconstructs the relational engine's transactional substrate, exploring:
1. The mathematical definition of ACID guarantees and Write-Ahead Logging (WAL) physics.
2. The ANSI SQL-92 isolation levels versus modern engine realities and the Berenson et al. concurrency anomaly taxonomy ($G_0$ to Write Skew).
3. The internal mechanics of PostgreSQL's Repeatable Read and Serializable Snapshot Isolation (SSI) using rw-antidependency graphs.
4. The 8x8 Heavyweight Table Lock conflict matrix, row-level locks, and lock-free queue processing with `FOR UPDATE SKIP LOCKED`.
5. Deadlock detection algorithms (Wait-For Graph cycle searches), mitigation via strict resource ordering, and distributed Two-Phase Commit (2PC) failure modes.

```
+---------------------------------------------------------------------------------------------------+
|                            POSTGRESQL TRANSACTION CONCURRENCY ENGINE                              |
|                                                                                                   |
|   +-------------------------------------------------------------------------------------------+   |
|   |                              SHARED MEMORY LOCK MANAGER                                   |   |
|   |                                                                                           |   |
|   |   +------------------------------------+       +--------------------------------------+   |   |
|   |   |       HEAVYWEIGHT LOCK TABLE       |       |          SIREAD LOCK TABLE           |   |   |
|   |   |  (Relation, Page, Tuple, XID,      |       |  (Serializable Snapshot Isolation:   |   |   |
|   |   |   VirtualXID, Advisory Locks)      |       |   Tracks rw-antidependencies)        |   |   |
|   |   +-----------------+------------------+       +-------------------+------------------+   |   |
|   |                     |                                              |                      |   |
|   |                     v                                              v                      |   |
|   |   +------------------------------------+       +--------------------------------------+   |   |
|   |   |     WAIT-FOR GRAPH (WFG) ENGINE    |       |      DANGEROUS STRUCTURE DETECTOR    |   |   |
|   |   |  Cycle Search (DFS / Tarjan's)     |       |      (T1 --rw--> T2 --rw--> T3)      |   |   |
|   |   |  Fires after 'deadlock_timeout'    |       |      Fires on serialization conflict |   |   |
|   |   +-----------------+------------------+       +-------------------+------------------+   |   |
|   +---------------------|----------------------------------------------|----------------------+   |
|                         | (Deadlock Victim Abort: 40P01)               | (Serialization: 40001)   |
|                         v                                              v                          |
|   +-------------------------------------------------------------------------------------------+   |
|   |                             LOCAL CLIENT BACKEND PROCESSES                                |   |
|   |                                                                                           |   |
|   |   [Backend Txn 101]                     [Backend Txn 102]                 [Backend Txn 103]|  |
|   |    Isolation: READ COMMITTED             Isolation: REPEATABLE READ        Isolation: SERIAL   |
|   |    Snapshot: Statement-level             Snapshot: Txn-start level         Snapshot: SSI Graph|
|   +-------------------------------------------------------------------------------------------+   |
|                                                 |                                                 |
|                                                 v                                                 |
|   +-------------------------------------------------------------------------------------------+   |
|   |                             STORAGE ENGINE & DURABILITY LAYER                             |   |
|   |                                                                                           |   |
|   |   CLOG (pg_xact): 2-bit Transaction Status [IN_PROGRESS | COMMITTED | ABORTED]            |   |
|   |   WAL (pg_wal): Write-Ahead Log Stream (fsync boundary guaranteeing ACID durability)      |   |
|   |   HEAP: 8KB Slotted Pages with Tuple Headers (t_xmin, t_xmax, infomask lock bits)         |   |
|   +-------------------------------------------------------------------------------------------+   |
+---------------------------------------------------------------------------------------------------+
```

---

## 1. The Theoretical Foundations of ACID Guarantees

The ACID acronym describes the four non-negotiable guarantees required for reliable transaction processing:

### Atomicity: All-or-Nothing Execution
- **The Guarantee**: A transaction is an indivisible unit of work. Either all physical mutations succeed, or the entire state reverts to its pre-transaction state.
- **Physical Implementation**: Atomicity is **not** achieved by undoing changes in memory during a crash. It is enforced through **Write-Ahead Logging (WAL)** and the **Commit Log (CLOG / `pg_xact`)**. When modifications occur, changes are written to the WAL ring buffer before dirty pages are written to heap storage. If a crash occurs or an application issues `ROLLBACK`, PostgreSQL simply sets the transaction's 2-bit status in the CLOG to `ABORTED`. Dead tuples left in heap pages are subsequently ignored by MVCC visibility checks and later reclaimed by `VACUUM`.

### Consistency: Invariant Preservation
- **The Guarantee**: A transaction transitions the database from one valid state to another valid state, satisfying all explicit relational constraints (`PRIMARY KEY`, `FOREIGN KEY`, `CHECK`, `NOT NULL`, `EXCLUSION`) and implicit application invariants (e.g. "Total bank reserves must equal the sum of account balances").
- **Physical Implementation**: Enforced through synchronous constraint checking engines, immediate vs deferred constraint evaluation (`SET CONSTRAINTS ALL DEFERRED`), and isolation guarantees.

### Isolation: Interference Freedom
- **The Guarantee**: The concurrent execution of multiple transactions results in a system state identical to some serial execution of those transactions.
- **Physical Implementation**: Multi-Version Concurrency Control (MVCC) combined with the Shared Memory Lock Manager (Heavyweight locks, Row locks, MultiXact arrays, and SIREAD predicate locks).

### Durability: Committed Changes Persist
- **The Guarantee**: Once a transaction commits and receives an acknowledgment, its modifications will not be lost, even in the event of an immediate total power failure or OS crash.
- **Physical Implementation**: The synchronous commit protocol. Prior to reporting success to the client, PostgreSQL executes an OS `fsync()` system call on the Write-Ahead Log segment on disk.
- **The Performance Tradeoff**: Tuning `synchronous_commit = off` allows backends to return immediately upon writing to WAL buffers in RAM without waiting for the physical `fsync` disk flush, boosting write throughput by $10\times$ at the risk of losing up to $3 \times \text{wal\_writer\_delay}$ (typically 30–60ms) of committed transactions during an abrupt hardware crash (data remains crash-consistent, but recent writes are lost).

---

## 2. PostgreSQL MVCC vs Traditional Two-Phase Locking (2PL)

Relational database engines historically resolved concurrency through **Rigorous Two-Phase Locking (SS2PL)**:
1. **Growing Phase**: Locks are acquired; no locks can be released.
2. **Shrinking Phase**: Locks are released only upon transaction commit or abort.

### The Problem with Pure 2PL (e.g., MySQL InnoDB Default Locking / SQL Server)
In a pure 2PL engine:
- A `SELECT` query acquires **Shared Locks (S-Locks)** on rows/pages, strictly blocking concurrent writers (`INSERT`, `UPDATE`, `DELETE`).
- A write query acquires **Exclusive Locks (X-Locks)**, strictly blocking all concurrent readers.
- This creates massive lock contention: read-heavy analytics queries stall transactional write pipelines, causing lock escalations, thread pool exhaustion, and lock convoying.

### The PostgreSQL MVCC Solution: Readers Never Block Writers
PostgreSQL eliminates 2PL read locks for standard queries. Every modification writes a new tuple version with metadata headers (`t_xmin`, `t_xmax`). Readers inspect the tuple headers against their transaction snapshot. Consequently:
- **Readers never block Writers**: A long-running `SELECT` query does not acquire shared locks on data tuples, allowing concurrent transactions to update or delete those rows freely.
- **Writers never block Readers**: A high-volume `UPDATE` or `INSERT` batch does not prevent concurrent client sessions from reading the previous committed versions of those rows.
- **Writers ONLY block Writers**: Two transactions attempting to modify the exact same physical row concurrently must queue via row-level locks.

---

## 3. ANSI SQL-92 Isolation Levels vs Modern Relational Reality

The ANSI SQL-92 standard defined four isolation levels using three specific phenomena:

```
+--------------------+------------------+-----------------------+--------------------+
| ANSI Level         | Dirty Read (A1)  | Non-Repeatable (A2)   | Phantom Read (A3)  |
+--------------------+------------------+-----------------------+--------------------+
| Read Uncommitted   | Allowed          | Allowed               | Allowed            |
| Read Committed     | Prevented        | Allowed               | Allowed            |
| Repeatable Read    | Prevented        | Prevented             | Allowed            |
| Serializable       | Prevented        | Prevented             | Prevented          |
+--------------------+------------------+-----------------------+--------------------+
```

### The Flaws of ANSI SQL-92 (Berenson et al., 1995)
In the seminal paper *"A Critique of ANSI SQL Isolation Levels"* (Berenson, Bernstein, Gray, Melton, O'Neil, O'Neil), database researchers proved that the ANSI definitions were fundamentally incomplete:
1. They assumed a lock-based 2PL execution engine, failing to account for multi-version engines.
2. They missed critical concurrency anomalies such as **Lost Updates ($P4$)**, **Read Skew ($A5a$)**, and **Write Skew ($A5b$)**.

### PostgreSQL's Implementation Reality
In PostgreSQL:
1. **Read Uncommitted is identical to Read Committed**: Because PostgreSQL uses MVCC, an uncommitted row version is never visible to other transactions unless it was inserted by the same transaction. Dirty reads are physically impossible under PostgreSQL's storage engine!
2. **Repeatable Read PREVENTS Phantom Reads**: Unlike standard ANSI Repeatable Read (which allows new rows to appear in range queries), PostgreSQL’s snapshot isolation locks the view of the entire database to the transaction start time, completely eliminating phantoms!
3. **Repeatable Read DOES NOT Guarantee Serializability**: Repeatable Read remains vulnerable to **Write Skew**. True serializability requires PostgreSQL's `SERIALIZABLE` level (backed by Serializable Snapshot Isolation).

---

## 4. Taxonomy of Concurrency Anomalies (From $G_1$ to Write Skew)

Understanding transaction safety requires classifying the precise anomalies that occur under concurrent interleaved schedules:

```
+-----------------------------------+------------------------------------------------------------------------------------------+
| Concurrency Anomaly               | Formal Anomaly Mechanism & Physical Consequence                                          |
+-----------------------------------+------------------------------------------------------------------------------------------+
| Dirty Read (G1 / A1)              | T1 updates row R. T2 reads uncommitted R. T1 aborts. T2 acted on phantom data that never  |
|                                   | existed in committed history!                                                            |
+-----------------------------------+------------------------------------------------------------------------------------------+
| Non-Repeatable / Fuzzy Read (G2a) | T1 reads row R. T2 updates/deletes R and commits. T1 re-reads R and sees different values |
|                                   | or discovers the row has vanished mid-transaction!                                       |
+-----------------------------------+------------------------------------------------------------------------------------------+
| Phantom Read (A3)                 | T1 scans rows matching predicate P. T2 inserts a new row matching P and commits. T1      |
|                                   | re-executes the scan and observes new "phantom" rows in the result set.                   |
+-----------------------------------+------------------------------------------------------------------------------------------+
| Lost Update (P4)                  | T1 reads balance $100. T2 reads balance $100. T1 adds $50 and commits $150. T2 adds $20   |
|                                   | and overwrites with $120. T1's deposit is completely obliterated!                        |
+-----------------------------------+------------------------------------------------------------------------------------------+
| Read Skew (A5a)                   | Invariant: Balances A ($500) + B ($500) = $1,000. T2 transfers $100 from B to A and      |
|                                   | commits. T1 reads A ($500 before transfer) and B ($400 after transfer), calculating an    |
|                                   | inconsistent total of $900!                                                              |
+-----------------------------------+------------------------------------------------------------------------------------------+
| Write Skew (A5b)                  | Invariant: At least 1 doctor must be on call. Both Alice and Bob are on call. Alice       |
|                                   | checks on_call count (2), goes off call. Bob concurrently checks on_call count (2), goes |
|                                   | off call. Both commit! on_call count is now ZERO (Hospital has no doctors)!               |
+-----------------------------------+------------------------------------------------------------------------------------------+
```

---

## 5. Read Committed Isolation: Statement Snapshots & EvalPlanQual (EPQ)

`READ COMMITTED` is PostgreSQL's default isolation level.

### Statement-Level Snapshot Mechanics
Under Read Committed, a new transaction snapshot is acquired **at the beginning of every individual SQL statement**.
- If Transaction 1 executes a query at 12:00:01, it sees data committed prior to 12:00:01.
- If Transaction 2 commits an update at 12:00:02, and Transaction 1 issues a second query at 12:00:03, Transaction 1 **immediately sees Transaction 2's new values**.
- This enables high concurrency but permits Non-Repeatable Reads, Read Skew, and Lost Updates.

### Concurrent Update Rescanning: The EvalPlanQual (EPQ) Mechanism
What happens when Transaction 1 executes:
```sql
UPDATE accounts SET balance = balance - 100 WHERE id = 42;
```
while Transaction 2 is concurrently updating row 42?
1. Transaction 1 reaches row 42 and attempts to acquire an exclusive row lock.
2. It detects that Transaction 2 already holds a lock on row 42 (`t_xmax = Txn2`).
3. Transaction 1 **sleeps**, waiting on Transaction 2's completion.
4. **If Transaction 2 Aborts**: Transaction 1 wakes up and immediately proceeds with its update against the original row version.
5. **If Transaction 2 Commits**: Transaction 1 executes the **EvalPlanQual (EPQ)** protocol:
   - It fetches Transaction 2's newly committed tuple version.
   - It **re-evaluates the `WHERE` clause** against the new tuple!
   - If the new tuple still satisfies `WHERE id = 42`, the update proceeds on the new version.
   - If the new tuple no longer satisfies the predicate (e.g. if Txn2 changed `status` from `ACTIVE` to `CLOSED`), the row is skipped!

---

## 6. Repeatable Read Isolation & The First-Committer-Wins Rule

Under `REPEATABLE READ`, a transaction acquires **one single snapshot at the start of its first non-transaction-control statement** and retains that exact snapshot until final commit or abort.

### The Mechanics
- All queries executed within the transaction see the exact same frozen point-in-time view of the database.
- Non-repeatable reads and phantom reads are mathematically impossible.

### The First-Committer-Wins Rule & Serialization Failure (`40001`)
Under Repeatable Read, if Transaction 1 attempts to `UPDATE` or `DELETE` a row that was updated by a concurrent transaction that committed after Transaction 1's snapshot was taken:
1. PostgreSQL **refuses to overwrite the row** (unlike Read Committed, which uses EPQ to re-evaluate the new version).
2. Because updating would violate snapshot isolation, PostgreSQL immediately aborts Transaction 1 with a serialization failure:
   ```
   ERROR: could not serialize access due to concurrent update (SQLSTATE 40001)
   ```
3. **Application Responsibility**: Any application utilizing `REPEATABLE READ` **must implement an automated retry loop** with exponential backoff and jitter to catch and replay transactions that fail with SQLSTATE `40001`!

---

## 7. Serializable Snapshot Isolation (SSI): Mathematics of Write Skew Prevention

PostgreSQL’s `SERIALIZABLE` level implements **Serializable Snapshot Isolation (SSI)**, developed by Michael Cahill, Uwe Röhm, and Alan Fekete in 2008.

SSI provides true mathematical serializability **without locking readers**. Readers do not block writers, and writers do not block readers!

```
                    [SSI DANGEROUS STRUCTURE DETECTION]

            T1 (Alice)                        T2 (Bob)
         [Reads on_call]                   [Reads on_call]
                |                                 |
                v                                 v
   +-------------------------+       +-------------------------+
   | rw-antidependency:      |       | rw-antidependency:      |
   | T1 read rows that T2 is |       | T2 read rows that T1 is |
   | concurrently modifying! |       | concurrently modifying! |
   +------------+------------+       +------------+------------+
                |                                 |
                +----------------+----------------+
                                 |
                                 v
        [CYCLE DETECTED: T1 --rw--> T2 --rw--> T1]
      *** Aborts T2 with SQLSTATE 40001: could not serialize ***
```

### The Dependency Graph & rw-Antidependencies
SSI monitors transaction dependencies in shared memory (`src/backend/storage/lmgr/predicate.c`):
- $T_1 \xrightarrow{ww} T_2$: $T_1$ wrote a tuple version, and $T_2$ overwrote it.
- $T_1 \xrightarrow{wr} T_2$: $T_1$ wrote a tuple version, and $T_2$ read it.
- $T_1 \xrightarrow{rw} T_2$: **rw-antidependency (uncommitted read-write conflict)**: $T_1$ read a tuple version, and $T_2$ created a newer version that would have affected $T_1$'s read if executed sequentially!

### The Dangerous Structure Theorem
Cahill et al. mathematically proved that a non-serializable execution anomaly (such as Write Skew) can occur if and only if the dependency graph contains a **cycle containing two consecutive rw-antidependencies**:
$$T_{\text{prev}} \xrightarrow{rw} T_{\text{curr}} \xrightarrow{rw} T_{\text{next}}$$
- PostgreSQL maintains in-memory **`SIREAD` locks** on read tuples, pages, and relations. `SIREAD` locks do not block any operations!
- When a backend writes to a tuple or index page, it inspects existing `SIREAD` locks to record $rw$-antidependencies.
- If a cycle of two consecutive $rw$-edges forms, the SSI engine immediately flags a **Dangerous Structure**, selects the younger transaction, and aborts it with:
  ```
  ERROR: could not serialize access due to read/write dependencies among transactions (SQLSTATE 40001)
  ```

---

## 8. Heavyweight Table Locks: The 8x8 Conflict Matrix

PostgreSQL maintains two distinct lock managers in shared memory: **Heavyweight Locks** (coarse-grained, table/relation level) and **Row-Level Locks** (fine-grained, stored inside heap tuple headers).

Heavyweight table locks govern structural DDL and table-wide operations. There are **8 lock modes**, organized in order of increasing exclusivity:

```
+--------------------------+----+----+----+----+----+----+----+----+
| Heavyweight Lock Mode    | 1  | 2  | 3  | 4  | 5  | 6  | 7  | 8  |
+--------------------------+----+----+----+----+----+----+----+----+
| 1. AccessShareLock       |    |    |    |    |    |    |    | X  |
| 2. RowShareLock          |    |    |    |    |    |    | X  | X  |
| 3. RowExclusiveLock      |    |    |    |    | X  | X  | X  | X  |
| 4. ShareUpdateExclusive  |    |    |    | X  | X  | X  | X  | X  |
| 5. ShareLock             |    |    | X  | X  |    | X  | X  | X  |
| 6. ShareRowExclusive     |    |    | X  | X  | X  | X  | X  | X  |
| 7. ExclusiveLock         |    | X  | X  | X  | X  | X  | X  | X  |
| 8. AccessExclusiveLock   | X  | X  | X  | X  | X  | X  | X  | X  |
+--------------------------+----+----+----+----+----+----+----+----+
  (X denotes a conflict where the second requester is blocked)
```

### Typical SQL Statement Lock Mapping
- **`AccessShareLock` (1)**: Acquired by `SELECT`. Conflicts only with `AccessExclusiveLock`.
- **`RowShareLock` (2)**: Acquired by `SELECT FOR UPDATE / FOR SHARE`.
- **`RowExclusiveLock` (3)**: Acquired by `INSERT`, `UPDATE`, `DELETE`. Permits concurrent readers and writers on different rows!
- **`ShareUpdateExclusiveLock` (4)**: Acquired by `VACUUM` (non-full), `ANALYZE`, `CREATE INDEX CONCURRENTLY`, `ALTER TABLE VALIDATE CONSTRAINT`. Conflicts with itself and higher locks.
- **`ShareLock` (5)**: Acquired by standard `CREATE INDEX` (non-concurrent). Allows concurrent `SELECT`, blocks all `INSERT`, `UPDATE`, `DELETE`.
- **`AccessExclusiveLock` (8)**: Acquired by `DROP TABLE`, `TRUNCATE`, `VACUUM FULL`, `ALTER TABLE ADD COLUMN`. Conflicts with **every other lock mode**, completely blocking all concurrent reads and writes!

---

## 9. Row-Level Locks & The Tuples-In-Heap Architecture

PostgreSQL provides four explicit row-level locking clauses:

```
+--------------------+-----------------------------------------------------+---------------------------------------+
| Lock Clause        | Lock Mode Acquired                                  | Conflicting Concurrent Requests       |
+--------------------+-----------------------------------------------------+---------------------------------------+
| FOR UPDATE         | Exclusive Row Lock                                  | FOR UPDATE, FOR NO KEY UPDATE,        |
|                    |                                                     | FOR SHARE, FOR KEY SHARE, UPDATE, DEL |
+--------------------+-----------------------------------------------------+---------------------------------------+
| FOR NO KEY UPDATE  | Weak Exclusive (Non-PK column updates)              | FOR UPDATE, FOR NO KEY UPDATE,        |
|                    |                                                     | FOR SHARE, UPDATE, DELETE             |
+--------------------+-----------------------------------------------------+---------------------------------------+
| FOR SHARE          | Shared Row Lock                                     | FOR UPDATE, FOR NO KEY UPDATE, DELETE |
+--------------------+-----------------------------------------------------+---------------------------------------+
| FOR KEY SHARE      | Weak Shared (Foreign Key validation)                | FOR UPDATE, DELETE                    |
+--------------------+-----------------------------------------------------+---------------------------------------+
```

### How Row Locks are Physically Stored: Zero Memory Overhead!
In many database engines, locking 10,000,000 rows exhausts shared memory lock tables, forcing **lock escalation** to a table-level lock.
PostgreSQL **never escalates locks**. Why?
**Row locks are written directly into the 8KB Heap Page tuple header!**
- When a transaction acquires `FOR UPDATE` on a row, it stamps its own `XID` into the tuple's `t_xmax` field and sets the bit flag `HEAP_XMAX_EXCL_LOCK` in `t_infomask`.
- If multiple transactions acquire shared locks on the same row, PostgreSQL allocates a **MultiXactId** (stored in `pg_multixact`) and writes the MultiXact ID into `t_xmax`.
- Because row locks reside inside the tuple headers on disk/cache, a transaction can lock billions of rows without consuming a single additional byte in the shared memory lock table!

---

## 10. High-Throughput Queue Processing: `SKIP LOCKED` vs `NOWAIT`

In modern distributed microservices, databases are frequently used as transactional message brokers or task queues (e.g. background job dispatching, payment event queues).

```
                      [JOB QUEUE WORKER DISPATCH COMPARISON]

  1. Naive SELECT FOR UPDATE:
     Worker 1: Locks Job #1 --------------> Processing (100ms)
     Worker 2: Requests Job #1 -----------> BLOCKED! (Stalls until Worker 1 commits)
     Worker 3: Requests Job #1 -----------> BLOCKED! (Thread pool starvation)

  2. SELECT FOR UPDATE SKIP LOCKED:
     Worker 1: Locks Job #1 --------------> Processing (100ms)
     Worker 2: Skips Job #1, Locks Job #2 -> Processing immediately!
     Worker 3: Skips Jobs #1 & #2, Locks #3> Processing immediately!
     *** 100% Lock-Free Concurrent Scaling Across All Worker Threads ***
```

### The Fail-Fast Alternative: `NOWAIT`
```sql
SELECT * FROM payments WHERE id = 100 FOR UPDATE NOWAIT;
```
If another transaction currently holds a lock on row 100, the query **aborts immediately** with `SQLSTATE 55P03: lock_not_available`, preventing connection pool starvation.

### The Lock-Free Worker Pattern: `SKIP LOCKED`
```sql
-- Production Queue Worker Fetch Query:
WITH next_job AS (
    SELECT id, payload 
    FROM job_queue 
    WHERE status = 'PENDING' 
    ORDER BY priority DESC, id ASC 
    LIMIT 1 
    FOR UPDATE SKIP LOCKED
)
UPDATE job_queue 
SET status = 'PROCESSING', started_at = NOW() 
FROM next_job 
WHERE job_queue.id = next_job.id 
RETURNING job_queue.id, job_queue.payload;
```
- **Zero Contention**: Every worker instantly skips past rows currently locked by other workers.
- **No Head-of-Line Blocking**: Long-running jobs do not stall adjacent tasks.

---

## 11. PostgreSQL Advisory Locks

Advisory locks are application-defined mutexes managed by the PostgreSQL lock manager. They do not lock tables or rows; they lock arbitrary 64-bit integers.

### Operational Primitives
1. **Transaction-Scoped vs Session-Scoped**:
   - `pg_advisory_xact_lock(bigint)`: Automatically released when the surrounding transaction commits or aborts. **Safest for backend APIs**.
   - `pg_advisory_lock(bigint)`: Retained across transactions; must be explicitly released via `pg_advisory_unlock(bigint)`. Vulnerable to connection pooling leaks!
2. **Blocking vs Non-Blocking**:
   - `pg_try_advisory_xact_lock(bigint)`: Returns `TRUE` if acquired immediately, or `FALSE` without blocking if already held.

### Production Enterprise Use Cases
- **Distributed Cron / Singleton Workers**: Preventing duplicate execution across multi-pod Kubernetes deployments:
  ```sql
  SELECT pg_try_advisory_xact_lock(hashtext('daily_billing_job'));
  ```
- **External Resource Mutexes**: Preventing concurrent webhook dispatch or third-party payment gateway calls per tenant.

---

## 12. Deadlock Mechanics, Detection & The Wait-For Graph

A deadlock occurs when two or more concurrent transactions have an unresolved circular dependency: each holds a lock that the other requires to proceed.

```
Transaction 1: Holds Lock on Account A ------------> Demands Lock on Account B (BLOCKED)
      ^                                                            |
      |                                                            v
Transaction 2: Demands Lock on Account A (BLOCKED) <--------- Holds Lock on Account B
```

### The Coffman Conditions
A deadlock can only occur if all four conditions are met simultaneously:
1. **Mutual Exclusion**: Resources cannot be shared concurrently.
2. **Hold and Wait**: A transaction holds at least one lock while waiting for another.
3. **No Preemption**: Locks cannot be forcibly confiscated by the engine before commit.
4. **Circular Wait**: A closed chain of transactions exists where each waits on the next.

### PostgreSQL Deadlock Detection Engine
1. **`deadlock_timeout` (Default: 1000ms / 1 second)**: When a transaction is blocked waiting for a lock, PostgreSQL **does not immediately run the deadlock detector**. Running cycle detection on every lock acquisition would cause massive CPU thrashing. Instead, it starts a timer.
2. **Wait-For Graph (WFG) Construction**: If the timer reaches `deadlock_timeout`, the backend wakes up the deadlock detection routine. It inspects the shared memory lock tables and builds a directed Wait-For Graph where nodes are transactions and edges represent wait dependencies ($T_1 \to T_2$).
3. **Cycle Detection (Tarjan’s Algorithm / DFS)**: If a directed cycle is identified, PostgreSQL selects a **victim transaction** (typically the one that triggered the check or the one that made the least changes) and forcibly aborts it:
   ```
   ERROR: deadlock detected (SQLSTATE 40P01)
   DETAIL: Process 14022 waits for ExclusiveLock on relation; blocked by process 14023.
           Process 14023 waits for ShareLock on relation; blocked by process 14022.
   ```

---

## 13. Deadlock Prevention Architectures in Production

While deadlock detection ensures the database does not hang permanently, relying on deadlock aborts degrades application throughput. Production architectures prevent deadlocks proactively:

### Strategy 1: Strict Resource Ordering (The Invariant Rule)
If all transactions in the codebase acquire locks on multiple resources in the **exact same global order**, a circular wait condition is mathematically impossible!
```python
# ANTIPATTERN (Prone to deadlocks when accounts transfer concurrently in reverse):
def transfer(from_id: int, to_id: int, amount: Decimal):
    # Txn 1: locks 10 then 20. Txn 2: locks 20 then 10 -> DEADLOCK!
    account_from = Account.objects.select_for_update().get(id=from_id)
    account_to = Account.objects.select_for_update().get(id=to_id)

# ENTERPRISE PATTERN: Always sort primary keys before locking!
def transfer_safe(from_id: int, to_id: int, amount: Decimal):
    first_id, second_id = sorted([from_id, to_id])
    first_acct = Account.objects.select_for_update().get(id=first_id)
    second_acct = Account.objects.select_for_update().get(id=second_id)
    # Circular wait is mathematically impossible!
```

### Strategy 2: Defensive Timeouts
Prevent transactions from holding locks indefinitely:
```sql
SET lock_timeout = '2000ms';         -- Max time waiting to acquire any lock
SET statement_timeout = '5000ms';    -- Max total query execution time
```

### Strategy 3: Zero Network I/O Inside Transactions
Never execute HTTP requests, third-party API calls, or email dispatch inside a database transaction! If a third-party gateway experiences a 10-second latency spike, database row locks remain held for 10 seconds, cascading into system-wide connection exhaustion.

---

## 14. Two-Phase Commit (2PC) & Distributed Transactions

In microservice architectures or multi-database deployments, atomic cross-database coordination requires the **Two-Phase Commit (2PC)** protocol.

```
Coordinator                         Participant A (DB 1)            Participant B (DB 2)
    |                                        |                               |
    |--- 1. PREPARE TRANSACTION 'xact_1' --->|                               |
    |--- 1. PREPARE TRANSACTION 'xact_1' ----------------------------------->|
    |                                        |                               |
    |<-- VOTE: PREPARED ---------------------|                               |
    |<-- VOTE: PREPARED -----------------------------------------------------|
    |                                        |                               |
    | [Decision: ALL PREPARED -> COMMIT]     |                               |
    |                                        |                               |
    |--- 2. COMMIT PREPARED 'xact_1' ------->|                               |
    |--- 2. COMMIT PREPARED 'xact_1' --------------------------------------->|
```

### Physical Implementation in PostgreSQL
1. **`PREPARE TRANSACTION 'tx_identifier'`**: The backend flushes all state to WAL, creates a persistent state file under `$PGDATA/pg_twophase/`, and releases the client connection. **Crucially, all row and table locks remain held!**
2. **`COMMIT PREPARED 'tx_identifier'`**: The prepared transaction is finalized, locks are released, and the state file is removed.
3. **`ROLLBACK PREPARED 'tx_identifier'`**: The prepared transaction is aborted.

### The Catastrophic Orphaned 2PC Trap
If a distributed coordinator crashes after phase 1 (`PREPARE`) and fails to issue `COMMIT PREPARED` or `ROLLBACK PREPARED`, the prepared transaction **remains alive indefinitely**:
- It holds row and table locks forever, blocking future writes.
- It prevents Autovacuum from cleaning dead tuples, causing explosive table bloat.
- Its `xmin` holds back the cluster-wide transaction horizon, eventually triggering emergency shutdown due to **Transaction ID (XID) Wraparound**!
- **Runbook**: Audit orphaned 2PC transactions via `SELECT * FROM pg_prepared_xacts;`.

---

## 15. Production Lock Monitoring & Diagnostics Runbook

### Step 1: Detect Live Blocked Transactions & The Blocker Tree
```sql
SELECT 
    blocked_locks.pid     AS blocked_pid,
    blocked_activity.usename  AS blocked_user,
    blocking_locks.pid    AS blocking_pid,
    blocking_activity.usename AS blocking_user,
    blocked_activity.query    AS blocked_statement,
    blocking_activity.query   AS blocking_statement,
    NOW() - blocked_activity.query_start AS waiting_duration
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks 
    ON blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
    AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
    AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

### Step 2: Gracefully Terminate Blocker Processes
1. **Cancel the Current Query (Safe)**:
   ```sql
   SELECT pg_cancel_backend(<blocking_pid>);
   ```
2. **Terminate Backend Process (Forces Disconnect)**:
   ```sql
   SELECT pg_terminate_backend(<blocking_pid>);
   ```
