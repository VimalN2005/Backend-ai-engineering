# Day 13: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **Cooperative Multitasking:**
  - `asyncio` runs on a single OS thread.
  - Tasks yield CPU control cooperatively when reaching `await`.
  - Memory footprint: ~few hundred bytes per coroutine vs ~8 KB–1 MB per OS thread. Supports 50,000+ tasks easily.

- ⚡ **The Golden Rule: Never Block the Event Loop:**
  - Running synchronous blocking calls (`time.sleep()`, synchronous DB drivers, `requests.get()`) freezes the single-threaded event loop for all concurrent connections.
  - Offload legacy blocking calls to worker threads via `await asyncio.to_thread(func, *args)`.

- ⚡ **Structured Concurrency with `TaskGroup` (Python 3.11+):**
  - Always prefer `async with asyncio.TaskGroup() as tg:` over legacy `asyncio.gather()`.
  - If any task fails, `TaskGroup` automatically cancels all other sibling tasks, eliminating orphaned zombie tasks.

- ⚡ **Cancellation & `CancelledError`:**
  - `task.cancel()` injects `asyncio.CancelledError` at the coroutine's suspension point.
  - Always clean up resources in `finally` and **re-raise** `CancelledError`. Never swallow it blindly!

- ⚡ **Modern Timeouts (`asyncio.timeout`):**
  - Use `async with asyncio.timeout(seconds):` instead of legacy `asyncio.wait_for`.
  - Composable, clean, and raises standard `TimeoutError`.

- ⚡ **Concurrency Throttling with `Semaphore`:**
  - Wrap upstream API calls in `async with semaphore:` to bound concurrent in-flight requests and prevent HTTP 429 rate-limiting errors.

- ⚡ **The Disappearing Task Bug:**
  - `asyncio.create_task()` holds only weak references in the event loop.
  - Always store active background tasks in a strong reference container (e.g. `set`) to prevent mid-flight garbage collection.

- ⚡ **Async Synchronization:**
  - `asyncio.Lock` does not block the thread; it suspends only the requesting task, leaving the event loop free to run other tasks.
  - Race conditions can occur across `await` points—always protect shared state with `asyncio.Lock`.
