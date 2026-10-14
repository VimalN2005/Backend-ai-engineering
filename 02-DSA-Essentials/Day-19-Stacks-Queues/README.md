# Day 19: Stacks & Queues: Order Preservation, LIFO/FIFO Mechanics & Middleware Pipelines

In enterprise backend engineering and distributed systems, controlling the **temporal order of execution** is fundamental. Whether managing call stacks during recursive parsing, buffering asynchronous background jobs, unwinding database transaction savepoints, or routing HTTP requests through nested middleware layers, **Stacks (LIFO)** and **Queues (FIFO)** govern the flow of data.

---

## 1. The Stack & Queue Foundations in Backend Systems

Stacks and Queues are sequential abstract data types defined by their ingress and egress policies:

```
               STACK: LIFO (Last-In, First-Out)
          Push ──► [ Top Element ] ──► Pop (Same End)
                   [ Element 2   ]
                   [ Element 1   ]
                   [ Base        ]

               QUEUE: FIFO (First-In, First-Out)
    Enqueue ──► [ Back / Tail ] ───► [ Front / Head ] ──► Dequeue
                (Write Head)          (Read Head)
```

### Core Backend Use Cases:
- **Stacks (LIFO)**:
  - Call stack execution and traceback generation in Python runtime (`sys._getframe()`).
  - Middleware request/response "onion" architecture in FastAPI/Django.
  - Undo/Redo buffers and transactional saga compensation rollbacks.
  - Syntax compilers, balanced delimiter verification, and Reverse Polish Notation (RPN) evaluation.
- **Queues (FIFO)**:
  - Distributed background task queues (Celery, BullMQ, Redis `LPUSH`/`RPOP`).
  - Event loop microtask and macrotask scheduling in Asyncio and Node.js.
  - Rate limiting leaky bucket buffers and network packet throttling.
  - Bounded consumer buffers managing backpressure between fast producers and slow database writers.

---

## 2. CPython Memory Mechanics: `list` vs `collections.deque`

Why should you **never** use a Python `list` as a FIFO queue?

### 1. Python `list` Internals (Contiguous Dynamic Array)
- A Python list is backed by a single contiguous C array of 8-byte object pointers (`PyObject**`).
- `list.append(x)` and `list.pop()` operate at the **end** of the array in amortized $\mathcal{O}(1)$ time.
- However, `list.pop(0)` removes the element from index 0. Because the array is contiguous, CPython must invoke C's `memmove()` to **shift all remaining $N - 1$ elements to the left by one position**:
  $$\text{Cost of } \text{list.pop}(0) = \mathcal{O}(N)$$
- For a queue of $N = 100{,}000$ tasks, 100,000 dequeues require $10{,}000{,}000{,}000$ pointer shifts, causing seconds of frozen CPU time!

### 2. `collections.deque` Internals (Doubly Linked Block List)
CPython's `collections.deque` is implemented in C as a **doubly linked list of fixed-size memory blocks**:
- Each block is a contiguous C array holding exactly **64 object pointers** (`BLOCKLEN = 64`).
- Blocks are linked together via `leftblock` and `rightblock` pointers.

```
       Block 1 (64 ptrs)        Block 2 (64 ptrs)        Block 3 (64 ptrs)
     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
  ◄──┤ [left] ... [right]├────►┤ [left] ... [right]├────►┤ [left] ... [right]├──►
     └──────────────────┘     └──────────────────┘     └──────────────────┘
              ▲                                                 ▲
          leftindex                                         rightindex
       (O(1) popleft)                                      (O(1) append)
```

- **`deque.append(x)`**: Inserts into the rightmost block. If full, allocates a new 64-element block in $\mathcal{O}(1)$.
- **`deque.popleft()`**: Increments `leftindex` within the leftmost block. If the block empties, frees the block and advances the pointer in **strictly $\mathcal{O}(1)$ time**. Zero elements are shifted!

| Metric | Python `list` as Queue | `collections.deque` as Queue |
| :--- | :--- | :--- |
| **`append(x)` (Enqueue)** | Amortized $\mathcal{O}(1)$ | **Strict $\mathcal{O}(1)$** |
| **`pop()` (Stack LIFO)** | Amortized $\mathcal{O}(1)$ | **Strict $\mathcal{O}(1)$** |
| **`pop(0)` / `popleft()` (FIFO)** | **$\mathcal{O}(N)$ memory copy** | **Strict $\mathcal{O}(1)$** |
| **Random Index Access (`q[i]`)** | **Instant $\mathcal{O}(1)$** | $\mathcal{O}(N)$ block traversal |
| **Memory Overhead** | Low (contiguous array) | Slightly higher (block pointers) |

---

## 3. Stack Architecture & Call Stack Overflow

The call stack in CPython manages execution frames during function invocations:
```python
def deep_recursion(n):
    if n == 0:
        return
    deep_recursion(n - 1)
```
- Each function call pushes a `PyFrameObject` to the call stack containing local variables, bytecode instruction pointer, and evaluation stack.
- To prevent C-level stack overflow (segmentation fault), Python enforces a recursion limit (default: 1000):
  ```python
  import sys
  print(sys.getrecursionlimit())  # 1000
  ```
- Exceeding this limit raises `RecursionError: maximum recursion depth exceeded`.
- **Engineering Principle**: Convert deep recursive algorithms (e.g. tree/graph traversals, AST parsing) to **explicit iterative Stacks** to execute millions of steps safely on the heap without hitting recursion limits.

---

## 4. Queue Architecture, Bounded Buffers & Backpressure

When downstream consumers (e.g. database workers or external API clients) process tasks slower than incoming producers:
- An **unbounded queue** grows indefinitely, consuming all available system RAM until the Linux Out-Of-Memory (OOM) killer terminates the process.
- A **bounded queue** (`collections.deque(maxlen=K)` or `asyncio.Queue(maxsize=K)`) enforces backpressure:
  1. **Drop Oldest Strategy**: Automatically discards oldest elements (`maxlen` in `deque`).
  2. **Reject / Fail Fast Strategy**: Returns HTTP 429 / 503 to client when queue is full.
  3. **Producer Throttling**: Blocks the producer thread/coroutine (`await queue.put()`) until space becomes available.

---

## 5. Pattern 1: Balanced Delimiters & Valid Parentheses

Validating nested syntax in JSON, SQL, or code formatters requires matching every closing bracket with its most recent open bracket:

```
String: "{ [ ( ) ] }"
Char: '{' -> Push to Stack: ['{']
Char: '[' -> Push to Stack: ['{', '[']
Char: '(' -> Push to Stack: ['{', '[', '(']
Char: ')' -> Matches top '('! Pop: ['{', '[']
Char: ']' -> Matches top '['! Pop: ['{']
Char: '}' -> Matches top '{'! Pop: []
Stack empty at end -> VALID!
```

### Generic Balanced Delimiter Invariant
1. If incoming character is an **opening delimiter** (`(`, `[`, `{`), push to stack.
2. If incoming character is a **closing delimiter** (`)`, `]`, `}`):
   - If stack is empty $\implies$ **Invalid** (closing bracket without opener).
   - If `stack.pop()` does not match the closing delimiter $\implies$ **Invalid** (mismatched nesting like `(]`).
3. After scanning entire input, return `len(stack) == 0`.

---

## 6. Pattern 2: Postfix Evaluation (Reverse Polish Notation)

In standard infix arithmetic (`3 + 4 * 2`), operator precedence and parentheses require complex parsing.
In **Postfix / Reverse Polish Notation (RPN)** (`3 4 2 * +`):
- Operators follow their operands.
- Precedence is inherently unambiguous with **zero parentheses required**.

```
RPN Tokens: ["4", "13", "5", "/", "+"]

Token "4"  -> Push: [4]
Token "13" -> Push: [4, 13]
Token "5"  -> Push: [4, 13, 5]
Token "/"  -> Pop b=5, a=13. Evaluate: int(13 / 5) = 2. Push: [4, 2]
Token "+"  -> Pop b=2, a=4.  Evaluate: 4 + 2 = 6. Push: [6]
Final Result: 6
```
This is the exact evaluation mechanism used by compilers, calculators, and database expression evaluators.

---

## 7. Pattern 3: Dual-Stack Queue Implementation

Implementing a FIFO Queue using only two LIFO Stacks (`in_stack` and `out_stack`):

```
       Enqueue ──► [ in_stack ] ──► (Transfer on demand) ──► [ out_stack ] ──► Dequeue
```

### The Lazy Transfer Invariant
- **`push(x)`**: Always push onto `in_stack` in $\mathcal{O}(1)$ time.
- **`pop()` / `peek()`**:
  - If `out_stack` has elements: pop/peek from `out_stack` in $\mathcal{O}(1)$ time.
  - If `out_stack` is empty: pop all elements from `in_stack` and push them onto `out_stack` (reversing their order to FIFO!), then pop from `out_stack`.

### Amortized Analysis ($\mathcal{O}(1)$ Average):
Every element is pushed to `in_stack` once, transferred to `out_stack` once, and popped from `out_stack` once. Across $N$ operations, total element moves $\le 3N$. The amortized cost per operation is **strictly $\mathcal{O}(1)$**.

---

## 8. Pattern 4: Min Stack / Max Stack ($\mathcal{O}(1)$ Time and Space)

Standard stacks provide $\mathcal{O}(1)$ `push()` and `pop()`, but finding the minimum element requires an $\mathcal{O}(N)$ scan.
A **MinStack** retrieves the minimum element in **$\mathcal{O}(1)$ time**:

### Implementation Strategies:
1. **Parallel Tracking Stack**: Maintain a secondary stack `min_stack` recording the running minimum at each depth. When an element is pushed, push $\min(\text{val}, \text{min\_stack}[-1])$.
2. **Delta Encoding (Zero Extra Space)**: Store the difference $(val - \text{current\_min})$ in a single stack. If difference is negative, a new minimum has been found!

---

## 9. Pattern 5: Monotonic Queue Basics

A **Monotonic Queue** maintains elements in strictly increasing or decreasing order by evicting smaller (or larger) elements before appending. It solves window bottleneck problems (like finding the minimum or maximum in a streaming queue) in amortized $\mathcal{O}(1)$ time.

---

## 10. Backend Application 1: HTTP Middleware Onion Architecture

Web frameworks like Django, FastAPI, Starlette, and Express execute middleware using a Stack-based "onion" model:

```
                  THE MIDDLEWARE ONION PIPELINE
                     Incoming HTTP Request
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │  Layer 1: Security & CORS Middleware       │
        │  ┌─────────────────────────────────────┐  │
        │  │  Layer 2: Authentication Middleware │  │
        │  │  ┌───────────────────────────────┐  │  │
        │  │  │  Layer 3: Rate Limiting & Logs│  │  │
        │  │  │  ┌─────────────────────────┐  │  │  │
        │  │  │  │   Route Handler Endpoint│  │  │  │
        │  │  │  └─────────────────────────┘  │  │  │
        │  │  │  ◄── Outbound Response ───────┘  │  │
        │  │  └──────────────────────────────────┘  │
        │  └────────────────────────────────────────┘
                              │
                              ▼
                     Outgoing HTTP Response
```

- **Request Phase (Forward Order)**: Each middleware inspects the request, starts timers, and pushes its cleanup context to an execution Stack.
- **Endpoint Execution**: Handler produces the HTTP response.
- **Response Phase (Reverse LIFO Order)**: The framework **unwinds the stack**, executing the post-response handlers in the exact reverse order:
  $$\text{Exec: } L_1 \to L_2 \to L_3 \to \text{Handler} \to L_3' \to L_2' \to L_1'$$
- If an exception occurs, the stack unwinds immediately, allowing error handling middleware to catch and serialize the error into a JSON 500 response.

---

## 11. Backend Application 2: Distributed Job Queues & Worker Pools

In asynchronous architectures, tasks cannot execute synchronously within the HTTP request lifecycle (e.g. sending emails, generating reports, transcribing audio):
1. **Producer**: Web API enqueues a job payload to a Redis list (`LPUSH queue:jobs JSON_PAYLOAD`).
2. **Broker**: Holds the FIFO queue of pending work.
3. **Consumers (Workers)**: Background worker processes block on the queue (`BRPOP queue:jobs 0`), receive tasks in FIFO order, execute them, and acknowledge completion.
4. **Dead-Letter Queue (DLQ)**: If a job fails after $M$ retries, it is moved to a separate DLQ for manual engineer inspection.

---

## 12. Backend Application 3: Transactional Undo/Redo & Saga Rollback

In distributed microservices (the **Saga Pattern**), a transaction spanning multiple services (Order Service $\to$ Payment Service $\to$ Inventory Service) cannot use ACID database transactions:
- The orchestrator maintains a **Compensation Stack**.
- Each successful step pushes its corresponding inverse compensation action onto the stack:
  1. `ReserveInventory()` $\implies$ Push `ReleaseInventory()`
  2. `ChargeCard()` $\implies$ Push `RefundCard()`
  3. `CreateOrder()` $\implies$ Push `CancelOrder()`
- If step 3 fails:
  - The engine pops and executes compensation actions in **strict LIFO order**:
    1. Pop and execute `RefundCard()`
    2. Pop and execute `ReleaseInventory()`
- This guarantees system consistency without leaving orphaned database states.

---

## 13. Backend Application 4: Leaky Bucket Rate Limiting

The **Leaky Bucket** rate limiter models a queue with constant leak rate:
- Incoming HTTP requests enter a FIFO queue of capacity $B$.
- If the queue is full ($B$ requests pending), incoming requests overflow and are dropped immediately with HTTP 429.
- The queue leaks requests at a constant rate $R$ requests/second to downstream servers, smoothing spiky traffic into an even, predictable flow.

---

## 14. Complexity Comparison Matrix

| Data Structure / Pattern | Push / Enqueue | Pop / Dequeue | Peek | Auxiliary Space | Key Backend Advantage |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Python `list` (as Stack)** | $\mathcal{O}(1)$ amortized | $\mathcal{O}(1)$ amortized | $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | Minimal memory, contiguous cache hits |
| **Python `list` (as Queue)** | $\mathcal{O}(1)$ amortized | **$\mathcal{O}(N)$ memory shift** | $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | **ANTIPATTERN - Never use in prod!** |
| **`collections.deque`** | **Strict $\mathcal{O}(1)$** | **Strict $\mathcal{O}(1)$** | $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | 64-element blocks eliminate memory copy |
| **Queue via Dual Stacks** | $\mathcal{O}(1)$ | **$\mathcal{O}(1)$ amortized** | $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | Pure functional / immutable queues |
| **MinStack** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | Constant-time minimum extraction |

---

## 15. The Golden Rules for Backend Engineers
1. **Never use `list.pop(0)` in production**; always use `collections.deque.popleft()`.
2. **Always bound your queues** (`deque(maxlen=K)`); unbounded queues cause memory exhaustion.
3. **Check `if not stack:` before calling `stack.pop()`** to prevent unhandled `IndexError`.
4. **Convert deep recursion to explicit iterative Stacks** to eliminate `RecursionError`.
5. **Use LIFO stacks for rollback compensations** in distributed transactions (Sagas) to undo actions in exact reverse order of execution.

---

## Summary Checklist for Day 19
- [x] Master LIFO vs FIFO execution models and their system-level implications.
- [x] Understand CPython `deque` 64-element block list internals vs `list` contiguous memory.
- [x] Implement the balanced delimiters / valid parentheses validation engine.
- [x] Evaluate Reverse Polish Notation (RPN) expressions via stack operand evaluation.
- [x] Prove amortized $\mathcal{O}(1)$ complexity of Queue using Dual Stacks.
- [x] Implement MinStack with $\mathcal{O}(1)$ time for `get_min()`.
- [x] Architect a production HTTP Middleware Pipeline with LIFO response unwinding.
- [x] Build bounded task worker queues with backpressure and dead-letter queue (DLQ) support.
