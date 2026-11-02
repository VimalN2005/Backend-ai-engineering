# Day 46: 5-Minute Rapid Revision — Advanced SQL, CTEs & Window Functions

---

## 1. Logical SQL Execution Order Sequence

```
1. FROM & JOIN     ──► Cartesian product & join conditions evaluated
2. WHERE           ──► Individual row filters applied BEFORE grouping
3. GROUP BY        ──► Rows collapsed into summary buckets
4. HAVING          ──► Group summary filters applied
5. SELECT          ──► Column expressions calculated & aliases created
6. DISTINCT        ──► Duplicate projected rows removed
7. WINDOW          ──► Analytic functions (OVER) calculated
8. ORDER BY        ──► Output sorted (Can use SELECT aliases!)
9. LIMIT / OFFSET  ──► Row pagination slicing
```

> [!IMPORTANT]
> `WHERE` cannot reference aliases defined in `SELECT` because `WHERE` runs at Step 2 and `SELECT` runs at Step 5!

---

## 2. Window Functions Quick Reference

### The `OVER` Clause Blueprint:
```sql
FUNCTION(...) OVER (
    [PARTITION BY col1, col2]
    [ORDER BY sort_col [ASC|DESC]]
    [ROWS BETWEEN frame_start AND frame_end]
)
```

### Ranking Matrix:
| Function | On Ties | Next Rank After 2 Ties | Use Case |
|:---|:---|:---:|:---|
| **`ROW_NUMBER()`** | No ties ($1, 2, 3, 4$) | $3$ | Deduplication, Keyset pagination |
| **`RANK()`** | Identical ($1, 2, 2, 4$) | $4$ (Skips) | Sports/Olympics leaderboards |
| **`DENSE_RANK()`** | Identical ($1, 2, 2, 3$) | $3$ (No skip) | Top-N salaries per department |

### Value Navigation:
- **`LAG(col, 1, default)`**: Accesses previous row (e.g. Month-over-Month growth).
- **`LEAD(col, 1, default)`**: Accesses subsequent row.

---

## 3. Window Frames: `ROWS` vs `RANGE`

| Metric | `ROWS` | `RANGE` (Dangerous Default!) |
|:---|:---|:---|
| **Measurement** | Physical row count ($1, 2, 3$) | Logical value comparison |
| **Duplicate Ties** | Accumulates row by row | **Collapses all tied peers into same sum!** |
| **Performance** | **$O(N)$ single-pass accumulator** | **Slow**: must collate and buffer peer groups |
| **Production Rule** | **Always declare `ROWS BETWEEN ...`** | Avoid on unindexed or non-unique columns |

### Safe Running Total:
```sql
SUM(amount) OVER (
    PARTITION BY account_id 
    ORDER BY created_at, id
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

---

## 4. Recursive CTE Template with Cycle Defense

```sql
WITH RECURSIVE org_hierarchy AS (
    -- 1. Anchor Member (Root nodes)
    SELECT id, name, manager_id, 1 AS depth, ARRAY[id] AS visited_nodes
    FROM employees
    WHERE manager_id IS NULL

    UNION ALL

    -- 2. Recursive Member (Child joins)
    SELECT e.id, e.name, e.manager_id, oh.depth + 1, oh.visited_nodes || e.id
    FROM employees e
    JOIN org_hierarchy oh ON e.manager_id = oh.id
    WHERE NOT (e.id = ANY(oh.visited_nodes)) -- Cycle Defense
      AND oh.depth < 20                     -- Depth Defense
)
SELECT id, name, depth, visited_nodes
FROM org_hierarchy
ORDER BY depth, id;
```

---

## 5. Ten-Second Interview Defense Checklist

1. *Why does `NOT IN (SELECT ...)` return 0 rows?* -> If the subquery contains a single `NULL`, three-valued logic turns the entire condition into `UNKNOWN`. Fix: Use `NOT EXISTS`.
2. *Why is `RANGE BETWEEN` dangerous in window functions?* -> Combines all rows sharing the same timestamp into a single aggregate, corrupting running totals. Fix: Use `ROWS BETWEEN`.
3. *Why can't you filter window functions in `WHERE`?* -> Window functions execute at Step 7, after `WHERE` (Step 2). Fix: Wrap in a CTE or subquery.
4. *How do you solve the Gap-and-Island problem?* -> Group consecutive dates using `date - (ROW_NUMBER() * INTERVAL '1 day')`.
5. *What are the three join algorithms in PostgreSQL?* -> Nested Loop (small/indexed outer table), Hash Join (large unindexed in-memory join), and Merge Join (both tables pre-sorted).
