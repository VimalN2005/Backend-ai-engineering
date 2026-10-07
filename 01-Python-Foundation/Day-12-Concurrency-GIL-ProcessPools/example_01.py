"""Day 12: Core Concurrency, Race Conditions, GIL Benchmarking, and Executors.

This module demonstrates:
1. Multi-threaded Race Conditions and Resolution with threading.Lock.
2. The Global Interpreter Lock (GIL) Benchmark: Threading vs Multiprocessing for CPU tasks.
3. ThreadPoolExecutor for High-Concurrency I/O operations.
4. ProcessPoolExecutor for True Multi-Core CPU Parallelism.
5. Windows/macOS Safe Process Spawning (if __name__ == "__main__").
"""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import hashlib
import os
import threading
import time
from typing import List, Tuple


# =====================================================================
# 1. RACE CONDITION DEMONSTRATION & SYNCHRONIZATION VIA LOCK
# =====================================================================
class UnsafeCounter:
    """Demonstrates that the GIL does NOT protect application state."""

    def __init__(self) -> None:
        self.value = 0

    def increment(self) -> None:
        # WHY a race condition occurs here?
        # self.value += 1 is not atomic in Python bytecode.
        # It expands to LOAD_FAST, LOAD_CONST, BINARY_OP, STORE_FAST.
        # Threads switch context mid-operation, overwriting each other's updates.
        current = self.value
        time.sleep(0.00001)  # Artificially trigger CPython thread switch
        self.value = current + 1


class SafeCounter:
    """Thread-safe counter protected by a mutual exclusion lock."""

    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        # with self._lock guarantees mutual exclusion and deterministic release
        with self._lock:
            current = self.value
            time.sleep(0.00001)
            self.value = current + 1


def demonstrate_race_conditions() -> None:
    print("=" * 65)
    print("1. RACE CONDITION DEMONSTRATION (GIL DOES NOT ENSURE THREAD SAFETY)")
    print("=" * 65)

    NUM_THREADS = 10
    INCREMENTS_PER_THREAD = 50
    EXPECTED_TOTAL = NUM_THREADS * INCREMENTS_PER_THREAD

    # A. Unsynchronized Execution
    unsafe_counter = UnsafeCounter()
    threads: List[threading.Thread] = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(
            target=lambda: [unsafe_counter.increment() for _ in range(INCREMENTS_PER_THREAD)]
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    print(f"[*] Unsafe Counter: Expected={EXPECTED_TOTAL} | Actual={unsafe_counter.value}")
    print(f"    -> Lost Updates Detected: {EXPECTED_TOTAL - unsafe_counter.value > 0}")

    # B. Synchronized Execution
    safe_counter = SafeCounter()
    threads = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(
            target=lambda: [safe_counter.increment() for _ in range(INCREMENTS_PER_THREAD)]
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    print(f"[*] Safe Counter  : Expected={EXPECTED_TOTAL} | Actual={safe_counter.value} (Guaranteed Clean!)")


# =====================================================================
# 2. CPU-BOUND WORKLOAD DEFINITION (FOR GIL BENCHMARKING)
# =====================================================================
def cpu_heavy_hash_task(work_chunk: Tuple[int, int]) -> int:
    """Compute intensive SHA-256 hashes to simulate heavy CPU tokenization/math.

    Must be a top-level function for multiprocessing to pickle it cleanly.
    """
    task_id, iterations = work_chunk
    computed_count = 0
    payload = f"token_payload_seed_{task_id}".encode("utf-8")
    for _ in range(iterations):
        payload = hashlib.sha256(payload).digest()
        computed_count += 1
    return computed_count


# =====================================================================
# 3. I/O-BOUND WORKLOAD (SIMULATING REMOTE LLM API CALLS)
# =====================================================================
def mock_remote_llm_api_call(request_id: int) -> dict:
    """Simulate network latency to an upstream LLM API provider."""
    # When time.sleep() runs, CPython explicitly RELEASES the GIL,
    # allowing all other worker threads to execute concurrently.
    time.sleep(0.05)  # 50ms simulated network latency
    return {
        "request_id": request_id,
        "status": "COMPLETED",
        "tokens": 42 + (request_id % 10),
    }


def demonstrate_io_bound_threadpool() -> None:
    print("\n" + "=" * 65)
    print("2. I/O-BOUND CONCURRENCY VIA THREADPOOLEXECUTOR")
    print("=" * 65)

    TOTAL_REQUESTS = 20
    MAX_WORKERS = 10

    start_time = time.perf_counter()
    results = []

    # ThreadPoolExecutor is optimal for I/O: lightweight OS threads, shared memory
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(mock_remote_llm_api_call, req_id): req_id
            for req_id in range(1, TOTAL_REQUESTS + 1)
        }
        for future in as_completed(future_map):
            results.append(future.result())

    elapsed_sec = time.perf_counter() - start_time
    sequential_estimate = TOTAL_REQUESTS * 0.05

    print(f"[*] Dispatched {TOTAL_REQUESTS} simulated API requests using {MAX_WORKERS} threads.")
    print(f"[*] Total Execution Time : {elapsed_sec:.3f}s (Sequential would take ~{sequential_estimate:.2f}s)")
    print(f"[*] Speedup Factor        : ~{sequential_estimate / elapsed_sec:.1f}x faster via concurrent I/O!")


# =====================================================================
# 4. CPU-BOUND BENCHMARK: THREADS VS PROCESSES (PROVING THE GIL)
# =====================================================================
def run_gil_cpu_benchmark() -> None:
    print("\n" + "=" * 65)
    print("3. THE GIL IN ACTION: THREADS VS PROCESSES ON CPU WORKLOAD")
    print("=" * 65)

    CPU_CORES = os.cpu_count() or 4
    TASK_COUNT = 4
    ITERATIONS_PER_TASK = 150_000
    tasks = [(i, ITERATIONS_PER_TASK) for i in range(TASK_COUNT)]

    print(f"[*] Hardware CPU Cores Available: {CPU_CORES}")
    print(f"[*] Running {TASK_COUNT} CPU-heavy cryptographic hash tasks ({ITERATIONS_PER_TASK:,} iterations each)...")

    # A. Multi-Threaded Execution (Serialized by the GIL)
    start_threads = time.perf_counter()
    with ThreadPoolExecutor(max_workers=TASK_COUNT) as executor:
        thread_results = list(executor.map(cpu_heavy_hash_task, tasks))
    thread_duration = time.perf_counter() - start_threads
    print(f"[*] Multi-Threaded Time : {thread_duration:.3f}s (Blocked by single-core GIL serialization!)")

    # B. Multi-Process Execution (True Multi-Core Parallelism)
    start_processes = time.perf_counter()
    with ProcessPoolExecutor(max_workers=TASK_COUNT) as executor:
        process_results = list(executor.map(cpu_heavy_hash_task, tasks))
    process_duration = time.perf_counter() - start_processes
    print(f"[*] Multi-Process  Time : {process_duration:.3f}s (Parallelized across physical CPU cores!)")

    speedup = thread_duration / process_duration
    print(f"[*] Multiprocessing Speedup: {speedup:.2f}x faster than Threading on multi-core CPU!")


# =====================================================================
# MAIN ENTRY POINT (MANDATORY GUARD FOR SPAWN METHOD)
# =====================================================================
if __name__ == "__main__":
    # WHY if __name__ == "__main__":?
    # On Windows and macOS, multiprocessing defaults to 'spawn'.
    # In 'spawn' mode, Python launches a fresh interpreter and re-imports the main script.
    # Without this guard, the child process would re-execute the process creation code,
    # causing an infinite recursive fork bomb that crashes the OS!
    demonstrate_race_conditions()
    demonstrate_io_bound_threadpool()
    run_gil_cpu_benchmark()
