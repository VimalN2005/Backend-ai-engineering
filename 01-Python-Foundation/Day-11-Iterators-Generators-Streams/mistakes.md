# Day 11: Common Iterator & Generator Antipatterns

Avoid these 7 production bugs when implementing streaming pipelines, token chunkers, and memory-bounded batchers.

---

### 1. Exhausting a Generator and Expecting Reusability
- **Root Cause**: Passing an already-consumed generator to another consumer or re-iterating over it in a second loop.
- **Consequence**: Generators are stateful, single-use streams. Once exhausted, subsequent iterations terminate immediately with 0 items without throwing an error, causing silent data drops.

```python
# ❌ SILENT BUG: Second loop executes 0 times!
def get_tokens():
    yield from ["token_1", "token_2", "token_3"]

tokens = get_tokens()
print("First pass count:", sum(1 for _ in tokens))  # Prints 3
print("Second pass count:", sum(1 for _ in tokens)) # Prints 0! (Stream exhausted)

# ✅ PRODUCTION PATTERN: Pass an iterable factory or recreate the generator
def process_tokens(token_factory):
    count_1 = sum(1 for _ in token_factory())
    count_2 = sum(1 for _ in token_factory())
```

---

### 2. Nesting Eager List Comprehensions Inside Generator Pipelines
- **Root Cause**: Accidentally mixing square brackets `[...]` inside round brackets `(...)`.
- **Consequence**: Defeats the entire purpose of streaming. The inner list comprehension materializes the entire multi-gigabyte dataset into heap RAM, triggering the Linux OOM killer.

```python
# ❌ FATAL ANTIPATTERN: Allocates 10 million ints in RAM before generator starts!
stream = (x * 2 for x in [i for i in range(10_000_000)])

# ✅ PRODUCTION PATTERN: Chain generator expressions end-to-end
stream = (x * 2 for x in (i for i in range(10_000_000)))  # Strict O(1) memory!
```

---

### 3. Attempting to `.send()` Into an Unprimed Generator
- **Root Cause**: Calling `gen.send("my_val")` on a freshly instantiated generator without advancing it to its first `yield` expression.
- **Consequence**: Raises `TypeError: can't send non-None value to a just-started generator`.

```python
# ❌ CRASHES: Unprimed coroutine
def consumer():
    while True:
        val = yield
        print(f"Received: {val}")

c = consumer()
c.send("data")  # Raises TypeError!

# ✅ PRODUCTION PATTERN: Prime the generator with next() or .send(None)
c = consumer()
next(c)         # Or c.send(None)
c.send("data")  # Succeeded
```

---

### 4. Manually Raising `StopIteration` Inside a Generator (PEP 479)
- **Root Cause**: Manually raising `StopIteration()` to terminate a generator function.
- **Consequence**: Under PEP 479 (enforced in Python 3.7+), uncaught `StopIteration` exceptions inside a generator are transformed into `RuntimeError: generator raised StopIteration`, crashing the application.

```python
# ❌ BROKEN IN MODERN PYTHON:
def bounded_stream(items):
    for item in items:
        if item == "STOP":
            raise StopIteration  # Transformed to RuntimeError!
        yield item

# ✅ PRODUCTION PATTERN: Use a simple return statement to terminate
def bounded_stream(items):
    for item in items:
        if item == "STOP":
            return  # Clean, idiomatic generator termination
        yield item
```

---

### 5. Omitting `try...finally` for Resource Teardown
- **Root Cause**: Acquiring sockets, database connections, or file descriptors inside a generator without `try...finally`.
- **Consequence**: If a consumer breaks early (`break`) or the generator is garbage collected, the remaining code in the generator function never runs, leaking open file descriptors and connection pool sockets.

```python
# ❌ RESOURCE LEAK: File descriptor leaks if consumer breaks early
def read_log_records(filepath):
    f = open(filepath, "r")
    for line in f:
        yield line
    f.close()  # Never executed if consumer breaks out of loop!

# ✅ PRODUCTION PATTERN: Context manager or try...finally guarantees cleanup on .close()
def read_log_records(filepath):
    with open(filepath, "r") as f:
        for line in f:
            yield line
```

---

### 6. Memory Leaks with `itertools.tee` on Diverging Consumers
- **Root Cause**: Using `itertools.tee(stream, 2)` to fork a stream, but consuming one iterator much faster than the other.
- **Consequence**: `itertools.tee` buffers all items that one iterator has consumed but the other has not yet read in an internal FIFO queue. If the consumers diverge, memory consumption balloons to $\mathcal{O}(N)$.

```python
# ❌ MEMORY LEAK RISK: Diverging consumers
import itertools

stream = (x for x in range(10_000_000))
iter1, iter2 = itertools.tee(stream, 2)

# iter1 runs to completion while iter2 sits idle
results = list(iter1)  # Internal tee queue now holds 10 million elements in RAM!
```

---

### 7. Attempting to Slice Generators with Subscript Notation
- **Root Cause**: Attempting to slice a generator directly via `gen[0:10]`.
- **Consequence**: Generators do not implement `__getitem__` and cannot be indexed. Raises `TypeError: 'generator' object is not subscriptable`.

```python
# ❌ FAILS:
stream = (x for x in range(100))
top_10 = stream[0:10]  # Raises TypeError: 'generator' object is not subscriptable

# ✅ PRODUCTION PATTERN: Use itertools.islice for zero-memory slicing
import itertools

stream = (x for x in range(100))
top_10 = list(itertools.islice(stream, 0, 10))  # O(1) memory slicing
```
