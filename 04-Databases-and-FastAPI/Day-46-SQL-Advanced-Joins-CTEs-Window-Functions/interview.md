# Day 46: Senior Backend Engineer Interview Q&A — Advanced SQL, CTEs & Window Functions

This guide provides deep technical responses to senior and staff backend engineering interview questions regarding SQL query optimization, internal execution models, relational join mechanics, recursive CTEs, and window frames.

---

### Question 1: Detail the exact logical SQL query execution order and explain why column aliases defined in `SELECT` are unavailable in `WHERE` and `HAVING`.

#### Answer:
While an SQL query is lexically written starting with `SELECT`, the database query processor executes the query stages in a fundamentally different **Logical Execution Order**:

```
1. FROM & JOIN     ──► Builds Cartesian product of relations; applies ON join predicates.
2. WHERE           ──► Evaluates row-level filters BEFORE grouping.
3. GROUP BY        ──► Collapses filtered rows into aggregation groups.
4. HAVING          ──► Evaluates group-level filters (aggregate expressions).
5. SELECT          ──► Projects column expressions, scalar subqueries, and binds aliases.
6. DISTINCT        ──► Eliminates duplicate projected tuples.
7. WINDOW          ──► Evaluates OVER (PARTITION BY ... ORDER BY ...) window functions.
8. ORDER BY        ──► Sorts the final output stream.
9. LIMIT / OFFSET  ──► Slices the sorted result set.
```

#### Why Column Aliases Fail in `WHERE` and `HAVING`:
- When the query planner executes the `WHERE` clause at **Step 2**, the `SELECT` clause (which evaluates at **Step 5**) has **not yet executed**.
- The column aliases (e.g. `SELECT salary * 0.10 AS bonus`) do not exist in the relational engine's symbol table or memory registers when Step 2 evaluates.
- Similarly, `HAVING` executes at **Step 4** (before `SELECT`), so standard SQL prohibits referencing `SELECT` aliases in `HAVING` (unless supported by non-standard dialect extensions like MySQL).
- Conversely, `ORDER BY` executes at **Step 8** (*after* `SELECT`), which is why column aliases are fully valid inside `ORDER BY`.

---

### Question 2: Explain the mechanical difference between `ROW_NUMBER()`, `RANK()`, and `DENSE_RANK()`. Provide an execution trace with duplicate scores.

#### Answer:
All three functions assign integer ranks to rows within an analytic window partition, but they handle **ties (identical sorting values)** with fundamentally different algorithms:

```
Sample Data: Test Scores = [100, 95, 95, 80]

Score | ROW_NUMBER() | RANK() | DENSE_RANK()
--------------------------------------------
 100  |      1       |   1    |      1
  95  |      2       |   2    |      2
  95  |      3       |   2    |      2
  80  |      4       |   4    |      3       <-- RANK skipped 3; DENSE_RANK did not!
```

#### Detailed Mechanical Analysis:
1. **`ROW_NUMBER()`**:
   - Strictly monotonic sequential integer increment ($1, 2, 3, 4, \dots$).
   - Never produces ties. If multiple rows share identical sort values, the assignment is non-deterministic unless deterministic secondary tie-breakers are provided in `ORDER BY`.
   - **Use Case**: Keyset pagination, deduplication, extracting exactly 1 arbitrary row.
2. **`RANK()`**:
   - Assigns identical ranks to tied rows.
   - **Skips subsequent ranks**: the rank of the next row after a tie is $\text{rank} = \text{row\_number}$.
   - For two tied rows at rank 2, the next rank is 4 (rank 3 is skipped).
   - **Use Case**: Olympic medal distribution (two silver medalists means no bronze is awarded).
3. **`DENSE_RANK()`**:
   - Assigns identical ranks to tied rows.
   - **Never skips ranks**: the rank of the next row is always $\text{previous\_dense\_rank} + 1$.
   - For two tied rows at rank 2, the next rank is 3.
   - **Use Case**: "Top 3 highest salaries in each department", ensuring all employees within the top 3 salary tiers are captured without rank gaps.

---

### Question 3: Contrast `ROWS BETWEEN ...` vs `RANGE BETWEEN ...` in window frame specifications. What is the default frame when `ORDER BY` is present, and why is it dangerous?

#### Answer:
A **Window Frame** defines the subset of rows within the partition that are visible to an aggregate or analytic function at the current row's evaluation point.

#### The Fundamental Difference:
- **`ROWS` (Physical Row Offsets)**: Specifies the window frame in terms of physical row counts relative to the current row (e.g. `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW`). It does not care about duplicate values.
- **`RANGE` (Logical Value Offsets)**: Specifies the frame in terms of the **value** of the `ORDER BY` expression. It treats all rows sharing the **exact same value as a single peer group**.

#### The Dangerous ANSI SQL Default:
When an `ORDER BY` clause is present without an explicit frame, ANSI SQL defaults to:
`RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`

#### The Failure Mechanics:
1. **Unintended Aggregation Collapse**: If multiple ledger entries share the exact same timestamp or date:
   - `ROWS` accumulates row-by-row: 10, then 30, then 45.
   - `RANGE` treats all rows with that timestamp as peers and extends the frame to the **last peer in the group**!
   - Result: Both duplicate rows display the **combined group total** ($30, 30$) instead of accumulating individually!
2. **Severe Performance Penalty**: Because `RANGE` must identify and track all duplicate peer values in memory, the database engine cannot execute a simple running accumulator. It must perform peer collation, causing query execution to be **orders of magnitude slower**.
3. **Rule**: **Always explicitly write `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`**.

---

### Question 4: How do Join algorithms (Nested Loop, Hash Join, Merge Join) work under the hood? When does the PostgreSQL query planner select each?

#### Answer:

| Join Algorithm | Mechanism | Time Complexity | Memory Requirement | Planner Selection Profile |
|:---|:---|:---|:---|:---|
| **Nested Loop Join** | For every row in outer table $R$, scan inner table $S$ using an index. | $O(\|R\| \times \log \|S\|)$ | Minimal ($O(1)$) | Outer table is tiny ($< 100$ rows) and inner table has a B-Tree index on join key. |
| **Hash Join** | Builds in-memory Hash Table on inner table $S$; sequentially scans outer table $R$ probing the hash table. | $O(\|R\| + \|S\|)$ | High ($O(\|S\|)$ in `work_mem`) | Large unindexed tables, equality joins (`=`). |
| **Merge Join** (Sort-Merge) | Pre-sorts both relations on join key; scans both sorted streams in lock-step. | $O(\|R\| \log \|R\| + \|S\| \log \|S\|)$ | Medium | Both relations are already pre-sorted by an existing index or clustered table. |

#### Production Trap: Hash Join Spilling to Disk
If the inner relation $S$ exceeds the configured `work_mem` (PostgreSQL default: 4 MB):
- The query planner cannot fit the hash table in RAM.
- It splits the hash table into batches and spills them to temporary disk files (`pgsql_tmp`).
- Disk I/O explodes, and query latency degrades from 15 ms to 8,000 ms.
- **Remediation**: Increase `work_mem` for analytical queries: `SET work_mem = '64MB';`.

---

### Question 5: How do Recursive CTEs execute internally? Explain the anchor member, working table, and intermediate evaluation step.

#### Answer:
A Recursive Common Table Expression (`WITH RECURSIVE`) is an iterative loop executed inside the database engine.

#### Standard Structure:
```sql
WITH RECURSIVE cte_name AS (
    -- 1. Anchor Member
    SELECT ...
    UNION ALL
    -- 2. Recursive Member
    SELECT ... FROM cte_name JOIN ...
)
SELECT * FROM cte_name;
```

#### Internal Execution Algorithm:
1. **Initialization**:
   - The database executes the **Anchor Member**.
   - The resulting rows are placed into two internal structures:
     1. **Result Table** (accumulates the total output).
     2. **Working Table** (contains only rows produced by the latest iteration, initially $R_0$).
2. **The Iteration Loop**:
   - While the **Working Table** is not empty:
     1. The engine executes the **Recursive Member**, substituting the reference `cte_name` with the **current contents of the Working Table**.
     2. This produces a new intermediate result set $R_{k+1}$.
     3. The current Working Table is cleared.
     4. $R_{k+1}$ is inserted into the Result Table.
     5. $R_{k+1}$ replaces the Working Table for the next iteration.
3. **Termination**:
   - When the Recursive Member produces **zero rows** ($R_{k+1} = \emptyset$), the loop terminates.
   - The engine returns the complete accumulated **Result Table**.

#### Infinite Loop Vulnerability:
If the underlying relational graph contains a cycle (e.g. Node A reports to Node B, and Node B reports to Node A), $R_{k+1}$ is never empty! The query runs indefinitely until worker timeout or memory exhaustion. Always guard with cycle detection (`ARRAY[id]` path checking).
