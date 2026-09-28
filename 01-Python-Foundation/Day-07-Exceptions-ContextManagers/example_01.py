"""
Day 07: Robust Exception Hierarchies, Context Managers & Resource Safety
File: example_01.py - Exception Chaining, Context Manager Protocol & Lifecycle

COMMENT PHILOSOPHY:
Comments explain *WHY* exception mechanics and resource protocols operate the way
they do inside CPython, not simply *WHAT* the code is executing.
"""

import sys
import time
from contextlib import contextmanager
from typing import Generator, Any, Optional


# =====================================================================
# 1. CUSTOM DOMAIN EXCEPTION HIERARCHY
# =====================================================================

class AIServiceError(Exception):
    """
    Base exception for all AI platform failures.
    
    WHY INHERIT FROM Exception:
    CPython reserves BaseException for fatal system-level signals:
    SystemExit, KeyboardInterrupt, and GeneratorExit.
    Inheriting from Exception guarantees standard application handlers catch our errors
    without accidentally disabling OS signals or container shutdown hooks.
    """
    pass


class VectorIndexSearchError(AIServiceError):
    """Raised when vector similarity search fails."""
    pass


class TokenBudgetExceededError(AIServiceError):
    """Raised when request prompt tokens exceed quota limit."""
    pass


def demonstrate_exception_chaining() -> None:
    """
    Demonstrates explicit exception chaining (PEP 3134).
    
    WHY THIS MATTERS:
    When translating low-level network or driver errors into domain exceptions,
    writing `raise CustomError()` discards the original traceback context.
    Writing `raise CustomError() from err` preserves the original root-cause
    inside the `__cause__` attribute, allowing observability tools (Sentry/CloudWatch)
    to pinpoint the exact socket/query failure.
    """
    print("=" * 65)
    print("1. EXCEPTION CHAINING (raise ... from err)")
    print("=" * 65)

    def low_level_network_call():
        # Low-level network driver timeout
        raise ConnectionResetError("TCP socket reset by peer: 10.0.4.15:6333")

    def execute_vector_query():
        try:
            low_level_network_call()
        except ConnectionResetError as driver_err:
            # WHY 'from driver_err': Explicitly links root-cause to domain error
            raise VectorIndexSearchError("Failed to query Qdrant vector index") from driver_err

    try:
        execute_vector_query()
    except VectorIndexSearchError as domain_err:
        print(f"[Caught Domain Error]: {domain_err}")
        print(f"[Original Root Cause]: {domain_err.__cause__!r}")
        print(f"[Traceback Preserved]: {domain_err.__traceback__ is not None}")


def demonstrate_try_except_else_finally_lifecycle() -> None:
    """
    Demonstrates the precise execution ordering of try-except-else-finally.
    
    WHY THIS MATTERS:
    - 'else' runs ONLY if try block succeeded without exceptions.
      Keep code that should run on success in 'else', avoiding accidental catches of its errors.
    - 'finally' runs UNCONDITIONALLY, guaranteeing cleanup even if returns or errors occur.
    """
    print("\n" + "=" * 65)
    print("2. THE try...except...else...finally LIFECYCLE")
    print("=" * 65)

    def process_transaction(should_fail: bool) -> str:
        print("  -> Step 1: Entering [try] block")
        try:
            if should_fail:
                raise ValueError("Simulated validation failure")
            result = "SUCCESS_TOKEN"
        except ValueError as err:
            print(f"  -> Step 2: Entering [except] block caught: {err}")
            return "FAILED"
        else:
            print("  -> Step 2: Entering [else] block (No errors occurred)")
            return result
        finally:
            # WHY: finally ALWAYS executes before the function exits, even after return!
            print("  -> Step 3: Entering [finally] block (Releasing locks/descriptors)")

    print("--- [Scenario A: Successful Run] ---")
    print("Return Value:", process_transaction(should_fail=False))

    print("\n--- [Scenario B: Failed Run] ---")
    print("Return Value:", process_transaction(should_fail=True))


# =====================================================================
# 3. CLASS-BASED CONTEXT MANAGER PROTOCOL
# =====================================================================

class ManagedExecutionTimer:
    """
    Class-based context manager demonstrating __enter__ and __exit__.
    
    WHY THIS MATTERS:
    Context managers provide deterministic setup and teardown semantics.
    The __exit__ method receives the active exception information (if any).
    """

    def __init__(self, operation_name: str):
        self.operation_name = operation_name
        self.start_time: float = 0.0

    def __enter__(self) -> "ManagedExecutionTimer":
        # WHY: __enter__ runs before the with-block starts
        self.start_time = time.perf_counter()
        print(f"  [TIMER START] Measuring: '{self.operation_name}'")
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any]
    ) -> bool:
        # WHY: __exit__ runs when with-block exits, receiving exception metadata
        elapsed_ms = (time.perf_counter() - self.start_time) * 1000
        print(f"  [TIMER END] '{self.operation_name}' completed in {elapsed_ms:.2f} ms")

        if exc_type is not None:
            print(f"  [TIMER ALERT] Exception caught during execution: {exc_val}")
            # Returning False tells Python NOT to suppress the error; let it bubble up!
            return False
        return True


# =====================================================================
# 4. GENERATOR-BASED CONTEXT MANAGER (contextlib)
# =====================================================================

@contextmanager
def temporary_database_session(connection_uri: str) -> Generator[Dict[str, str], None, None]:
    """
    Generator-based context manager using contextlib.
    
    WHY @contextmanager IS PREFERRED:
    Writing __enter__ and __exit__ boilerplate for simple resources is verbose.
    @contextmanager allows writing setup logic before 'yield' and cleanup logic in 'finally'.
    """
    print(f"\n[SESSION POOL] Connecting to: {connection_uri}")
    mock_session = {"connection_id": "conn_49182", "status": "active"}
    try:
        yield mock_session  # Hand execution to the caller
    finally:
        # Guaranteed cleanup regardless of client errors
        print("[SESSION POOL] Disconnecting and returning socket to connection pool.")


def demonstrate_context_managers() -> None:
    print("\n" + "=" * 65)
    print("3. CONTEXT MANAGER PROTOCOL IN ACTION")
    print("=" * 65)

    # Class-based context manager usage
    with ManagedExecutionTimer("LLM Chunking Operation") as timer:
        time.sleep(0.02)

    # Generator-based context manager usage
    with temporary_database_session("postgresql://app:secret@db.internal:5432/vectors") as session:
        print(f"  Using Database Session: {session['connection_id']}")


if __name__ == "__main__":
    demonstrate_exception_chaining()
    demonstrate_try_except_else_finally_lifecycle()
    demonstrate_context_managers()
