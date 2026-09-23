# Day 03: Technical Interview Questions & In-Depth Engineering Answers

These questions are frequently asked in Senior Python, Backend Systems, and Database Infrastructure interviews:

---

### Q1: How does CPython's list achieve amortized $\mathcal{O}(1)$ appends under the hood?
**Answer:**
A Python list is a dynamic array of contiguous 64-bit pointers (`PyObject*`). If CPython reallocated the array by exactly 1 slot on every `.append()`, it would trigger a `realloc()` system call and copy all $N$ existing pointers on every operation, resulting in an unacceptable $\mathcal{O}(N^2)$ cumulative time.

Instead, CPython over-allocates extra capacity using an internal geometric growth formula:
$$\text{allocated} = \text{new\_size} + (\text{new\_size} \gg 3) + (\text{new\_size} < 9 \ ? \ 3 : 6)$$

When an append occurs:
1. If empty slots exist in the over-allocated buffer, the pointer is stored at index $N$, and length increments by 1. Time complexity: **$\mathcal{O}(1)$**.
2. If the buffer is full, CPython allocates a larger contiguous block, copies over existing pointers, and frees the old buffer. This single resize takes $\mathcal{O}(N)$ time.

Because resizing happens exponentially less often as the list grows ($4 \to 8 \to 16 \to 24 \to 32 \to 40 \dots$), the expensive resizing cost is distributed ("amortized") across thousands of instant appends. Thus, list appending is **amortized $\mathcal{O}(1)$**.

---

### Q2: Explain the memory architecture of CPython's compact dictionary (PEP 468). Why was it introduced?
**Answer:**
Prior to Python 3.6, CPython dictionaries were implemented as sparse hash tables where every bucket was a 24-byte struct:
`struct { Py_hash_t hash; PyObject *key; PyObject *value; }`

Because open-addressing hash tables require ~33% of buckets to remain empty to prevent collision cascades, roughly one-third of this large 24-byte array was empty, wasting significant memory. Furthermore, iteration order was pseudo-random based on hash distributions.

**Modern Compact Dict Architecture:**
CPython decoupled the sparse hash table from the stored entries:
1. **Indices Table (Sparse)**: A sparse array of small integers (1 byte `int8` for tables $< 128$ entries, 2 bytes `int16` for larger tables).
2. **Entries Table (Dense)**: A tightly packed array where entries are appended sequentially in the exact order they arrive:
   `entries = [(hash, key_ptr, value_ptr), ...]`

**Benefits:**
- **30% to 40% RAM Reduction**: Unoccupied slots in the hash table now consume only 1 byte (an index of `-1`) rather than 24 bytes.
- **Deterministic Insertion Order**: Because the `entries` table is filled sequentially, iterating over the dictionary naturally visits keys in their exact insertion order.

---

### Q3: What is a hash collision, and how does CPython resolve collisions in sets and dicts?
**Answer:**
A hash collision occurs when two distinct keys $K_1 \neq K_2$ yield identical bucket indices after modulo masking:
$$\text{index} = \text{hash}(K) \ \& \ \text{mask}$$

Unlike languages like Java (which use **Separate Chaining** with linked lists or red-black trees at each bucket), CPython uses **Open Addressing with Pseudo-Random Perturbation**:
1. When bucket $i$ is already occupied by a different key, CPython does not use simple linear probing ($i + 1$), which causes primary clustering.
2. Instead, it incorporates higher-order bits of the original 64-bit hash via a perturbation shift:
   $$\text{perturb} \gg= 5; \quad i = (5 \cdot i + 1 + \text{perturb}) \ \& \ \text{mask}$$
3. This perturbation formula generates a deterministic, pseudo-random sequence that traverses every slot in the table without getting stuck in loops until an empty bucket or matching key is found.

---

### Q4: What makes an object "hashable" in Python, and why can't mutable objects be dictionary keys?
**Answer:**
According to Python's data model contract, an object is **hashable** if:
1. It implements a `__hash__()` method that returns an integer that **never changes** during the object's lifetime.
2. It implements an `__eq__()` method for equality comparison.
3. If $a == b$, then $\text{hash}(a) == \text{hash}(b)$ must be strictly True.

**Why Mutable Objects (like `list` or `dict`) Cannot Be Keys:**
If a `list` were hashable, inserting `my_list = [1, 2]` into a dictionary would place the entry in bucket $B_{\text{initial}}$ calculated from `hash([1, 2])`.

If the developer subsequently mutated the list via `my_list.append(3)`, the list's hash would change. A future lookup `my_dict[my_list]` would calculate a completely different bucket $B_{\text{new}}$, where the key does not exist! The entry in $B_{\text{initial}}$ would become permanently orphaned and unreachable, corrupting the hash table. Hence, mutable types raise `TypeError: unhashable type`.

---

### Q5: Why is `collections.deque` asymptotically superior to `list` for implementing FIFO queues?
**Answer:**
- **`list.pop(0)` is $\mathcal{O}(N)$**: Python lists are stored as contiguous memory arrays. When the element at index 0 is removed, CPython must execute an internal `memmove()` to shift all remaining $N-1$ pointers one slot to the left. For a queue processing millions of jobs, this introduces massive CPU cache invalidation and latency spikes.
- **`collections.deque.popleft()` is $\mathcal{O}(1)$**: A `deque` (double-ended queue) is implemented internally as a doubly-linked list of fixed-size 64-element memory blocks (`BLOCKLEN = 64`). Popping from either end requires only advancing a pointer within the head/tail block, or deallocating an empty boundary block in $\mathcal{O}(1)$ constant time without shifting any remaining elements.
