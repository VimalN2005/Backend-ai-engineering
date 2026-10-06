# Day 11: Practical Hands-On Exercises

Master Python's iteration protocols, lazy generator pipelines, subgenerator delegation, and memory optimization by solving these 3 production challenges.

---

### Exercise 1: Time- and Size-Bounded Stream Batcher (Medium)
When ingesting live analytics events or LLM completion streams, waiting for a fixed batch size (e.g., 100 items) can introduce unacceptable latency during low-traffic periods.

Implement a generator function `time_and_size_batcher(stream: Iterable[Any], max_batch_size: int = 50, max_latency_seconds: float = 0.5) -> Generator[list[Any], None, None]`:
1. Collects items from `stream`.
2. Yields the accumulated batch immediately if:
   - The batch size reaches `max_batch_size`, OR
   - The time elapsed since the first item in the batch exceeds `max_latency_seconds`.
3. Yields any trailing un-flushed items upon stream exhaustion.
4. Operates in strictly $\mathcal{O}(\text{batch\_size})$ memory.

---

### Exercise 2: Bidirectional Stateful Token-Bucket Coroutine with `.send()` (Medium)
Build a stateful rate-limiting coroutine `token_bucket_coroutine(capacity: int, refill_rate_per_sec: float) -> Generator[bool, int, None]`:
1. Maintains internal token capacity and last-refill timestamp.
2. Accepts token requests via `.send(requested_tokens: int)`.
3. Upon receiving a request:
   - Computes elapsed time since last request and refills tokens proportionally up to `capacity`.
   - If available tokens $\ge$ `requested_tokens`, deducts tokens and yields `True`.
   - If available tokens < `requested_tokens`, yields `False`.
4. Implement a priming decorator `@coroutine` that automatically calls `next()` so callers can immediately invoke `.send()`.

---

### Exercise 3: Resilient Multi-Partition Log Streamer with `yield from` (Advanced)
Build a generator pipeline simulating a Kafka consumer reading multiple partition log files:
1. Define a subgenerator `read_partition_stream(partition_id: int, records_count: int) -> Generator[dict, None, int]`:
   - Yields individual record dictionaries: `{"partition": partition_id, "offset": i, "payload": ...}`.
   - Wraps iteration in a `try...finally` block that prints a cleanup notice.
   - Returns the total number of processed records upon completion.
2. Define a master orchestrator `merge_partitions(partitions: list[int]) -> Generator[dict, None, dict[int, int]]`:
   - Uses `yield from` to delegate iteration sequentially across all partitions.
   - Captures each subgenerator's return value and returns a summary dict: `{partition_id: total_records}`.
3. Test early termination by breaking out of the consumer loop and verifying that subgenerators execute their `finally` cleanup handlers.
