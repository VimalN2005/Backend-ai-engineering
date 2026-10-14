# Day 19: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Stacks, Queues, CPython Memory Mechanics, and Middleware Execution Pipelines for backend engineers.

---

## 1. LIFO vs FIFO Execution Models

| Dimension | Stack (LIFO) | Queue (FIFO) |
| :--- | :--- | :--- |
| **Ingress / Egress** | Insert & Remove from **Same End** (Top) | Insert at **Tail**, Remove from **Head** |
| **Primary Operations** | `push(x)` / `pop()` | `enqueue(x)` / `dequeue()` |
| **System Implementations**| Python Call Stack, Execution Frames | Asyncio Event Loop, Task Brokers |
| **Backend Use Cases** | Middleware Unwinding, Saga Rollbacks | Background Workers, Job Pipelines |

---

## 2. CPython `list` vs `collections.deque`

```
  Python list (Contiguous Array)          collections.deque (Block List)
  ┌───┬───┬───┬───┬───┐                   ┌──────────────┐     ┌──────────────┐
  │ 0 │ 1 │ 2 │ 3 │ 4 │                   │ Block 0 (64) ├────►│ Block 1 (64) │
  └───┴───┴───┴───┴───┘                   └──────────────┘     └──────────────┘
  pop(0) = O(N) memory shift!             popleft() = O(1) block pointer advance!
```

- **Rule**: NEVER use `list.pop(0)` for a FIFO queue; ALWAYS use `collections.deque.popleft()`.

---

## 3. Core Algorithmic Templates

### Template A: Valid Parentheses Delimiter Checker ($\mathcal{O}(N)$ Time)
```python
def is_valid(s: str) -> bool:
    bracket_map = {")": "(", "]": "[", "}": "{"}
    stack = []
    for char in s:
        if char in bracket_map:
            if not stack or stack.pop() != bracket_map[char]:
                return False
        else:
            stack.append(char)
    return len(stack) == 0
```

### Template B: MinStack ($\mathcal{O}(1)$ Min Retrieval)
```python
class MinStack:
    def __init__(self):
        self.stack = []
        self.min_stack = []

    def push(self, val: int):
        self.stack.append(val)
        curr_min = self.min_stack[-1] if self.min_stack else val
        self.min_stack.append(min(val, curr_min))

    def pop(self):
        self.stack.pop()
        self.min_stack.pop()

    def top(self) -> int:
        return self.stack[-1]

    def get_min(self) -> int:
        return self.min_stack[-1]
```

### Template C: Queue via Dual Stacks (Amortized $\mathcal{O}(1)$)
```python
class MyQueue:
    def __init__(self):
        self.in_stack = []
        self.out_stack = []

    def push(self, x: int):
        self.in_stack.append(x)

    def pop(self) -> int:
        self._shift()
        return self.out_stack.pop()

    def peek(self) -> int:
        self._shift()
        return self.out_stack[-1]

    def _shift(self):
        if not self.out_stack:
            while self.in_stack:
                self.out_stack.append(self.in_stack.pop())
```

---

## 4. Middleware Onion Unwinding Pattern

```
Request Ingress:  Layer 1 -> Layer 2 -> Layer 3 -> Route Handler
Response Egress:  Route Handler -> Layer 3' -> Layer 2' -> Layer 1' (LIFO Unwinding)
```
- Inbound middleware pushes cleanup/response logic onto an execution Stack.
- Outbound responses pop and unwind the stack in reverse order.

---

## 5. The Golden Rules for Backend Engineers
1. **Never use `list.pop(0)`**; use `collections.deque.popleft()` for $\mathcal{O}(1)$ speedups exceeding $2{,}000\times$.
2. **Always check `if not stack:`** before calling `pop()` or inspecting `stack[-1]` to prevent `IndexError`.
3. **Bound your queues (`deque(maxlen=K)`)** to prevent Out-Of-Memory (OOM) crashes.
4. **Use Lazy Transfer** in two-stack queues to preserve amortized $\mathcal{O}(1)$ execution.
5. **Use LIFO Compensation Stacks** for transactional rollbacks in distributed Sagas.
