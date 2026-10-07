# Day 13: Top 5 Technical Interview Questions

---

### Question 1: How does the Asyncio Event Loop work under the hood with OS kernel multiplexers (epoll/kqueue/IOCP)?

#### Expected Answer:
The Asyncio event loop is a single-threaded cooperative multitasking scheduler built on top of OS-level I/O multiplexing primitives:
1. **The Polling Mechanism**:
   When a coroutine initiates a non-blocking I/O operation (e.g., an HTTP GET request or database query), CPython requests a non-blocking socket from the OS. Instead of blocking the thread waiting for bytes, the event loop registers the socket file descriptor with the OS kernel's notification system:
   - Linux: `epoll`
   - macOS / BSD: `kqueue`
   - Windows: `IOCP` (I/O Completion Ports via `ProactorEventLoop`)
2. **The Run Loop**:
   The event loop runs in a continuous cycle:
   - It polls the OS selector with a timeout for ready socket file descriptors (`selector.select(timeout)`).
   - Any tasks whose sockets are ready with data are moved to the internal **Ready Queue**.
   - The event loop executes tasks in the ready queue one by one until each hits an `await` statement and yields control.
   - It repeats the cycle, achieving thousands of concurrent connections on a single OS thread.

---

### Question 2: What is Structured Concurrency, and why was `asyncio.TaskGroup` introduced in Python 3.11 over `asyncio.gather`?

#### Expected Answer:
**Structured Concurrency** is an architectural paradigm where concurrent tasks have clear hierarchical lifespans bound to lexical scope: child tasks cannot outlive the scope that created them.

#### The Flaw of `asyncio.gather`:
When executing `results = await asyncio.gather(t1, t2)`:
- If `t1` raises an unhandled exception, `gather` immediately raises that exception to the caller.
- However, `t2` **continues executing in the background as an orphaned zombie task**!
- This leaks database connections, burns network bandwidth, and causes difficult-to-trace bugs.

#### The `TaskGroup` Guarantee (Python 3.11+):
Using `async with asyncio.TaskGroup() as tg:`:
1. **Clean Scoping**: Exiting the context block waits for all scheduled tasks to complete.
2. **Automatic Sibling Cancellation**: If any task inside the group raises an unhandled exception, `TaskGroup` automatically cancels all other sibling tasks within the group.
3. **Exception Bundling**: Multiple concurrent errors are bundled into an `ExceptionGroup`, ensuring no failure is silently discarded.

---

### Question 3: What happens when a synchronous blocking call (e.g. `time.sleep()` or `requests.get()`) is executed inside a coroutine?

#### Expected Answer:
Because `asyncio` runs on a **single operating system thread**, executing a synchronous blocking call blocks the **entire process**:
- The single thread enters kernel sleep or blocks on synchronous socket I/O.
- The event loop cannot run its evaluation cycle to poll other sockets or advance ready tasks.
- If your server has 10,000 active WebSocket or SSE connections, **all 10,000 connections freeze completely** for the duration of the synchronous blocking call!

#### The Solution:
If you must interact with legacy blocking libraries (such as `boto3`, PIL, or SQLite):
- Offload the call to a background thread pool using `await asyncio.to_thread(blocking_func, *args)`.
- This runs the blocking call in a separate OS worker thread while leaving the main event loop thread completely free to serve incoming asynchronous requests.

---

### Question 4: How does coroutine cancellation work, and why must `asyncio.CancelledError` never be swallowed blindly?

#### Expected Answer:
When `task.cancel()` is called:
1. The event loop marks the task as cancelled.
2. The next time the coroutine pauses or resumes at an `await` expression, the event loop **injects an `asyncio.CancelledError`** directly into the coroutine's execution frame.
3. If the coroutine is inside a `try...finally` block, the `finally` clause executes, allowing cleanup of open sockets and transactions.

#### Why Swallowing It is Dangerous:
If a developer writes:
```python
except BaseException:
    pass  # Swallows CancelledError!
```
- The task ignores the cancellation signal and resumes running.
- When an HTTP client aborts a request, the server continues computing and burning GPU/CPU tokens needlessly.
- During server shutdown (`SIGTERM`), the application will hang indefinitely because the task refuses to terminate.
- **Rule**: Catch `CancelledError` only to perform resource cleanup, and always **re-raise** it!

---

### Question 5: How does `asyncio` differ from multi-threading in terms of memory overhead and concurrency scale? Why does FastAPI use it?

#### Expected Answer:

| Dimension | Multi-Threading (`threading`) | Asynchronous (`asyncio`) |
| :--- | :--- | :--- |
| **Concurrency Model** | Preemptive multitasking (OS kernel preempts threads) | Cooperative multitasking (Tasks yield via `await`) |
| **Memory Footprint** | ~8 KB to 1 MB per thread stack memory | ~few hundred bytes per coroutine object |
| **Maximum Concurrency** | ~1,000 to 5,000 threads (limited by OS kernel and RAM) | 50,000+ coroutines simultaneously on a standard server |
| **Context Switch Cost** | High (CPU registers swapped, kernel mode switches) | Low (User-space frame pointer swap, zero syscalls) |
| **Data Safety** | Race conditions on bytecode instructions; requires Locks | Atomic between `await` points; requires Locks only across `await` |

FastAPI uses `asyncio` because modern web APIs spend 95%+ of their time waiting on network I/O (database queries, Redis cache lookups, upstream LLM API calls). Asyncio allows a single worker process to handle tens of thousands of idle connections with negligible memory overhead.
