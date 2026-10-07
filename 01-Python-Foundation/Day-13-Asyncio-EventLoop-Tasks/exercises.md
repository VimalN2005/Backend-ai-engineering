# Day 13: Practical Hands-On Exercises

Master Python's event loop, structured concurrency with `TaskGroup`, async context managers, and high-concurrency throttling by solving these 3 production challenges.

---

### Exercise 1: Multi-Source Vector Search Aggregator with `TaskGroup` (Medium)
Build an asynchronous vector search aggregator `aggregate_vector_search(query_vector: list[float], top_k: int = 5) -> list[dict]`:
1. Queries three distinct vector backends simultaneously:
   - `search_qdrant(vector)` (takes ~40ms).
   - `search_pinecone(vector)` (takes ~60ms).
   - `search_pgvector(vector)` (takes ~50ms).
2. Uses `asyncio.TaskGroup` to execute all three searches concurrently.
3. Wraps the entire aggregation in an `async with asyncio.timeout(0.08)` to enforce an 80ms total latency ceiling.
4. Deduplicates hits across all sources by `doc_id`, sorts by descending `similarity_score`, and returns the top $K$ results.

---

### Exercise 2: Rate-Limited Async Token Bucket Pipeline (Medium)
Build an asynchronous token-bucket rate limiter `AsyncTokenBucket`:
1. Attributes: `capacity: int`, `refill_rate_per_sec: float`.
2. Implements an async context manager interface:
   ```python
   async with rate_limiter:
       # Block until a token is available
       await call_upstream_llm()
   ```
3. Uses `asyncio.Lock` to ensure task-safe token accounting.
4. If tokens are exhausted, coroutines wait asynchronously via `await asyncio.sleep()` until enough tokens have refilled, without blocking the event loop.
5. Test by launching 30 concurrent tasks to verify that requests are smoothly throttled to the configured rate.

---

### Exercise 3: Resilient WebSocket Connection Manager with Async Context Manager (Advanced)
Build a resilient client connection manager `ResilientAsyncConnection`:
1. Implements the Async Context Manager protocol:
   - `__aenter__`: Connects to a simulated remote WebSocket endpoint.
   - `__aexit__`: Gracefully closes the connection, handling unhandled exceptions.
2. Implements an exponential backoff retry mechanism (max 3 retries) with randomized jitter if the initial connection fails.
3. Implements an async generator `stream_messages(heartbeat_interval: float = 1.0) -> AsyncGenerator[str, None]` that yields incoming messages while running a concurrent background task sending heartbeat pings.
4. Demonstrates graceful teardown and socket closure when cancelled.
