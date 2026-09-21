"""
Day 01: Python Environment, Execution Flow & Memory References
File: example_01.py - Exploring CPython Internals, Memory & Bytecode

Comment Rule: Comments explain *WHY* an operation happens under the hood,
not simply *WHAT* code is doing.
"""

import sys
import dis
from typing import Any


def explain_execution_flow() -> None:
    """Demonstrates how CPython compiles source code to bytecode."""
    print("=" * 60)
    print("1. DISASSEMBLING CPYTHON BYTECODE")
    print("=" * 60)

    def calculate_total(price: float, tax_rate: float) -> float:
        # WHY: Local variables use fast-array indexing in PVM (LOAD_FAST / STORE_FAST)
        # rather than dictionary lookups (LOAD_GLOBAL), yielding ~30% faster execution.
        total = price + (price * tax_rate)
        return total

    # WHY: Inspecting bytecode reveals what the Python Virtual Machine (PVM)
    # actually evaluates in its ceval.c loop.
    dis.dis(calculate_total)


def explain_memory_references() -> None:
    """Demonstrates object identity, pointers, and reference counting."""
    print("\n" + "=" * 60)
    print("2. VARIABLES AS POINTERS & REFERENCE COUNTING")
    print("=" * 60)

    # WHY: Python variables do NOT hold raw values in stack frames.
    # They are C pointers (PyObject*) pointing to heap-allocated objects.
    initial_list = [10, 20, 30]
    alias_list = initial_list

    print(f"Address of initial_list: {hex(id(initial_list))}")
    print(f"Address of alias_list:   {hex(id(alias_list))}")

    # WHY: Both aliases share the exact same memory pointer.
    # Therefore, in-place mutations through one label affect all references.
    alias_list.append(40)
    print(f"initial_list after alias mutation: {initial_list}")
    print(f"Are they the identical object? (is): {initial_list is alias_list}")

    # WHY: sys.getrefcount() returns current count + 1 because passing the
    # object as an argument temporarily creates another reference on the frame.
    ref_count = sys.getrefcount(initial_list)
    print(f"Active references to list object: {ref_count}")


def explain_interning_mechanics() -> None:
    """Demonstrates CPython's small integer caching and string interning."""
    print("\n" + "=" * 60)
    print("3. CPYTHON OPTIMIZATION: SMALL INTEGER & STRING INTERNING")
    print("=" * 60)

    # WHY: CPython pre-allocates an array of integer objects from -5 to 256
    # during interpreter startup because small integers are used ubiquitously in
    # indexing, loop counters, and arithmetic. Reusing singletons saves heap allocs.
    a = 256
    b = 256
    print(f"256 is 256? -> {a is b} (Shared singleton pointer)")

    # WHY: Numbers outside [-5, 256] typically allocate distinct PyLongObject
    # instances when evaluated separately, so their memory addresses differ.
    large_num_1 = 10000
    large_num_2 = 10000
    print(f"Values equal? (==): {large_num_1 == large_num_2}")
    print(f"Addresses equal? (is): {large_num_1 is large_num_2}")


def inspect_pyobject_header(obj: Any) -> None:
    """Explains the underlying C structure of every Python object."""
    print("\n" + "=" * 60)
    print("4. UNDER THE HOOD: PyObject HEADER")
    print("=" * 60)
    # WHY: Every Python object has at least a 16-byte header on 64-bit platforms:
    # 1. ob_refcnt (8 bytes): Reference count for Garbage Collection.
    # 2. ob_type   (8 bytes): Pointer to the type struct (e.g. PyType_Type).
    size_in_bytes = sys.getsizeof(obj)
    print(f"Type: {type(obj).__name__}")
    print(f"Heap Memory Size: {size_in_bytes} bytes")
    print(f"Memory Address: {hex(id(obj))}")


if __name__ == "__main__":
    explain_execution_flow()
    explain_memory_references()
    explain_interning_mechanics()
    inspect_pyobject_header(42)
    inspect_pyobject_header("Production Backend")
    inspect_pyobject_header([1, 2, 3])
