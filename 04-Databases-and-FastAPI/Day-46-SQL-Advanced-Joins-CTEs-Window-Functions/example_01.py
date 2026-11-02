"""
Day 46: SQL Mastery — Advanced Joins, CTEs & Analytic Window Functions
======================================================================

Core Concepts Demonstrated:
1. Advanced Relational Joins:
   - INNER, LEFT OUTER, FULL OUTER, ANTI-JOIN, and SEMI-JOIN implementations.
2. Common Table Expressions (CTEs):
   - Non-recursive modular query pipelining.
   - Recursive CTE execution engine (Anchor member + Working table loop + Cycle detection).
3. Analytic Window Functions:
   - Ranking: ROW_NUMBER(), RANK(), DENSE_RANK() with tie-handling mechanics.
   - Value Navigation: LAG() and LEAD() with offset and default fallback.
   - Window Frames: ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW (Running Totals).
   - Moving Window Averages: ROWS BETWEEN N PRECEDING AND CURRENT ROW.
4. Classical SQL Patterns Solved:
   - Top-N per group using DENSE_RANK().
   - Month-over-Month (MoM) revenue growth using LAG().
   - Recursive management tree hierarchy traversal.
   - The Gap-and-Island problem: calculating consecutive login streaks.

Zero External Dependencies: Pure Python Standard Library.
"""

from __future__ import annotations

import collections
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple


# ============================================================================
# SECTION 1: IN-MEMORY RELATIONAL ENGINE & JOIN PROCESSOR
# ============================================================================

class RelationalDataset:
    """Represents an in-memory SQL table/relation."""
    def __init__(self, name: str, rows: List[Dict[str, Any]]) -> None:
        self.name = name
        self.rows = rows

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


class JoinEngine:
    """
    Simulates database join algorithms (Hash Join & Nested Loop).
    """
    @staticmethod
    def inner_join(
        left: List[Dict[str, Any]],
        right: List[Dict[str, Any]],
        left_key: str,
        right_key: str,
        prefix_right: str = ""
    ) -> List[Dict[str, Any]]:
        # Hash Join implementation: O(|Right|) + O(|Left|)
        right_lookup: Dict[Any, List[Dict[str, Any]]] = collections.defaultdict(list)
        for r in right:
            k = r.get(right_key)
            if k is not None:
                right_lookup[k].append(r)

        joined: List[Dict[str, Any]] = []
        for l in left:
            k = l.get(left_key)
            if k in right_lookup:
                for r in right_lookup[k]:
                    combined = dict(l)
                    for rk, rv in r.items():
                        combined[f"{prefix_right}{rk}"] = rv
                    joined.append(combined)
        return joined

    @staticmethod
    def left_outer_join(
        left: List[Dict[str, Any]],
        right: List[Dict[str, Any]],
        left_key: str,
        right_key: str,
        right_fields: List[str],
        prefix_right: str = ""
    ) -> List[Dict[str, Any]]:
        right_lookup: Dict[Any, List[Dict[str, Any]]] = collections.defaultdict(list)
        for r in right:
            k = r.get(right_key)
            if k is not None:
                right_lookup[k].append(r)

        joined: List[Dict[str, Any]] = []
        for l in left:
            k = l.get(left_key)
            if k in right_lookup:
                for r in right_lookup[k]:
                    combined = dict(l)
                    for rk, rv in r.items():
                        combined[f"{prefix_right}{rk}"] = rv
                    joined.append(combined)
            else:
                combined = dict(l)
                for rf in right_fields:
                    combined[f"{prefix_right}{rf}"] = None
                joined.append(combined)
        return joined

    @staticmethod
    def anti_join(
        left: List[Dict[str, Any]],
        right: List[Dict[str, Any]],
        left_key: str,
        right_key: str
    ) -> List[Dict[str, Any]]:
        """
        Simulates: SELECT * FROM left LEFT JOIN right ON ... WHERE right.id IS NULL
        Returns left rows that have ZERO matches in right relation.
        """
        right_keys = {r[right_key] for r in right if r.get(right_key) is not None}
        return [l for l in left if l.get(left_key) not in right_keys]

    @staticmethod
    def semi_join(
        left: List[Dict[str, Any]],
        right: List[Dict[str, Any]],
        left_key: str,
        right_key: str
    ) -> List[Dict[str, Any]]:
        """
        Simulates: SELECT * FROM left WHERE EXISTS (SELECT 1 FROM right WHERE ...)
        Returns left rows that have AT LEAST ONE match in right relation without row multiplication.
        """
        right_keys = {r[right_key] for r in right if r.get(right_key) is not None}
        return [l for l in left if l.get(left_key) in right_keys]


# ============================================================================
# SECTION 2: RECURSIVE COMMON TABLE EXPRESSIONS (CTE) ENGINE
# ============================================================================

class RecursiveCTE:
    """
    Executes recursive SQL graph/tree evaluation:
    WITH RECURSIVE cte AS (
        [Anchor Query]
        UNION ALL
        [Recursive Query]
    )
    """
    @staticmethod
    def execute_hierarchy(
        records: List[Dict[str, Any]],
        parent_col: str,
        id_col: str,
        name_col: str
    ) -> List[Dict[str, Any]]:
        """
        Traverses hierarchical employee-manager tree, computing:
        - depth (root = 1)
        - breadcrumb path string ('CEO -> VP -> Director')
        """
        results: List[Dict[str, Any]] = []

        # 1. Anchor Member: roots where parent is None
        working_table = []
        for r in records:
            if r.get(parent_col) is None:
                item = dict(r)
                item["depth"] = 1
                item["path"] = str(r[name_col])
                results.append(item)
                working_table.append(item)

        # 2. Recursive Loop: join records with working_table
        depth_limit = 20  # Cycle safeguard
        current_depth = 1

        while working_table and current_depth < depth_limit:
            next_working_table = []
            working_ids = {w[id_col]: w for w in working_table}

            for r in records:
                parent_id = r.get(parent_col)
                if parent_id in working_ids:
                    parent_node = working_ids[parent_id]
                    child = dict(r)
                    child["depth"] = parent_node["depth"] + 1
                    child["path"] = f"{parent_node['path']} -> {r[name_col]}"
                    results.append(child)
                    next_working_table.append(child)

            working_table = next_working_table
            current_depth += 1

        return results


# ============================================================================
# SECTION 3: ANALYTIC WINDOW FUNCTION ENGINE
# ============================================================================

class WindowEngine:
    """
    Simulates SQL Window Function processing:
    OVER (PARTITION BY ... ORDER BY ... [ROWS BETWEEN ...])
    """
    @staticmethod
    def partition_and_sort(
        rows: List[Dict[str, Any]],
        partition_by: Optional[List[str]] = None,
        order_by: Optional[List[Tuple[str, bool]]] = None
    ) -> Dict[Tuple[Any, ...], List[Dict[str, Any]]]:
        """
        Groups rows into partitions, sorting rows within each partition.
        order_by is list of (col_name, is_descending_bool).
        """
        partitions: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = collections.defaultdict(list)

        for r in rows:
            if partition_by:
                part_key = tuple(r.get(c) for c in partition_by)
            else:
                part_key = ()
            partitions[part_key].append(dict(r))

        if order_by:
            for p_key in partitions:
                for col, is_desc in reversed(order_by):
                    partitions[p_key].sort(key=lambda x: (x.get(col) is None, x.get(col)), reverse=is_desc)

        return partitions

    @classmethod
    def apply_ranking(
        cls,
        rows: List[Dict[str, Any]],
        partition_by: List[str],
        sort_col: str,
        descending: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Computes ROW_NUMBER(), RANK(), and DENSE_RANK() in a single pass.
        """
        partitions = cls.partition_and_sort(rows, partition_by, [(sort_col, descending)])
        output: List[Dict[str, Any]] = []

        for p_key, p_rows in partitions.items():
            row_num = 1
            current_rank = 1
            current_dense_rank = 1
            prev_val = None

            for idx, r in enumerate(p_rows):
                val = r.get(sort_col)
                if idx > 0:
                    if val != prev_val:
                        current_rank = row_num
                        current_dense_rank += 1

                r["row_number"] = row_num
                r["rank"] = current_rank
                r["dense_rank"] = current_dense_rank

                prev_val = val
                row_num += 1
                output.append(r)

        return output

    @classmethod
    def apply_lag(
        cls,
        rows: List[Dict[str, Any]],
        partition_by: List[str],
        order_col: str,
        target_col: str,
        offset: int = 1,
        default: Any = None
    ) -> List[Dict[str, Any]]:
        """
        Computes LAG(target_col, offset, default) OVER (PARTITION BY ... ORDER BY ...).
        """
        partitions = cls.partition_and_sort(rows, partition_by, [(order_col, False)])
        output: List[Dict[str, Any]] = []

        for p_key, p_rows in partitions.items():
            for idx, r in enumerate(p_rows):
                lag_idx = idx - offset
                if lag_idx >= 0:
                    r[f"prev_{target_col}"] = p_rows[lag_idx].get(target_col)
                else:
                    r[f"prev_{target_col}"] = default
                output.append(r)

        return output

    @classmethod
    def apply_running_sum(
        cls,
        rows: List[Dict[str, Any]],
        partition_by: List[str],
        order_col: str,
        value_col: str
    ) -> List[Dict[str, Any]]:
        """
        Computes:
        SUM(value_col) OVER (PARTITION BY ... ORDER BY ... ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        """
        partitions = cls.partition_and_sort(rows, partition_by, [(order_col, False)])
        output: List[Dict[str, Any]] = []

        for p_key, p_rows in partitions.items():
            accumulated = Decimal("0.00")
            for r in p_rows:
                val = Decimal(str(r.get(value_col, "0")))
                accumulated += val
                r["running_total"] = accumulated
                output.append(r)

        return output

    @classmethod
    def apply_moving_average(
        cls,
        rows: List[Dict[str, Any]],
        order_col: str,
        value_col: str,
        window_size: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Computes:
        AVG(value_col) OVER (ORDER BY ... ROWS BETWEEN (window_size - 1) PRECEDING AND CURRENT ROW)
        """
        partitions = cls.partition_and_sort(rows, partition_by=None, order_by=[(order_col, False)])
        sorted_rows = partitions[()]
        output: List[Dict[str, Any]] = []

        for idx, r in enumerate(sorted_rows):
            start = max(0, idx - window_size + 1)
            window_slice = sorted_rows[start : idx + 1]
            total = sum(Decimal(str(x.get(value_col, "0"))) for x in window_slice)
            avg = total / Decimal(str(len(window_slice)))
            r["moving_avg"] = round(avg, 2)
            output.append(r)

        return output


# ============================================================================
# SECTION 4: GAP-AND-ISLAND SOLVER
# ============================================================================

def calculate_login_streaks(user_logins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Solves the Classical Gap-and-Island Problem:
    Given login dates, group contiguous dates into consecutive streaks.
    
    Mathematical Invariant:
    login_date - ROW_NUMBER() * 1 day produces identical island_key for contiguous dates!
    """
    # 1. Sort by user and date
    sorted_logins = sorted(user_logins, key=lambda x: (x["user_id"], x["login_date"]))

    # 2. Number logins per user
    islands: Dict[Tuple[int, date], List[date]] = collections.defaultdict(list)
    user_rn: Dict[int, int] = collections.defaultdict(int)

    for r in sorted_logins:
        u_id = r["user_id"]
        d = r["login_date"]
        user_rn[u_id] += 1
        rn = user_rn[u_id]

        # Calculate island group anchor
        island_group_date = d - timedelta(days=rn)
        islands[(u_id, island_group_date)].append(d)

    # 3. Aggregate streaks
    streaks: List[Dict[str, Any]] = []
    for (u_id, _), dates in islands.items():
        streaks.append({
            "user_id": u_id,
            "streak_start": min(dates),
            "streak_end": max(dates),
            "streak_length": len(dates)
        })

    streaks.sort(key=lambda x: (x["user_id"], x["streak_start"]))
    return streaks


# ============================================================================
# SECTION 5: VERIFICATION TEST HARNESS
# ============================================================================

def run_tests() -> None:
    print("=" * 70)
    print(" Day 46: Advanced SQL Joins, CTEs & Analytic Window Functions Tests")
    print("=" * 70)

    # ------------------------------------------------------------------------
    # [TEST 1] Advanced Joins (Anti-Join and Semi-Join)
    # ------------------------------------------------------------------------
    print("\n[TEST 1] Advanced Relational Joins (Anti-Join & Semi-Join)...")
    users = [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
        {"id": 3, "name": "Charlie"},
        {"id": 4, "name": "Diana"},
    ]
    orders = [
        {"id": 101, "user_id": 1, "amount": 100},
        {"id": 102, "user_id": 1, "amount": 250},
        {"id": 103, "user_id": 3, "amount": 50},
    ]

    # Anti-join: users who have NEVER placed an order
    anti = JoinEngine.anti_join(users, orders, left_key="id", right_key="user_id")
    assert len(anti) == 2
    anti_names = {u["name"] for u in anti}
    assert anti_names == {"Bob", "Diana"}
    print(f"  -> Anti-Join verified: Customers with 0 orders = {anti_names}")

    # Semi-join: users who HAVE placed at least one order (no duplication)
    semi = JoinEngine.semi_join(users, orders, left_key="id", right_key="user_id")
    assert len(semi) == 2
    semi_names = {u["name"] for u in semi}
    assert semi_names == {"Alice", "Charlie"}
    print(f"  -> Semi-Join verified: Customers with >=1 order = {semi_names}")

    # ------------------------------------------------------------------------
    # [TEST 2] Ranking Window Functions: ROW_NUMBER vs RANK vs DENSE_RANK
    # ------------------------------------------------------------------------
    print("\n[TEST 2] Ranking Window Functions & Top-N Per Group...")
    employee_salaries = [
        {"dept": "Engineering", "emp": "Alice", "salary": 150000},
        {"dept": "Engineering", "emp": "Bob", "salary": 130000},
        {"dept": "Engineering", "emp": "Charlie", "salary": 130000},  # Tie with Bob
        {"dept": "Engineering", "emp": "David", "salary": 110000},
        {"dept": "Sales", "emp": "Eve", "salary": 140000},
        {"dept": "Sales", "emp": "Frank", "salary": 120000},
    ]

    ranked = WindowEngine.apply_ranking(
        employee_salaries,
        partition_by=["dept"],
        sort_col="salary",
        descending=True
    )

    eng_ranks = [r for r in ranked if r["dept"] == "Engineering"]
    charlie_row = next(r for r in eng_ranks if r["emp"] == "Charlie")
    david_row = next(r for r in eng_ranks if r["emp"] == "David")

    # Bob and Charlie tie at 130k
    assert charlie_row["rank"] == 2
    assert charlie_row["dense_rank"] == 2
    # David follows tie: RANK skips to 4, DENSE_RANK is 3!
    assert david_row["rank"] == 4, f"Expected RANK 4, got {david_row['rank']}"
    assert david_row["dense_rank"] == 3, f"Expected DENSE_RANK 3, got {david_row['dense_rank']}"
    print(f"  -> Tie Handling Verified: David has RANK={david_row['rank']} and DENSE_RANK={david_row['dense_rank']}.")

    # Top-2 earners using DENSE_RANK <= 2
    top_2 = [r for r in ranked if r["dense_rank"] <= 2 and r["dept"] == "Engineering"]
    assert len(top_2) == 3  # Alice (1), Bob (2), Charlie (2)
    print(f"  -> Top-2 Earner Filter Verified: {[(r['emp'], r['salary']) for r in top_2]}")

    # ------------------------------------------------------------------------
    # [TEST 3] Month-over-Month (MoM) Growth via LAG()
    # ------------------------------------------------------------------------
    print("\n[TEST 3] Month-over-Month Growth Calculation via LAG()...")
    monthly_sales = [
        {"store": "Main", "month": "2026-01", "sales": Decimal("10000.00")},
        {"store": "Main", "month": "2026-02", "sales": Decimal("12500.00")},
        {"store": "Main", "month": "2026-03", "sales": Decimal("15000.00")},
    ]

    lagged = WindowEngine.apply_lag(
        monthly_sales,
        partition_by=["store"],
        order_col="month",
        target_col="sales",
        offset=1,
        default=None
    )

    m1 = lagged[0]
    m2 = lagged[1]
    assert m1["prev_sales"] is None
    assert m2["prev_sales"] == Decimal("10000.00")
    growth_feb = ((m2["sales"] - m2["prev_sales"]) / m2["prev_sales"]) * 100
    assert growth_feb == Decimal("25.0")
    print(f"  -> February Sales Growth: +{growth_feb}% over January (LAG verified).")

    # ------------------------------------------------------------------------
    # [TEST 4] Recursive CTE: Organizational Tree Traversal
    # ------------------------------------------------------------------------
    print("\n[TEST 4] Recursive CTE: Hierarchical Organization Chart...")
    org_chart = [
        {"id": 1, "name": "CEO", "manager_id": None},
        {"id": 2, "name": "CTO", "manager_id": 1},
        {"id": 3, "name": "VP Product", "manager_id": 1},
        {"id": 4, "name": "Principal Architect", "manager_id": 2},
        {"id": 5, "name": "Senior Staff Engineer", "manager_id": 4},
    ]

    hierarchy = RecursiveCTE.execute_hierarchy(
        org_chart,
        parent_col="manager_id",
        id_col="id",
        name_col="name"
    )

    staff_node = next(h for h in hierarchy if h["id"] == 5)
    assert staff_node["depth"] == 4
    assert staff_node["path"] == "CEO -> CTO -> Principal Architect -> Senior Staff Engineer"
    print(f"  -> Recursive Depth: {staff_node['depth']}")
    print(f"  -> Traversal Path: {staff_node['path']}")
    print("  -> Recursive CTE Traversal PASSED.")

    # ------------------------------------------------------------------------
    # [TEST 5] Gap-and-Island: Consecutive Login Streaks
    # ------------------------------------------------------------------------
    print("\n[TEST 5] Gap-and-Island Problem: Active Login Streak Detection...")
    user_logins = [
        # User 101: Streak 1 (Nov 1, 2, 3) = 3 days; Streak 2 (Nov 6, 7) = 2 days
        {"user_id": 101, "login_date": date(2026, 11, 1)},
        {"user_id": 101, "login_date": date(2026, 11, 2)},
        {"user_id": 101, "login_date": date(2026, 11, 3)},
        {"user_id": 101, "login_date": date(2026, 11, 6)},
        {"user_id": 101, "login_date": date(2026, 11, 7)},
    ]

    streaks = calculate_login_streaks(user_logins)
    assert len(streaks) == 2
    assert streaks[0]["streak_length"] == 3
    assert streaks[0]["streak_start"] == date(2026, 11, 1)
    assert streaks[0]["streak_end"] == date(2026, 11, 3)

    assert streaks[1]["streak_length"] == 2
    assert streaks[1]["streak_start"] == date(2026, 11, 6)
    assert streaks[1]["streak_end"] == date(2026, 11, 7)
    print(f"  -> User 101 Streak 1: {streaks[0]['streak_length']} days ({streaks[0]['streak_start']} to {streaks[0]['streak_end']})")
    print(f"  -> User 101 Streak 2: {streaks[1]['streak_length']} days ({streaks[1]['streak_start']} to {streaks[1]['streak_end']})")
    print("  -> Gap-and-Island Mathematical Algorithm PASSED.")

    print("\nAll Day 46 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
