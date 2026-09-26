"""
Day 06: Closures, Custom Decorators & Functional Python with Functools
File: example_01.py - Closures, Cell Objects, @wraps Anatomy & Configurable Decorators

COMMENT PHILOSOPHY:
Comments explain *WHY* memory closures and wrapper functions operate the way
they do inside CPython, not simply *WHAT* the code is executing.
"""

import sys
import time
import functools
from typing import Callable, Any, Dict


def demonstrate_closure_cell_internals() -> None:
    """
    Demonstrates how CPython keeps captured variables alive on the heap.
    
    WHY THIS MATTERS:
    When an outer function returns, its call stack frame (PyFrameObject) is popped.
    However, if an inner function references a variable from the outer frame (a 'free variable'),
    CPython transfers that reference to a heap-allocated PyCellObject.
    The inner function holds a reference to this cell in its `__closure__` tuple attribute,
    preventing the garbage collector from deallocating the captured state!
    """
    print("=" * 65)
    print("1. CLOSURE INTERNALS & PyCellObject INSPECTION")
    print("=" * 65)

    def make_rate_limiter(max_rpm: int) -> Callable[[], None]:
        # 'max_rpm' and 'request_count' become free variables stored in PyCellObjects
        request_count = 0

        def check_limit() -> None:
            nonlocal request_count
            request_count += 1
            print(f"Request #{request_count} processed (Max allowed: {max_rpm})")

        return check_limit

    limiter = make_rate_limiter(max_rpm=60)
    limiter()
    limiter()

    # Introspecting the internal cell objects on the heap:
    print("\nInspecting limiter.__closure__:")
    for idx, cell in enumerate(limiter.__closure__):
        print(f"  Cell [{idx}] object  : {cell}")
        print(f"  Cell [{idx}] contents: {cell.cell_contents} ({type(cell.cell_contents).__name__})")


def demonstrate_functools_wraps_disaster() -> None:
    """
    Demonstrates the severe metadata loss that occurs without @functools.wraps.
    
    WHY THIS MATTERS:
    Frameworks like FastAPI, Flask, and Sphinx documentation generators inspect
    a function's `__name__`, `__doc__`, and `__annotations__` at startup.
    A naive decorator replaces the target function with the generic wrapper function.
    Without `@functools.wraps`, all route names become 'wrapper', docstrings become None,
    and OpenAPI / Swagger interactive documentation is completely corrupted!
    """
    print("\n" + "=" * 65)
    print("2. THE METADATA DISASTER: Naive vs @functools.wraps")
    print("=" * 65)

    # Naive Decorator (BAD)
    def naive_audit(func: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        return wrapper

    # Production Decorator with @functools.wraps (GOOD)
    def production_audit(func: Callable) -> Callable:
        # WHY: @functools.wraps copies __module__, __name__, __qualname__,
        # __doc__, and __annotations__ from 'func' into 'wrapper'.
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        return wrapper

    @naive_audit
    def calculate_embedding_cost(tokens: int) -> float:
        """Calculates billing cost for OpenAI text-embedding-3-small."""
        return tokens * 0.00002

    @production_audit
    def calculate_completion_cost(tokens: int) -> float:
        """Calculates billing cost for GPT-4o output tokens."""
        return tokens * 0.005

    print(f"Naive Function Name   : {calculate_embedding_cost.__name__} (Lost original name!)")
    print(f"Naive Function Doc    : {calculate_embedding_cost.__doc__} (Lost docstring!)")
    print("-" * 50)
    print(f"Production Func Name  : {calculate_completion_cost.__name__} (Preserved!)")
    print(f"Production Func Doc   : {calculate_completion_cost.__doc__} (Preserved!)")
    print(f"Underlying Unwrapped  : {calculate_completion_cost.__wrapped__}")


def demonstrate_3_tier_configurable_decorator() -> None:
    """
    Demonstrates building a 3-tier decorator that accepts configuration arguments.
    
    WHY THIS MATTERS:
    In production backends, decorators often need dynamic parameters:
    `@retry(max_retries=3, backoff=2.0)` or `@cache(ttl=60)`.
    This requires a 3-tier function architecture:
    1. Outer function: Receives configuration parameters.
    2. Middle function: Receives the target function to decorate.
    3. Inner function: Receives the runtime *args and **kwargs during execution.
    """
    print("\n" + "=" * 65)
    print("3. 3-TIER CONFIGURABLE DECORATOR (Dynamic Retry Policy)")
    print("=" * 65)

    def retry(max_attempts: int = 3, fallback_value: Any = None):
        # Tier 1: Configuration scope
        def decorator(func: Callable) -> Callable:
            # Tier 2: Decorator binding scope
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                # Tier 3: Runtime execution scope
                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as err:
                        print(f"  [Attempt {attempt}/{max_attempts} failed]: {err}")
                        if attempt == max_attempts:
                            print(f"  Max retries exhausted. Returning fallback: {fallback_value}")
                            return fallback_value
            return wrapper
        return decorator

    @retry(max_attempts=2, fallback_value={"status": "offline"})
    def fetch_remote_vector_index(endpoint: str) -> Dict[str, str]:
        # Simulating temporary network outage
        raise ConnectionResetError(f"Connection to {endpoint} was refused by peer")

    result = fetch_remote_vector_index("https://qdrant.internal.cloud:6333")
    print(f"Invocation Result: {result}")


def demonstrate_functools_lru_cache() -> None:
    """
    Demonstrates O(1) in-memory memoization using functools.lru_cache.
    
    WHY THIS MATTERS:
    Computing heavy mathematical transformations, token counts of static system prompts,
    or loading static configuration tables from disk can be cached in RAM with a single line.
    lru_cache uses an internal C-level dictionary combined with a doubly-linked list
    to provide O(1) cache lookups and automatic least-recently-used eviction.
    """
    print("\n" + "=" * 65)
    print("4. MEMOIZATION WITH functools.lru_cache")
    print("=" * 65)

    @functools.lru_cache(maxsize=128)
    def compute_heavy_hash(token_sequence: str) -> int:
        # Simulate heavy CPU-bound computation
        time.sleep(0.01)
        return hash(token_sequence)

    # First call: Cache miss (takes ~10ms)
    t0 = time.perf_counter()
    _ = compute_heavy_hash("system_prompt_v1_rag_instruction_set")
    miss_duration = (time.perf_counter() - t0) * 1000

    # Second call: Cache hit (instant O(1) memory lookup)
    t0 = time.perf_counter()
    _ = compute_heavy_hash("system_prompt_v1_rag_instruction_set")
    hit_duration = (time.perf_counter() - t0) * 1000

    print(f"Cache Miss Latency: {miss_duration:.3f} ms")
    print(f"Cache Hit Latency : {hit_duration:.6f} ms (~{miss_duration / max(hit_duration, 0.0001):,.0f}x faster!)")
    print(f"Cache Stats       : {compute_heavy_hash.cache_info()}")


if __name__ == "__main__":
    demonstrate_closure_cell_internals()
    demonstrate_functools_wraps_disaster()
    demonstrate_3_tier_configurable_decorator()
    demonstrate_functools_lru_cache()
