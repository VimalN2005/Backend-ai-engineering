# Day 12: Practical Hands-On Exercises

Master Python's threading primitives, GIL mechanics, multiprocessing architectures, and executor pools by solving these 3 production challenges.

---

### Exercise 1: Thread-Safe Multi-Tenant LRU Cache with `RLock` (Medium)
Build a thread-safe in-memory cache `ThreadSafeCache[K, V]` that:
1. Stores values associated with tenant keys: `(tenant_id, key)`.
2. Uses `threading.RLock` to protect all internal dictionary operations.
3. Implements reentrant methods:
   - `get(tenant_id: str, key: str) -> Optional[V]`
   - `set(tenant_id: str, key: str, value: V, ttl_seconds: float = 60.0) -> None`
   - `get_or_set(tenant_id: str, key: str, default_factory: Callable[[], V]) -> V`: Calls `get()` first; if missing, calls `set()` using `default_factory()`. Must not deadlock despite nested lock calls!
4. Launches 20 concurrent threads reading and writing to the cache simultaneously to verify zero race conditions or deadlocks.

---

### Exercise 2: Parallel Document Tokenizer via `ProcessPoolExecutor` (Medium)
Build a batch document tokenizer `parallel_tokenize_corpus(documents: list[str], max_workers: int = 4) -> list[dict]`:
1. Distributes a list of 1,000 raw documents across worker processes using `ProcessPoolExecutor`.
2. In each worker:
   - Strips HTML tags using regular expressions.
   - Computes unique word vocabulary count.
   - Returns a structured dictionary: `{"doc_index": int, "token_count": int, "unique_words": int}`.
3. Uses `as_completed()` to stream results back as workers finish, displaying progress with completion percentage.
4. Ensures proper entry point protection (`if __name__ == "__main__":`).

---

### Exercise 3: Resilient Multi-Process Task Queue with Poison Pill Teardown (Advanced)
Build a multi-process worker pool architecture using raw `multiprocessing.Queue`:
1. Master Process:
   - Creates a task queue `multiprocessing.Queue()` and a result queue `multiprocessing.Queue()`.
   - Spawns $N$ worker processes running a target function `worker_loop`.
   - Enqueues 100 computationally heavy tasks (e.g. SHA-256 brute-force puzzles).
2. Worker Processes:
   - Pull tasks from the task queue, compute the result, and push to the result queue.
   - Graceful Shutdown: When a worker receives a sentinel value (`None`, the "Poison Pill"), it terminates its loop and exits cleanly.
3. Master collects all 100 results, enqueues $N$ poison pills to shut down all workers, calls `.join()` on all child processes, and logs performance metrics.
