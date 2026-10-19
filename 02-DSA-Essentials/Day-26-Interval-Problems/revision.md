# Day 26 Rapid Revision: Interval Problems & Sweep-Line

A condensed 5-minute reference sheet for interval scheduling algorithms, boundary handling, and capacity management.

---

## 1. Algorithmic Decision Matrix

| Problem Type | Target Goal | Primary Invariant | Sorting Key | Optimal Data Structure |
|---|---|---|---|---|
| **Merge Intervals** | Union overlapping ranges | Chronological start order | `x[0]` (Start Time) | Sorted Array + Linear Scan ($O(N \log N)$) |
| **Insert Interval** | Splice new range into disjoint list | Three-phase partition (before, overlap, after) | Pre-sorted by `x[0]` | Single Pass ($O(N)$ time, $O(1)$ extra) |
| **Meeting Rooms I** | Detect any overlap boolean | Adjacent finish check | `x[0]` (Start Time) | Sorted Array ($O(N \log N)$) |
| **Meeting Rooms II** | Find peak concurrent resources | Track active room releases | `x[0]` (Start Time) | Min-Heap of end times ($O(N \log N)$) |
| **Peak Load Timeline** | Multi-resource continuous profile | Event difference array | Chronological timestamp | Sweep-Line / Difference Array ($O(N \log N)$) |
| **Erase Overlap (ISMP)** | Maximize non-overlapping count | Earliest finish leaves max future room | `x[1]` (End Time) | Greedy Forward Scan ($O(N \log N)$) |
| **Room Assignment** | Allocate lowest available room ID | Free earlier rooms first | `x[0]` (Start Time) | Dual Min-Heaps (Available & Occupied) |

---

## 2. Core Code Templates

### Template 1: Merge Intervals (Union Overlaps)
```python
def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])  # Sort by START time
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)  # MUST take max()!
        else:
            merged.append([start, end])
    return merged
```

### Template 2: Sweep-Line Event Difference Array (Peak Concurrency)
```python
def min_meeting_rooms_sweepline(intervals: list[list[int]]) -> int:
    events = []
    for start, end in intervals:
        events.append((start, +1))  # Start event: acquires resource
        events.append((end, -1))    # End event: releases resource
    
    # Tie-breaking: -1 sorts before +1 when timestamps match
    events.sort(key=lambda x: (x[0], x[1]))
    
    peak = 0
    current = 0
    for timestamp, delta in events:
        current += delta
        peak = max(peak, current)
    return peak
```

### Template 3: Min-Heap Active Resource Tracker
```python
import heapq

def min_meeting_rooms_heap(intervals: list[list[int]]) -> int:
    if not intervals:
        return 0
    intervals.sort(key=lambda x: x[0])
    end_times_heap = []
    for start, end in intervals:
        # Free rooms that finished before current meeting starts
        if end_times_heap and end_times_heap[0] <= start:
            heapq.heappop(end_times_heap)
        heapq.heappush(end_times_heap, end)
    return len(end_times_heap)
```

### Template 4: Interval Scheduling Maximization (Greedy ISMP)
```python
def erase_overlap_intervals(intervals: list[list[int]]) -> int:
    if not intervals:
        return 0
    intervals.sort(key=lambda x: x[1])  # CRITICAL: Sort by END time
    non_overlapping = 0
    last_end = float("-inf")
    for start, end in intervals:
        if start >= last_end:
            non_overlapping += 1
            last_end = end
    return len(intervals) - non_overlapping
```

---

## 3. Non-Negotiable Invariants & Rules

1. **The Overlap Theorem**:
   Two half-open intervals $[s_1, e_1)$ and $[s_2, e_2)$ overlap if and only if:
   $$\max(s_1, s_2) < \min(e_1, e_2)$$
   Their intersection is:
   $$[\max(s_1, s_2), \min(e_1, e_2))$$

2. **Containment Rule in Merge**:
   Never write `merged[-1][1] = curr.end`. Always write `merged[-1][1] = max(merged[-1][1], curr.end)` to avoid truncating completely nested intervals like `[1, 10]` and `[2, 5]`.

3. **Sweep-Line Tie-Breaking Rule**:
   In half-open boundaries $[start, end)$, departure $(-1)$ **must** sort before arrival $(+1)$ at identical timestamps:
   $$(T, -1) < (T, +1)$$
   Reversing this order causes phantom capacity spikes and false rejection errors.

4. **Greedy Maximization Sorting Rule**:
   - Merge/Group intervals $\longrightarrow$ Sort by **Start Time**.
   - Maximize non-overlapping jobs $\longrightarrow$ Sort by **End Time**.

---

## 4. Complexity Reference Table

| Algorithm | Pre-sorting Cost | Sweep / Traversal Cost | Total Time | Auxiliary Space |
|---|---|---|---|---|
| **Merge Intervals** | $O(N \log N)$ | $O(N)$ | $O(N \log N)$ | $O(N)$ output |
| **Insert Interval** | $0$ (Pre-sorted) | $O(N)$ | $O(N)$ | $O(N)$ output |
| **Interval Intersection** | $0$ (Pre-sorted) | $O(N + M)$ | $O(N + M)$ | $O(N + M)$ output |
| **Meeting Rooms I** | $O(N \log N)$ | $O(N)$ | $O(N \log N)$ | $O(1)$ extra |
| **Meeting Rooms II (Heap)**| $O(N \log N)$ | $O(N \log K)$ | $O(N \log N)$ | $O(K)$ heap |
| **Meeting Rooms II (Sweep)**| $O(N \log N)$ | $O(N)$ | $O(N \log N)$ | $O(N)$ events |
| **Erase Overlap (ISMP)** | $O(N \log N)$ | $O(N)$ | $O(N \log N)$ | $O(1)$ extra |
| **Employee Free Time** | $O(N \log K)$ | $O(N \log K)$ | $O(N \log K)$ | $O(K)$ heap |
