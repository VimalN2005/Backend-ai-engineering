# Day 46: Common Antipatterns in Advanced SQL, CTEs & Window Functions

This guide examines 7 critical production antipatterns encountered when writing advanced SQL queries, Recursive CTEs, and Analytic Window Functions in backend systems. Each entry breaks down the underlying relational engine failure mechanism, illustrates vulnerable SQL, and provides a production-grade remediation.

---

## 1. The `NOT IN` with `NULL` Trap (Silent Empty Result Set)

### Failure Mechanism
In SQL three-valued logic, boolean expressions evaluate to `TRUE`, `FALSE`, or `UNKNOWN`.
When executing `WHERE id NOT IN (SELECT foreign_id FROM table)`:
- If the subquery contains even **a single `NULL` value**, the expression evaluates to:
  `id != val1 AND id != val2 AND id != NULL`.
- In ANSI SQL, any comparison with `NULL` (including `!=`) evaluates to `UNKNOWN`.
- An `AND` expression containing `UNKNOWN` can never evaluate to `TRUE`.
- **Result**: The entire query silently returns **zero rows**, even if millions of rows satisfy the business condition.

### Vulnerable Code
```sql
-- DANGEROUS: If any inactive_account has a NULL user_id, this returns 0 rows!
SELECT id, email, created_at
FROM users
WHERE id NOT IN (
    SELECT user_id FROM inactive_accounts
);
```

### Production-Grade Remediation
Never use `NOT IN` with subqueries on nullable columns. Use `NOT EXISTS` (which operates on row existence rather than value equality) or explicitly filter out `NULL`:

```sql
-- SECURE & PREDICTABLE: Immune to NULL values
SELECT u.id, u.email, u.created_at
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM inactive_accounts ia 
    WHERE ia.user_id = u.id
);
```

---

## 2. Using Default `RANGE BETWEEN` Instead of `ROWS BETWEEN` in Running Totals

### Failure Mechanism
When defining a window function with `ORDER BY`, ANSI SQL defaults the window frame to:
`RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`
Crucially, `RANGE` groups rows that have **identical values** in the `ORDER BY` column into peer groups.
- If multiple transactions share the exact same timestamp or date, `RANGE` sums all duplicate peers together into every row of the group, rather than accumulating row by row.
- Furthermore, `RANGE` forces the database engine to track and compare duplicate value peer boundaries, incurring significant memory and CPU overhead.

### Vulnerable Code
```sql
-- BUG: If multiple entries share the exact same created_at, running_balance 
-- jumps to the total of all peers on every peer row!
SELECT id, account_id, amount, created_at,
       SUM(amount) OVER (
           PARTITION BY account_id 
           ORDER BY created_at
       ) AS running_balance
FROM ledger_entries;
```

### Production-Grade Remediation
Always explicitly declare `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`. `ROWS` operates on physical row offsets, guaranteeing deterministic row-by-row accumulation:

```sql
SELECT id, account_id, amount, created_at,
       SUM(amount) OVER (
           PARTITION BY account_id 
           ORDER BY created_at, id
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ) AS running_balance
FROM ledger_entries;
```

---

## 3. Correlated Subqueries Inside `SELECT` and `WHERE` ($O(N^2)$ Quadratic Bottlenecks)

### Failure Mechanism
A correlated subquery references values from the outer query. The database engine must re-evaluate the subquery **for every single outer row**.
- In a table with 100,000 rows, a correlated subquery executes 100,000 separate index or table scans, requiring up to 10 billion row evaluations.
- This drives database CPU to 100% and locks memory buffers.

### Vulnerable Code
```sql
-- DISASTER: 1 subquery execution per employee row!
SELECT e.id, e.name, e.department_id, e.salary,
       (SELECT AVG(salary) 
        FROM employees 
        WHERE department_id = e.department_id) AS dept_avg_salary
FROM employees e;
```

### Production-Grade Remediation
Replace the correlated subquery with an Analytic Window Function (`AVG() OVER (PARTITION BY ...)`). Window functions evaluate in a single $O(N \log N)$ or $O(N)$ pass:

```sql
-- HIGH PERFORMANCE: Evaluates in a single scan
SELECT id, name, department_id, salary,
       AVG(salary) OVER (PARTITION BY department_id) AS dept_avg_salary
FROM employees;
```

---

## 4. Infinite Recursion Loops in Recursive CTEs (Cycle Failure)

### Failure Mechanism
Recursive CTEs (`WITH RECURSIVE`) traverse graphs and hierarchical trees. If the data contains an unintended circular reference (e.g. Employee A reports to Employee B, and Employee B reports to Employee A), the recursive member loop **never terminates**.
The query runs indefinitely until the database crashes from Out-Of-Memory (OOM) or exceeds the max recursion depth limit.

### Vulnerable Code
```sql
WITH RECURSIVE org_tree AS (
    SELECT id, name, manager_id
    FROM employees
    WHERE manager_id IS NULL
    
    UNION ALL
    
    SELECT e.id, e.name, e.manager_id
    FROM employees e
    JOIN org_tree ot ON e.manager_id = ot.id  -- BUG: Infinite loop if graph has a cycle!
)
SELECT * FROM org_tree;
```

### Production-Grade Remediation
Implement cycle detection by tracking visited node IDs in an array, or enforce a maximum recursion depth:

```sql
WITH RECURSIVE org_tree AS (
    -- Anchor member
    SELECT id, name, manager_id, 1 AS depth, ARRAY[id] AS visited_path
    FROM employees
    WHERE manager_id IS NULL

    UNION ALL

    -- Recursive member with cycle defense
    SELECT e.id, e.name, e.manager_id, ot.depth + 1, ot.visited_path || e.id
    FROM employees e
    JOIN org_tree ot ON e.manager_id = ot.id
    WHERE NOT (e.id = ANY(ot.visited_path)) -- HALT if node already visited!
      AND ot.depth < 25                     -- Hard safety depth cap
)
SELECT id, name, depth, visited_path
FROM org_tree;
```

---

## 5. Referencing `SELECT` Column Aliases in `WHERE` or `GROUP BY`

### Failure Mechanism
The SQL execution engine processes queries in a strict logical order:
`FROM` $\rightarrow$ `WHERE` $\rightarrow$ `GROUP BY` $\rightarrow$ `HAVING` $\rightarrow$ `SELECT` $\rightarrow$ `ORDER BY`.
Because `WHERE` and `GROUP BY` execute **before** `SELECT`, column aliases defined in `SELECT` do not exist when `WHERE` and `GROUP BY` are evaluated, resulting in a syntax error (`column does not exist`).

### Vulnerable Code
```sql
-- SYNTAX ERROR in ANSI SQL:
SELECT id, total_amount, (total_amount * tax_rate) AS calculated_tax
FROM orders
WHERE calculated_tax > 100  -- ERROR: column "calculated_tax" does not exist!
GROUP BY calculated_tax;
```

### Production-Grade Remediation
Either repeat the scalar expression in the `WHERE` clause, or define the alias upstream inside a Common Table Expression (CTE):

```sql
-- CLEAN & MODULAR CTE:
WITH calculated_orders AS (
    SELECT id, total_amount, (total_amount * tax_rate) AS calculated_tax
    FROM orders
)
SELECT id, total_amount, calculated_tax
FROM calculated_orders
WHERE calculated_tax > 100;
```

---

## 6. Filtering on Window Functions Directly in `WHERE`

### Failure Mechanism
Window functions evaluate at Step 7 of the execution pipeline (after `WHERE`, `GROUP BY`, and `HAVING`).
Attempting to filter on a window function directly in `WHERE` (e.g. `WHERE ROW_NUMBER() <= 3`) is prohibited by ANSI SQL because it creates an unresolvable circular dependency: you cannot filter the rows before defining the window partition!

### Vulnerable Code
```sql
-- SYNTAX ERROR:
SELECT id, department_id, salary
FROM employees
WHERE ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY salary DESC) <= 3;
-- ERROR: window functions are not allowed in WHERE
```

### Production-Grade Remediation
Compute the window function inside a CTE or subquery, then filter on the resulting rank column in the outer `WHERE` clause:

```sql
WITH ranked_employees AS (
    SELECT id, department_id, salary,
           DENSE_RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) AS salary_rank
    FROM employees
)
SELECT id, department_id, salary, salary_rank
FROM ranked_employees
WHERE salary_rank <= 3;
```

---

## 7. Unindexed Foreign Keys in Multi-Table Joins (Hash Join Spill to Disk)

### Failure Mechanism
When joining large tables (e.g. `orders` with 10M rows and `users` with 1M rows) on `orders.user_id = users.id`:
- Django ORM automatically indexes Primary Keys, but **does NOT automatically index raw foreign key columns or join targets unless `db_index=True` or `ForeignKey` is declared**.
- Without an index on `user_id`, PostgreSQL cannot use an index-based Nested Loop join.
- It falls back to a **Hash Join**. If the build table exceeds `work_mem` (default: 4 MB in PostgreSQL), the hash table **spills to disk** (batch files in `pgsql_tmp`), degrading query speed by 10x–50x and thrashing disk I/O.

### Vulnerable Code
```sql
-- If orders.user_id lacks a B-Tree index, joining 10,000,000 orders causes full table scan
SELECT u.email, o.id, o.amount
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE u.created_at >= '2026-01-01';
```

### Production-Grade Remediation
1. Verify that all foreign key and join predicate columns are indexed with B-Tree indexes:
   ```sql
   CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders(user_id);
   ```
2. Allocate sufficient `work_mem` for analytical queries:
   ```sql
   SET work_mem = '64MB';
   ```
3. Use `EXPLAIN (ANALYZE, BUFFERS)` to confirm joins execute in-memory without spilling to disk (`Batches: 1, Memory Usage: ...`).
