# Day 11: Top 5 Technical Interview Questions

---

### Question 1: How does CPython implement generator frame suspension at the virtual machine level?

#### Expected Answer:
When a standard Python function is called:
1. CPython creates a `PyFrameObject` containing local variables, the value stack, and bytecode instructions.
2. When the function returns, the frame is popped from the call stack and destroyed by the garbage collector.

When a function containing the `yield` keyword is called:
1. CPython creates a `PyGenObject` on the **heap** that wraps the `PyFrameObject`. The function's code does not execute immediately.
2. When `next(gen)` is called, the CPython evaluation loop (`_PyEval_EvalFrameDefault`) resumes the frame.
3. When the `YIELD_VALUE` bytecode instruction is executed:
   - CPython records the current bytecode instruction pointer (`f_lasti`).
   - The state of all local variables in the frame is preserved on the heap.
   - The frame status transitions from `FRAME_EXECUTING` to `FRAME_SUSPENDED`.
   - Control returns to the caller with the yielded object.
4. On the subsequent `next(gen)` call, CPython restores the heap frame and jumps directly to the instruction immediately following `f_lasti`.

---

### Question 2: What is the exact contract between an Iterable and an Iterator? Why must an Iterator implement `__iter__()` returning `self`?

#### Expected Answer:
1. **Iterable Protocol**:
   - An object is an Iterable if it implements `__iter__()`, which must return an Iterator object.
   - Examples: `list`, `dict`, `set`, `str`.
2. **Iterator Protocol**:
   - An object is an Iterator if it implements:
     - `__next__()`: Returns the next element in the sequence, or raises `StopIteration` when the sequence is exhausted.
     - `__iter__()`: Returns `self`.

#### Why `Iterator.__iter__()` must return `self`:
This ensures that Iterators are themselves valid Iterables. It enables:
- Passing an iterator directly into functions expecting an iterable (e.g., `for x in my_iterator:`, `list(my_iterator)`, `sum(my_iterator)`).
- Slicing and consuming iterators partially with functions like `itertools.islice(my_iterator, 5)`.

---

### Question 3: How does `yield from` work under the hood (PEP 380), and how does it handle exceptions and return values?

#### Expected Answer:
`yield from <subgenerator>` establishes a direct, bidirectional link between the outer caller and the inner subgenerator:
1. **Transparent Value Forwarding**:
   Values yielded by the subgenerator are passed directly to the caller of the delegating generator, completely bypassing intermediate Python evaluation loops.
2. **Bidirectional Communication (`.send()` and `.throw()`)**:
   Values sent via `delegating_gen.send(val)` are sent directly to `subgenerator.send(val)`. Exceptions injected via `delegating_gen.throw(exc)` are passed into `subgenerator.throw(exc)`.
3. **Return Value Extraction**:
   When the subgenerator terminates via `return expr`, CPython raises `StopIteration(expr)`. The `yield from` expression catches this `StopIteration` automatically and evaluates to `expr`:
   ```python
   result = yield from subgen()  # result gets the value passed to return in subgen!
   ```

---

### Question 4: What is PEP 479, and why does raising `StopIteration` inside a generator cause a `RuntimeError` in modern Python?

#### Expected Answer:
Prior to Python 3.5, if a function called inside a generator raised `StopIteration` (for example, `next(empty_iterator)`), the exception would propagate out of the generator. Python's `for` loop would catch the `StopIteration` and assume the outer generator had naturally completed, silently truncating iteration prematurely.

#### PEP 479 Change:
Under PEP 479 (default in Python 3.7+):
- CPython catches any uncaught `StopIteration` escaping from a generator function and transforms it into a `RuntimeError: generator raised StopIteration`.
- **The Rule**: A generator must terminate by running off the end of the function or executing a **`return`** statement. Raising `StopIteration` manually inside a generator is strictly an error.

---

### Question 5: How do generators provide natural backpressure in high-throughput streaming architectures (e.g., AI token chunking, Kafka pipelines)?

#### Expected Answer:
In streaming architectures, **backpressure** prevents a fast producer from overwhelming a slower consumer and causing buffer bloat or memory exhaustion:

1. **Pull-Based Model**:
   Generators operate strictly on a **pull-based (demand-driven)** model. The producer code only advances when the downstream consumer explicitly calls `next()`.
2. **Zero In-Flight Buffering**:
   If the consumer blocks (e.g., waiting for an external LLM API response or writing to disk), the producer remains suspended at its `yield` statement. No new records are fetched or processed.
3. **Bounded Memory**:
   Because items are produced on demand, memory usage remains strictly $\mathcal{O}(1)$ regardless of whether the pipeline processes 1,000 or 1,000,000,000 records.
