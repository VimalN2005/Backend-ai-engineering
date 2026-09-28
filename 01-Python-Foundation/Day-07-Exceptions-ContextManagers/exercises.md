# Day 07: Practical Exercises & Week 01 Milestone Review

---

## 🎯 Part 1: Day 07 Hands-On Challenges

### Exercise 1: Safe Atomic File Writer Context Manager (Medium)
Build a context manager `atomic_write(filepath: str, mode: str = "w")` that:
1. Writes changes to a temporary file in the same directory (`.filepath.tmp`).
2. If the caller's with-block completes without error, atomically replaces the target file using `os.replace()`.
3. If an error occurs, unlinks the temporary file and lets the exception propagate, ensuring the original file is never corrupted or left in a partial state.

```python
import os
from contextlib import contextmanager
from typing import Generator

@contextmanager
def atomic_write(filepath: str) -> Generator[Any, None, None]:
    # TODO: Implement temporary file writing and atomic os.replace()
    pass
```

---

### Exercise 2: Distributed Lock Simulation with Suppression Control (Medium)
Create a class-based context manager `RedisLockSimulator(lock_name: str, timeout_seconds: float)` that:
1. Simulates acquiring a named lock in `__enter__`.
2. In `__exit__`, releases the lock safely.
3. Accepts a constructor flag `suppress_timeout: bool = False`. If True and a `TimeoutError` occurred, suppresses the exception by returning `True`; otherwise allows it to bubble up.

---

### Exercise 3: RFC 7807 Error Response Normalizer (Advanced)
Build a custom exception hierarchy and error handler function `normalize_exception(exc: Exception) -> dict` that:
1. Maps custom domain exceptions (`ValidationError`, `NotFoundError`, `RateLimitError`) to standard HTTP status codes (400, 404, 429).
2. Extracts the underlying root cause (`exc.__cause__`) and attaches an automated correlation request ID.
3. Formats the output according to the **RFC 7807 Problem Details for HTTP APIs** standard.

---

# 🚀 Part 2: Week 01 Milestone — 10 Extra Production Practice Challenges

> **End of Week 1 Practice Pack**: 10 real-world software engineering challenges covering all Week 1 topics (Python Internals, Strings/Unicode/Regex, Collections/Dicts, Control Flow/Comprehensions, Functions/Scopes/Types, Closures/Decorators, and Exceptions/Context Managers).

---

### Challenge 1: Token-Bucket Rate Limiter with Thread-Safe Context Manager
- **Skills Tested**: Closures, `nonlocal`, Context Manager Protocol, Threading Lock.
- **Problem**: Build a rate limiter class `TokenBucketLimiter(rate: int, per_seconds: float)` that can be used both as a decorator (`@limiter`) and as a context manager (`with limiter:`). Ensure thread-safe state mutations using `threading.Lock`.

### Challenge 2: Streaming Log Parser with Named Regex Groups
- **Skills Tested**: Compiled Regular Expressions, Slicing, Generator Expressions.
- **Problem**: Write a generator function that streams a 100,000-line server access log, extracts IP, Timestamp, HTTP Method, URL, and Status Code using pre-compiled named regex groups, and filters out 200 OK requests, yielding only client/server errors (4xx and 5xx).

### Challenge 3: In-Memory Inverted Search Engine with Set Algebra
- **Skills Tested**: Dictionary and Set Internals, Hash lookups, Tokenization.
- **Problem**: Implement a full-text search index for 10,000 markdown documents. Provide a query method `search(query_str: str) -> Set[int]` that parses boolean queries (`python AND backend NOT django`) and executes fast set intersections and differences in $\mathcal{O}(1)$ time per token.

### Challenge 4: High-Performance Database Snapshot Reconciliation Engine (CDC)
- **Skills Tested**: Dict comprehension, Set difference, Hash comparisons.
- **Problem**: Write a reconciliation engine that accepts two snapshots of 50,000 database entities and returns a structured report containing: `created`, `updated`, `deleted`, and `unchanged` record IDs in strictly linear $\mathcal{O}(N)$ time.

### Challenge 5: Guard Clause Refactoring for API Controller
- **Skills Tested**: Control Flow, Clean Code, Guard Clauses.
- **Problem**: Refactor a heavily nested 5-tier if-else authentication controller into flat guard clauses with early returns, reducing cyclomatic complexity to 1.

### Challenge 6: Dynamic Function Schema Generator for LLM Tool Calling
- **Skills Tested**: `inspect`, `typing.get_type_hints`, Positional/Keyword boundaries.
- **Problem**: Build a decorator `@register_llm_tool` that inspects arbitrary Python functions and generates OpenAI/Gemini-compliant JSON schemas, asserting that all required parameters are properly mapped to JSON Schema types (`string`, `integer`, `boolean`, `array`).

### Challenge 7: Resilient LLM API Gateway Decorator with Randomized Jitter
- **Skills Tested**: 3-Tier Decorators, `@functools.wraps`, Exception Handling, Jitter.
- **Problem**: Implement a decorator `@resilient_call(max_retries=3, base_delay=0.5)` that catches HTTP 429 (RateLimit) and 503 (Unavailable) exceptions, applies exponential backoff with randomized jitter to prevent Thundering Herd lockups, and preserves all function metadata.

### Challenge 8: ReDoS-Safe Input Validator with Timeout Guard
- **Skills Tested**: Regex safety, Exception Chaining, Security.
- **Problem**: Write an input validation engine that tests user-supplied regexes against payloads. Wrap execution in a timeout mechanism that aborts catastrophic backtracking attempts after 100ms, raising a chained `ReDoSVulnerabilityError` with preserved root causes.

### Challenge 9: Universal Async/Sync Execution Benchmarking Decorator
- **Skills Tested**: Closures, `inspect.iscoroutinefunction`, Async/Await, Functools.
- **Problem**: Build a single `@benchmark_latency` decorator that transparently instruments both standard synchronous functions and asynchronous coroutines without breaking the async event loop or returning un-awaited coroutine objects.

### Challenge 10: Atomic Multi-Resource Transaction Context Manager
- **Skills Tested**: Context Manager Protocol (`__enter__`, `__exit__`), Rollbacks.
- **Problem**: Build an atomic transaction manager that coordinates writes between a PostgreSQL session and a Vector Database (Qdrant) collection. If the vector upsert crashes, automatically invoke `db_session.rollback()` and raise a chained `TransactionAbortedError`.
