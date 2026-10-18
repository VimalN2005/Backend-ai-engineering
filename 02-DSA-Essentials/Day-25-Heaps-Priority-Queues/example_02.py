"""
Day 25: Example 02 - Production Architecture: Priority Task Scheduler with Lazy Cancellation & Heavy Hitters

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In enterprise task engines (Celery, Temporal, AWS SQS FIFO) and API gateways:

1. Asynchronous Priority Task Schedulers:
   High-priority jobs (e.g., user checkout payments, fraud detection alerts) must leapfrog
   low-priority background batch tasks (e.g., video compression, report generation).
   Furthermore, tasks often require delayed scheduling (e.g., exponential backoff retries).
   Crucially, in-flight tasks are frequently cancelled or reprioritized. Because arbitrary heap
   deletion is O(N), production engines implement the **Tombstone (Lazy Deletion) Pattern**:
   marking entries as cancelled in a hash table and discarding them lazily in O(log N) upon pop.

2. Heavy Hitters & Top-K Telemetry Stream:
   API Gateways (Cloudflare, Envoy) must identify Top-K abusive IP addresses or high-latency
   endpoints in real-time without buffering gigabytes of request logs. A bounded Min-Heap
   maintains the Top-K heavy hitters with sub-microsecond ingestion latency.

This module implements:
1. `ProductionPriorityScheduler`: Priority task engine with delayed execution, FIFO tie-breaking,
   and O(log N) lazy cancellation tombstones.
2. `HeavyHittersDetector`: Real-time streaming frequency tracker using a bounded Min-Heap.
3. Comprehensive verification suite and high-throughput execution simulation.
"""

from __future__ import annotations

import heapq
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. TASK MODELS & PRIORITIES
# ============================================================================

class TaskPriority(int, Enum):
    CRITICAL = 0   # Highest priority (e.g. Security breach, Payment processing)
    HIGH = 1       # Interactive user requests
    NORMAL = 2     # Standard API processing
    LOW = 3        # Asynchronous background batch jobs


@dataclass
class ScheduledTask:
    """Represents a scheduled unit of work."""
    task_id: str
    payload: Any
    priority: TaskPriority = TaskPriority.NORMAL
    execute_at: float = 0.0  # Unix timestamp for delayed execution
    retries: int = 0


# ============================================================================
# 2. PRODUCTION PRIORITY SCHEDULER (LAZY DELETION / TOMBSTONE PATTERN)
# ============================================================================

class ProductionPriorityScheduler:
    """
    Enterprise priority task scheduler with O(log N) dispatching, delayed retries,
    FIFO tie-breaking, and lazy tombstone cancellation.
    
    DUAL-HEAP ARCHITECTURE:
    1. `_delayed_heap`: Min-Heap sorted by [execute_at, seq_id, entry].
       Holds tasks scheduled for future execution (delays / retries).
    2. `_ready_heap`: Min-Heap sorted by [priority_val, seq_id, entry].
       Holds tasks ready to execute NOW, ordered strictly by priority (and FIFO sequence).
       
    When popping tasks, any matured delayed tasks (execute_at <= now) are transferred
    from `_delayed_heap` to `_ready_heap` in O(log N), allowing ready tasks to be
    dispatched strictly by priority without blocked head-of-line anomalies!
    """

    def __init__(self) -> None:
        # Delayed heap: [execute_at, seq_id, entry]
        self._delayed_heap: List[List[Any]] = []
        # Ready heap: [priority_val, seq_id, entry]
        self._ready_heap: List[List[Any]] = []
        
        # Mapping from task_id to entry: [task_id, priority_val, execute_at, task, is_cancelled]
        self._entry_map: Dict[str, List[Any]] = {}
        self._seq_counter: int = 0
        self._total_scheduled: int = 0
        self._total_cancelled: int = 0
        self._total_dispatched: int = 0

    def schedule(
        self,
        task: ScheduledTask,
        delay_seconds: float = 0.0,
    ) -> None:
        """
        Schedules a task for execution in O(log N) time.
        If task_id already exists, updates priority and execution time.
        """
        if task.task_id in self._entry_map:
            self.cancel(task.task_id)

        now = time.time()
        execute_at = now + max(0.0, delay_seconds)
        task.execute_at = execute_at

        # Entry: [task_id, priority_val, execute_at, task, is_cancelled]
        entry = [task.task_id, task.priority.value, execute_at, task, False]
        self._seq_counter += 1
        self._total_scheduled += 1
        self._entry_map[task.task_id] = entry

        if delay_seconds > 0.0:
            # Future delayed task -> route to delayed heap
            heapq.heappush(self._delayed_heap, [execute_at, self._seq_counter, entry])
        else:
            # Immediate ready task -> route to ready priority heap
            heapq.heappush(self._ready_heap, [task.priority.value, self._seq_counter, entry])

    def cancel(self, task_id: str) -> bool:
        """
        Cancels a pending task in O(1) time using the Tombstone Pattern.
        """
        if task_id in self._entry_map:
            entry = self._entry_map.pop(task_id)
            entry[4] = True  # Mark is_cancelled = True
            self._total_cancelled += 1
            return True
        return False

    def _promote_delayed_tasks(self, now: float) -> None:
        """Transfers any matured delayed tasks to the ready priority heap."""
        while self._delayed_heap and self._delayed_heap[0][0] <= now:
            execute_at, seq_id, entry = heapq.heappop(self._delayed_heap)
            if not entry[4]:  # If not cancelled
                heapq.heappush(self._ready_heap, [entry[1], seq_id, entry])

    def pop_ready_task(self, current_time: Optional[float] = None) -> Optional[ScheduledTask]:
        """
        Retrieves the highest-priority task ready for execution in amortized O(log N).
        Discards tombstones lazily. Returns None if no tasks are ready.
        """
        now = current_time if current_time is not None else time.time()

        # Promote any delayed tasks that have matured
        self._promote_delayed_tasks(now)

        while self._ready_heap:
            priority_val, seq_id, entry = heapq.heappop(self._ready_heap)
            task_id, _, _, task, is_cancelled = entry

            # Skip tombstones
            if is_cancelled:
                continue

            # Remove from active map
            self._entry_map.pop(task_id, None)
            self._total_dispatched += 1
            return task

        return None

    @property
    def pending_count(self) -> int:
        """Returns number of active non-cancelled tasks."""
        return len(self._entry_map)

    @property
    def telemetry(self) -> Dict[str, Any]:
        return {
            "active_pending": len(self._entry_map),
            "delayed_heap_size": len(self._delayed_heap),
            "ready_heap_size": len(self._ready_heap),
            "total_scheduled": self._total_scheduled,
            "total_cancelled": self._total_cancelled,
            "total_dispatched": self._total_dispatched,
        }


# ============================================================================
# 3. HEAVY HITTERS STREAMING DETECTOR (BOUNDED TOP-K MIN-HEAP)
# ============================================================================

class HeavyHittersDetector:
    """
    Tracks Top-K heavy hitters (e.g. highest request volume clients or IP addresses)
    over a streaming sliding window using a Bounded Min-Heap.
    """

    def __init__(self, top_k: int) -> None:
        self.k = top_k
        self._frequencies: Dict[str, int] = defaultdict(int)

    def record_hit(self, client_id: str, count: int = 1) -> None:
        """Ingests an event in O(1) time."""
        self._frequencies[client_id] += count

    def get_top_k(self) -> List[Tuple[str, int]]:
        """
        Returns the Top-K heavy hitters in O(M log K) time and O(K) memory
        where M is the number of unique clients.
        """
        # Min-heap stores: (frequency, client_id)
        min_heap: List[Tuple[int, str]] = []

        for client_id, freq in self._frequencies.items():
            if len(min_heap) < self.k:
                heapq.heappush(min_heap, (freq, client_id))
            elif freq > min_heap[0][0]:
                heapq.heappushpop(min_heap, (freq, client_id))

        # Return sorted descending
        result = []
        while min_heap:
            freq, client_id = heapq.heappop(min_heap)
            result.append((client_id, freq))

        return result[::-1]


# ============================================================================
# 4. VERIFICATION SUITE & PRODUCTION SIMULATION
# ============================================================================

def run_tests() -> None:
    """Validates priority ordering, delayed execution, cancellation, and Top-K."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR PRODUCTION HEAP ARCHITECTURES")
    print("======================================================================")

    # 1. Priority Ordering & FIFO Tie-Breaking
    scheduler = ProductionPriorityScheduler()

    t_low = ScheduledTask("t_low", "Generate Monthly PDF", priority=TaskPriority.LOW)
    t_norm1 = ScheduledTask("t_norm1", "Process Webhook A", priority=TaskPriority.NORMAL)
    t_norm2 = ScheduledTask("t_norm2", "Process Webhook B", priority=TaskPriority.NORMAL)
    t_crit = ScheduledTask("t_crit", "Process Payment Gateway", priority=TaskPriority.CRITICAL)

    scheduler.schedule(t_low)
    scheduler.schedule(t_norm1)
    scheduler.schedule(t_norm2)
    scheduler.schedule(t_crit)

    # Critical task must pop first
    p1 = scheduler.pop_ready_task()
    assert p1 and p1.task_id == "t_crit", f"Expected t_crit, got {p1}"

    # Normal tasks pop next in strict FIFO tie-breaker order (t_norm1 before t_norm2)
    p2 = scheduler.pop_ready_task()
    assert p2 and p2.task_id == "t_norm1", f"Expected t_norm1, got {p2}"
    p3 = scheduler.pop_ready_task()
    assert p3 and p3.task_id == "t_norm2", f"Expected t_norm2, got {p3}"

    # Low task pops last
    p4 = scheduler.pop_ready_task()
    assert p4 and p4.task_id == "t_low", f"Expected t_low, got {p4}"
    assert scheduler.pop_ready_task() is None
    print("[PASS] Priority dispatching and FIFO tie-breaking verified.")

    # 2. Delayed Execution Barrier
    now = time.time()
    t_delayed = ScheduledTask("t_delayed", "Scheduled Cleanup", priority=TaskPriority.CRITICAL)
    scheduler.schedule(t_delayed, delay_seconds=10.0)

    # Should not pop immediately
    assert scheduler.pop_ready_task(current_time=now) is None
    # Should pop when virtual time advances past delay
    popped_delayed = scheduler.pop_ready_task(current_time=now + 10.1)
    assert popped_delayed and popped_delayed.task_id == "t_delayed"
    print("[PASS] Delayed execution scheduling verified.")

    # 3. Tombstone Cancellation
    t_cancel = ScheduledTask("t_cancel", "Will be aborted", priority=TaskPriority.CRITICAL)
    t_stay = ScheduledTask("t_stay", "Will execute", priority=TaskPriority.HIGH)
    scheduler.schedule(t_cancel)
    scheduler.schedule(t_stay)

    assert scheduler.cancel("t_cancel") is True
    assert scheduler.cancel("t_cancel") is False  # Second cancel returns False

    popped = scheduler.pop_ready_task()
    assert popped and popped.task_id == "t_stay"  # t_cancel safely purged!
    assert scheduler.pop_ready_task() is None
    print("[PASS] Tombstone lazy deletion verified.")

    # 4. Heavy Hitters
    hh = HeavyHittersDetector(top_k=2)
    hh.record_hit("client_alpha", 50)
    hh.record_hit("client_beta", 120)
    hh.record_hit("client_gamma", 80)
    hh.record_hit("client_delta", 10)

    top_2 = hh.get_top_k()
    assert top_2 == [("client_beta", 120), ("client_gamma", 80)]
    print("[PASS] HeavyHittersDetector bounded Min-Heap verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks scheduling and dispatching 50,000 tasks with cancellations."""
    print("======================================================================")
    print("BENCHMARK: 50,000 PRIORITY TASKS SCHEDULING & DISPATCHING")
    print("======================================================================")

    scheduler = ProductionPriorityScheduler()
    total_tasks = 50_000

    start_sched = time.perf_counter()
    for i in range(total_tasks):
        prio = TaskPriority(i % 4)
        task = ScheduledTask(f"job_{i:06d}", f"Compute Payload {i}", priority=prio)
        scheduler.schedule(task)

    sched_time = time.perf_counter() - start_sched

    # Cancel 5,000 random tasks
    for i in range(0, total_tasks, 10):
        scheduler.cancel(f"job_{i:06d}")

    start_dispatch = time.perf_counter()
    dispatched_count = 0
    while True:
        task = scheduler.pop_ready_task()
        if not task:
            break
        dispatched_count += 1
    dispatch_time = time.perf_counter() - start_dispatch

    print(f"Scheduled {total_tasks:,} tasks in:           {sched_time * 1000:8.2f} ms")
    print(f"Scheduling Rate:                     {total_tasks / sched_time:10,.0f} tasks/sec")
    print(f"Dispatched {dispatched_count:,} tasks in:          {dispatch_time * 1000:8.2f} ms")
    print(f"Dispatching Rate:                    {dispatched_count / dispatch_time:10,.0f} tasks/sec")
    print(f"Average Dispatch Latency:            {(dispatch_time / dispatched_count) * 1_000_000:8.4f} microseconds/task")
    print("\nTelemetry Report:")
    for k, v in scheduler.telemetry.items():
        print(f"  {k:18s}: {v}")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
