# Day 03: Data Structures Internals (Lists, Tuples, Sets & Dictionaries)

---

## 1. Definition
Python's built-in collections are foundational to all backend and data pipelines:
- **`list` (`PyListObject`)**: A mutable, dynamic array of contiguous 64-bit object pointers (`PyObject*`).
- **`tuple` (`PyTupleObject`)**: An immutable, fixed-length array of object pointers, optimized for fast creation and memory locality.
- **`set` (`PySetObject`)**: A mutable, unordered collection of unique elements implemented as an open-addressing hash table storing only keys.
- **`dict` (`PyDictObject`)**: A high-performance, insertion-ordered key-value mapping built using an optimized **compact hash table architecture** (PEP 468 / Python 3.6+).

---

## 2. Why? (Problem it Solves)
Choosing the correct data structure directly dictates whether a backend API scales or collapses:
1. **Lookup Scalability**: Checking membership (`item in collection`) in a list of 100,000 items requires an $\mathcal{O}(N)$ linear scan (~5 milliseconds). In a `set` or `dict`, the same lookup executes in $\mathcal{O}(1)$ constant time (~50 nanoseconds)—**100,000x faster**.
2. **Memory Over-allocation**: Rather than reallocating memory on every single `.append()`, CPython lists use an over-allocation growth formula, providing **amortized $\mathcal{O}(1)$** appends.
3. **Compact Dictionaries**: The modern dictionary implementation reduces RAM usage by 30% to 40% compared to legacy Python 2 hash tables, while guaranteeing deterministic insertion order.

---

## 3. How? (Under the Hood / Working Principle)

### 3.1 CPython Dynamic Array Growth (`list`)
A Python list does not store actual values directly inside its buffer; it stores an array of memory pointers pointing to objects on the heap. When the internal pointer array fills up, CPython resizes it using this specific proportional over-allocation formula (`listobject.c`):

$$\text{new\_allocated} = \text{new\_size} + (\text{new\_size} \gg 3) + (\text{new\_size} < 9 \ ? \ 3 : 6)$$

- For example, when appending elements: allocated slots grow as `0 ➔ 4 ➔ 8 ➔ 16 ➔ 24 ➔ 32 ➔ 40 ➔ 52 ➔ 64...`
- Because allocations occur in geometric leaps, copying pointers during resize happens infrequently. Hence, `.append()` is **amortized $\mathcal{O}(1)$**.
- Inserting at the beginning (`list.insert(0, item)`) is **$\mathcal{O}(N)$** because all $N$ existing pointers must be shifted right in memory.

### 3.2 Compact Dictionary Architecture (PEP 468)
In legacy Python, dictionary hash tables stored a sparse array of 24-byte structs: `(hash, key_ptr, value_ptr)`. Because hash tables require ~33% empty buckets to minimize collisions, a massive amount of RAM was wasted on empty 24-byte slots.

Modern CPython splits the dictionary into two distinct tables:
1. **Indices Table (Sparse)**: A small sparse array storing only 1-byte, 2-byte, or 4-byte integer indices (e.g., `[-1, 0, -1, 1, 2, -1]`).
2. **Entries Table (Dense)**: A packed, compact array storing actual entries in exact insertion order:
   ```text
   indices = [ 1, -1,  0, -1,  2]  <-- Sparse hash buckets (small integers)
   entries = [
       0: (hash_a, "user_id", 4012),
       1: (hash_b, "role", "admin"),
       2: (hash_c, "is_active", True)
   ]  <-- Dense packed array (zero wasted space!)
   ```
This separation reduced dictionary memory by 30–40% and inherently preserved key insertion order!

### 3.3 Hash Collisions & Open Addressing
Python dictionaries and sets do **not** use separate chaining (linked lists). They use **Open Addressing with Pseudo-Random Probing**:
1. When a key is inserted, its index is computed: $i = \text{hash}(\text{key}) \ \& \ \text{mask}$.
2. If bucket $i$ is occupied by another key with a different hash (collision), CPython calculates the next probe index using a perturbation formula:
   $$\text{perturb} \gg= 5; \quad i = (5 \cdot i + 1 + \text{perturb}) \ \& \ \text{mask}$$
3. This probing sequence scans through alternative buckets in a deterministic pseudo-random order until an empty slot is located.

### 3.4 Hashability Contract
For an object to serve as a dictionary key or set element, it must implement `__hash__()` and `__eq__()` such that:
- If `a == b`, then $\text{hash}(a) == \text{hash}(b)$ **must always be True**.
- The hash value must remain strictly invariant throughout the object's lifetime.
- Mutable containers (`list`, `dict`, `set`) do not implement `__hash__` because modifying an item changes its identity, which would trap it forever in the wrong hash bucket!

---

## 4. Syntax & Basic Contract
```python
# List unpacking and slicing
first, *middle, last = [10, 20, 30, 40, 50]  # middle = [20, 30, 40]

# Set mathematical operations
admins = {"alice@cloud.io", "bob@cloud.io"}
active_users = {"bob@cloud.io", "charlie@cloud.io"}

active_admins = admins & active_users      # Intersection: {'bob@cloud.io'}
all_users = admins | active_users          # Union
revoked_admins = admins - active_users     # Difference: {'alice@cloud.io'}

# Dictionary merge operator (Python 3.9+)
default_config = {"timeout": 30, "retries": 3}
user_config = {"timeout": 60}
merged_config = default_config | user_config  # {'timeout': 60, 'retries': 3}
```

---

## 5. Example 1: Conceptual Walkthrough
Demonstrating how list over-allocation occurs under the hood:

```python
import sys

# Track list capacity jumps
numbers = []
last_size = sys.getsizeof(numbers)

for i in range(25):
    numbers.append(i)
    current_size = sys.getsizeof(numbers)
    if current_size != last_size:
        print(f"Items: {len(numbers):<2} | Allocated Size: {current_size} bytes (Memory reallocated!)")
        last_size = current_size
```

---

## 6. Example 2: Edge Cases & Gotchas

### Gotcha A: Modifying a Collection During Iteration
```python
# ANTIPATTERN: Causes RuntimeError in dict, and silent item-skipping in list!
items = {"a": 1, "b": 2, "c": 3}
for k in items:
    if k == "b":
        del items[k]  # RuntimeError: dictionary changed size during iteration

# FIX: Iterate over a static copy of keys:
for k in list(items.keys()):
    if k == "b":
        del items[k]
```

### Gotcha B: Misunderstanding `setdefault()`
`dict.setdefault(key, default)` always evaluates the default expression **eagerly**, even if the key already exists:
```python
# Inefficient: A new list() is instantiated in memory on EVERY call, even when key exists!
cache.setdefault(user_id, []).append(event)

# Optimized: Use collections.defaultdict to avoid useless memory allocations
from collections import defaultdict
cache = defaultdict(list)
cache[user_id].append(event)
```

---

## 7. Production-Grade Example
High-speed Database Snapshot Reconciliation Engine (Change Data Capture) used to compare 100,000 database records and isolate New, Modified, and Deleted entries in linear time.
*(See complete runnable code in [example_02.py](example_02.py))*

```python
from typing import Dict, Set, Tuple

def reconcile_records(
    db_snapshot: Dict[int, str],
    incoming_payload: Dict[int, str]
) -> Tuple[Set[int], Set[int], Set[int]]:
    """
    Computes differences between two states in O(N) using set operations.
    Returns: (created_ids, updated_ids, deleted_ids)
    """
    db_keys = set(db_snapshot.keys())
    incoming_keys = set(incoming_payload.keys())

    created_ids = incoming_keys - db_keys
    deleted_ids = db_keys - incoming_keys
    common_ids = db_keys & incoming_keys

    # Fast hash check for modifications
    updated_ids = {k for k in common_ids if db_snapshot[k] != incoming_payload[k]}

    return created_ids, updated_ids, deleted_ids
```

---

## 8. Common Mistakes & Antipatterns
- ❌ **Mistake**: Using a `list` for frequent membership checks (`if user_id in active_users_list:`).
  - ✅ **Correction**: Convert to a `set` once. Lists take $\mathcal{O}(N)$ time; sets take $\mathcal{O}(1)$ time.
- ❌ **Mistake**: Using mutable default collections in function signatures (`def process(data={}):`).
  - ✅ **Correction**: Use `None` and initialize inside: `if data is None: data = {}`.
- ❌ **Mistake**: Using `dict.keys()` to check existence: `if key in my_dict.keys():`.
  - ✅ **Correction**: Write `if key in my_dict:`. Checking the dict directly is faster and idiomatic.
- ❌ **Mistake**: Assuming `dict.copy()` creates an independent clone of nested structures.
  - ✅ **Correction**: `dict.copy()` is a **shallow copy**. Use `copy.deepcopy()` for nested dictionaries.

---

## 9. Performance & Complexity Matrix

| Operation | `list` (Dynamic Array) | `tuple` (Fixed Array) | `set` (Hash Table) | `dict` (Compact Hash Table) |
| :--- | :--- | :--- | :--- | :--- |
| **Lookup / Membership** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ avg / $\mathcal{O}(N)$ worst | $\mathcal{O}(1)$ avg / $\mathcal{O}(N)$ worst |
| **Append / Add** | $\mathcal{O}(1)$ amortized | N/A (Immutable) | $\mathcal{O}(1)$ avg | $\mathcal{O}(1)$ avg |
| **Insert at index 0** | $\mathcal{O}(N)$ (shifts pointers) | N/A (Immutable) | N/A (Unordered) | N/A (Key-based) |
| **Delete element** | $\mathcal{O}(N)$ (shifts pointers) | N/A (Immutable) | $\mathcal{O}(1)$ avg | $\mathcal{O}(1)$ avg |
| **Iteration** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **Memory Overhead** | Low (over-allocated array) | Lowest (exact allocation) | Higher (hash table sparse) | Moderate (compact indices) |

---

## 10. Security Implications
1. **Hash DoS (Denial of Service) Attacks**: If an attacker knows the hashing algorithm, they can send HTTP requests with crafted keys designed to produce identical hashes. This forces the hash table into catastrophic $\mathcal{O}(N)$ probing. CPython protects against this by salting hashes with a randomized 128-bit secret (`PYTHONHASHSEED`) generated at interpreter boot.
2. **Unchecked `**kwargs` Injection**: Blindly unpacking untrusted request JSON into internal functions (`create_user(**payload)`) allows clients to overwrite unintended fields (e.g., `is_superuser=True`). Always filter through Pydantic or explicit schemas.

---

## 11. When to Use?
- **`list`**: Sequential data where order matters, frequent appends/pops at the end, or when elements are accessed by integer index.
- **`tuple`**: Fixed data records (coordinates, database row tuples), functions returning multiple values, and dictionary keys.
- **`set`**: Deduplication, fast membership testing, mathematical set operations (union, difference, intersection).
- **`dict`**: Key-based lookup tables, caching, JSON data structures, and relational mappings.

---

## 12. When NOT to Use?
- **Do NOT use `list` for FIFO Queues**: `list.pop(0)` is $\mathcal{O}(N)$ because every remaining pointer shifts left. Use `collections.deque` instead ($\mathcal{O}(1)$ popleft).
- **Do NOT use `dict` when keys are strictly small integers $0 \dots N$**: An array/list is significantly faster and uses less memory.

---

## 13. Top Interview Questions
1. *How does CPython's list achieve amortized $\mathcal{O}(1)$ appends under the hood?*
2. *Explain the architecture of CPython's compact dictionary (PEP 468). Why was it introduced?*
3. *What is a hash collision, and how does CPython resolve collisions in sets and dicts?*
4. *Why can't a `list` or a `set` be used as a dictionary key? What makes an object hashable?*
5. *Why is `collections.deque` preferred over `list` for implementing queues?*

---

## 14. Practice Problems
1. Implement a custom Dictionary Frequency Grouper that reverses a key-value mapping (handling duplicate values by grouping keys into sets).
2. Build an in-memory reconciliation utility that compares two snapshots of 50,000 JSON records and outputs `created`, `updated`, and `deleted` IDs in linear $\mathcal{O}(N)$ time.
3. Write a function that demonstrates how `collections.deque` outperforms `list` for 100,000 queue operations.

---

## 15. 5-Minute Revision Notes
- Python lists are dynamic arrays of **pointers**, not raw values.
- List appends are **amortized $\mathcal{O}(1)$** thanks to geometric over-allocation.
- `list.insert(0, x)` and `list.pop(0)` are **$\mathcal{O}(N)$**; always use `collections.deque` for queues.
- Dictionaries use a **compact architecture**: a sparse indices array + a dense entries table, preserving insertion order and saving ~35% RAM.
- Hash lookups in sets and dicts are **$\mathcal{O}(1)$ average**; collisions are resolved via **open addressing with pseudo-random probing**.
- Objects must be immutable and implement `__hash__` and `__eq__` to serve as dict keys or set members.
- Never modify a collection while iterating over it; iterate over `list(d.keys())` or `d.copy()`.
