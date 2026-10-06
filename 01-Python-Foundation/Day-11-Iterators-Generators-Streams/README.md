# Day 11: Iterators, Generators (`yield from`) & Memory Stream Optimization

In enterprise backends and production AI systems, processing data efficiently often means processing data you cannot afford to hold entirely in memory. Whether streaming 100-gigabyte training corpora for LLM fine-tuning, ingesting continuous event logs from Apache Kafka, or computing overlapping token chunks for Retrieval-Augmented Generation (RAG), mastering Python's iterator protocol and generator pipelines is non-negotiable.

---

## 1. The Python Iteration Protocol: Iterable vs. Iterator

Python's iteration model is built on two distinct interfaces:

```
+-------------------------------------------------------------------------+
|                                Iterable                                 |
|   Any object implementing `__iter__()` that returns an Iterator.        |
|   Examples: list, dict, set, str, Path.glob(), custom collections       |
+-------------------------------------------------------------------------+
                                    │
                         iter(iterable) calls `__iter__()`
                                    ▼
+-------------------------------------------------------------------------+
|                                Iterator                                 |
|   An object that produces a sequence of values one at a time.           |
|   Must implement:                                                       |
|     1. `__next__()`: Returns the next element or raises `StopIteration` |
|     2. `__iter__()`: Returns `self` (Iterators are also Iterables)      |
+-------------------------------------------------------------------------+
```

### The Under-the-Hood Mechanics of `for item in container:`
When Python executes a `for` loop, it desugars into:
```python
iterator = iter(container)  # Calls container.__iter__()
while True:
    try:
        item = next(iterator)  # Calls iterator.__next__()
        # Loop body executes here
    except StopIteration:
        break  # Clean termination without leaking exceptions
```

---

## 2. CPython Frame Suspension Mechanics (`PyFrameObject`)

When a standard function executes, CPython pushes a `PyFrameObject` onto the call stack. When the function returns, that frame is destroyed, and all local variables are deallocated.

A **Generator function** behaves fundamentally differently:

```
+--------------------------------------------------------------------------+
| Standard Function:                                                       |
|   Caller -----> [ Call: Creates Frame ] -----> [ Return: Destroys Frame ]|
+--------------------------------------------------------------------------+

+--------------------------------------------------------------------------+
| Generator Function:                                                      |
|   Caller -----> [ Calls GenFn: Creates PyGenObject on Heap ]              |
|                   │                                                      |
|   next(gen) ──> [ Frame Resumed at `f_lasti` ]                           |
|                   │                                                      |
|   `yield val` ─> [ Frame Suspended: Local state frozen on Heap ]         |
|                   │                                                      |
|   next(gen) ──> [ Resumes from exact yield instruction ]                 |
+--------------------------------------------------------------------------+
```

1. Calling a function containing the `yield` keyword does not run code; it immediately returns a **Generator Object** (`PyGenObject`).
2. The generator's execution frame lives on the **heap** rather than the transient C call stack.
3. When `yield` is reached, CPython saves the instruction pointer (`f_lasti`) and freezes all local variable references.
4. Execution control is yielded back to the caller along with the yielded value.
5. On the subsequent `next()` call, CPython restores the frozen frame and resumes execution from the exact instruction after `yield`.

---

## 3. Memory Complexity: $\mathcal{O}(1)$ Streams vs. $\mathcal{O}(N)$ Lists

The difference between eager list evaluation and lazy generator streaming is the difference between production stability and Out-Of-Memory (OOM) crashes:

```python
# EAGER EVALUATION: O(N) Space Complexity
# For 10,000,000 records, allocates ~800 MB of heap memory immediately!
def load_all_embeddings_list(filepath: str) -> list[dict]:
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records

# LAZY STREAMING: O(1) Constant Space Complexity
# For 10,000,000 records, allocates ~8 KB of buffer memory regardless of file size!
def stream_embeddings_generator(filepath: str) -> Generator[dict, None, None]:
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)
```

---

## 4. Generator Expressions vs. List Comprehensions

- **List Comprehension (`[...]`)**: Eagerly evaluates all elements and stores the entire array in RAM.
- **Generator Expression (`(...)`)**: Lazily creates a generator object that produces items on demand.

```python
# 100 Million items:
list_comp = [x * 2 for x in range(100_000_000)]  # Consumes ~800+ MB RAM (or crashes)
gen_exp   = (x * 2 for x in range(100_000_000))  # Consumes 112 bytes of RAM!
```

---

## 5. Subgenerator Delegation: `yield from` Mechanics (PEP 380)

Before Python 3.3, chaining generators required manual loops:
```python
# Obsolete Manual Chaining:
for item in subgenerator():
    yield item
```

The **`yield from`** expression establishes a transparent, direct two-way pipe between the outer caller and the inner subgenerator:

```
[Caller] <═══════════════════════════════> [Subgenerator]
                     yield from
             (Direct Bidirectional Channel)
```

### Why `yield from` is Essential:
1. **Performance**: Bypasses Python bytecode iteration overhead; delegating at the C runtime level.
2. **Transparent Exception Forwarding**: Exceptions thrown into the delegating generator via `.throw()` are passed directly into the subgenerator.
3. **Return Value Capture**: A generator can return a final value with `return result`. The delegating generator captures this return value:
   ```python
   result = yield from subgenerator()
   ```

---

## 6. Generator Return Values & `StopIteration`

A generator can return a value:
```python
def chunk_accumulator() -> Generator[str, None, int]:
    yield "chunk_1"
    yield "chunk_2"
    return 42  # Carried inside StopIteration(42)
```
When exhausted, CPython raises `StopIteration(42)`. The exception object holds the return value in its `.value` attribute. When consumed via `yield from`, Python automatically assigns this value to the left-hand variable:

```python
def main_pipeline():
    total_tokens = yield from chunk_accumulator()
    print(f"Accumulated tokens: {total_tokens}")  # Prints 42
```

---

## 7. Bidirectional Coroutines: `.send(value)` (PEP 342)

Generators can receive values from the caller:

```python
def moving_average_coroutine() -> Generator[float, float, None]:
    total = 0.0
    count = 0
    average = 0.0
    while True:
        # Execution pauses here. The yielded value is sent to caller.
        # The value passed to .send() becomes the result of the yield expression!
        val = yield average
        total += val
        count += 1
        average = total / count
```

### The Priming Rule:
Before sending values with `gen.send(val)`, a generator must be **primed** to advance execution to the first `yield` expression:
- Call `next(gen)` or `gen.send(None)`. Passing a non-`None` value to an unprimed generator raises `TypeError: can't send non-None value to a just-started generator`.

---

## 8. Generator Lifecycle & Teardown: `.throw()` and `.close()`

- **`gen.close()`**: Raises a `GeneratorExit` exception inside the suspended generator frame. If the generator wraps code in a `try...finally` block, cleanup code (closing sockets, DB connections) is guaranteed to execute.
- **`gen.throw(exc_type, exc_val)`**: Injects an arbitrary exception into the suspended frame at the point of the `yield`. Allows the generator to handle or bubble the error.

```python
def managed_resource_stream():
    conn = acquire_db_connection()
    try:
        while True:
            batch = conn.fetch_batch()
            yield batch
    finally:
        conn.close()  # Guaranteed to execute when caller calls gen.close() or on garbage collection
```

---

## 9. PEP 479 & The `StopIteration` Transformation

In Python 3.7+, raising `StopIteration` manually inside a generator function is strictly forbidden:
- If a generator raises `StopIteration` directly (or an uncaught `StopIteration` leaks out of a function called inside the generator), CPython catches it and transforms it into:
  ```
  RuntimeError: generator raised StopIteration
  ```
- **Rule**: To terminate a generator cleanly, use a simple **`return`** statement!

---

## 10. `itertools` Mastery for Enterprise Systems

The standard library `itertools` module implements high-performance C-level iterator combinators:

| Combinator | Purpose | Memory | Production Backend Use Case |
| :--- | :--- | :--- | :--- |
| **`islice(it, start, stop)`** | Slices an iterator | $\mathcal{O}(1)$ | Pagination without loading entire result sets. |
| **`chain(*iterables)`** | Sequentially concatenates iterators | $\mathcal{O}(1)$ | Merging multiple database query cursors or log files. |
| **`chain.from_iterable(it)`** | Flattens a 2D iterator stream | $\mathcal{O}(1)$ | Streaming batches of document tokens. |
| **`groupby(it, key)`** | Groups contiguous elements | $\mathcal{O}(1)$ | Streaming aggregation on presorted database rows. |
| **`takewhile(pred, it)`** | Yields while predicate is true | $\mathcal{O}(1)$ | Consuming time-windowed log entries. |
| **`tee(it, n)`** | Duplicates an iterator into $n$ independent streams | $\mathcal{O}(\Delta)$ | Forking a telemetry stream (Warning: memory leaks if consumers diverge). |

---

## 11. Pipelining Generators: Unix Pipe Architecture

In enterprise backends, compose small, single-responsibility generator stages:

```
[Raw File Stream] ──> [Filter Stage] ──> [Transform Stage] ──> [Batch Accumulator] ──> [Sink]
```

```python
def extract_lines(filepath: Path) -> Generator[str, None, None]:
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            yield line.strip()

def filter_non_empty(stream: Iterable[str]) -> Generator[str, None, None]:
    for item in stream:
        if item:
            yield item

def parse_json(stream: Iterable[str]) -> Generator[dict, None, None]:
    for item in stream:
        yield json.loads(item)

# Composed pipeline: executes in strict O(1) memory!
pipeline = parse_json(filter_non_empty(extract_lines(data_file)))
for record in pipeline:
    process_record(record)
```

---

## 12. Sliding Window Chunking for RAG Systems

RAG (Retrieval-Augmented Generation) requires splitting text documents into overlapping token windows to preserve semantic context across chunk boundaries:

```
Document Tokens: [ T0, T1, T2, T3, T4, T5, T6, T7, T8, T9 ]
Window Size = 4, Overlap = 2, Step = 2

Chunk 0: [ T0, T1, T2, T3 ]
Chunk 1:         [ T2, T3, T4, T5 ]
Chunk 2:                 [ T4, T5, T6, T7 ]
Chunk 3:                         [ T6, T7, T8, T9 ]
```

Implementing this via generators using `collections.deque(maxlen=window_size)` guarantees strictly $\mathcal{O}(W)$ memory where $W$ is the window size, irrespective of whether the document has 1,000 or 10,000,000 tokens.

---

## 13. Infinite Generators & Backpressure

Generators can represent unbounded infinite data sources:
```python
def heartbeat_generator(interval_seconds: float = 1.0) -> Generator[float, None, None]:
    while True:
        yield time.time()
        time.sleep(interval_seconds)
```
Because generators are pull-based (the producer only computes a value when the consumer requests `next()`), they provide **natural backpressure**: the producer cannot overwhelm the consumer with unbuffered data.

---

## 14. Scientific Memory Profiling (`tracemalloc`)

To prove memory efficiency in architectural audits:

```python
import tracemalloc

tracemalloc.start()
# Execute streaming pipeline
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics("lineno")
print(f"Peak memory allocation: {tracemalloc.get_traced_memory()[1] / 1024:.2f} KB")
tracemalloc.stop()
```

---

## 15. Preview: Async Generators (`async for` & `async def yield`)

In modern asynchronous architectures (FastAPI, asyncio):
```python
async def stream_chat_completions(prompt: str) -> AsyncGenerator[str, None]:
    async for chunk in upstream_llm_client.stream(prompt):
        yield chunk.text
```
Async generators merge CPython generator frame suspension with the **Asyncio Event Loop**, allowing the worker thread to handle other HTTP requests while waiting for network I/O packets.
