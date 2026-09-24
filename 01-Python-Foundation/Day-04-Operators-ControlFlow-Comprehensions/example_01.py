"""
Day 04: Operators, Control Flow & High-Performance Comprehensions
File: example_01.py - Bytecode Optimizations, Short-Circuiting & Memory Profiling

COMMENT PHILOSOPHY:
Comments explain *WHY* control flow constructs behave the way they do inside CPython,
not simply *WHAT* the code is executing.
"""

import sys
import dis
import time
from typing import List, Generator


def demonstrate_bytecode_comprehension_optimization() -> None:
    """
    Demonstrates why comprehensions run faster than traditional .append() loops.
    
    WHY THIS MATTERS:
    In a standard loop, calling `result.append()` requires two expensive runtime operations:
    1. LOAD_METHOD: Looks up the 'append' attribute in the list's method table on EVERY iteration.
    2. CALL: Pushes a new C stack frame and executes the method.
    
    In a list comprehension, CPython emits the specialized LIST_APPEND opcode.
    This instruction directly appends the top-of-stack object into the list buffer in C,
    completely bypassing method resolution.
    """
    print("=" * 65)
    print("1. BYTECODE DISASSEMBLY: For-Loop vs List Comprehension")
    print("=" * 65)

    def traditional_loop(items: List[int]) -> List[int]:
        out = []
        for x in items:
            out.append(x * 2)
        return out

    def comprehension_loop(items: List[int]) -> List[int]:
        return [x * 2 for x in items]

    print("\n--- [A] Traditional For-Loop Bytecode (Notice LOAD_METHOD / CALL) ---")
    dis.dis(traditional_loop)

    print("\n--- [B] List Comprehension Bytecode (Notice optimized LIST_APPEND) ---")
    dis.dis(comprehension_loop)


def demonstrate_short_circuit_behavior() -> None:
    """
    Demonstrates short-circuit evaluation rules and operand returns.
    
    WHY THIS MATTERS:
    In Python, 'and' and 'or' do NOT coerce results to boolean True/False.
    They evaluate from left to right and return the operand that settled the truthiness.
    This enables defensive programming (guarding against None before attribute access)
    without triggering AttributeError exceptions.
    """
    print("\n" + "=" * 65)
    print("2. SHORT-CIRCUIT EVALUATION IN ACTION")
    print("=" * 65)

    # In 'A and B', if A is falsy, Python returns A immediately without evaluating B.
    empty_list = []
    result_and = empty_list and "fallback_value"
    print(f"[] and 'fallback_value' -> {result_and!r} (Empty list returned immediately)")

    # In 'A or B', if A is falsy, Python evaluates and returns B.
    result_or = empty_list or "default_auth_token"
    print(f"[] or 'default_auth_token' -> {result_or!r} (Fallback token returned)")

    # Real-world defensive attribute navigation:
    user_session = {"user": {"permissions": ["read", "write"]}}
    
    # Safe check: if any parent key is missing, evaluation halts without KeyError
    has_write = (
        user_session 
        and "user" in user_session 
        and "permissions" in user_session["user"]
        and "write" in user_session["user"]["permissions"]
    )
    print(f"User has write permission: {has_write}")


def demonstrate_for_else_retry_loop() -> None:
    """
    Demonstrates the 'for...else' construct for database connection retries.
    
    WHY THIS MATTERS:
    In distributed systems, operations like connecting to Postgres or Redis
    often require a finite number of retry attempts with backoff.
    The 'else' block attached to a 'for' loop executes IF AND ONLY IF the loop
    exhausts all iterations without hitting a 'break'.
    """
    print("\n" + "=" * 65)
    print("3. FOR...ELSE RETRY LOGIC (Database Connection Simulation)")
    print("=" * 65)

    max_retries = 3
    connected = False

    for attempt in range(1, max_retries + 1):
        print(f"Attempting to connect to PostgreSQL (Attempt {attempt}/{max_retries})...")
        
        # Simulate connection condition (fails in this test)
        if connected:
            print("Successfully connected to Database!")
            break
    else:
        # WHY: This block executes only when all retries fail without encountering 'break'
        print("[ALERT] All database connection attempts exhausted! Triggering PagerDuty/Alert.")


def compare_memory_list_comp_vs_generator(item_count: int = 1_000_000) -> None:
    """
    Demonstrates the radical memory difference between list comprehensions and generators.
    
    WHY THIS MATTERS:
    In LLM streaming APIs or RAG document loaders, creating a list of 1,000,000 text nodes
    buffers all objects into RAM at once (~8 MB to 500 MB depending on object size).
    A generator expression evaluates lazily on demand, keeping memory constant at ~112 bytes.
    """
    print("\n" + "=" * 65)
    print(f"4. MEMORY PROFILE: List Comprehension vs Generator ({item_count:,} items)")
    print("=" * 65)

    # List comprehension creates the entire collection in RAM upfront
    list_comp = [x * 2 for x in range(item_count)]
    list_size_bytes = sys.getsizeof(list_comp)

    # Generator expression stores only the generator state machine
    gen_exp = (x * 2 for x in range(item_count))
    gen_size_bytes = sys.getsizeof(gen_exp)

    print(f"List Comprehension Memory : {list_size_bytes:,} bytes (~{list_size_bytes / (1024 * 1024):.2f} MB)")
    print(f"Generator Expression Memory: {gen_size_bytes:,} bytes (~{gen_size_bytes / 1024:.2f} KB)")
    print(f"Memory Reduction Factor    : ~{list_size_bytes / gen_size_bytes:,.0f}x less RAM consumed!")


if __name__ == "__main__":
    demonstrate_bytecode_comprehension_optimization()
    demonstrate_short_circuit_behavior()
    demonstrate_for_else_retry_loop()
    compare_memory_list_comp_vs_generator()
