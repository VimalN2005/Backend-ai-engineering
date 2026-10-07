# Day 12: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **Concurrency vs. Parallelism:**
  - **Concurrency**: Structuring tasks to interleave execution (handling many things at once).
  - **Parallelism**: Simultaneous physical execution on multiple hardware CPU cores (doing many things at once).

- ⚡ **The CPython GIL Reality Check:**
  - The GIL serializes Python bytecode execution to **one thread at a time** per process to protect internal C reference counts.
  - **I/O-Bound**: The GIL is explicitly released during blocking I/O syscalls (network, disk, `sleep`). Threads provide true concurrent speedups.
  - **CPU-Bound**: The GIL is continuously held. Multi-threaded CPU math is slower than sequential execution. Must use `ProcessPoolExecutor`!

- ⚡ **The Thread-Safety Fallacy:**
  - The GIL does **not** protect application data!
  - `counter += 1` expands to 4 bytecode instructions. Threads can switch context mid-operation. Always use `threading.Lock` around shared mutable state.

- ⚡ **Lock vs. RLock:**
  - `Lock`: Standard mutual exclusion. A thread calling `acquire()` twice deadlocks itself.
  - `RLock`: Reentrant lock. The owning thread can acquire it multiple times without deadlocking. Ideal for recursive methods.

- ⚡ **The Windows/macOS Multiprocessing Guard:**
  - On `spawn`-based platforms (Windows, macOS, Python 3.14+), child processes re-import the entry module from scratch.
  - Omitting `if __name__ == "__main__":` creates an infinite process fork bomb that freezes the operating system.

- ⚡ **IPC & Pickling Cost:**
  - Processes do not share RAM. Data passed between processes is serialized via `pickle`.
  - Passing unpickleable objects (lambdas, generators, open sockets, DB connections) raises `PicklingError`.

- ⚡ **Optimal Worker Pool Sizing:**
  - **CPU-Bound (`ProcessPoolExecutor`)**: $N_{\text{workers}} = \texttt{os.cpu\_count()}$.
  - **I/O-Bound (`ThreadPoolExecutor`)**: $N_{\text{workers}} = N_{\text{cores}} \times (1 + \text{Wait}/\text{Compute})$. Typically 10 to 50 threads.

- ⚡ **Free-Threaded Python (PEP 703 / Python 3.13+):**
  - Optional `--disable-gil` CPython build using Biased Reference Counting and `mimalloc`.
  - Enables true multi-core parallel execution of Python bytecode across standard threads without process overhead.
