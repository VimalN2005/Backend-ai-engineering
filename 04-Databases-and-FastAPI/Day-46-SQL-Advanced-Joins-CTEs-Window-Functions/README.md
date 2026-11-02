# Day 46: SQL Mastery — Advanced Joins, CTEs & Analytic Window Functions

## 1. Executive Architectural Overview

In high-performance backend systems, the database is not merely a passive document store or dumb persistence layer; it is an optimized, set-based relational execution engine. Writing inefficient queries and shifting set manipulation, aggregations, and window computations into Python application code leads to massive memory bloat, high network serialization overhead, and CPU starvation.

Senior backend engineers leverage advanced SQL constructs—**Advanced Joins**, **Common Table Expressions (CTEs)**, and **Analytic Window Functions**—to execute complex aggregations directly within the database engine in $O(N \log N)$ or $O(N)$ time, leveraging disk-backed B-Trees, hash tables, and shared memory buffers.

```
========================================================================================================
                                     LOGICAL SQL EXECUTION PIPELINE
========================================================================================================

    [1. FROM & JOIN] ────────► Resolves table cartesian products, applies ON predicates, joins tables
            │
            ▼
       [2. WHERE]    ────────► Filters individual rows BEFORE grouping (Cannot use aggregate aliases)
            │
            ▼
     [3. GROUP BY]   ────────► Collapses rows into group partitions
            │
            ▼
      [4. HAVING]    ────────► Filters aggregated groups (Evaluates aggregate predicates)
            │
            ▼
      [5. SELECT]    ────────► Computes expressions, scalar subqueries, and output column projections
            │
            ▼
     [6. DISTINCT]   ────────► Removes duplicate projected tuples
            │
            ▼
      [7. WINDOW]    ────────► Computes OVER (PARTITION BY ... ORDER BY ...) analytic functions
            │
            ▼
     [8. ORDER BY]   ────────► Sorts final output rows (Can reference SELECT aliases and positions)
            │
            ▼
  [9. LIMIT / OFFSET] ───────► Slices result set (Discards preceding rows for pagination)
```

---

## 2. Logical Query Execution Order: The Mechanics

The fundamental reason developers encounter syntax errors such as `"column alias does not exist"` or `"window functions are not allowed in WHERE"` is the mismatch between **Lexical Syntax Order** (how SQL is written) and **Logical Execution Order** (how the SQL query planner processes the query).

### Why Aliases Fail in `WHERE`:
```sql
-- SYNTAX ERROR in standard SQL:
SELECT id, (salary * 0.10) AS bonus
FROM employees
WHERE bonus > 5000; -- ERROR: column "bonus" does not exist!
```
**Physical Mechanism**: The `WHERE` clause executes at Step 2 to filter rows from disk *before* the `SELECT` clause (Step 5) projects the output columns and binds the alias `bonus`. The alias does not exist in memory when `WHERE` is evaluated.

### Why Window Functions Fail in `WHERE` and `HAVING`:
Window functions evaluate at Step 7, *after* `WHERE`, `GROUP BY`, and `HAVING` have executed. Allowing a window function in `WHERE` would create a circular dependency: you would need to know the window partition results to filter rows, but you need to filter rows to define the window partition!
To filter on a window function (e.g. `ROW_NUMBER() <= 3`), the query **must wrap the window function in a CTE or subquery**.

---

## 3. Advanced Relational Joins & Execution Algorithms

Relational joins combine tuples from two relations based on a join predicate.

### 3.1 Advanced Join Variants
- **`INNER JOIN`**: Returns tuples where the join predicate evaluates to `TRUE` in both tables.
- **`LEFT OUTER JOIN`**: Returns all tuples from the left relation, matched with tuples from the right relation or padded with `NULL` if no match exists.
- **`FULL OUTER JOIN`**: Returns all tuples from both relations, matching where possible and padding with `NULL` on either side where matches fail.
- **`CROSS JOIN`**: Cartesian product ($|R| \times |S|$ rows). Used for generating matrices, date dimensions, or testing permutations.
- **`ANTI-JOIN`**: Returns rows from the left table that have **no matching row** in the right table:
  ```sql
  SELECT u.id, u.email
  FROM users u
  LEFT JOIN orders o ON u.id = o.user_id
  WHERE o.id IS NULL; -- Anti-Join: users with ZERO orders
  ```
- **`SEMI-JOIN`**: Returns rows from the left table where at least one match exists in the right table, without duplicating rows if multiple matches exist:
  ```sql
  SELECT u.id, u.email
  FROM users u
  WHERE EXISTS (
      SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.status = 'COMPLETED'
  );
  ```

### 3.2 Join Execution Algorithms Under the Hood
PostgreSQL's query planner selects one of three physical algorithms based on statistics and table size:

```
1. NESTED LOOP JOIN:
   For each row in outer table R:
       Scan index of inner table S for matching keys
   Complexity: O(|R| * log(|S|)) with index. Optimal for small R or indexed lookups.

2. HASH JOIN:
   Build in-memory Hash Table of inner table S on join key: O(|S|)
   Scan outer table R, probing Hash Table: O(|R|)
   Complexity: O(|R| + |S|). Optimal for large unindexed joins fitting in work_mem.

3. MERGE JOIN (Sort-Merge):
   Sort table R on join key: O(|R| log |R|)
   Sort table S on join key: O(|S| log |S|)
   Scan both sorted streams in parallel: O(|R| + |S|)
   Complexity: O(N log N). Optimal when both inputs are pre-sorted by index.
```

---

## 4. Subqueries: Scalar, Table & Correlated

### 4.1 Correlated Subquery Pitfall ($O(N^2)$ Trap)
A subquery is **correlated** when it references columns from the outer query:
```sql
-- DANGEROUS: Executes 1 subquery PER ROW in outer table!
SELECT e.id, e.name, e.salary,
       (SELECT AVG(salary) FROM employees WHERE department_id = e.department_id) AS dept_avg
FROM employees e;
```
If `employees` contains 100,000 rows, the subquery executes 100,000 separate index or table scans ($O(N^2)$).
**Remediation**: Use an Analytic Window Function:
```sql
-- OPTIMAL: Evaluates in O(N log N) via single window scan
SELECT id, name, salary,
       AVG(salary) OVER (PARTITION BY department_id) AS dept_avg
FROM employees;
```

### 4.2 The `NOT IN` with `NULL` Catastrophe
```sql
-- DANGEROUS: If ANY row in inactive_users has a NULL id, this returns ZERO ROWS!
SELECT id, email FROM users
WHERE id NOT IN (SELECT user_id FROM inactive_users);
```
**Mathematical Failure**: In SQL three-valued logic, `id NOT IN (1, 2, NULL)` expands to:
`id != 1 AND id != 2 AND id != NULL`.
Because `id != NULL` evaluates to `UNKNOWN`, the entire boolean conjunction evaluates to `UNKNOWN` (falsy) for every single row!
**Remediation**: Always use `NOT EXISTS` or `IS NOT NULL`:
```sql
SELECT id, email FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM inactive_users iu WHERE iu.user_id = u.id
);
```

---

## 5. Common Table Expressions (CTEs): Non-Recursive & Recursive

### 5.1 Non-Recursive CTEs (`WITH`)
CTEs define temporary, named result sets that exist only within the execution scope of a single query:
```sql
WITH regional_sales AS (
    SELECT region, SUM(amount) AS total_sales
    FROM orders
    GROUP BY region
),
top_regions AS (
    SELECT region
    FROM regional_sales
    WHERE total_sales > (SELECT AVG(total_sales) FROM regional_sales)
)
SELECT o.id, o.customer_id, o.amount, o.region
FROM orders o
JOIN top_regions tr ON o.region = tr.region;
```

#### Optimization Invariant (PostgreSQL 12+):
Historically, PostgreSQL treated all CTEs as optimization fences (`MATERIALIZED`), computing the entire CTE into memory or temporary disk before joining.
Since PostgreSQL 12, CTEs are **inlined by default** (`AS NOT MATERIALIZED`), allowing the planner to push predicates down into the CTE. You can explicitly force materialization if a CTE is referenced multiple times:
```sql
WITH heavy_calculation AS MATERIALIZED (
    SELECT ...
)
```

### 5.2 Recursive CTEs (`WITH RECURSIVE`)
Recursive CTEs are essential for querying **hierarchical, tree, and graph data structures** (organization charts, category taxonomies, bill of materials, network traversal):

```
RECURSIVE CTE EXECUTION ENGINE:
===============================
1. Execute ANCHOR QUERY ────────► Yields Base Result Set R_0
                                           │
                                           ▼
                                 Insert R_0 into Working Table
                                           │
                                           ▼
2. RECURSIVE LOOP: ─────────────► Execute RECURSIVE QUERY joining with Working Table
                                           │
                                  Yields New Intermediate Results R_k
                                           │
                                  ┌────────┴────────┐
                                  ▼                 ▼
                         (If R_k is EMPTY)   (If R_k NOT EMPTY)
                                  │                 │
                             [TERMINATE]     Replace Working Table with R_k
                                             Accumulate into Final Result
                                             Repeat Loop!
```

#### Organizational Tree Traversal Example:
```sql
WITH RECURSIVE employee_hierarchy AS (
    -- 1. Anchor Member: CEO / Top-level managers (no manager)
    SELECT id, name, manager_id, 1 AS depth, name::text AS path
    FROM employees
    WHERE manager_id IS NULL

    UNION ALL

    -- 2. Recursive Member: Direct reports of previous level
    SELECT e.id, e.name, e.manager_id, eh.depth + 1, (eh.path || ' -> ' || e.name)
    FROM employees e
    JOIN employee_hierarchy eh ON e.manager_id = eh.id
)
SELECT id, name, depth, path
FROM employee_hierarchy
ORDER BY depth, id;
```

---

## 6. Analytic Window Functions: The Comprehensive Guide

A **Window Function** performs calculations across a set of table rows that are related to the current row, without collapsing the rows into a single group (unlike `GROUP BY`). Every original row retains its distinct identity while gaining access to aggregated or relative context.

### 6.1 The `OVER` Clause Anatomy
```sql
FUNCTION(...) OVER (
    [PARTITION BY partition_col1, partition_col2]
    [ORDER BY sort_col1 [ASC|DESC], sort_col2]
    [ROWS|RANGE frame_start AND frame_end]
)
```

1. **`PARTITION BY`**: Divides rows into distinct processing groups (partitions). The function evaluates independently within each partition. If omitted, the entire result set is treated as a single partition.
2. **`ORDER BY`**: Determines the sorting sequence of rows inside the partition.
3. **Window Frame**: Specifies the subset of rows within the partition that are visible to the function at the current row evaluation point.

---

## 7. Ranking Functions: `ROW_NUMBER` vs `RANK` vs `DENSE_RANK`

Understanding how ranking functions handle tie values (identical sort keys) is a frequent senior interview test:

```
Sample Dataset: Scores = [100, 90, 90, 80]

Score | ROW_NUMBER() | RANK() | DENSE_RANK()
--------------------------------------------
 100  |      1       |   1    |      1
  90  |      2       |   2    |      2
  90  |      3       |   2    |      2
  80  |      4       |   4    |      3       <-- Notice RANK skipped 3; DENSE_RANK did not!
```

### The Invariants:
- **`ROW_NUMBER()`**: Strictly monotonic ($1, 2, 3, 4, \dots$). Never produces ties. Order among duplicate values is non-deterministic unless tie-breakers are specified in `ORDER BY`.
- **`RANK()`**: Assigns identical rank to ties, but **skips subsequent ranks** according to the number of tied rows ($1, 2, 2, 4$).
- **`DENSE_RANK()`**: Assigns identical rank to ties, but **does not skip subsequent ranks** ($1, 2, 2, 3$). Essential when computing "Top 3 highest salaries" where ties must not displace the 3rd rank.

---

## 8. Value Navigation Functions: `LEAD` and `LAG`

Value functions access neighboring rows within the partition without self-joins.

- **`LAG(column, [offset=1], [default_value=NULL])`**: Looks backward $N$ rows relative to the current row.
- **`LEAD(column, [offset=1], [default_value=NULL])`**: Looks forward $N$ rows relative to the current row.

### Production Pattern: Month-over-Month (MoM) Growth
```sql
WITH monthly_revenue AS (
    SELECT DATE_TRUNC('month', created_at) AS revenue_month,
           SUM(amount) AS revenue
    FROM orders
    GROUP BY revenue_month
)
SELECT revenue_month,
       revenue,
       LAG(revenue, 1) OVER (ORDER BY revenue_month) AS prev_month_revenue,
       ROUND(
           ((revenue - LAG(revenue, 1) OVER (ORDER BY revenue_month)) /
            NULLIF(LAG(revenue, 1) OVER (ORDER BY revenue_month), 0)) * 100, 2
       ) AS mom_growth_percentage
FROM monthly_revenue
ORDER BY revenue_month;
```

---

## 9. Window Frame Specifications: `ROWS` vs `RANGE`

A common source of performance degradation and subtle bugs is failing to understand the difference between `ROWS` and `RANGE`.

```sql
-- DANGEROUS DEFAULT:
SUM(amount) OVER (PARTITION BY account_id ORDER BY created_at)
-- Under the hood, SQL standard defaults to:
-- RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW!
```

### 9.1 The Difference:
- **`ROWS`**: Treats the frame in physical row offsets ($1, 2, 3$). Duplicate values in the `ORDER BY` column do not collapse together.
- **`RANGE`**: Treats the frame logically based on the value of the `ORDER BY` column. If multiple rows share the **exact same timestamp or value**, `RANGE` includes **ALL tied peers up to the last duplicate in the frame**!

```
Row | Date       | Amount | SUM() OVER (ORDER BY Date ROWS) | SUM() OVER (ORDER BY Date RANGE)
-------------------------------------------------------------------------------------------------
 1  | 2026-11-01 |   10   |               10                |               30  <-- Added both rows!
 2  | 2026-11-01 |   20   |               30                |               30  <-- Added both rows!
 3  | 2026-11-02 |   15   |               45                |               45
```

### 9.2 The Golden Performance Rule:
`RANGE` requires sorting and tracking peer groups, incurring high memory and CPU overhead. **Always explicitly declare `ROWS BETWEEN ...` for running totals and moving averages**:
```sql
SUM(amount) OVER (
    PARTITION BY account_id 
    ORDER BY created_at 
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

---

## 10. Moving Window Aggregates (Moving Averages)

```sql
-- 7-Day Moving Average of Daily Active Users (DAU):
SELECT metric_date,
       active_users,
       ROUND(
           AVG(active_users) OVER (
               ORDER BY metric_date 
               ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
           ), 2
       ) AS rolling_7day_avg
FROM daily_metrics
ORDER BY metric_date;
```

---

## 11. Senior SQL Pattern 1: Top-N Per Group

### Problem:
Find the top 3 highest-spending customers for every country.

### Solution via `DENSE_RANK()`:
```sql
WITH ranked_customers AS (
    SELECT country,
           customer_id,
           total_spend,
           DENSE_RANK() OVER (
               PARTITION BY country 
               ORDER BY total_spend DESC
           ) AS rank_in_country
    FROM customer_spends
)
SELECT country, customer_id, total_spend, rank_in_country
FROM ranked_customers
WHERE rank_in_country <= 3
ORDER BY country, rank_in_country;
```

---

## 12. Senior SQL Pattern 2: The Gap-and-Island Problem

### Problem:
Given a table of daily user login dates, calculate the length of **consecutive login streaks** (islands separated by inactive gaps).

### The Mathematical Insight:
If you take a sequence of consecutive dates and subtract a strictly incrementing sequence of integers (`ROW_NUMBER()`), the resulting difference (`date - row_number`) is **identical for all dates within the same continuous streak**!

```
Login Date | ROW_NUMBER() | Date - (ROW_NUMBER * 1 Day) | Island Group
----------------------------------------------------------------------
2026-11-01 |      1       | 2026-10-31                  | Group A
2026-11-02 |      2       | 2026-10-31                  | Group A  <-- Streak of 3 days!
2026-11-03 |      3       | 2026-10-31                  | Group A
2026-11-06 |      4       | 2026-11-02                  | Group B  <-- New Island!
2026-11-07 |      5       | 2026-11-02                  | Group B  <-- Streak of 2 days!
```

### The SQL Solution:
```sql
WITH numbered_logins AS (
    SELECT user_id,
           login_date,
           ROW_NUMBER() OVER (
               PARTITION BY user_id 
               ORDER BY login_date
           ) AS rn
    FROM user_logins
),
islands AS (
    SELECT user_id,
           login_date,
           (login_date - (rn * INTERVAL '1 day')) AS island_group
    FROM numbered_logins
)
SELECT user_id,
       MIN(login_date) AS streak_start,
       MAX(login_date) AS streak_end,
       COUNT(*) AS streak_days
FROM islands
GROUP BY user_id, island_group
ORDER BY user_id, streak_start;
```

---

## 13. Senior SQL Pattern 3: Inactivity Sessionization

### Problem:
Web analytics clickstream events: group a user's events into distinct browsing sessions, where a new session begins if the user is inactive for $> 30$ minutes.

### The SQL Solution:
```sql
WITH time_diffs AS (
    SELECT user_id,
           event_time,
           LAG(event_time) OVER (
               PARTITION BY user_id 
               ORDER BY event_time
           ) AS prev_event_time
    FROM user_events
),
new_session_flags AS (
    SELECT user_id,
           event_time,
           CASE 
               WHEN prev_event_time IS NULL THEN 1
               WHEN EXTRACT(EPOCH FROM (event_time - prev_event_time)) > 1800 THEN 1
               ELSE 0
           END AS is_new_session
    FROM time_diffs
),
sessionized AS (
    SELECT user_id,
           event_time,
           -- Cumulative sum of session boundary flags yields unique session_id!
           SUM(is_new_session) OVER (
               PARTITION BY user_id 
               ORDER BY event_time 
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS session_id
    FROM new_session_flags
)
SELECT user_id, session_id,
       MIN(event_time) AS session_start,
       MAX(event_time) AS session_end,
       COUNT(*) AS total_clicks
FROM sessionized
GROUP BY user_id, session_id;
```

---

## 14. Performance Benchmarks: Window Functions vs Correlated Subqueries

On a dataset of 50,000 ledger transactions:
- **Correlated Subquery** (Running balance): $O(N^2)$ execution scanning 1.25 billion rows $\rightarrow$ **14.8 seconds**.
- **Analytic Window Function** (`SUM() OVER (ORDER BY date ROWS...)`): $O(N \log N)$ execution scanning exactly 50,000 rows once $\rightarrow$ **0.024 seconds** (**616x speedup**).

---

## 15. Master Production Checklist for Senior SQL

| Requirement | Failure Risk | Production Standard |
|:---|:---|:---|
| **Join Indexing** | Foreign keys without indexes cause nested loop deaths | Always index foreign key columns used in `ON` clauses |
| **`NOT IN` Trap** | Null values in subquery cause entire query to return 0 rows | Always use `NOT EXISTS` instead of `NOT IN (SELECT...)` |
| **Window Frames** | Unintended `RANGE` defaults cause peer row collapse & slowdowns | Explicitly declare `ROWS BETWEEN ...` on all running aggregates |
| **Recursive CTEs** | Cycles in parent-child graphs cause infinite recursion loops | Implement cycle detection (`ARRAY[id]` path or `CYCLE` clause) |
| **Tie-Breakers** | `ROW_NUMBER()` without deterministic tie-breaker flips between queries | Always append primary key `id` as final tie-breaker in `ORDER BY` |
| **Materialization** | Large CTEs referenced multiple times re-execute without cache | Use `AS MATERIALIZED` in PostgreSQL 12+ for heavy multi-use CTEs |
