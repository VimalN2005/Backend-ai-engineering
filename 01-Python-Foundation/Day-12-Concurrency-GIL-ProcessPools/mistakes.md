# Day 12: Common Concurrency & Multiprocessing Antipatterns

Avoid these 7 production bugs when implementing multi-threaded and multi-process architectures in Python.

---

### 1. Omitting `if __name__ == "__main__":` (The Windows/macOS Fork Bomb)
- **Root Cause**: Launching multiprocessing code at the root level of a script without wrapping it in an entry guard.
- **Consequence**: On Windows and macOS, multiprocessing defaults to the `spawn` start method. `spawn` imports the script from scratch in every child process. Without the guard, each child process spawns more child processes indefinitely, triggering an exponential process fork bomb that freezes the operating system.

```python
# ❌ FATAL ANTIPATTERN: Freezes Windows and macOS!
import multiprocessing

def worker():
    print("Working...")

p = multiprocessing.Process(target=worker)
p.start()  # Infinite recursive spawn bomb!

# ✅ PRODUCTION PATTERN: Mandatory entry guard
import multiprocessing

def worker():
    print("Working...")

if __name__ == "__main__":
    p = multiprocessing.Process(target=worker)
    p.start()
```

---

### 2. Using Threads to Parallelize CPU-Bound Math
- **Root Cause**: Assuming that spawning 8 threads will utilize 8 CPU cores for math-heavy code (e.g. matrix multiplication, image processing, tokenization).
- **Consequence**: Due to the GIL, all 8 threads serialize onto a single CPU core. Worse, constant GIL release and re-acquisition overhead makes multi-threaded CPU code **slower** than simple sequential execution.

```python
# ❌ SLOWER THAN SEQUENTIAL: GIL serializes all 8 threads on 1 core!
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=8) as executor:
    results = list(executor.map(cpu_heavy_hash, large_dataset))

# ✅ PRODUCTION PATTERN: Use ProcessPoolExecutor for CPU-bound tasks
from concurrent.futures import ProcessPoolExecutor

if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(cpu_heavy_hash, large_dataset))
```

---

### 3. Passing Unpickleable Objects Across Process Boundaries
- **Root Cause**: Passing closures, lambdas, open file descriptors, database connections, or active generators to child processes.
- **Consequence**: Multiprocessing uses Python's `pickle` protocol to serialize arguments. Unpickleable objects raise `_pickle.PicklingError: Can't pickle <function <lambda>>: attribute lookup failed`.

```python
# ❌ FAILS: Lambda cannot be pickled across process boundaries
with ProcessPoolExecutor() as executor:
    executor.submit(lambda x: x ** 2, 10)  # Raises PicklingError!

# ✅ PRODUCTION PATTERN: Use top-level named functions and plain data structures
def square(x: int) -> int:
    return x ** 2

if __name__ == "__main__":
    with ProcessPoolExecutor() as executor:
        executor.submit(square, 10)
```

---

### 4. Assuming the GIL Makes Compound Operations Thread-Safe
- **Root Cause**: Believing that because CPython has a GIL, operations like `cache[key] = cache.get(key, 0) + 1` or `counter += 1` are safe from race conditions.
- **Consequence**: The GIL only protects CPython C-level memory structures. Python bytecode instructions interleave between read and write operations, causing silent data corruption and lost updates in high-concurrency microservices.

```python
# ❌ RACE CONDITION: Interleaves bytecode across threads!
def record_hit(cache, key):
    cache[key] = cache.get(key, 0) + 1  # Lost updates!

# ✅ PRODUCTION PATTERN: Synchronize with threading.Lock
lock = threading.Lock()
def record_hit(cache, key):
    with lock:
        cache[key] = cache.get(key, 0) + 1
```

---

### 5. Self-Deadlock via Recursive Calls on Standard `Lock`
- **Root Cause**: Calling a locked helper method from inside a method that already acquired a standard `threading.Lock`.
- **Consequence**: The thread hangs indefinitely waiting for itself to release the lock, completely freezing the worker thread.

```python
# ❌ DEADLOCK: Thread waits on its own lock
lock = threading.Lock()

def outer():
    with lock:
        inner()

def inner():
    with lock:  # DEADLOCK! Standard Lock is not reentrant.
        print("Done")

# ✅ PRODUCTION PATTERN: Use threading.RLock (Reentrant Lock)
rlock = threading.RLock()

def outer():
    with rlock:
        inner()

def inner():
    with rlock:  # Succeeded! Same thread can re-acquire RLock.
        print("Done")
```

---

### 6. Leaving Non-Daemon Background Threads Hanging
- **Root Cause**: Spawning background logging or heartbeat threads without setting `daemon=True`.
- **Consequence**: CPython will not terminate as long as any non-daemon thread is alive. When the main program finishes, the terminal or container hangs indefinitely.

```python
# ❌ HANGS THE PROCESS ON SHUTDOWN: Non-daemon thread blocks exit
t = threading.Thread(target=heartbeat_loop)
t.start()

# ✅ PRODUCTION PATTERN: Mark background support threads as daemon
t = threading.Thread(target=heartbeat_loop, daemon=True)
t.start()  # Exits cleanly when main thread completes
```

---

### 7. Over-Subscribing Worker Pools
- **Root Cause**: Setting `max_workers = 100` on a `ProcessPoolExecutor` on an 8-core CPU.
- **Consequence**: Spawning 100 OS processes consumes gigabytes of RAM. The operating system spends more CPU cycles performing kernel context switches than executing actual bytecode, collapsing throughput.

```python
# ❌ CPU THRASHING: 100 heavy processes fighting over 8 cores
import os
executor = ProcessPoolExecutor(max_workers=100)

# ✅ PRODUCTION PATTERN: Match CPU workers to physical core count
executor = ProcessPoolExecutor(max_workers=os.cpu_count() or 4)
```
