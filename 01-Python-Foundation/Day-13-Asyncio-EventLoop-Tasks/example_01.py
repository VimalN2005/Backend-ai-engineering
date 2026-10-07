"""Day 13: Core Asyncio Primitives, Structured Concurrency, and Protocols.

This module demonstrates:
1. Coroutine suspension vs Task scheduling with asyncio.create_task.
2. Modern Structured Concurrency via asyncio.TaskGroup (Python 3.11+).
3. Concurrency bounding via asyncio.Semaphore.
4. Async Context Manager protocol (__aenter__ and __aexit__).
5. Async Iterator protocol (__aiter__ and __anext__).
6. Offloading blocking legacy code via asyncio.to_thread.
7. Retaining strong references to prevent garbage collection of background tasks.
"""

import asyncio
import time
from typing import AsyncIterator, List, Set


# =====================================================================
# 1. ASYNC CONTEXT MANAGER (Safe Resource Teardown)
# =====================================================================
class AsyncDatabasePool:
    """Async context manager managing a virtual connection pool."""

    async def __aenter__(self) -> "AsyncDatabasePool":
        # Simulates non-blocking socket handshake
        await asyncio.sleep(0.02)
        print("  [DB POOL] Connection pool initialized asynchronously.")
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        # Guaranteed cleanup: drains connection sockets
        await asyncio.sleep(0.01)
        print("  [DB POOL] Connection pool drained and closed.")

    async def query(self, sql: str) -> str:
        await asyncio.sleep(0.02)
        return f"Result for: {sql}"


# =====================================================================
# 2. ASYNC ITERATOR (Streaming Chunks)
# =====================================================================
class AsyncTokenStream:
    """Async iterator protocol yielding tokens with simulated network latency."""

    def __init__(self, text: str) -> None:
        self._tokens = text.split()
        self._cursor = 0

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._cursor >= len(self._tokens):
            raise StopAsyncIteration

        # Simulate network packet delay
        await asyncio.sleep(0.01)
        token = self._tokens[self._cursor]
        self._cursor += 1
        return token


# =====================================================================
# 3. BLOCKING CODE OFFLOADING (asyncio.to_thread)
# =====================================================================
def blocking_cpu_hash(data: str) -> str:
    """Simulates a legacy synchronous CPU-heavy or blocking I/O operation.

    WHY asyncio.to_thread?
    Running this directly inside an async function would freeze the single-threaded
    event loop, causing all other concurrent network requests to stall.
    asyncio.to_thread executes it in a separate thread pool and yields control.
    """
    time.sleep(0.05)  # Synchronous blocking sleep!
    return f"HASH_{hash(data)}"


# =====================================================================
# DEMONSTRATION WORKERS
# =====================================================================
async def simulated_api_call(service_name: str, delay: float) -> str:
    await asyncio.sleep(delay)
    return f"Response from {service_name} after {delay}s"


# Global set to retain strong references to background tasks
ACTIVE_BACKGROUND_TASKS: Set[asyncio.Task] = set()


async def background_telemetry_audit(event: str) -> None:
    """Background task requiring a strong reference to prevent GC deletion."""
    await asyncio.sleep(0.03)
    print(f"  [AUDIT] Background event persisted: '{event}'")


# =====================================================================
# MAIN RUNNER
# =====================================================================
async def main() -> None:
    print("=" * 65)
    print("1. STRUCTURED CONCURRENCY WITH asyncio.TaskGroup (Python 3.11+)")
    print("=" * 65)

    start_time = time.perf_counter()

    # WHY TaskGroup over asyncio.gather?
    # TaskGroup guarantees that if any task fails, all sibling tasks are
    # automatically cancelled. Tasks cannot leak as orphaned zombies!
    async with asyncio.TaskGroup() as tg:
        task_openai = tg.create_task(simulated_api_call("OpenAI", 0.04))
        task_gemini = tg.create_task(simulated_api_call("Gemini", 0.06))
        task_anthropic = tg.create_task(simulated_api_call("Anthropic", 0.05))

    elapsed = time.perf_counter() - start_time
    print(f"[*] TaskGroup completed all 3 API calls in {elapsed:.3f}s (Concurrent!)")
    print(f"    - OpenAI Result   : {task_openai.result()}")
    print(f"    - Gemini Result   : {task_gemini.result()}")
    print(f"    - Anthropic Result: {task_anthropic.result()}")

    print("\n" + "=" * 65)
    print("2. BOUNDED CONCURRENCY VIA asyncio.Semaphore")
    print("=" * 65)

    # Allow at most 2 concurrent requests
    rate_limiter = asyncio.Semaphore(2)

    async def throttled_request(req_id: int) -> int:
        async with rate_limiter:
            print(f"    [SLOT ACQUIRED] Request #{req_id} entering critical section...")
            await asyncio.sleep(0.03)
            print(f"    [SLOT RELEASED] Request #{req_id} exiting.")
            return req_id

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(throttled_request(i)) for i in range(1, 5)]

    print("\n" + "=" * 65)
    print("3. TIMEOUT HANDLING VIA asyncio.timeout (Python 3.11+)")
    print("=" * 65)

    try:
        # Enforce a 0.03s hard deadline on an operation that takes 0.10s
        async with asyncio.timeout(0.03):
            await simulated_api_call("SlowDatabase", 0.10)
    except TimeoutError:
        print("[*] Successfully caught TimeoutError: SlowDatabase call exceeded deadline!")

    print("\n" + "=" * 65)
    print("4. ASYNC CONTEXT MANAGER & ASYNC ITERATOR PROTOCOLS")
    print("=" * 65)

    async with AsyncDatabasePool() as pool:
        query_result = await pool.query("SELECT * FROM documents LIMIT 5")
        print(f"[*] Query Output: {query_result}")

    # Consume Async Iterator
    stream = AsyncTokenStream("Attention is all you need for transformers")
    print("[*] Consuming AsyncTokenStream tokens:")
    tokens_collected = []
    async for token in stream:
        tokens_collected.append(token)
    print(f"    Streamed Tokens: {' -> '.join(tokens_collected)}")

    print("\n" + "=" * 65)
    print("5. OFFLOADING BLOCKING SYNC CODE (asyncio.to_thread)")
    print("=" * 65)

    start_thread = time.perf_counter()
    # Runs the synchronous function in the default ThreadPoolExecutor
    hashed_val = await asyncio.to_thread(blocking_cpu_hash, "sensitive_document_payload")
    print(f"[*] Offloaded synchronous hash in {time.perf_counter() - start_thread:.3f}s: {hashed_val}")

    print("\n" + "=" * 65)
    print("6. STRONG REFERENCE RETENTION FOR BACKGROUND TASKS")
    print("=" * 65)

    # Retain reference in a global set to prevent garbage collection
    bg_task = asyncio.create_task(background_telemetry_audit("user_login_event"))
    ACTIVE_BACKGROUND_TASKS.add(bg_task)
    bg_task.add_done_callback(ACTIVE_BACKGROUND_TASKS.discard)

    # Await background completion before script exits
    await bg_task
    print("[*] All demonstrations completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
