# Day 19: 7 Critical Antipatterns in Stacks & Queues

Stacks and Queues appear deceptively simple, but naive implementations in production backends trigger memory leaks, process crashes, and severe latency degradation. Below are 7 critical antipatterns, their architectural consequences, and enterprise remediations.

---

## 1. Using Python `list` as a FIFO Queue (`list.pop(0)`)

### The Mistake
Using a standard Python list and popping from the front to process incoming jobs:
```python
# WRONG: Severe O(N) memory shift on every single task!
class BadTaskQueue:
    def __init__(self):
        self.queue = []

    def enqueue(self, task):
        self.queue.append(task)

    def dequeue(self):
        return self.queue.pop(0)  # C memmove() shifts all remaining N-1 pointers!
```

### The Consequence
Because Python lists are contiguous C pointer arrays, `pop(0)` shifts every single remaining element left by 8 bytes. For a queue of $N = 100{,}000$ jobs, processing all tasks takes $\frac{100{,}000 \times 100{,}000}{2} = 5{,}000{,}000{,}000$ pointer shifts, causing the microservice worker to freeze for seconds and trigger timeout alerts.

### The Correct Approach
Always use `collections.deque` for FIFO queues:
```python
# CORRECT: O(1) double-ended queue using 64-pointer block list
from collections import deque

class FastTaskQueue:
    def __init__(self):
        self.queue = deque()

    def enqueue(self, task):
        self.queue.append(task)       # O(1)

    def dequeue(self):
        return self.queue.popleft()   # Strict O(1)
```

---

## 2. Calling `stack.pop()` or `stack[-1]` Without Checking Empty State

### The Mistake
Assuming the stack contains elements when processing closing delimiters:
```python
# WRONG: Crashes with IndexError on inputs starting with closing brackets!
def is_valid_parentheses(s: str) -> bool:
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for char in s:
        if char in pairs:
            # If s = "]", stack is empty! stack.pop() raises IndexError!
            if stack.pop() != pairs[char]:
                return False
        else:
            stack.append(char)
    return len(stack) == 0
```

### The Consequence
If user input begins with a closing bracket (e.g. `"]"`, `"}"`), calling `stack.pop()` on an empty list immediately raises an unhandled `IndexError: pop from empty list`, crashing the parser or throwing an unhandled HTTP 500 error.

### The Correct Approach
Always verify `stack` truthiness before popping or inspecting the top:
```python
# CORRECT: Safe empty check guard clause
if char in pairs:
    if not stack or stack.pop() != pairs[char]:
        return False
```

---

## 3. Unbounded In-Memory Queues Causing Out-Of-Memory (OOM) Outages

### The Mistake
Instantiating worker queues without a maximum capacity limit:
```python
# WRONG: Unbounded queue absorbs unlimited tasks until process crashes!
from collections import deque

task_queue = deque()  # No maxlen specified!

def handle_incoming_webhook(payload: dict):
    task_queue.append(payload)  # Influx spikes consume all system RAM
```

### The Consequence
During upstream traffic surges (e.g. Black Friday or external webhook retries), incoming tasks arrive faster than worker threads can consume them. The queue balloons to millions of items, consuming gigabytes of heap memory until the Linux kernel OOM killer terminates the service with `SIGKILL` (exit code 137).

### The Correct Approach
Enforce bounded queues and backpressure policies:
```python
# CORRECT: Explicit capacity limit with backpressure
from collections import deque

task_queue = deque(maxlen=10000)  # Or reject with HTTP 429 when len(q) >= 10000
```

---

## 4. Transferring Elements on *Every* Operation in `QueueUsingStacks`

### The Mistake
Re-transferring elements back and forth between `in_stack` and `out_stack` on every push or pop:
```python
# WRONG: Degenerates amortized O(1) into catastrophic O(N) per operation!
class InefficientQueue:
    def __init__(self):
        self.s1 = []
        self.s2 = []

    def push(self, x):
        # Moves all elements to s2, appends x, moves all elements back to s1!
        while self.s1:
            self.s2.append(self.s1.pop())
        self.s1.append(x)
        while self.s2:
            self.s1.append(self.s2.pop())  # O(N) on EVERY single push!
```

### The Consequence
Instead of achieving amortized $\mathcal{O}(1)$ time, every single push costs $\mathcal{O}(N)$ operations. Pushing 10,000 elements requires $100{,}000{,}000$ operations!

### The Correct Approach
Use **Lazy Transfer**: push strictly to `in_stack` in $\mathcal{O}(1)$, and only transfer to `out_stack` when `out_stack` is **completely empty**:
```python
# CORRECT: Amortized O(1) per operation via Lazy Transfer
class OptimalQueue:
    def __init__(self):
        self.in_stack = []
        self.out_stack = []

    def push(self, x):
        self.in_stack.append(x)  # Pure O(1)

    def pop(self):
        if not self.out_stack:
            while self.in_stack:
                self.out_stack.append(self.in_stack.pop())
        return self.out_stack.pop()  # Amortized O(1)
```

---

## 5. Corrupting Running Minimums in MinStack by Skipping Duplicates

### The Mistake
Only pushing to `min_stack` when the incoming value is strictly strictly smaller (`<`), but failing to account for duplicate minimum values during pops:
```python
# WRONG: Breaks when duplicate minimums are pushed and popped!
class BrokenMinStack:
    def __init__(self):
        self.stack = []
        self.min_stack = []

    def push(self, val):
        self.stack.append(val)
        if not self.min_stack or val < self.min_stack[-1]:  # BUG: Strictly less than!
            self.min_stack.append(val)

    def pop(self):
        val = self.stack.pop()
        if val == self.min_stack[-1]:
            self.min_stack.pop()
```

### The Consequence
If you push `[2, 0, 0]`:
- Push 2: `min_stack = [2]`
- Push 0: `min_stack = [2, 0]`
- Push 0: 0 is not `< 0`, so `min_stack` remains `[2, 0]`.
- Call `pop()`: removes top 0, sees `0 == min_stack[-1]`, pops 0 from `min_stack`!
- Now `get_min()` returns `2`, even though another `0` is still in the stack!

### The Correct Approach
Push when `val <= self.min_stack[-1]`, or pair every stack entry with its running minimum:
```python
# CORRECT: Store running min at each depth
def push(self, val):
    self.stack.append(val)
    current_min = self.min_stack[-1] if self.min_stack else val
    self.min_stack.append(min(val, current_min))
```

---

## 6. Relying on Call Stack Recursion Instead of Explicit Heap Stacks

### The Mistake
Using recursive functions to process deeply nested JSON structures, AST trees, or file directory hierarchies:
```python
# WRONG: Crashes with RecursionError when nesting depth exceeds 1000!
def parse_nested_json(node):
    for child in node.get("children", []):
        parse_nested_json(child)  # RecursionError on deep structures!
```

### The Consequence
In CPython, each recursion level consumes a C stack frame. When processing user-generated deeply nested JSON or recursive AST trees, the process crashes with `RecursionError: maximum recursion depth exceeded`. Increasing `sys.setrecursionlimit()` risks segfaulting the OS process.

### The Correct Approach
Convert deep recursion to an **explicit iterative Stack allocated on the heap**:
```python
# CORRECT: Heap memory can hold millions of nodes without hitting stack limits
def parse_nested_json_iterative(root):
    stack = [root]
    while stack:
        node = stack.pop()
        for child in reversed(node.get("children", [])):
            stack.append(child)
```

---

## 7. Modifying Shared Queue Elements Without Concurrency Locks

### The Mistake
Pushing mutable dictionaries or objects into a shared in-memory queue, and modifying the dictionary in the producer thread while the consumer is dequeuing:
```python
# WRONG: Race condition mutating shared state across threads
shared_queue = deque()

# Producer Thread:
task = {"id": 1, "status": "PENDING"}
shared_queue.append(task)
task["status"] = "UPDATED"  # Race condition with consumer reading task!
```

### The Consequence
In multi-threaded backends, concurrent mutation of shared dicts causes inconsistent state, race conditions, and corrupted telemetry.

### The Correct Approach
Enqueue **immutable objects** (frozen dataclasses or namedtuples) or pass deep copies:
```python
# CORRECT: Immutable dataclass prevents mutation race conditions
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class TaskMessage:
    id: int
    status: str
```

---

## Summary Checklist
- [x] Never use `list.pop(0)`; always use `collections.deque.popleft()`.
- [x] Always verify `if not stack:` before calling `stack.pop()` or `stack[-1]`.
- [x] Enforce `maxlen` on in-memory queues to provide backpressure and prevent OOM.
- [x] Implement Lazy Transfer in `QueueUsingStacks` to preserve amortized $\mathcal{O}(1)$ performance.
- [x] Synchronize `min_stack` on duplicate minimums in MinStack.
- [x] Use explicit heap Stacks instead of recursion for deep trees to prevent `RecursionError`.
- [x] Enqueue immutable frozen dataclasses to eliminate concurrency race conditions.
