# Day 13: Asyncio: Event Loop, Coroutines, Tasks & Async Context Managers

In high-scale enterprise backend engineering and production AI systems, asynchronous programming with `asyncio` is the backbone of modern web frameworks (FastAPI), real-time streaming engines (WebSockets, SSE), and AI gateway proxies. While multi-threading manages concurrency through the operating system kernel, `asyncio` achieves massive concurrency within a single OS thread through **cooperative multitasking**.

---

## 1. The Paradigm Shift: Cooperative vs. Preemptive Concurrency

```
PREEMPTIVE MULTITASKING (OS Threads)
Thread 1: [ Runs ] ──(OS Interrupts)──> [ Suspended ]
Thread 2:                               [ Runs ] ──(OS Interrupts)──> [ Suspended ]
* OS scheduler forcibly switches context at arbitrary bytecode instructions.
* High memory overhead (~8 KB - 1 MB per thread stack). Maximum ~1,000-5,000 threads.

COOPERATIVE MULTITASKING (Asyncio Event Loop)
Task 1:   [ Runs ] ──(awaits I/O)──────> [ Paused ] ─────────────────────────> [ Resumes ]
Task 2:                                  [ Runs ] ──(awaits I/O)──> [ Paused ]
* Tasks voluntarily yield control via `await` when waiting for I/O.
* Ultra-low memory overhead (~few hundred bytes per coroutine). Supports 50,000+ tasks easily!
```

---

## 2. The Asyncio Architecture & Event Loop Mechanics

The **Event Loop** is the central orchestrator of an asynchronous Python program:

```
+-------------------------------------------------------------------------+
|                           Asyncio Event Loop                            |
|                                                                         |
|   +-----------------------+              +--------------------------+   |
|   |      Ready Queue      |              |    OS Poller / Selector  |   |
|   |  (Tasks ready to run) |              | (epoll / kqueue / IOCP)  |   |
|   |  [Task A] -> [Task B] |              |  Waiting for I/O Sockets |   |
|   +-----------------------+              +--------------------------+   |
|               │                                       │                 |
|               ▼ Executes on single thread             ▼ Socket Ready    |
|   [ CPU Core (Thread 0) ] ───────────────> Moves Task to Ready Queue    |
+-------------------------------------------------------------------------+
```

1. **Single-Threaded**: The event loop runs in a single thread, eliminating multi-threading race conditions on standard synchronous bytecode.
2. **Kernel I/O Multiplexing**: When a coroutine waits for a socket (HTTP response, database query), the event loop registers the socket handle with the OS multiplexer (`epoll` on Linux, `kqueue` on macOS, `IOCP` via `ProactorEventLoop` on Windows).
3. **Non-Blocking Execution**: While the socket waits for data across the network, the event loop runs other tasks in the ready queue. When data arrives, the OS notifies the event loop, which wakes the suspended coroutine.

---

## 3. Coroutines, Awaitables, and `await` Desugaring

In Python, three primary objects are **awaitable**:
1. **Coroutines**: Functions defined with `async def`. Calling them returns a coroutine object.
2. **Tasks**: Concrete scheduled units of work wrapping a coroutine.
3. **Futures**: Low-level objects representing an eventual result from an asynchronous operation.

### The `await` Mechanics:
```python
result = await fetch_data()
```
When `await` is executed:
- The calling coroutine **suspends its execution frame**.
- Control is yielded back to the event loop.
- The event loop executes other ready tasks.
- When `fetch_data()` completes, the event loop resumes the caller, assigning the return value to `result`.

---

## 4. Coroutines vs. Tasks (`asyncio.create_task`)

- **Calling a coroutine directly (`coro()`)**: Creates an idle coroutine object. **It does not run until awaited!**
- **Scheduling with `asyncio.create_task(coro())`**: Wraps the coroutine in an `asyncio.Task` and immediately registers it on the event loop's ready queue to run concurrently in the background.

```python
# Sequential execution (Total time = 2.0s):
await step_one()  # Takes 1.0s
await step_two()  # Takes 1.0s

# Concurrent execution via Tasks (Total time = ~1.0s):
task_1 = asyncio.create_task(step_one())
task_2 = asyncio.create_task(step_two())
await task_1
await task_2
```

---

## 5. The Cardinal Sin: Blocking the Event Loop

Because `asyncio` is single-threaded, **any synchronous blocking operation blocks the entire process and starves all other tasks**:

```python
# ❌ FATAL DISASTER: Blocks all 10,000 concurrent user requests for 2 seconds!
async def handle_request():
    time.sleep(2.0)            # Synchronous blocking call!
    data = requests.get(url)   # Synchronous blocking network call!

# ✅ PRODUCTION PATTERN: Use asynchronous non-blocking equivalents
async def handle_request():
    await asyncio.sleep(2.0)   # Non-blocking: yields to event loop
    async with httpx.AsyncClient() as client:
        data = await client.get(url)
```

---

## 6. Structured Concurrency: `asyncio.TaskGroup` (Python 3.11+)

In older Python code, concurrent tasks were joined using `asyncio.gather()`:
```python
# Legacy Pattern: Fragile error handling
results = await asyncio.gather(task1, task2, return_exceptions=True)
```
**The Flaw in `gather`**: If `task1` crashes, `task2` continues running as an orphaned, unmanaged "ghost task," leaking memory and connections.

### Modern Structured Concurrency with `asyncio.TaskGroup`:
Introduced in Python 3.11, `TaskGroup` guarantees that **either all child tasks complete successfully, or if any task raises an exception, all sibling tasks are automatically cancelled**:

```python
async def run_pipeline():
    async with asyncio.TaskGroup() as tg:
        t1 = tg.create_task(fetch_user_metadata(user_id))
        t2 = tg.create_task(fetch_vector_embeddings(user_id))
    # Both tasks are guaranteed completed or cancelled when exiting context!
    print(t1.result(), t2.result())
```
If an error occurs, `TaskGroup` bundles all exceptions into an `ExceptionGroup`.

---

## 7. Cancellation & `asyncio.CancelledError`

Any running task can be cancelled via `task.cancel()`:
- The event loop injects an `asyncio.CancelledError` into the coroutine at its current `await` suspension point.
- **Critical Rule**: Always allow `CancelledError` to propagate! Never catch `except Exception:` and swallow it without re-raising, or tasks cannot be cancelled cleanly during server shutdown.

```python
async def resilient_worker():
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        # Perform clean resource teardown
        await close_database_connection()
        raise  # Must re-raise CancelledError!
```

---

## 8. Modern Timeouts: `asyncio.timeout` (Python 3.11+)

Prior to Python 3.11, timeouts used `asyncio.wait_for()`, which created extra task wrapping overhead.
Modern Python uses **`async with asyncio.timeout()`**:

```python
async def query_ai_service():
    try:
        async with asyncio.timeout(3.0):  # 3.0 second hard deadline
            response = await upstream_llm_client.generate(prompt)
            return response
    except TimeoutError:
        logger.warning("Upstream LLM provider timed out after 3.0s")
        return fallback_cached_response()
```

---

## 9. Concurrency Throttling: `asyncio.Semaphore`

When querying upstream AI APIs (OpenAI, Gemini, Anthropic), blasting 5,000 concurrent requests triggers HTTP 429 (Rate Limit Exceeded) errors.
Use **`asyncio.Semaphore`** to bound active concurrency:

```python
rate_limiter = asyncio.Semaphore(10)  # Max 10 concurrent requests

async def call_llm_with_rate_limit(prompt: str):
    async with rate_limiter:
        # At most 10 coroutines can execute inside this block simultaneously
        return await llm_client.complete(prompt)
```

---

## 10. Async Synchronization Primitives

The `asyncio` module provides synchronization primitives tailored for the event loop:

| Primitive | Purpose | Best Use Case |
| :--- | :--- | :--- |
| **`asyncio.Lock`** | Non-blocking mutual exclusion | Protecting shared in-memory caches or token counters. |
| **`asyncio.Semaphore`** | Bounded concurrency slots | Limiting concurrent HTTP/DB connections. |
| **`asyncio.Queue`** | Asynchronous FIFO queue | Producer-Consumer pipelines across worker tasks. |
| **`asyncio.Event`** | Task coordination & signaling | Signaling worker tasks that a cache warmup has completed. |

> **Note**: Unlike `threading.Lock`, `asyncio.Lock` does **not** block the thread. It suspends the requesting coroutine and yields execution to the event loop.

---

## 11. Async Context Managers (`__aenter__` and `__aexit__`)

Async context managers handle acquisition and release of asynchronous resources:

```python
class AsyncDatabaseSession:
    async def __aenter__(self):
        self.conn = await acquire_connection_from_pool()
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.conn.close()
        if exc_type is not None:
            await self.conn.rollback()

# Usage:
async with AsyncDatabaseSession() as session:
    await session.execute("SELECT 1")
```

---

## 12. Async Iterators & Generators

Stream chunks over time without loading whole sequences into RAM:

```python
async def stream_llm_tokens(prompt: str) -> AsyncGenerator[str, None]:
    for token in ["Attention ", "is ", "all ", "you ", "need."]:
        await asyncio.sleep(0.02)  # Simulate network chunk delay
        yield token

# Consuming an Async Generator:
async for token in stream_llm_tokens("Hello"):
    print(token, end="", flush=True)
```

---

## 13. Bridging Sync and Async: `asyncio.to_thread`

When you must call a legacy synchronous blocking library (e.g. `boto3`, PIL image resizing, bcrypt hashing), **never run it directly inside a coroutine**.
Offload it to a background thread pool using `asyncio.to_thread`:

```python
# Runs blocking_sync_function in a background OS thread,
# freeing the event loop to continue serving other requests!
result = await asyncio.to_thread(cpu_or_disk_heavy_sync_function, arg1, arg2)
```

---

## 14. The "Disappearing Task" Garbage Collection Trap

If you create a task with `asyncio.create_task()` without saving a reference to it:
```python
# ❌ FATAL BUG: Task may be garbage collected mid-execution!
asyncio.create_task(background_audit_logger(data))
```
CPython's event loop holds only **weak references** to scheduled tasks. If Python runs garbage collection while the task is suspended, the task object may be destroyed mid-flight!

**Production Rule**: Always retain strong references in a set:
```python
active_background_tasks = set()

task = asyncio.create_task(background_audit_logger(data))
active_background_tasks.add(task)
task.add_done_callback(active_background_tasks.discard)
```

---

## 15. Production Architecture: High-Scale AI Gateway

```
[Client HTTP/SSE Requests] (FastAPI Endpoint)
             │
             ▼
[asyncio.Semaphore (Concurrency Guard: Max 100)]
             │
             ▼
[async with asyncio.timeout(10.0)]
             │
             ▼
[AsyncGenerator: Token Streaming via SSE]
             │
             ▼
[asyncio.TaskGroup] ─── Parallel Metadata Logging / Vector DB Cache Check
```
