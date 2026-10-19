# Day 26: Interval Problems: Merge Intervals, Meeting Rooms & Calendar Scheduling

In backend engineering, distributed cloud systems, and database internals, sequential data is frequently partitioned into discrete temporal or spatial ranges:
- **Cloud Infrastructure & Autoscaling**: Allocating Kubernetes worker nodes or GPU clusters based on overlapping batch reservation windows (Meeting Rooms II).
- **Time-Series Database Compaction**: Merging overlapping metric chunks in Prometheus, TimescaleDB, or InfluxDB (Merge Intervals).
- **Network Security & Firewalls**: Consolidating overlapping IP CIDR ranges and port rules in AWS Security Groups or Linux `iptables`.
- **Calendar & Booking Engines**: Preventing double/triple bookings in Google Calendar, Airbnb reservation engines, and airline ticket allocations.

Mastering interval problems requires mastering two fundamental algorithmic paradigms: **Chronological Sorting Invariants** and the **Sweep-Line (Difference Array) Algorithm**.

---

## 1. Interval Anatomy & Boundary Conventions

An interval represents a contiguous range between two values:
```
Closed Interval:      [start, end]   -> Contains both start and end points.
Half-Open Interval:   [start, end)   -> Contains start, but excludes end.
```

### Why Systems Engineering Standardizes on Half-Open `[start, end)`:
1. **Duration Calculation**: $\text{Duration} = \text{end} - \text{start}$ without $+ 1$ offsets.
2. **Seamless Adjacency**: Two consecutive intervals $[10, 15)$ and $[15, 20)$ meet at timestamp `15` without overlapping. A meeting ending at 11:00 AM does not conflict with a meeting starting at 11:00 AM.
3. **Empty Interval Representation**: An empty interval is simply $[x, x)$.

---

## 2. The 6 Spatial Relationships Between Two Intervals

When comparing two intervals $A$ and $B$ (where $A.\text{start} \le B.\text{start}$):

```
1. Disjoint (A finishes before B starts):
   A: [──────]
   B:           [──────]
   Condition: A.end < B.start  (or A.end <= B.start for half-open)

2. Partial Overlap (B starts before A ends, but finishes after):
   A: [──────────]
   B:       [──────────]
   Condition: B.start <= A.end and B.end > A.end
   Merged: [A.start, max(A.end, B.end)]

3. Complete Containment (B is entirely inside A):
   A: [────────────────]
   B:     [────────]
   Condition: B.start >= A.start and B.end <= A.end
   Merged: A

4. Complete Intersection:
   A: [──────────]
   B:       [──────────]
   Overlap Window: [max(A.start, B.start), min(A.end, B.end)]
```

---

## 3. Pattern 1: Merge Intervals ($\mathcal{O}(N \log N)$ Time, $\mathcal{O}(1)$ Space)

Given an array of intervals, merge all overlapping intervals into non-overlapping segments.

```python
def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    if not intervals:
        return []

    # CRITICAL: Sort by START time!
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]

    for current in intervals[1:]:
        prev = merged[-1]

        # Overlap condition: current starts before or when previous ends
        if current[0] <= prev[1]:
            # Extend the previous interval's end if current reaches further
            prev[1] = max(prev[1], current[1])
        else:
            # Disjoint interval: start a new segment
            merged.append(current)

    return merged
```

### Why Sorting by Start Time is Non-Negotiable:
Sorting guarantees that for any interval $i$, all preceding intervals have start times $\le \text{intervals}[i][0]$. We only ever need to compare the incoming interval against the **last merged interval** (`merged[-1]`).

---

## 4. Pattern 2: Insert Interval in Sorted Non-Overlapping Set ($\mathcal{O}(N)$ Time)

Given a set of non-overlapping intervals sorted by start time, insert a new interval and merge if necessary.

Instead of inserting and sorting the whole list in $\mathcal{O}(N \log N)$, we use a **Three-Phase Linear Sweep** in strictly **$\mathcal{O}(N)$ time**:

```python
def insert_interval(intervals: list[list[int]], new_interval: list[int]) -> list[list[int]]:
    result = []
    i = 0
    n = len(intervals)

    # Phase 1: Add all intervals ending strictly before new_interval starts
    while i < n and intervals[i][1] < new_interval[0]:
        result.append(intervals[i])
        i += 1

    # Phase 2: Merge all overlapping intervals
    while i < n and intervals[i][0] <= new_interval[1]:
        new_interval[0] = min(new_interval[0], intervals[i][0])
        new_interval[1] = max(new_interval[1], intervals[i][1])
        i += 1
    result.append(new_interval)

    # Phase 3: Add all remaining intervals starting strictly after new_interval ends
    while i < n:
        result.append(intervals[i])
        i += 1

    return result
```

---

## 5. Pattern 3: Interval List Intersections ($\mathcal{O}(N + M)$ Time)

Given two sorted lists of disjoint intervals, return the intersection of these two interval lists.

### The Two Pointers Invariant:
```python
def interval_intersection(first_list: list[list[int]], second_list: list[list[int]]) -> list[list[int]]:
    i, j = 0, 0
    result = []

    while i < len(first_list) and j < len(second_list):
        a_start, a_end = first_list[i]
        b_start, b_end = second_list[j]

        # Overlap window is bounded by the max of starts and min of ends
        start = max(a_start, b_start)
        end = min(a_end, b_end)

        if start <= end:
            result.append([start, end])

        # ADVANCE INVARIANT: Discard the interval that ends earlier!
        # The interval that ends later could potentially overlap with future intervals.
        if a_end < b_end:
            i += 1
        else:
            j += 1

    return result
```

---

## 6. Pattern 4: Meeting Rooms II (Minimum Server / Room Capacity)

Given meeting time intervals `[[0, 30], [5, 10], [15, 20]]`, find the minimum number of conference rooms (or server pods) required to hold all meetings.

### Solution A: Min-Heap of End Times ($\mathcal{O}(N \log N)$)
Sort meetings by start time. Maintain a Min-Heap storing the **end times** of currently occupied rooms:
```python
import heapq

def min_meeting_rooms_heap(intervals: list[list[int]]) -> int:
    if not intervals:
        return 0

    intervals.sort(key=lambda x: x[0])
    # Min-heap stores end times of active meetings
    room_heap = []
    heapq.heappush(room_heap, intervals[0][1])

    for start, end in intervals[1:]:
        # If the earliest ending meeting finished before or when current starts:
        # Reuse that room!
        if room_heap[0] <= start:
            heapq.heappop(room_heap)

        # Allocate room (either reused or brand new)
        heapq.heappush(room_heap, end)

    # Total rooms needed = maximum simultaneous active rooms
    return len(room_heap)
```

---

## 7. The Sweep-Line Algorithm (Difference Array on Continuum)

The **Sweep-Line Algorithm** is one of the most powerful paradigms in computer science for processing overlapping ranges.

### The Concept:
Imagine a vertical time line sweeping from left to right along the horizontal time axis:
- When an interval starts at $T_{\text{start}}$, resource demand **increases by 1** ($+1$).
- When an interval ends at $T_{\text{end}}$, resource demand **decreases by 1** ($-1$).

```
Meeting 1: [0, 30]  -> Event(0, +1),  Event(30, -1)
Meeting 2: [5, 10]  -> Event(5, +1),  Event(10, -1)
Meeting 3: [15, 20] -> Event(15, +1), Event(20, -1)

Chronologically Sorted Events:
Time:    0    5   10   15   20   30
Delta:  +1   +1   -1   +1   -1   -1
Rooms:   1    2    1    2    1    0
              ▲         ▲
              Max Concurrent Rooms = 2!
```

```python
def min_meeting_rooms_sweepline(intervals: list[list[int]]) -> int:
    events = []
    for start, end in intervals:
        events.append((start, 1))    # Meeting start: +1 room
        events.append((end, -1))     # Meeting end:   -1 room

    # Tie-breaking rule: If a meeting ends at time T and another starts at T,
    # the END event must be processed FIRST (-1 before +1)!
    # (-1 < +1), so standard Python tuple sort handles this automatically!
    events.sort(key=lambda x: (x[0], x[1]))

    max_rooms = 0
    current_rooms = 0

    for time_point, delta in events:
        current_rooms += delta
        max_rooms = max(max_rooms, current_rooms)

    return max_rooms
```

---

## 8. Pattern 5: Non-Overlapping Intervals (Greedy Interval Scheduling)

Given a collection of intervals, find the **minimum number of intervals you need to remove** to make the rest of the intervals non-overlapping (Interval Scheduling Maximization Problem - ISMP).

### The Counter-Intuitive Invariant:
**Sort by END TIME, NOT start time!**

```
Why End Time?
To maximize the number of compatible intervals, we must greedily select the
interval that FINISHES EARLIEST! Finishing early leaves the maximum possible
unoccupied time for future intervals.
```

```python
def erase_overlap_intervals(intervals: list[list[int]]) -> int:
    if not intervals:
        return 0

    # CRITICAL: Sort by END time!
    intervals.sort(key=lambda x: x[1])

    non_overlapping_count = 1
    last_end = intervals[0][1]

    for start, end in intervals[1:]:
        if start >= last_end:
            # Compatible interval: retain it and update last_end
            non_overlapping_count += 1
            last_end = end

    # Removals = Total - Retained
    return len(intervals) - non_overlapping_count
```

---

## 9. Calendar Booking Engines (My Calendar I, II, III)

### 1. My Calendar I (Single Booking / No Overlaps):
Use `bisect` over sorted start times or a Balanced BST to check adjacent intervals in $\mathcal{O}(\log N)$ time.

### 2. My Calendar II (Double Booking Allowed / No Triple Bookings):
Maintain two lists:
1. `bookings`: List of all accepted intervals.
2. `overlaps`: List of intervals where two bookings overlap.
A new booking is valid if it does not overlap with ANY interval in `overlaps`.

### 3. My Calendar III ($K$-Booking Peak Concurrency):
Using a Sweep-Line / Sorted Map: on each booking `book(start, end)`, add `+1` at `start` and `-1` at `end`. Calculate prefix sums to find the global peak $K$.

---

## 10. Firewall Rules & CIDR Block Compaction

In AWS VPC routing tables and Linux `iptables`, network engineers configure overlapping IP ranges.

### Example:
- Block 1: `10.0.0.0 - 10.0.0.255` (`10.0.0.0/24`)
- Block 2: `10.0.0.128 - 10.0.1.255`
Convert IPv4 addresses into 32-bit unsigned integers:
`[167772160, 167772415]` and `[167772288, 167772671]`.
Run `merge_intervals()`. Convert merged integer ranges back to optimal CIDR masks in linear time!

---

## 11. 7 Golden Invariant Rules for Interval Engineering

1. **Sort by Start for Merging & Grouping**: `intervals.sort(key=lambda x: x[0])` ensures monotonic left boundaries.
2. **Sort by End for Greedy Scheduling**: To minimize removals or maximize non-overlapping jobs, always sort by `end` time (`key=lambda x: x[1]`).
3. **Tie-Break Ends Before Starts in Sweep-Line**: In half-open intervals $[A, B)$ and $[B, C)$, ensure the departure $(-1)$ is processed before the arrival $(+1)$ at timestamp $B$.
4. **Advance the Earlier Ending Pointer**: In two-pointer list intersections, always increment the pointer whose interval finishes earlier (`a_end < b_end ? i++ : j++`).
5. **Always Use Max for Right Extension**: When merging overlapping intervals, `prev.end = max(prev.end, curr.end)` to handle complete containment.
6. **Use Min-Heaps for Resource Sizing**: When computing concurrent rooms or pods, maintain a Min-Heap of end times to track earliest available capacity.
7. **Normalize Timezones to UTC Unix Epochs**: Never sort calendar intervals across mixed timezone strings; convert all timestamps to integer seconds first.

---

## 12. Complexity Reference Matrix

| Problem / Algorithm | Time Complexity | Auxiliary Space | Key Technique |
| :--- | :--- | :--- | :--- |
| **Merge Intervals** | $\mathcal{O}(N \log N)$ | $\mathcal{O}(N)$ or $\mathcal{O}(1)$ in-place | Sort by `start` + extend `prev.end` |
| **Insert Interval** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Three-phase linear sweep |
| **Interval Intersections** | $\mathcal{O}(N + M)$ | $\mathcal{O}(N + M)$ | Two pointers (advance earlier end) |
| **Meeting Rooms I** | $\mathcal{O}(N \log N)$ | $\mathcal{O}(1)$ | Sort by `start` + adjacent overlap check |
| **Meeting Rooms II (Heap)**| $\mathcal{O}(N \log N)$ | $\mathcal{O}(N)$ | Min-Heap of active end times |
| **Meeting Rooms II (Sweep)**| $\mathcal{O}(N \log N)$ | $\mathcal{O}(N)$ | Boundary events `(+1, -1)` + prefix sum |
| **Erase Overlap Intervals** | $\mathcal{O}(N \log N)$ | $\mathcal{O}(1)$ | Greedy choice: sort by `end` time |
| **My Calendar I** | $\mathcal{O}(N \log N)$ | $\mathcal{O}(N)$ | Binary search insertion point (`bisect`) |
