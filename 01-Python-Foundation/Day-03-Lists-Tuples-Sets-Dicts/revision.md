# Day 03: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or system design rounds:

---

- ⚡ **List Memory Model:**
  - Dynamic array of contiguous 64-bit pointers (`PyObject*`).
  - Appends are **amortized $\mathcal{O}(1)$** due to geometric over-allocation.
  - `list.insert(0, val)` and `list.pop(0)` are **$\mathcal{O}(N)$** because pointers must be shifted in memory.

- ⚡ **Queue Best Practice:**
  - Always use `collections.deque` for FIFO queues (`popleft()` is $\mathcal{O}(1)$ vs `list.pop(0)` which is $\mathcal{O}(N)$).

- ⚡ **Compact Dictionary Architecture (PEP 468):**
  - Modern dicts split storage into a **sparse indices table** (1-byte integers) + a **dense entries table** (`(hash, key, val)` tuples).
  - Saves 30% to 40% memory and inherently preserves insertion order.

- ⚡ **Hash Tables & Collision Resolution:**
  - Sets and Dictionaries use **Open Addressing with Pseudo-Random Perturbation** (`i = (5*i + 1 + perturb) & mask`).
  - Python does *not* use linked list chaining.
  - Average lookup/insert/delete is $\mathcal{O}(1)$; worst-case (during collisions) is $\mathcal{O}(N)$.

- ⚡ **Hashability Contract:**
  - An object is hashable if its `__hash__()` never changes over its lifecycle and it implements `__eq__()`.
  - Mutable types (`list`, `set`, `dict`) cannot be used as dictionary keys to prevent breaking hash table invariants.
  - Tuples are hashable **only if all elements inside them are also hashable**.

- ⚡ **Set-Theoretic Algebra:**
  - `A | B` (Union), `A & B` (Intersection), `A - B` (Difference), `A ^ B` (Symmetric Difference).
  - Use sets for Change Data Capture (CDC) to reconcile large database states in $\mathcal{O}(N)$ linear time.

- ⚡ **Iteration Rule:**
  - Never mutate the size of a dictionary or set while iterating directly over it (`RuntimeError`). Always iterate over `list(d.keys())` or use dictionary comprehensions.
