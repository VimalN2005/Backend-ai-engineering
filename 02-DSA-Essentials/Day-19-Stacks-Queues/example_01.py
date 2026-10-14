"""
Day 19: Example 01 - Core Stack & Queue Algorithms & Performance Benchmarks

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
Managing the temporal order of execution is a cornerstone of robust software.
From syntax compilers validating JSON/SQL schemas to asynchronous task runners
and expression evaluators, Stacks (LIFO) and Queues (FIFO) provide deterministic,
constant-time guarantees.

Crucially, in CPython, using `list.pop(0)` as a FIFO queue triggers an O(N) memory
shift via C `memmove()`, silently turning high-scale microservices into frozen bottlenecks.
Replacing `list` with `collections.deque` restores strict O(1) time complexity,
yielding speedups exceeding 1,000x on production data volumes.

This module implements:
1. Valid Parentheses Validator: Fast balanced bracket validation using a LIFO Stack.
2. Evaluate Reverse Polish Notation (RPN): Postfix arithmetic evaluation with truncation toward zero.
3. MinStack: O(1) time complexity for push, pop, top, and get_min operations.
4. QueueUsingStacks: FIFO Queue implemented using two LIFO Stacks with amortized O(1) operations.
5. High-volume performance benchmark: list.pop(0) O(N) vs collections.deque.popleft() O(1).
"""

from __future__ import annotations

import time
from collections import deque
from typing import List, Optional, Tuple


# ============================================================================
# 1. VALID PARENTHESES VALIDATOR (LIFO DELIMITER CHECKER)
# ============================================================================

def is_valid_parentheses(s: str) -> bool:
    """
    Validates if string of brackets '()[]{}' is syntactically balanced.
    
    WHY STACK:
    A closing delimiter must always close the most recently opened unmatched delimiter.
    This Last-In, First-Out behavior is naturally modeled by a Stack.
    
    Time Complexity:  O(N) - Scans string once.
    Space Complexity: O(N) - Stack holds at most N opening brackets.
    """
    bracket_map = {")": "(", "]": "[", "}": "{"}
    stack: List[str] = []

    for char in s:
        # If closing bracket encountered
        if char in bracket_map:
            # If stack is empty or top doesn't match expected opening bracket -> Invalid
            if not stack or stack[-1] != bracket_map[char]:
                return False
            stack.pop()
        else:
            # Opening bracket -> Push to stack
            stack.append(char)

    # Valid if and only if all opening brackets were matched and popped
    return len(stack) == 0


# ============================================================================
# 2. EVALUATE REVERSE POLISH NOTATION (POSTFIX STACK EVALUATION)
# ============================================================================

def eval_rpn(tokens: List[str]) -> int:
    """
    Evaluates arithmetic expression in Reverse Polish Notation (RPN).
    Supported operators: +, -, *, / (truncating division toward zero).
    
    WHY STACK:
    In postfix notation, operators immediately follow their two operands.
    Pushing numbers onto a stack and popping the two most recent operands upon
    encountering an operator guarantees unambiguous evaluation with zero parenthesis overhead.
    
    Time Complexity:  O(N) - Evaluates each token once.
    Space Complexity: O(N) - Stack stores operands.
    """
    stack: List[int] = []

    for token in tokens:
        if token in {"+", "-", "*", "/"}:
            b = stack.pop()
            a = stack.pop()

            if token == "+":
                stack.append(a + b)
            elif token == "-":
                stack.append(a - b)
            elif token == "*":
                stack.append(a * b)
            elif token == "/":
                # Integer division truncating toward zero (like C/Java, unlike Python floor //)
                stack.append(int(a / b))
        else:
            stack.append(int(token))

    return stack[0]


# ============================================================================
# 3. MIN STACK (O(1) MINIMUM RETRIEVAL)
# ============================================================================

class MinStack:
    """
    Stack data structure that supports push, pop, top, and retrieving the
    minimum element in strictly O(1) constant time.
    
    WHY PARALLEL TRACKING:
    Rather than rescanning the stack (O(N)) on get_min(), we maintain a secondary
    min_stack that stores the minimum element observed up to each corresponding depth.
    """

    def __init__(self) -> None:
        self._stack: List[int] = []
        self._min_stack: List[int] = []

    def push(self, val: int) -> None:
        """Pushes val onto stack in O(1) time."""
        self._stack.append(val)
        # Push to min_stack: either val or current minimum
        current_min = self._min_stack[-1] if self._min_stack else val
        self._min_stack.append(min(val, current_min))

    def pop(self) -> None:
        """Removes element on top of the stack in O(1) time."""
        if self._stack:
            self._stack.pop()
            self._min_stack.pop()

    def top(self) -> int:
        """Gets top element in O(1) time."""
        if not self._stack:
            raise IndexError("pop from empty stack")
        return self._stack[-1]

    def get_min(self) -> int:
        """Retrieves the minimum element in the stack in O(1) time."""
        if not self._min_stack:
            raise IndexError("get_min from empty stack")
        return self._min_stack[-1]


# ============================================================================
# 4. QUEUE IMPLEMENTED USING TWO STACKS (AMORTIZED O(1))
# ============================================================================

class QueueUsingStacks:
    """
    Implements a FIFO Queue using only two LIFO Stacks.
    
    WHY TWO STACKS:
    Popping from in_stack and pushing to out_stack reverses the element order,
    converting LIFO into FIFO! By delaying the transfer until out_stack is completely
    empty (Lazy Transfer), each element is moved at most once, yielding amortized O(1).
    """

    def __init__(self) -> None:
        self._in_stack: List[int] = []
        self._out_stack: List[int] = []

    def push(self, x: int) -> None:
        """Enqueues element to the back of the queue in O(1) time."""
        self._in_stack.append(x)

    def pop(self) -> int:
        """Dequeues element from the front of the queue in amortized O(1) time."""
        self._move_if_needed()
        if not self._out_stack:
            raise IndexError("pop from empty queue")
        return self._out_stack.pop()

    def peek(self) -> int:
        """Gets the front element in amortized O(1) time."""
        self._move_if_needed()
        if not self._out_stack:
            raise IndexError("peek from empty queue")
        return self._out_stack[-1]

    def empty(self) -> bool:
        """Returns True if queue is empty."""
        return len(self._in_stack) == 0 and len(self._out_stack) == 0

    def _move_if_needed(self) -> None:
        """Transfers elements from in_stack to out_stack ONLY when out_stack is empty."""
        if not self._out_stack:
            while self._in_stack:
                self._out_stack.append(self._in_stack.pop())


# ============================================================================
# 5. DEMONSTRATION & BENCHMARK SUITE
# ============================================================================

def run_stacks_queues_demo() -> None:
    print("=" * 75)
    print("DAY 19: CORE STACK & QUEUE ALGORITHMIC PATTERNS & BENCHMARKS")
    print("=" * 75)

    # 1. Test Valid Parentheses
    print("\n[PART 1] Testing Valid Parentheses Delimiter Validation:")
    test_cases_paren = [
        ("()", True),
        ("()[]{}", True),
        ("(]", False),
        ("([)]", False),
        ("{[]}", True),
        ("]", False),
        ("(", False),
    ]

    for s, expected in test_cases_paren:
        result = is_valid_parentheses(s)
        status = "PASSED" if result == expected else "FAILED"
        print(f"  - String: '{s:<8}' -> Balanced: {result} (Expected: {expected}) [{status}]")
        assert result == expected

    # 2. Test Evaluate Reverse Polish Notation (RPN)
    print("\n[PART 2] Testing Reverse Polish Notation (RPN) Evaluation:")
    test_cases_rpn = [
        (["2", "1", "+", "3", "*"], 9),                    # ((2 + 1) * 3) = 9
        (["4", "13", "5", "/", "+"], 6),                   # (4 + (13 / 5)) = 6
        (["10", "6", "9", "3", "+", "-11", "*", "/", "*", "17", "+", "5", "+"], 22),
    ]

    for tokens, expected_val in test_cases_rpn:
        calc_val = eval_rpn(tokens)
        status = "PASSED" if calc_val == expected_val else "FAILED"
        print(f"  - Tokens: {tokens} -> Evaluated: {calc_val} (Expected: {expected_val}) [{status}]")
        assert calc_val == expected_val

    # 3. Test MinStack
    print("\n[PART 3] Testing MinStack O(1) Operations:")
    min_stack = MinStack()
    min_stack.push(-2)
    min_stack.push(0)
    min_stack.push(-3)

    assert min_stack.get_min() == -3
    print(f"  - After pushing [-2, 0, -3] -> Min: {min_stack.get_min()} [PASSED]")

    min_stack.pop()  # Removes -3
    assert min_stack.top() == 0
    print(f"  - After popping top         -> Top: {min_stack.top()} [PASSED]")
    assert min_stack.get_min() == -2
    print(f"  - After popping -3          -> New Min: {min_stack.get_min()} [PASSED]")

    # 4. Test Queue Using Stacks
    print("\n[PART 4] Testing Queue Using Two Stacks (Amortized O(1)):")
    q = QueueUsingStacks()
    q.push(10)
    q.push(20)
    q.push(30)

    val1 = q.pop()
    print(f"  - Dequeued element 1: {val1} (Expected 10)")
    assert val1 == 10

    assert q.peek() == 20
    print(f"  - Peeking front element: {q.peek()} (Expected 20)")

    q.push(40)
    val2 = q.pop()
    val3 = q.pop()
    val4 = q.pop()
    print(f"  - Remaining dequeued elements: [{val2}, {val3}, {val4}] (Expected [20, 30, 40])")
    assert [val2, val3, val4] == [20, 30, 40]
    assert q.empty() is True
    print("  - Status: DUAL STACK QUEUE VERIFIED [PASSED]")

    # 5. Performance Benchmark: list.pop(0) O(N) vs collections.deque.popleft() O(1)
    print("\n[PART 5] Performance Benchmark: list.pop(0) O(N) vs deque.popleft() O(1):")
    queue_size = 50000
    ops_count = 10000

    test_list = list(range(queue_size))
    test_deque = deque(range(queue_size))

    # Benchmark list.pop(0): O(N) memory shift per pop
    start_time = time.perf_counter()
    for _ in range(ops_count):
        test_list.pop(0)
    list_ms = (time.perf_counter() - start_time) * 1000.0

    # Benchmark deque.popleft(): O(1) pointer advance
    start_time = time.perf_counter()
    for _ in range(ops_count):
        test_deque.popleft()
    deque_ms = (time.perf_counter() - start_time) * 1000.0

    print(f"  Dequeued {ops_count:,} items from a queue of {queue_size:,} initial elements:")
    print(f"  - Python list.pop(0) (O(N) memory shift) : {list_ms:.2f} ms")
    print(f"  - collections.deque.popleft() (O(1))     : {deque_ms:.4f} ms")
    speedup = list_ms / (deque_ms if deque_ms > 0 else 0.0001)
    print(f"  - Deque Speedup Advantage               : ~{speedup:,.1f}x faster execution!")

    print("\n" + "=" * 75)
    print("ALL CORE STACK & QUEUE ALGORITHMS PASSED AND VALIDATED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_stacks_queues_demo()
