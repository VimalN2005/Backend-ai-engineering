# Top 5 Senior Database Engineering Interview Questions: Transactions, ACID & Deadlocks

This guide provides deep, architectural responses to the most critical transaction concurrency and deadlock questions asked in senior backend and systems engineering interviews.

---

### Q1: What are the mechanical differences between PostgreSQL's `READ COMMITTED` and `REPEATABLE READ` isolation levels, and what is the "First-Committer-Wins" rule?

#### The Architectural Answer
The fundamental mechanical difference between `READ COMMITTED` and `REPEATABLE READ` lies in **when and how transaction snapshots are acquired**:

1. **`READ COMMITTED` (Statement-Level Snapshots)**:
   - A brand-new MVCC snapshot is acquired **at the beginning of every individual SQL statement**.
   - If Query 1 executes at $T_1$, and concurrent Transaction $B$ commits an update at $T_2$, Query 2 executed at $T_3$ within the same transaction **will see Transaction B's changes**.
   - Concurrent updates use the **EvalPlanQual (EPQ)** protocol: if row $R$ is concurrently updated by a committed transaction, PostgreSQL fetches the new version of $R$ and re-evaluates the `WHERE` clause before applying the update.

2. **`REPEATABLE READ` (Transaction-Level Snapshot)**:
   - A single snapshot is acquired at the beginning of the **first non-transaction-control statement** and retained across the entire duration of the transaction.
   - All subsequent queries see the exact same point-in-time database image. Non-repeatable reads and phantom reads are mathematically impossible.

#### The "First-Committer-Wins" Rule (`40001`)
Under Repeatable Read, if Transaction 1 attempts to `UPDATE` or `DELETE` a row that was modified by a concurrent transaction that committed *after* Transaction 1’s snapshot was acquired:
- PostgreSQL **strictly refuses to overwrite the row** (it cannot use EPQ, because mutating the new version would violate snapshot isolation).
- PostgreSQL immediately aborts Transaction 1 with:
  ```
  ERROR: could not serialize access due to concurrent update (SQLSTATE 40001)
  ```
- **Architectural Requirement**: Any system operating under `REPEATABLE READ` must implement an automated client-side retry loop with exponential backoff and randomized jitter to replay transactions that fail with SQLSTATE `40001`.

---

### Q2: What is the Write Skew anomaly, why does `REPEATABLE READ` fail to prevent it, and how does Serializable Snapshot Isolation (SSI) mathematically detect it without locking readers?

#### The Architectural Answer

#### The Write Skew Mechanism
Write Skew occurs when two concurrent transactions read overlapping datasets, verify a system-wide invariant, and make **disjoint writes** that independently preserve the invariant from their local view, but together violate the global invariant.

**The On-Call Doctors Example**:
- Invariant: At least one doctor must be on call ($N \ge 1$).
- Alice and Bob are both currently on call ($N = 2$).
- Alice checks on-call count ($N=2$), sets `alice.on_call = false`.
- Bob concurrently checks on-call count ($N=2$), sets `bob.on_call = false`.
- Under `REPEATABLE READ`, Alice modifies row `alice`, and Bob modifies row `bob`. Because the rows modified are completely disjoint, no row lock conflicts occur! Both commit successfully. Total on-call doctors is now **zero**, violating the invariant!

#### Why Repeatable Read Fails
Repeatable Read only detects concurrent writes to the **exact same row version**. Because Alice and Bob updated different physical rows, the First-Committer-Wins rule never triggers.

#### The SSI Solution: The Dangerous Structure Theorem
PostgreSQL’s `SERIALIZABLE` level implements **Serializable Snapshot Isolation (SSI)** (Cahill et al., 2008):
1. **Zero Reader Blocking**: SSI does not acquire exclusive read locks. Readers never block writers!
2. **`SIREAD` Locks**: Readers record non-blocking `SIREAD` lock flags in shared memory (`predicate.c`).
3. **$rw$-Antidependencies**: When a writer modifies a tuple, it checks existing `SIREAD` locks. If another active transaction read that tuple version, an $rw$-antidependency edge is recorded:
   $$T_{\text{reader}} \xrightarrow{rw} T_{\text{writer}}$$
4. **Dangerous Structures**: Cahill proved that non-serializable schedules only occur when the serialization graph contains a cycle with two consecutive $rw$-antidependencies:
   $$T_1 \xrightarrow{rw} T_2 \xrightarrow{rw} T_1$$
   When Alice updates `alice` (which Bob read), Bob $\xrightarrow{rw}$ Alice is created. When Bob updates `bob` (which Alice read), Alice $\xrightarrow{rw}$ Bob is created. A bidirectional cycle forms!
5. **Resolution**: The SSI engine flags the dangerous structure and aborts one transaction with `SQLSTATE 40001: could not serialize access due to read/write dependencies`.

---

### Q3: How does PostgreSQL store row-level locks on disk, and why does locking 100,000,000 rows never cause lock escalation to a table-level lock?

#### The Architectural Answer
In traditional database engines (e.g. Microsoft SQL Server or older relational systems), row locks are allocated as dynamic data structures in a fixed-size shared memory lock table. If a transaction locks millions of rows, the memory lock table exhausts its allocated RAM, forcing the engine to execute **Lock Escalation** (converting millions of fine-grained row locks into a single coarse-grained table-level `ExclusiveLock`, stalling concurrent queries).

#### PostgreSQL's Tuples-In-Heap Architecture
PostgreSQL **never escalates locks** because row locks are **not stored in the shared memory lock table**:
1. When a transaction acquires `SELECT FOR UPDATE` on a row, the engine writes the transaction ID directly into the physical tuple header's **`t_xmax`** field inside the 8KB heap page.
2. It sets the bit flag `HEAP_XMAX_EXCL_LOCK` in the tuple header’s `t_infomask`.
3. If multiple transactions acquire shared locks on the same row (`FOR SHARE`), PostgreSQL allocates a **MultiXactId** in `$PGDATA/pg_multixact/` and stores the MultiXact ID in `t_xmax`.
4. **Infinite Scalability**: Because the lock state is embedded directly inside the 8KB slotted page on disk and in `shared_buffers`, a transaction can lock 100 million rows without consuming a single extra byte of shared memory!

---

### Q4: Explain the operational differences between `SELECT FOR UPDATE`, `NOWAIT`, and `SKIP LOCKED`. How does `SKIP LOCKED` eliminate lock convoying in concurrent worker architectures?

#### The Architectural Answer

#### 1. Bare `SELECT FOR UPDATE` (Pessimistic Blocking)
Acquires an exclusive lock on matching rows. If another transaction already holds a lock on a matching row, the query **blocks and sleeps** until the holding transaction commits or aborts.
- **Problem in Queues (Lock Convoying)**: If 10 Celery or Kubernetes workers execute `SELECT * FROM tasks WHERE status = 'PENDING' LIMIT 1 FOR UPDATE`, Worker 1 locks Task #1. Workers 2 through 10 all block waiting for Worker 1 to finish. Worker concurrency collapses from 10 to 1!

#### 2. `NOWAIT` (Fail-Fast)
Attempts to acquire row locks. If *any* candidate row is locked by another transaction, the query **aborts immediately** with:
```
ERROR: could not obtain lock on row in relation "tasks" (SQLSTATE 55P03)
```
- **Use Case**: Interactive user-facing APIs where waiting on a lock would breach latency SLAs.

#### 3. `SKIP LOCKED` (Lock-Free Concurrency)
Instructs the storage engine that if a candidate row is currently locked by another transaction, it should be **instantly skipped and omitted from the result set**:
```sql
WITH next_task AS (
    SELECT id 
    FROM tasks 
    WHERE status = 'PENDING' 
    ORDER BY priority DESC, id ASC 
    LIMIT 1 
    FOR UPDATE SKIP LOCKED
)
UPDATE tasks 
SET status = 'PROCESSING', worker_id = 'worker_pod_04' 
FROM next_task 
WHERE tasks.id = next_task.id 
RETURNING tasks.*;
```
- **Architectural Value**: Worker 1 locks Task #1. Worker 2 skips Task #1 and locks Task #2. Worker 3 skips Tasks #1 and #2 and locks Task #3. All $N$ workers process distinct jobs simultaneously with **zero lock contention and zero latency overhead**!

---

### Q5: How does PostgreSQL detect deadlocks? Explain the Wait-For Graph (WFG) cycle detection algorithm and how strict resource ordering prevents deadlocks in production code.

#### The Architectural Answer

#### Deadlock Detection Mechanics
A deadlock occurs when two or more transactions form a closed circular dependency in lock acquisition: $T_1$ holds Lock $A$ and demands Lock $B$; $T_2$ holds Lock $B$ and demands Lock $A$.

1. **The `deadlock_timeout` Filter (Default: 1000ms)**:
   Checking for deadlocks on every lock wait would saturate database CPUs with graph traversals. PostgreSQL avoids this by starting a timer when a transaction blocks. The deadlock detector only activates if a lock acquisition has been blocked for longer than `deadlock_timeout`.
2. **Wait-For Graph (WFG) Construction**:
   The backend traverses shared memory lock queues and builds a directed graph where nodes represent backend processes/transactions, and directed edges ($T_1 \to T_2$) represent wait dependencies ($T_1$ is waiting for a lock held by $T_2$).
3. **Cycle Detection (Tarjan’s Algorithm / DFS)**:
   The engine runs Depth-First Search to locate directed cycles. If a cycle is discovered, PostgreSQL selects a **victim transaction** (typically the one that triggered the detector) and forcibly aborts it with `SQLSTATE 40P01: deadlock detected`, freeing its locks and allowing the surviving transactions to proceed.

#### Deadlock Prevention: The Strict Resource Ordering Invariant
While the deadlock detector prevents permanent hangs, deadlock aborts degrade application throughput. Production code prevents deadlocks by enforcing **Global Resource Ordering**:
- Coffman's fourth necessary condition for deadlocks is **Circular Wait**.
- If all transactions in an application acquire locks on multiple resources in the **exact same global sort order** (e.g. sorting primary keys in ascending order: `ORDER BY id`), a circular wait condition is **mathematically impossible**!
```python
# Guaranteed Deadlock-Free Multi-Account Transfer:
def transfer(from_id: int, to_id: int, amount: Decimal):
    # Enforce global acquisition invariant: min(id) -> max(id)
    first_id, second_id = sorted([from_id, to_id])
    with transaction.atomic():
        first = Account.objects.select_for_update().get(id=first_id)
        second = Account.objects.select_for_update().get(id=second_id)
        # Execution is mathematically immune to deadlocks!
```
