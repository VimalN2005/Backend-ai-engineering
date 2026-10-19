"""
Production Architecture: Cloud GPU Cluster Reservation & Multi-Tier Calendar Booking Engine.

Demonstrates real-world interval management in high-throughput backend infrastructure:
1. CloudResourceReservationEngine:
   - Manages scarce compute resources (e.g., NVIDIA H100 clusters) across a continuous timeline.
   - Implements the Sweep-Line event differential algorithm for O(N log N) peak capacity validation.
   - Detects fragmented idle windows to schedule low-priority spot/batch training workloads.
2. DynamicRoomMeetingScheduler:
   - Assigns discrete meeting rooms (0..K-1) using a dual-heap approach (active rooms vs available rooms).
   - Minimizes allocated room footprint while guaranteeing zero double-bookings.
3. High-Volume Stress Test & Benchmark:
   - Validates latency and correctness under high-concurrency reservation loads.
"""

from dataclasses import dataclass, field
import heapq
import time
from typing import Dict, List, Optional, Tuple


# =====================================================================
# DOMAIN MODELS & DATA STRUCTURES
# =====================================================================

@dataclass(frozen=True, order=True)
class TimeWindow:
    """
    Immutable half-open time interval [start, end).
    
    WHY HALF-OPEN [start, end)?
    1. Duration calculation is strictly: duration = end - start (no +1 offset errors).
    2. Adjacent intervals meet cleanly without overlap: [9:00, 10:00) and [10:00, 11:00)
       have zero conflict, allowing back-to-back scheduling on identical hardware.
    """
    start: int
    end: int

    def __post_init__(self):
        if self.start >= self.end:
            raise ValueError(f"Invalid interval: start ({self.start}) must precede end ({self.end})")

    @property
    def duration(self) -> int:
        return self.end - self.start

    def overlaps_with(self, other: "TimeWindow") -> bool:
        """Two half-open intervals overlap iff max(start) < min(end)."""
        return max(self.start, other.start) < min(self.end, other.end)


@dataclass
class GpuReservation:
    """Represents a scheduled GPU compute workload reservation."""
    reservation_id: str
    tenant_id: str
    window: TimeWindow
    gpu_count: int
    created_at: float = field(default_factory=time.time)


@dataclass
class RoomMeeting:
    """Represents a booked meeting with assigned room."""
    meeting_id: str
    title: str
    window: TimeWindow
    room_id: int = -1


# =====================================================================
# 1. CLOUD GPU CLUSTER RESERVATION ENGINE
# =====================================================================

class CloudResourceReservationEngine:
    """
    Capacity-aware cluster scheduler managing dynamic resource allocation.
    
    In cloud AI platforms (e.g., Slurm, Ray, Kubernetes device plugins), multiple tenants
    request variable GPU allocations (e.g., 8 GPUs for fine-tuning, 64 GPUs for pre-training)
    over designated time windows.
    
    This engine enforces capacity constraints, computes peak utilization, and identifies
    idle fragmentation gaps where spot/batch workloads can be backfilled.
    """

    def __init__(self, cluster_id: str, max_gpu_capacity: int):
        self.cluster_id = cluster_id
        self.max_capacity = max_gpu_capacity
        self._reservations: Dict[str, GpuReservation] = {}

    @property
    def total_reservations(self) -> int:
        return len(self._reservations)

    def request_reservation(
        self,
        reservation_id: str,
        tenant_id: str,
        start_time: int,
        end_time: int,
        gpu_count: int,
    ) -> bool:
        """
        Attempts to reserve GPU capacity over [start_time, end_time).
        
        ALGORITHM: Sweep-Line Capacity Verification.
        1. Query only existing reservations that overlap with the target window.
        2. Construct difference events for all overlapping reservations plus the candidate.
        3. If running sum at any point exceeds max_capacity, reject candidate immediately.
        4. Otherwise, accept and persist reservation.
        
        Complexity: O(K log K) where K is number of overlapping reservations (K << N).
        """
        if gpu_count <= 0:
            raise ValueError("GPU count must be strictly positive.")
        if gpu_count > self.max_capacity:
            return False  # Candidate exceeds entire physical cluster size

        candidate_window = TimeWindow(start_time, end_time)

        # Step 1: Filter overlapping reservations
        # WHY: We do not need to process past or future reservations that do not intersect.
        overlapping: List[GpuReservation] = [
            res for res in self._reservations.values()
            if res.window.overlaps_with(candidate_window)
        ]

        # Step 2: Build Sweep-Line events
        # Event format: (timestamp, delta_gpu)
        # End events are negative; start events are positive.
        events: List[Tuple[int, int]] = []
        
        # Add candidate events
        events.append((candidate_window.start, gpu_count))
        events.append((candidate_window.end, -gpu_count))

        # Add overlapping reservations events clipped to candidate window
        for res in overlapping:
            events.append((res.window.start, res.gpu_count))
            events.append((res.window.end, -res.gpu_count))

        # Step 3: Sort events chronologically
        # TIE-BREAKING INVARIANT:
        # If reservation A ends at time T and reservation B starts at time T,
        # A's negative delta (-count) MUST be evaluated before B's positive delta (+count).
        # In Python tuples, sorting (T, -count) naturally places negative deltas before positive ones!
        events.sort(key=lambda item: (item[0], item[1]))

        # Step 4: Validate running capacity invariant
        current_gpus = 0
        for timestamp, delta in events:
            current_gpus += delta
            if current_gpus > self.max_capacity:
                # Capacity breached: Reject without mutating state
                return False

        # Invariant preserved: Commit reservation
        self._reservations[reservation_id] = GpuReservation(
            reservation_id=reservation_id,
            tenant_id=tenant_id,
            window=candidate_window,
            gpu_count=gpu_count,
        )
        return True

    def cancel_reservation(self, reservation_id: str) -> bool:
        """Cancels an existing reservation, releasing capacity."""
        if reservation_id in self._reservations:
            del self._reservations[reservation_id]
            return True
        return False

    def get_peak_utilization(self, window: TimeWindow) -> Tuple[int, int]:
        """
        Returns (peak_gpu_usage, peak_timestamp) within the specified query window.
        Uses Sweep-Line scanning restricted to the query horizon.
        """
        overlapping = [
            res for res in self._reservations.values()
            if res.window.overlaps_with(window)
        ]
        if not overlapping:
            return (0, window.start)

        events: List[Tuple[int, int]] = []
        for res in overlapping:
            # Clip event boundaries to query window
            eff_start = max(res.window.start, window.start)
            eff_end = min(res.window.end, window.end)
            if eff_start < eff_end:
                events.append((eff_start, res.gpu_count))
                events.append((eff_end, -res.gpu_count))

        events.sort(key=lambda item: (item[0], item[1]))

        max_usage = 0
        peak_time = window.start
        current_usage = 0

        for timestamp, delta in events:
            current_usage += delta
            if current_usage > max_usage:
                max_usage = current_usage
                peak_time = timestamp

        return (max_usage, peak_time)

    def find_idle_windows(self, horizon: TimeWindow, min_duration: int) -> List[TimeWindow]:
        """
        Detects unallocated time windows where ALL cluster GPUs are completely idle.
        
        WHY THIS MATTERS:
        Batch inference and model checkpoint validation jobs can be scheduled in these
        zero-cost idle gaps to maximize return on hardware investment.
        """
        overlapping = [
            res for res in self._reservations.values()
            if res.window.overlaps_with(horizon)
        ]
        if not overlapping:
            return [horizon] if horizon.duration >= min_duration else []

        # Merge busy intervals
        raw_intervals = [
            [max(res.window.start, horizon.start), min(res.window.end, horizon.end)]
            for res in overlapping
        ]
        raw_intervals.sort(key=lambda x: x[0])

        merged_busy: List[List[int]] = [raw_intervals[0]]
        for start, end in raw_intervals[1:]:
            if start <= merged_busy[-1][1]:
                merged_busy[-1][1] = max(merged_busy[-1][1], end)
            else:
                merged_busy.append([start, end])

        # Complement busy intervals against the horizon
        idle_windows: List[TimeWindow] = []
        curr_time = horizon.start

        for busy_start, busy_end in merged_busy:
            if busy_start > curr_time:
                gap_duration = busy_start - curr_time
                if gap_duration >= min_duration:
                    idle_windows.append(TimeWindow(curr_time, busy_start))
            curr_time = max(curr_time, busy_end)

        if curr_time < horizon.end:
            if (horizon.end - curr_time) >= min_duration:
                idle_windows.append(TimeWindow(curr_time, horizon.end))

        return idle_windows


# =====================================================================
# 2. DYNAMIC ROOM MEETING SCHEDULER (MIN-ROOM ALLOCATOR)
# =====================================================================

class DynamicRoomMeetingScheduler:
    """
    Production conference room allocator.
    
    Assigns each meeting an exact physical room ID (0, 1, 2, ... K-1).
    Uses a dual-heap architecture:
    1. available_rooms_heap: Min-heap of available integer room IDs (reuses lowest room ID first).
    2. active_meetings_heap: Min-heap of (end_time, room_id) tracking currently occupied rooms.
    """

    def __init__(self):
        self._meetings: List[RoomMeeting] = []

    def schedule_meetings(self, meetings: List[Tuple[str, str, int, int]]) -> List[RoomMeeting]:
        """
        Schedules a batch of requested meetings and allocates minimal room IDs.
        
        Input: List of (meeting_id, title, start_time, end_time)
        Output: List of RoomMeeting with assigned room_id.
        
        Complexity: O(N log N) time, O(N) space.
        """
        if not meetings:
            return []

        # Wrap in internal objects and sort by start time
        meeting_objs = [
            RoomMeeting(
                meeting_id=m[0],
                title=m[1],
                window=TimeWindow(m[2], m[3]),
            )
            for m in meetings
        ]
        # Sort by start time (critical invariant for room allocation)
        meeting_objs.sort(key=lambda m: m.window.start)

        # Min-heap of available reusable room IDs
        available_rooms: List[int] = []
        # Min-heap of active meetings: (end_time, room_id)
        active_heap: List[Tuple[int, int]] = []

        next_new_room_id = 0
        scheduled_results: List[RoomMeeting] = []

        for meeting in meeting_objs:
            curr_start = meeting.window.start

            # Release all rooms whose meetings have finished before curr_start
            while active_heap and active_heap[0][0] <= curr_start:
                released_end, freed_room_id = heapq.heappop(active_heap)
                heapq.heappush(available_rooms, freed_room_id)

            # Assign room: reuse existing released room if possible, else allocate new room
            if available_rooms:
                assigned_room = heapq.heappop(available_rooms)
            else:
                assigned_room = next_new_room_id
                next_new_room_id += 1

            # Mark room occupied until meeting end
            heapq.heappush(active_heap, (meeting.window.end, assigned_room))
            meeting.room_id = assigned_room
            scheduled_results.append(meeting)

        return scheduled_results


# =====================================================================
# 3. UNIT TESTS & VERIFICATION
# =====================================================================

def run_unit_tests():
    print("=" * 70)
    print("RUNNING UNIT TESTS FOR PRODUCTION SCHEDULING ENGINES")
    print("=" * 70)

    # 1. Test GPU Cluster Capacity Engine
    cluster = CloudResourceReservationEngine(cluster_id="h100-primary", max_gpu_capacity=64)

    # Reservation 1: 32 GPUs from t=100 to t=200 -> OK (32/64)
    assert cluster.request_reservation("res-1", "team-nlp", 100, 200, 32) is True
    # Reservation 2: 32 GPUs from t=150 to t=250 -> OK (64/64 between 150-200)
    assert cluster.request_reservation("res-2", "team-cv", 150, 250, 32) is True
    # Reservation 3: 8 GPUs from t=180 to t=190 -> REJECT (requires 72 GPUs, max is 64)
    assert cluster.request_reservation("res-3", "team-audio", 180, 190, 8) is False
    # Reservation 4: 8 GPUs from t=200 to t=250 -> OK (back-to-back with res-1, res-1 finishes at 200)
    # Total at t=200: 32 (res-2) + 8 (res-4) = 40 <= 64
    assert cluster.request_reservation("res-4", "team-audio", 200, 250, 8) is True

    # Test Peak Utilization Query
    peak_usage, peak_time = cluster.get_peak_utilization(TimeWindow(50, 300))
    assert peak_usage == 64, f"Expected peak 64, got {peak_usage}"
    assert peak_time == 150, f"Expected peak at time 150, got {peak_time}"
    print("[PASS] CloudResourceReservationEngine capacity checks & peak queries verified.")

    # Test Idle Window Detection
    # Busy: [100, 250) (combined res-1, res-2, res-4)
    # Horizon: [0, 400). Expected idle: [0, 100) duration 100, [250, 400) duration 150.
    idle_windows = cluster.find_idle_windows(TimeWindow(0, 400), min_duration=50)
    assert len(idle_windows) == 2
    assert idle_windows[0] == TimeWindow(0, 100)
    assert idle_windows[1] == TimeWindow(250, 400)
    print("[PASS] Idle fragmentation detection verified.")

    # 2. Test Dynamic Room Meeting Scheduler
    scheduler = DynamicRoomMeetingScheduler()
    # Meeting list: (id, name, start, end)
    test_meetings = [
        ("m1", "Sprint Review", 9, 11),
        ("m2", "Standup", 9, 10),
        ("m3", "1-on-1 Sync", 10, 12),
        ("m4", "Tech All-Hands", 11, 13),
    ]
    # m1: [9, 11) -> Room 0
    # m2: [9, 10) -> Room 1
    # m3: [10, 12) -> At t=10, m2 finishes, Room 1 is freed and reassigned to m3!
    # m4: [11, 13) -> At t=11, m1 finishes, Room 0 is freed and reassigned to m4!
    # Total rooms needed: exactly 2 rooms (0 and 1).
    scheduled = scheduler.schedule_meetings(test_meetings)
    room_allocations = {m.meeting_id: m.room_id for m in scheduled}

    assert room_allocations["m1"] == 0
    assert room_allocations["m2"] == 1
    assert room_allocations["m3"] == 1  # Reused Room 1
    assert room_allocations["m4"] == 0  # Reused Room 0
    print("[PASS] DynamicRoomMeetingScheduler optimal room reuse verified.")
    print("All unit tests passed successfully!\n")


# =====================================================================
# 4. HIGH-VOLUME PERFORMANCE BENCHMARK
# =====================================================================

def run_performance_benchmark():
    print("=" * 70)
    print("HIGH-VOLUME ENTERPRISE WORKLOAD BENCHMARK")
    print("=" * 70)

    import random
    random.seed(42)

    total_requests = 10_000
    engine = CloudResourceReservationEngine(cluster_id="perf-cluster", max_gpu_capacity=512)

    print(f"Generating and evaluating {total_requests:,} dynamic reservation requests...")

    start_time = time.perf_counter()
    accepted_count = 0
    rejected_count = 0

    for i in range(total_requests):
        # Generate random start and duration across a 10,000 unit timeline
        req_start = random.randint(1, 9_500)
        req_duration = random.randint(10, 150)
        req_gpus = random.choice([8, 16, 32, 64, 128])

        success = engine.request_reservation(
            reservation_id=f"req-{i}",
            tenant_id=f"tenant-{i % 50}",
            start_time=req_start,
            end_time=req_start + req_duration,
            gpu_count=req_gpus,
        )

        if success:
            accepted_count += 1
        else:
            rejected_count += 1

    elapsed = (time.perf_counter() - start_time) * 1000  # ms
    avg_latency = (elapsed / total_requests) * 1000      # microseconds

    print(f"Total Requests Evaluated: {total_requests:,}")
    print(f"Accepted Reservations:    {accepted_count:,}")
    print(f"Rejected Over-Capacity:   {rejected_count:,}")
    print(f"Total Processing Time:    {elapsed:.2f} ms")
    print(f"Average Decision Latency: {avg_latency:.2f} microseconds / request")
    print(f"Peak System Throughput:   {(total_requests / (elapsed / 1000)):,.0f} req/sec")

    # Horizon idle check
    idle_start = time.perf_counter()
    gaps = engine.find_idle_windows(TimeWindow(0, 10_000), min_duration=20)
    idle_elapsed = (time.perf_counter() - idle_start) * 1000

    print(f"Idle Windows Detected:    {len(gaps)} gaps (computed in {idle_elapsed:.2f} ms)")
    print("=" * 70)


if __name__ == "__main__":
    run_unit_tests()
    run_performance_benchmark()
