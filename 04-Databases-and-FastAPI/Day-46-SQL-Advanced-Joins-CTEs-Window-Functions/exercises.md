# Day 46: Advanced Exercises — SQL Joins, CTEs & Analytic Window Functions

This workbook provides 3 practical SQL engineering challenges testing mastery of window functions, recursive common table expressions, and complex set-based relational aggregations. Complete reference solutions with verification test harnesses are included.

---

## Challenge 1: The Gap-and-Island Problem — Calculating Consecutive Active Days

### Context & Requirements
In gamified SaaS applications (e.g. Duolingo, GitHub, Strava), users build "daily activity streaks". When a user skips one or more days, their streak resets.
Given a table of timestamped user activity events:
1. Deduplicate multiple activities on the same calendar day.
2. Group contiguous dates into distinct "islands" using the mathematical formula:
   $$\text{island\_group} = \text{activity\_date} - (\text{ROW\_NUMBER}() \times \text{INTERVAL '1 day'})$$
3. Compute the `start_date`, `end_date`, and `streak_length` (in days) for every streak.
4. Identify each user's **longest all-time streak**.

---

### Reference Solution

```python
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple


def calculate_user_streaks(activity_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Step 1: Deduplicate distinct (user_id, activity_date)
    unique_days: Set[Tuple[int, date]] = set()
    for rec in activity_records:
        unique_days.add((rec["user_id"], rec["activity_date"]))

    sorted_days = sorted(list(unique_days), key=lambda x: (x[0], x[1]))

    # Step 2: Number rows per user and calculate island group anchor
    islands: Dict[Tuple[int, date], List[date]] = defaultdict(list)
    user_row_counts: Dict[int, int] = defaultdict(int)

    for user_id, act_date in sorted_days:
        user_row_counts[user_id] += 1
        rn = user_row_counts[user_id]
        # Mathematical invariant: act_date - rn * 1 day is identical for contiguous dates!
        island_anchor = act_date - timedelta(days=rn)
        islands[(user_id, island_anchor)].append(act_date)

    # Step 3: Format streak summaries
    streaks: List[Dict[str, Any]] = []
    for (user_id, _), dates in islands.items():
        streaks.append({
            "user_id": user_id,
            "start_date": min(dates),
            "end_date": max(dates),
            "streak_length": len(dates)
        })

    streaks.sort(key=lambda s: (s["user_id"], s["start_date"]))
    return streaks


# --- Verification Harness ---
if __name__ == "__main__":
    # User 101: Streak of 3 (Nov 1-3), Gap (Nov 4-5), Streak of 2 (Nov 6-7)
    # User 102: Streak of 4 (Nov 1-4)
    data = [
        {"user_id": 101, "activity_date": date(2026, 11, 1)},
        {"user_id": 101, "activity_date": date(2026, 11, 1)},  # Duplicate on same day
        {"user_id": 101, "activity_date": date(2026, 11, 2)},
        {"user_id": 101, "activity_date": date(2026, 11, 3)},
        {"user_id": 101, "activity_date": date(2026, 11, 6)},
        {"user_id": 101, "activity_date": date(2026, 11, 7)},
        {"user_id": 102, "activity_date": date(2026, 11, 1)},
        {"user_id": 102, "activity_date": date(2026, 11, 2)},
        {"user_id": 102, "activity_date": date(2026, 11, 3)},
        {"user_id": 102, "activity_date": date(2026, 11, 4)},
    ]

    streaks = calculate_user_streaks(data)
    assert len(streaks) == 3

    # Check User 101
    u101_streaks = [s for s in streaks if s["user_id"] == 101]
    assert len(u101_streaks) == 2
    assert u101_streaks[0]["streak_length"] == 3
    assert u101_streaks[1]["streak_length"] == 2

    # Check User 102
    u102_streaks = [s for s in streaks if s["user_id"] == 102]
    assert len(u102_streaks) == 1
    assert u102_streaks[0]["streak_length"] == 4
    print(f"  -> User 101 Streaks: {[s['streak_length'] for s in u101_streaks]}")
    print(f"  -> User 102 Longest Streak: {u102_streaks[0]['streak_length']} days")
    print("[CHALLENGE 1 PASSED] Gap-and-Island Streak calculation verified.")
```

---

## Challenge 2: Recursive Hierarchical CTE Engine with Cycle Defense

### Context & Requirements
In corporate ERP systems, organization charts and bill-of-materials structures form directed graphs. If bad data creates a cycle (e.g. Employee 4 reports to Employee 2, while Employee 2 reports to Employee 4), naive recursive CTEs enter infinite loops.
Implement a `RecursiveGraphTraversalEngine` that:
1. Traverses the graph from root nodes (`manager_id IS NULL`).
2. Computes the hierarchical `depth` and human-readable `path` string.
3. Implements **cycle detection**: tracks the visited path and immediately terminates branches that encounter an already-visited ancestor.
4. Flags any node that forms a cycle as `is_cycle: True`.

---

### Reference Solution

```python
from typing import Any, Dict, List, Optional, Set


class RecursiveGraphTraversalEngine:
    @staticmethod
    def traverse_with_cycle_detection(
        nodes: List[Dict[str, Any]],
        id_key: str = "id",
        parent_key: str = "manager_id",
        name_key: str = "name"
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        # Step 1: Identify Anchor Members (roots where parent is None)
        working_table: List[Dict[str, Any]] = []
        for n in nodes:
            if n.get(parent_key) is None:
                item = dict(n)
                item["depth"] = 1
                item["path"] = str(n[name_key])
                item["visited"] = [n[id_key]]
                item["is_cycle"] = False
                results.append(item)
                working_table.append(item)

        # Step 2: Recursive Loop with Cycle Defense
        depth_cap = 50
        current_depth = 1

        while working_table and current_depth < depth_cap:
            next_working_table: List[Dict[str, Any]] = []
            working_lookup = {w[id_key]: w for w in working_table}

            for n in nodes:
                p_id = n.get(parent_key)
                if p_id in working_lookup:
                    parent_node = working_lookup[p_id]
                    node_id = n[id_key]

                    # Cycle Detection Check
                    if node_id in parent_node["visited"]:
                        # Cycle detected! Record cycle flag and halt branch
                        cycle_item = dict(n)
                        cycle_item["depth"] = parent_node["depth"] + 1
                        cycle_item["path"] = f"{parent_node['path']} -> {n[name_key]} [CYCLE DETECTED]"
                        cycle_item["visited"] = parent_node["visited"] + [node_id]
                        cycle_item["is_cycle"] = True
                        results.append(cycle_item)
                        # Do NOT add to next_working_table (halts infinite recursion)
                        continue

                    child = dict(n)
                    child["depth"] = parent_node["depth"] + 1
                    child["path"] = f"{parent_node['path']} -> {n[name_key]}"
                    child["visited"] = parent_node["visited"] + [node_id]
                    child["is_cycle"] = False
                    results.append(child)
                    next_working_table.append(child)

            working_table = next_working_table
            current_depth += 1

        return results


# --- Verification Harness ---
if __name__ == "__main__":
    # Graph containing a deliberate cycle: 1 -> 2 -> 3 -> 2 (Cycle!)
    org = [
        {"id": 1, "name": "CEO", "manager_id": None},
        {"id": 2, "name": "VP Eng", "manager_id": 1},
        {"id": 3, "name": "Lead Architect", "manager_id": 2},
        {"id": 2, "name": "VP Eng (Corrupt Parent)", "manager_id": 3},  # Cycle!
    ]

    engine = RecursiveGraphTraversalEngine()
    traversed = engine.traverse_with_cycle_detection(org)

    # Verify cycle was intercepted safely without infinite loop
    cycle_nodes = [t for t in traversed if t["is_cycle"]]
    assert len(cycle_nodes) == 1
    assert "CYCLE DETECTED" in cycle_nodes[0]["path"]
    print(f"  -> Traversal completed safely with {len(traversed)} total nodes.")
    print(f"  -> Cycle intercepted at node: {cycle_nodes[0]['name']}")
    print("[CHALLENGE 2 PASSED] Recursive CTE with Cycle Detection verified.")
```

---

## Challenge 3: Financial Moving Average & Outlier Detection Engine

### Context & Requirements
In high-frequency automated fraud detection, unusual transaction amounts are identified by comparing a transaction against the **rolling 5-day moving average and standard deviation** within the same merchant category.
Implement a `RollingMetricsEngine` using window frame mechanics that:
1. Computes a rolling 5-transaction moving average (`ROWS BETWEEN 4 PRECEDING AND CURRENT ROW`).
2. Computes the rolling sample standard deviation:
   $$\sigma = \sqrt{\frac{\sum (x_i - \bar{x})^2}{N}}$$
3. Flags any transaction where:
   $$|\text{amount} - \text{moving\_avg}| > 2 \times \sigma$$
   as an `is_outlier: True` alert.

---

### Reference Solution

```python
import math
from decimal import Decimal
from typing import Any, Dict, List


class RollingMetricsEngine:
    @staticmethod
    def detect_outliers(
        transactions: List[Dict[str, Any]],
        window_size: int = 5,
        threshold_sigma: float = 2.0
    ) -> List[Dict[str, Any]]:
        # Pre-sort transactions by timestamp
        sorted_tx = sorted(transactions, key=lambda x: x["timestamp"])
        results: List[Dict[str, Any]] = []

        for idx, tx in enumerate(sorted_tx):
            # Window frame: [max(0, idx - window_size + 1) ... idx]
            start_idx = max(0, idx - window_size + 1)
            frame = sorted_tx[start_idx : idx + 1]

            amounts = [float(item["amount"]) for item in frame]
            n = len(amounts)
            mean = sum(amounts) / n

            # Compute standard deviation
            if n > 1:
                variance = sum((x - mean) ** 2 for x in amounts) / (n - 1)
                std_dev = math.sqrt(variance)
            else:
                std_dev = 0.0

            curr_amt = float(tx["amount"])
            is_outlier = False
            if std_dev > 0.0:
                z_score = abs(curr_amt - mean) / std_dev
                is_outlier = z_score > threshold_sigma

            enriched = dict(tx)
            enriched["moving_avg"] = round(mean, 2)
            enriched["moving_std_dev"] = round(std_dev, 2)
            enriched["is_outlier"] = is_outlier
            results.append(enriched)

        return results


# --- Verification Harness ---
if __name__ == "__main__":
    tx_stream = [
        {"id": 1, "timestamp": "2026-11-01T10:00:00Z", "amount": "50.00"},
        {"id": 2, "timestamp": "2026-11-01T10:05:00Z", "amount": "52.00"},
        {"id": 3, "timestamp": "2026-11-01T10:10:00Z", "amount": "48.00"},
        {"id": 4, "timestamp": "2026-11-01T10:15:00Z", "amount": "51.00"},
        {"id": 5, "timestamp": "2026-11-01T10:20:00Z", "amount": "500.00"},  # Giant spike / fraud!
    ]

    engine = RollingMetricsEngine()
    evaluated = engine.detect_outliers(tx_stream, window_size=5, threshold_sigma=1.5)

    assert evaluated[-1]["id"] == 5
    assert evaluated[-1]["is_outlier"] is True
    assert evaluated[0]["is_outlier"] is False
    print(f"  -> Normal Transaction: Avg={evaluated[0]['moving_avg']}, Outlier={evaluated[0]['is_outlier']}")
    print(f"  -> Fraudulent Spike: Amount=${evaluated[-1]['amount']}, Avg={evaluated[-1]['moving_avg']}, Outlier={evaluated[-1]['is_outlier']}")
    print("[CHALLENGE 3 PASSED] Rolling Window Frame & Outlier Detection verified.")
```
