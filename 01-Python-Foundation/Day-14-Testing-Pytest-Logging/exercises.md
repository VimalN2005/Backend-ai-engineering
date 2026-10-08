# Day 14: Practical Exercises & Week 02 Milestone Review

---

## 🎯 Part 1: Day 14 Hands-On Challenges

### Exercise 1: Resilient Async HTTP Client Test Suite with `pytest-asyncio` (Medium)
Build a comprehensive Pytest test suite for an asynchronous HTTP retry client:
1. Client class `ResilientAsyncClient(max_retries=3, base_backoff=0.1)` with method `async def get(url: str) -> dict`.
2. Write unit tests using `pytest-asyncio` and `AsyncMock`:
   - Test happy path (returns on attempt 1).
   - Test intermittent failure with recovery (attempt 1 raises 503, attempt 2 raises 503, attempt 3 succeeds).
   - Test exhaustion failure (all 3 attempts fail with 500, asserts that `RuntimeError` is raised).
3. Verify that `AsyncMock` was awaited exactly the expected number of times.

---

### Exercise 2: Structured JSON Log Redaction Filter (Medium)
Implement a custom logging filter `SensitiveDataFilter(logging.Filter)` that:
1. Inspects every `logging.LogRecord` passing through the logging pipeline.
2. Uses pre-compiled regular expressions to redact:
   - Bearer / JWT Tokens (`Bearer eyJ...` -> `Bearer [REDACTED_JWT]`).
   - OpenAI / Anthropic API keys (`sk-[A-Za-z0-9_-]{20,}` -> `sk-[REDACTED_KEY]`).
   - Email addresses (`[REDACTED_EMAIL]`).
3. Write a Pytest test verifying that logging a dictionary containing an API key outputs the redacted token.

---

### Exercise 3: Pytest Session-Scoped Cache Matrix Fixture with Teardown (Advanced)
Build a modular `conftest.py` setup:
1. Define a session-scoped fixture `vector_test_index` that:
   - Sets up an in-memory cosine index with 1,000 synthetic 128-dimensional vectors.
   - Yields the queryable index to test modules.
   - Teardown: Flushes and clears memory allocations, logging execution metrics.
2. Define a function-scoped fixture `clean_query_log` that records queries and asserts zero cross-test state pollution.

---

# 🚀 Part 2: Week 02 Milestone — 10 Extra Production Practice Challenges

> **End of Week 2 Practice Pack**: 10 real-world software engineering challenges covering all Week 2 topics (File I/O & Pathlib, Memory-Mapped Files, OOP & C3 MRO, Pydantic V2 & Schema Generation, Iterators & `yield from`, Concurrency & GIL, Asyncio & TaskGroups, and Pytest Observability).

---

### Challenge 1: Memory-Mapped Binary Vector Index Scanner (`mmap` + Pathlib)
- **Skills Tested**: `pathlib.Path`, `mmap`, Binary Packing (`struct`).
- **Problem**: Build a high-performance vector index storage engine that persists 50,000 128-dimensional float32 vectors to disk as a packed binary file. Use `mmap.mmap()` to perform top-$K$ cosine similarity search via zero-copy memory pointers without reading the file into Python heap objects.

### Challenge 2: Dynamic LLM Tool Calling Schema Compiler (Pydantic V2 + Annotated)
- **Skills Tested**: Pydantic V2 `BaseModel`, `Annotated`, `Field`, `model_json_schema()`.
- **Problem**: Build an automated function-to-tool-schema converter. Accept arbitrary Python functions decorated with `@llm_tool`, inspect type hints, construct a strict Pydantic V2 model on the fly, and export an OpenAPI/JSON Schema compatible with OpenAI Function Calling and Google Gemini Tools.

### Challenge 3: Slotted Dense Vector Embedding Value Object (OOP & Dunder Protocols)
- **Skills Tested**: `__slots__`, `@functools.total_ordering`, `__matmul__`, `__hash__`, `__eq__`.
- **Problem**: Implement an immutable, slotted `DenseEmbedding` class. Overload the `@` operator for dot product calculation and Euclidean norm ordering (`<`, `>`). Verify that instance memory footprint is under 60 bytes and that embeddings can be stored in sets for $\mathcal{O}(1)$ deduplication.

### Challenge 4: High-Throughput Sliding Window Token Chunker (`yield from` + Deque)
- **Skills Tested**: Generators, `collections.deque`, `yield from`, Memory Profiling (`tracemalloc`).
- **Problem**: Build a multi-stage streaming generator pipeline that streams 100,000 document texts, removes PII via regex, and chunks tokens into a sliding window of 512 tokens with 64-token overlap. Prove via `tracemalloc` that peak memory never exceeds 200 KB.

### Challenge 5: Cooperative Multi-Tenant Audit & Rate-Limiting Mixin Pipeline (C3 MRO)
- **Skills Tested**: Multiple Inheritance, C3 Linearization, Cooperative `super()`.
- **Problem**: Build a 4-tier microservice controller hierarchy (`BaseController`, `AuthMixin`, `TenantIsolationMixin`, `RateLimitMixin`, `SecureAPIController`). Verify that all mixins execute in deterministic order using `super()` without duplicating calls or skipping mixins.

### Challenge 6: Hybrid Concurrency Multi-Model Ingestion Engine (Threads + Processes)
- **Skills Tested**: `ThreadPoolExecutor`, `ProcessPoolExecutor`, `threading.Lock`.
- **Problem**: Implement a hybrid engine that concurrently fetches 100 raw document payloads across simulated network endpoints using a thread pool (releasing the GIL), and offloads heavy cryptographic hashing and vector embedding normalization to a process pool (bypassing the GIL).

### Challenge 7: High-Concurrency Async LLM Gateway with Server-Sent Events (SSE)
- **Skills Tested**: `asyncio.TaskGroup`, `asyncio.Semaphore`, `asyncio.timeout`, Async Generators.
- **Problem**: Build an async streaming gateway that handles 50 concurrent client connections. Bound upstream LLM concurrency to 10 slots using `asyncio.Semaphore`, emit tokens as Server-Sent Events (SSE) via an async generator, and cleanly handle client disconnect cancellations (`asyncio.CancelledError`).

### Challenge 8: Bidirectional Stateful Token-Bucket Coroutine with `.send()`
- **Skills Tested**: Coroutine Priming, `.send()`, Time Dilation.
- **Problem**: Implement a generator-based rate-limiting coroutine that accepts token requests via `.send(requested_tokens)`. Calculate refill rates dynamically based on elapsed time and yield `True`/`False`. Decorate with `@coroutine` for automatic priming.

### Challenge 9: Atomic Resilient Rotating File Logger with Gzip Archiving
- **Skills Tested**: `tempfile`, `os.replace`, `os.fsync`, `gzip.open`.
- **Problem**: Create an atomic rotating file writer. When active log exceeds 1 MB, flush CPython buffers, force OS disk sync via `os.fsync()`, atomically rotate backup logs (`.1.gz`, `.2.gz`), and compress rotated logs in background without losing concurrent write events.

### Challenge 10: Complete Pytest & Structured JSON Observability Suite
- **Skills Tested**: `pytest`, `pytest-asyncio`, `AsyncMock`, `contextvars`, Custom JSON Logging.
- **Problem**: Build a full automated test suite for an async AI RAG pipeline. Implement async fixtures, test downstream vector database timeouts via `side_effect`, validate Pydantic output schemas, and verify that all log entries emit structured JSON containing request trace IDs propagated via `contextvars`.
