"""
Day 02: Strings, Unicode, Regular Expressions & Slicing Mechanics
File: example_01.py - String Internals, PEP 393 Memory Layout & Join Mechanics

COMMENT PHILOSOPHY:
Comments explain *WHY* things happen inside CPython and memory,
not simply *WHAT* the code is executing.
"""

import sys
import time
import tracemalloc
from typing import List


def demonstrate_pep_393_memory_impact() -> None:
    """
    Demonstrates PEP 393 Flexible String Representation.
    
    WHY THIS MATTERS:
    In Python 3.3+, CPython does not allocate a fixed 4 bytes per character.
    Instead, it inspects the widest code point in the string and sets the 
    entire buffer's character width to 1, 2, or 4 bytes.
    """
    print("=" * 65)
    print("1. PEP 393: FLEXIBLE STRING REPRESENTATION MEMORY AUDIT")
    print("=" * 65)

    # 1-byte Latin-1 string (all characters <= U+00FF)
    ascii_str = "A" * 1000
    
    # 2-byte UCS-2 string (contains Devanagari character <= U+FFFF)
    ucs2_str = "A" * 999 + "\u0905"  # Devanagari 'अ'
    
    # 4-byte UCS-4 string (contains an Emoji <= U+10FFFF)
    ucs4_str = "A" * 999 + "🚀"       # Rocket emoji: U+1F680

    # WHY: The header of an ASCII string is PyASCIIObject (48 bytes on 64-bit),
    # while non-ASCII strings use PyCompactUnicodeObject (72-80 bytes header).
    # Beyond the header, each character takes 1, 2, or 4 bytes for the ENTIRE string.
    print(f"ASCII  (1 byte / char)  Length: {len(ascii_str):<5} Size: {sys.getsizeof(ascii_str)} bytes")
    print(f"UCS-2  (2 bytes / char) Length: {len(ucs2_str):<5} Size: {sys.getsizeof(ucs2_str)} bytes")
    print(f"UCS-4  (4 bytes / char) Length: {len(ucs4_str):<5} Size: {sys.getsizeof(ucs4_str)} bytes")
    
    # WHY THIS MATTERS FOR BACKEND APIS:
    # Appending a single emoji to a 10MB ASCII log file in memory will instantly
    # expand the allocated heap buffer from ~10MB to ~40MB!


def demonstrate_string_interning() -> None:
    """
    Demonstrates manual string interning with sys.intern().
    
    WHY THIS MATTERS:
    CPython automatically interns identifier-like strings (variable names, dict keys).
    For high-throughput systems processing millions of repeated strings (e.g. HTTP status strings),
    interning forces them to point to a single singleton PyObject, reducing memory
    and turning string comparison from O(N) character loops into O(1) pointer checks.
    """
    print("\n" + "=" * 65)
    print("2. STRING INTERNING & O(1) POINTER EQUALITY")
    print("=" * 65)

    # Dynamically generated strings are NOT interned by default
    s1 = "".join(["order_", "status_", "pending"])
    s2 = "".join(["order_", "status_", "pending"])

    print(f"Values equal (s1 == s2)?   : {s1 == s2}")
    # WHY: Different heap addresses because they were constructed dynamically at runtime
    print(f"Pointers identical (s1 is s2)? : {s1 is s2}")

    # WHY: sys.intern() registers the string in CPython's internal interning dictionary.
    # If the string exists, it returns the existing pointer; if not, it stores and returns it.
    interned_s1 = sys.intern(s1)
    interned_s2 = sys.intern(s2)
    print(f"After sys.intern(): (is)     : {interned_s1 is interned_s2}")
    print(f"Address: {hex(id(interned_s1))} vs {hex(id(interned_s2))}")


def benchmark_concatenation_vs_join(iterations: int = 50_000) -> None:
    """
    Benchmarks '+=' string accumulation against str.join().
    
    WHY THIS MATTERS:
    Strings are immutable. 's += item' creates a brand new string on each step,
    copying all accumulated characters: (1 + 2 + 3 + ... + N) = O(N^2) complexity.
    
    'str.join()' performs two passes:
    1. First pass sums the lengths of all strings to allocate the EXACT required buffer once.
    2. Second pass memcpy's the bytes directly into the pre-allocated buffer: O(N) complexity.
    """
    print("\n" + "=" * 65)
    print(f"3. BENCHMARK: '+=' LOOP vs 'str.join()' ({iterations:,} items)")
    print("=" * 65)

    words = ["chunk"] * iterations

    # Benchmark += concatenation
    tracemalloc.start()
    start_time = time.perf_counter()
    accumulated_str = ""
    for w in words:
        accumulated_str += w
    concat_duration = time.perf_counter() - start_time
    _, concat_peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Benchmark str.join()
    tracemalloc.start()
    start_time = time.perf_counter()
    joined_str = "".join(words)
    join_duration = time.perf_counter() - start_time
    _, join_peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"'+=' Loop Duration   : {concat_duration:.4f} seconds | Peak Mem: {concat_peak_mem / 1024:.2f} KB")
    print(f"'str.join()' Duration: {join_duration:.4f} seconds | Peak Mem: {join_peak_mem / 1024:.2f} KB")
    print(f"Performance Speedup  : {concat_duration / join_duration:.1f}x faster using join()!")


def demonstrate_slicing_mechanics() -> None:
    """
    Demonstrates slicing pointer mechanics and step traversal.
    """
    print("\n" + "=" * 65)
    print("4. SLICING MECHANICS & SHALLOW COPIES")
    print("=" * 65)

    uri = "/api/v1/organizations/7842/users?page=2&limit=50"
    
    # WHY: Slicing allocates a fresh PyUnicodeObject on the heap.
    # It does not create an alias or mutate the original string.
    path_only = uri[:uri.find("?")]
    query_params = uri[uri.find("?") + 1:]

    print(f"Original URI : {uri}")
    print(f"Extracted Path : {path_only} (New object: {hex(id(path_only))})")
    print(f"Query Params   : {query_params}")

    # Step slicing: reverse traversal
    reversed_sample = "FastAPI"[::-1]
    print(f"Reversed with [::-1]: {reversed_sample}")


if __name__ == "__main__":
    demonstrate_pep_393_memory_impact()
    demonstrate_string_interning()
    benchmark_concatenation_vs_join()
    demonstrate_slicing_mechanics()
