# Practice Exercises: Interval Scheduling & Range Queries

Mastery of intervals requires tackling three advanced patterns:
1. **Multi-Stream Interval Merging (Employee Free Time)** — $K$-way merge and timeline complement.
2. **Resource-Constrained Delayed Scheduling (Meeting Rooms III)** — Dual min-heaps with priority queues and queue delays.
3. **Greedy Range Coverage (Video Stitching)** — Minimum interval jumps to bridge a target span $[0, T]$.

---

## Challenge 1: Employee Free Time (LeetCode 759 - Hard)

### Problem Statement
We are given a list `schedule` of employees, which represents the working time for each employee. Each employee has a list of non-overlapping `Intervals`, and these intervals are in sorted order.

Return the list of finite intervals representing **common, positive-length free time** for all employees, also in sorted order.

### Constraints
- $1 \le \text{schedule.length} \le 50$
- $1 \le \text{schedule}[i].\text{length} \le 500$
- $0 \le \text{start} < \text{end} \le 10^8$

### Optimal Solution ($K$-Way Heap Merge / Flatten & Sort)

```python
import heapq
from typing import List

class Interval:
    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end
    
    def __repr__(self):
        return f"[{self.start}, {self.end}]"

def employee_free_time(schedule: List[List[Interval]]) -> List[Interval]:
    """
    Finds common free intervals among all employees.
    
    ALGORITHM: K-Way Min-Heap Merge.
    Since each employee's schedule is already sorted, we maintain a min-heap
    containing the current earliest interval from each employee.
    
    Time Complexity: O(N log K) where N is total intervals, K is number of employees.
    Space Complexity: O(K) for the min-heap.
    """
    # Heap stores: (start_time, employee_idx, interval_idx)
    min_heap = []
    for emp_idx, emp_intervals in enumerate(schedule):
        if emp_intervals:
            heapq.heappush(min_heap, (emp_intervals[0].start, emp_idx, 0))

    if not min_heap:
        return []

    # Initialize current latest work finish time with the earliest starting interval
    first_start, first_emp, first_idx = min_heap[0]
    prev_end = schedule[first_emp][first_idx].start
    free_intervals: List[Interval] = []

    while min_heap:
        start, emp_idx, int_idx = heapq.heappop(min_heap)
        curr_interval = schedule[emp_idx][int_idx]

        # If current work starts after previous work ended, we found a free gap!
        if curr_interval.start > prev_end:
            free_intervals.append(Interval(prev_end, curr_interval.start))
            prev_end = curr_interval.end
        else:
            prev_end = max(prev_end, curr_interval.end)

        # Push the next interval from this employee into the heap
        if int_idx + 1 < len(schedule[emp_idx]):
            next_interval = schedule[emp_idx][int_idx + 1]
            heapq.heappush(min_heap, (next_interval.start, emp_idx, int_idx + 1))

    return free_intervals


# Verification
if __name__ == "__main__":
    test_schedule = [
        [Interval(1, 2), Interval(5, 6)],
        [Interval(1, 3)],
        [Interval(4, 10)],
    ]
    result = employee_free_time(test_schedule)
    assert [(r.start, r.end) for r in result] == [(3, 4)], f"Got {result}"
    print("[PASS] Challenge 1: Employee Free Time verified.")
```

---

## Challenge 2: Meeting Rooms III (LeetCode 2402 - Hard)

### Problem Statement
You are given an integer $n$. There are $n$ rooms numbered from $0$ to $n - 1$.
You are given a 2D integer array `meetings` where `meetings[i] = [start_i, end_i]` means that a meeting will be held during the half-open interval `[start_i, end_i)`. All `start_i` are unique.

Meetings are allocated to rooms according to the following rules:
1. Each meeting will take place in the unused room with the **lowest number**.
2. If there are no available rooms, the meeting will be **delayed** until a room becomes free. The delayed meeting maintains the same duration.
3. When a room frees up, meetings that arrived earlier have precedence (they were sorted by original start time).
4. Return the room number that held the most meetings. If multiple rooms have the same count, return the one with the lowest room number.

### Constraints
- $1 \le n \le 100$
- $1 \le \text{meetings.length} \le 10^5$
- `meetings[i].length == 2`
- $0 \le \text{start}_i < \text{end}_i \le 5 \times 10^5$

### Optimal Solution (Dual Priority Queues)

```python
import heapq
from typing import List

def most_booked_room(n: int, meetings: List[List[int]]) -> int:
    """
    Allocates meetings to rooms using dual min-heaps:
    1. available_rooms: stores integers (room_id).
    2. occupied_rooms: stores tuples of (available_at_time, room_id).
    
    Time Complexity: O(M log M + M log N) where M is meetings count, N is room count.
    Space Complexity: O(N) auxiliary space.
    """
    # Sort meetings chronologically by start time
    meetings.sort(key=lambda x: x[0])

    # Min-heap of idle rooms (lowest room_id first)
    available_rooms = list(range(n))
    heapq.heapify(available_rooms)

    # Min-heap of busy rooms: (time_when_freed, room_id)
    occupied_rooms: List[tuple[int, int]] = []

    # Frequency counter of meetings per room
    room_usage = [0] * n

    for start, end in meetings:
        duration = end - start

        # Release all rooms that become free at or before the meeting start time
        while occupied_rooms and occupied_rooms[0][0] <= start:
            freed_time, room_id = heapq.heappop(occupied_rooms)
            heapq.heappush(available_rooms, room_id)

        if available_rooms:
            # Immediate assignment: take lowest available room
            room = heapq.heappop(available_rooms)
            heapq.heappush(occupied_rooms, (end, room))
            room_usage[room] += 1
        else:
            # Delayed meeting: must wait for the earliest room to finish
            earliest_free_time, room = heapq.heappop(occupied_rooms)
            new_end = earliest_free_time + duration
            heapq.heappush(occupied_rooms, (new_end, room))
            room_usage[room] += 1

    # Find the room with max bookings (lowest index on tie)
    max_meetings = max(room_usage)
    for room_id in range(n):
        if room_usage[room_id] == max_meetings:
            return room_id

    return 0


# Verification
if __name__ == "__main__":
    rooms = 2
    meets = [[0, 10], [1, 5], [2, 7], [3, 4]]
    # Room 0: [0, 10]
    # Room 1: [1, 5]
    # At t=2: [2, 7] delayed until t=5, placed in Room 1: [5, 10]
    # At t=3: [3, 4] delayed until t=10, placed in Room 0: [10, 11]
    # Room 0: 2 meetings, Room 1: 2 meetings -> Tie goes to Room 0
    assert most_booked_room(rooms, meets) == 0
    print("[PASS] Challenge 2: Meeting Rooms III verified.")
```

---

## Challenge 3: Video Stitching (LeetCode 1024 - Medium)

### Problem Statement
You are given a series of video clips from a sporting event that lasted `time` seconds. These video clips can be represented as 2D integer array `clips` where `clips[i] = [start_i, end_i]` means that the $i$-th clip covers time from `start_i` to `end_i`.

We can cut these clips into segments freely. Return the **minimum number of clips needed** so that we can cut the clips into a sequence that completely covers the sporting event `[0, time]`. If the task is impossible, return `-1`.

### Constraints
- $1 \le \text{clips.length} \le 100$
- $0 \le \text{start}_i < \text{end}_i \le 100$
- $1 \le \text{time} \le 100$

### Optimal Solution (Greedy Reach / Jump Game Pattern)

```python
from typing import List

def video_stitching(clips: List[List[int]], time: int) -> int:
    """
    Finds the minimum clips to cover [0, time].
    
    ALGORITHM: Greedy Reach Extension.
    At any step, among all clips starting <= current_reach,
    pick the clip that extends furthest into the future (max end).
    
    Time Complexity: O(N log N) or O(N + T)
    Space Complexity: O(1)
    """
    # Sort clips primarily by start time, secondarily descending by end time
    clips.sort(key=lambda x: (x[0], -x[1]))

    clips_count = 0
    curr_reach = 0
    farthest_reach = 0
    i = 0
    n = len(clips)

    while curr_reach < time:
        # Greedily explore all clips that can bridge from curr_reach
        while i < n and clips[i][0] <= curr_reach:
            farthest_reach = max(farthest_reach, clips[i][1])
            i += 1

        # If we couldn't extend reach at all, there is a gap -> impossible
        if farthest_reach <= curr_reach:
            return -1

        # Take the best clip
        clips_count += 1
        curr_reach = farthest_reach

    return clips_count


# Verification
if __name__ == "__main__":
    sample_clips = [[0, 2], [4, 6], [8, 10], [1, 9], [1, 5], [5, 9]]
    target_time = 10
    # Pick [0, 2] -> reach 2
    # Pick [1, 9] -> reach 9
    # Pick [8, 10] -> reach 10
    # Total: 3 clips
    assert video_stitching(sample_clips, target_time) == 3
    print("[PASS] Challenge 3: Video Stitching verified.")
```

---

## Benchmark & Execution Script

Run all three challenge implementations locally to verify algorithmic correctness:

```powershell
python -c "
# Execute challenge tests
"
```
