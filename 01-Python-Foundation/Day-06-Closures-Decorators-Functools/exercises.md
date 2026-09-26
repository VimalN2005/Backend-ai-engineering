# Day 06: Practical Hands-On Exercises

Solve these three production-level challenges to master closures, custom decorators, and functional Python utilities.

---

## Exercise 1: Async-Aware Execution Timer & Token Auditor (Easy-Medium)

### Problem Statement:
In modern asynchronous frameworks like FastAPI, developers write both synchronous utility functions (`def`) and asynchronous route handlers (`async def`).
Write a universal decorator `@audit_execution(warn_threshold_ms: float = 100.0)` that:
1. Automatically detects whether the wrapped function is synchronous or asynchronous using `inspect.iscoroutinefunction()`.
2. Measures the exact execution duration in milliseconds using `time.perf_counter()`.
3. If the duration exceeds `warn_threshold_ms`, prints a warning: `"[SLOW OPERATION] {func_name} took {duration} ms!"`.
4. Correctly preserves metadata using `@functools.wraps`.

### Starter Template:
```python
import time
import inspect
import asyncio
import functools
from typing import Callable, Any

def audit_execution(warn_threshold_ms: float = 100.0) -> Callable:
    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                # TODO: Implement async timing logic with await
                pass
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                # TODO: Implement sync timing logic
                pass
            return sync_wrapper
    return decorator

# Test Verification:
@audit_execution(warn_threshold_ms=50.0)
def sync_task():
    time.sleep(0.08)
    return "Sync Done"

@audit_execution(warn_threshold_ms=50.0)
async def async_task():
    await asyncio.sleep(0.08)
    return "Async Done"
```

---

## Exercise 2: Sliding-Window Rate Limiter Decorator with Closures (Medium)

### Problem Statement:
To protect backend APIs from excessive token usage or abuse, create a client rate-limiting decorator without relying on external packages.
Implement a decorator `@rate_limit(max_requests: int = 5, window_seconds: float = 1.0)` that:
1. Uses a closure to maintain request timestamps for each unique `client_ip` (passed as a keyword or positional argument).
2. Evicts timestamps older than `window_seconds`.
3. If the number of requests in the current window exceeds `max_requests`, raises a custom `RateLimitError("Too Many Requests")`.
4. Otherwise, appends the current timestamp and executes the decorated function.

### Starter Code:
```python
import time
import functools
from collections import defaultdict
from typing import Callable, Any, Dict, List

class RateLimitError(Exception):
    pass

def rate_limit(max_requests: int = 5, window_seconds: float = 1.0) -> Callable:
    # Closure scope holding state across calls
    client_history: Dict[str, List[float]] = defaultdict(list)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, client_ip: str = "127.0.0.1", **kwargs: Any) -> Any:
            current_time = time.perf_counter()
            
            # TODO: Evict timestamps older than (current_time - window_seconds)
            # TODO: Check if len(client_history[client_ip]) >= max_requests
            # TODO: Record current_time and invoke func()
            pass
        return wrapper
    return decorator
```

---

## Exercise 3: Dynamic In-Memory Cache with TTL Invalidation (Advanced)

### Problem Statement:
Python's built-in `@functools.lru_cache` does not support Time-To-Live (TTL) expiration.
Write a custom caching decorator `@cache_with_ttl(ttl_seconds: float = 2.0, max_size: int = 128)` that:
1. Generates a cache key based on the function name and its serialized arguments `(*args, **kwargs)`.
2. Stores cache entries as a tuple: `(result, expiration_timestamp)`.
3. If the entry exists and has not expired, returns the cached result in $\mathcal{O}(1)$ time.
4. If expired or missing, executes the function, updates the cache, and evicts the oldest entry if size exceeds `max_size`.

### Starter Code:
```python
import time
import functools
from typing import Callable, Any, Dict, Tuple

def cache_with_ttl(ttl_seconds: float = 2.0, max_size: int = 128) -> Callable:
    cache_store: Dict[Any, Tuple[Any, float]] = {}

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # TODO: Build hashable cache key
            # TODO: Check cache_store and verify expiration
            # TODO: Manage max_size eviction and return result
            pass
        return wrapper
    return decorator
```
