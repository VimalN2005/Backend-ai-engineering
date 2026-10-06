# Day 11: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **Iterable vs. Iterator Contract:**
  - **Iterable**: Implements `__iter__()` returning an Iterator.
  - **Iterator**: Implements `__next__()` (yields item or raises `StopIteration`) and `__iter__()` returning `self`.

- ⚡ **CPython Frame Suspension (`PyGenObject`):**
  - Normal functions destroy call frames on return.
  - Generators allocate a `PyFrameObject` on the **heap**.
  - `yield` freezes instruction pointer `f_lasti` and local variables. Calling `next()` resumes execution from the exact instruction without reallocating state.

- ⚡ **Memory Complexity Guarantee:**
  - Eager list evaluation: $\mathcal{O}(N)$ memory. A 10M record list consumes hundreds of megabytes.
  - Lazy generator streaming: $\mathcal{O}(1)$ memory. Allocates bytes regardless of stream size.

- ⚡ **Subgenerator Delegation (`yield from`):**
  - Creates a direct, bidirectional, zero-overhead C-level pipe between the caller and subgenerator.
  - Transparently forwards values, exceptions (`.throw()`), and teardown signals (`.close()`).
  - Automatically captures the subgenerator's return value: `ret_val = yield from subgen()`.

- ⚡ **Bidirectional Coroutines (`.send()`):**
  - `val = yield expr`: Yields `expr` to caller and assigns whatever was sent via `.send(val)` to `val`.
  - **The Priming Rule**: Must call `next(gen)` or `gen.send(None)` before sending non-`None` values.

- ⚡ **Resource Teardown Safety:**
  - When consumers break early or invoke `gen.close()`, CPython raises `GeneratorExit` inside the frame.
  - Always wrap resource-intensive logic in `try...finally` (or context managers) to guarantee database connections and sockets are closed.

- ⚡ **PEP 479 Rule:**
  - Never manually raise `StopIteration` inside a generator.
  - Always terminate generators with a clean **`return`** statement. Raising `StopIteration` converts to `RuntimeError: generator raised StopIteration`.

- ⚡ **Key `itertools` Combinators:**
  - `itertools.islice(it, start, stop)`: Slices an infinite or large generator in $\mathcal{O}(1)$ memory.
  - `itertools.chain(*iterables)`: Concatenates streams without intermediate list allocations.
  - `itertools.tee(it, n)`: Forks streams; beware of memory leaks if consumers read at different speeds.
