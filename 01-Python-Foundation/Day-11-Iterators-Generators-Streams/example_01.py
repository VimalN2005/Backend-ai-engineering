"""Day 11: Core Iterator Protocols, Generators, `yield from`, and Bidirectional Coroutines.

This module demonstrates the mechanics of Python's streaming primitives:
1. The Dual Iterator Protocol (__iter__ and __next__).
2. Generator Frame Suspension & Heap Allocation.
3. Subgenerator Delegation & Return Value Capture (yield from).
4. Bidirectional Coroutines with .send() and Priming.
5. High-Performance Itertool Combinators (islice, chain).
6. Resource Teardown Lifecycle (.close() and GeneratorExit).
"""

import itertools
import sys
from typing import Generator, Iterable, Iterator, List, Tuple


# =====================================================================
# 1. THE DUAL ITERATOR PROTOCOL (__iter__ AND __next__)
# =====================================================================
class TokenBatchIterator:
    """Explicit class-based Iterator implementing the Python iteration contract.

    WHY?
    An Iterable must define __iter__() returning an Iterator.
    An Iterator must define __next__() to yield items and raise StopIteration,
    AND define __iter__() returning self so it can be consumed in for-loops.
    """

    def __init__(self, tokens: List[str], batch_size: int = 3) -> None:
        self._tokens = tokens
        self._batch_size = batch_size
        self._cursor = 0

    def __iter__(self) -> Iterator[List[str]]:
        # An iterator is its own iterable
        return self

    def __next__(self) -> List[str]:
        if self._cursor >= len(self._tokens):
            # Exhaustion signal: tells Python to terminate iteration
            raise StopIteration

        batch = self._tokens[self._cursor : self._cursor + self._batch_size]
        self._cursor += self._batch_size
        return batch


# =====================================================================
# 2. SUBGENERATOR DELEGATION & RETURN VALUES (yield from)
# =====================================================================
def subgenerator_worker(worker_id: str, count: int) -> Generator[str, None, int]:
    """Subgenerator that produces chunks and returns a summary token total.

    WHY yield from?
    Prior to Python 3.3, chaining subgenerators required boilerplate for-loops.
    yield from establishes a direct, bidirectional, zero-overhead C-level pipe
    between the outer caller and this worker, AND automatically passes through
    the worker's 'return' value via StopIteration.value.
    """
    total_chars = 0
    for i in range(1, count + 1):
        chunk = f"[{worker_id}_token_{i}]"
        total_chars += len(chunk)
        yield chunk

    # The return value is carried inside StopIteration(total_chars)
    return total_chars


def delegating_pipeline() -> Generator[str, None, Tuple[int, int]]:
    """Master generator delegating to multiple subgenerators via yield from."""
    # Delegate to worker Alpha
    chars_a = yield from subgenerator_worker("Alpha", count=3)

    # Delegate to worker Beta
    chars_b = yield from subgenerator_worker("Beta", count=2)

    return chars_a, chars_b


# =====================================================================
# 3. BIDIRECTIONAL COROUTINE COMMUNICATION (.send)
# =====================================================================
def running_latency_tracker() -> Generator[float, float, None]:
    """Stateful coroutine computing moving averages of API latencies.

    WHY .send()?
    Generators are not just producers; they can also be consumers.
    val = yield current_average suspends the frame, yields current_average to caller,
    and assigns whatever value caller passes into .send(val) to 'val'.
    """
    total = 0.0
    count = 0
    average = 0.0

    while True:
        # Pause and receive the next measurement from caller
        measurement = yield average
        total += measurement
        count += 1
        average = total / count


# =====================================================================
# 4. RESOURCE TEARDOWN LIFECYCLE (GeneratorExit & .close())
# =====================================================================
def managed_database_cursor() -> Generator[str, None, None]:
    """Generator with deterministic resource cleanup via try...finally.

    WHY try...finally?
    When an iteration terminates early (e.g. via break or gen.close()),
    CPython injects a GeneratorExit exception into the suspended frame.
    Wrapping iteration in try...finally guarantees that database connections
    or file descriptors are released cleanly without leaking.
    """
    print("  [RESOURCE] Acquiring virtual database cursor connection...")
    try:
        records = ["user_101", "user_102", "user_103", "user_104", "user_105"]
        for r in records:
            yield r
    finally:
        print("  [RESOURCE] CLEANUP: Virtual cursor released and socket closed.")


# =====================================================================
# DEMONSTRATION RUNNER
# =====================================================================
def run_demonstrations() -> None:
    print("=" * 65)
    print("1. CLASS-BASED ITERATOR PROTOCOL")
    print("=" * 65)
    sample_tokens = ["attention", "is", "all", "you", "need", "transformer", "architecture"]
    batch_iter = TokenBatchIterator(sample_tokens, batch_size=3)

    print(f"[*] Iterating through TokenBatchIterator:")
    for b in batch_iter:
        print(f"    Batch: {b}")

    print("\n" + "=" * 65)
    print("2. MEMORY PROFILING: LIST VS GENERATOR EXPRESSION")
    print("=" * 65)
    # Eager list allocates all elements immediately
    eager_list = [x for x in range(1_000_000)]
    # Lazy generator allocates only a single PyGenObject
    lazy_gen = (x for x in range(1_000_000))

    print(f"[*] Memory of eager list (1,000,000 ints) : {sys.getsizeof(eager_list):,} bytes")
    print(f"[*] Memory of lazy generator expression   : {sys.getsizeof(lazy_gen):,} bytes")
    print(f"[*] Instant Memory Reduction              : ~{((sys.getsizeof(eager_list) - sys.getsizeof(lazy_gen)) / sys.getsizeof(eager_list)) * 100:.2f}%")

    print("\n" + "=" * 65)
    print("3. SUBGENERATOR DELEGATION (yield from) & RETURN CAPTURE")
    print("=" * 65)
    pipeline = delegating_pipeline()
    streamed_items = []

    try:
        while True:
            item = next(pipeline)
            streamed_items.append(item)
    except StopIteration as stop_signal:
        # The return value is carried inside the StopIteration exception!
        alpha_chars, beta_chars = stop_signal.value
        print(f"[*] Streamed items via yield from : {streamed_items}")
        print(f"[*] Captured Worker Return Values : Alpha Chars={alpha_chars}, Beta Chars={beta_chars}")

    print("\n" + "=" * 65)
    print("4. BIDIRECTIONAL COROUTINE (.send()) & PRIMING")
    print("=" * 65)
    tracker = running_latency_tracker()

    # Priming step: must call next() or .send(None) to advance to first yield
    initial_avg = next(tracker)
    print(f"[*] Primed coroutine. Initial average: {initial_avg}ms")

    # Send values into the running coroutine
    latencies = [12.5, 18.2, 11.0, 14.3]
    for lat in latencies:
        new_avg = tracker.send(lat)
        print(f"    Sent latency: {lat:4.1f}ms -> Updated Moving Average: {new_avg:5.2f}ms")

    print("\n" + "=" * 65)
    print("5. ITERTOOLS COMBINATORS (islice & chain)")
    print("=" * 65)
    stream_a = (f"doc_{i}" for i in range(1, 4))
    stream_b = (f"doc_{i}" for i in range(4, 7))

    # itertools.chain merges streams without allocating intermediate lists
    chained_stream = itertools.chain(stream_a, stream_b)

    # itertools.islice slices an infinite or large generator in O(1) memory
    paged_slice = list(itertools.islice(chained_stream, 1, 5))
    print(f"[*] Zero-memory sliced stream [1:5]: {paged_slice}")

    print("\n" + "=" * 65)
    print("6. GENERATOR TEARDOWN & RESOURCE SAFETY (.close())")
    print("=" * 65)
    db_stream = managed_database_cursor()
    first_record = next(db_stream)
    second_record = next(db_stream)
    print(f"[*] Consumed 2 records: {first_record}, {second_record}")
    print("[*] Terminating generator early via .close():")
    db_stream.close()  # Injects GeneratorExit into the frame, executing finally block


if __name__ == "__main__":
    run_demonstrations()
