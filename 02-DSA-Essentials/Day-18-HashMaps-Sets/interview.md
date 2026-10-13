# Day 18: Senior Backend Interview Mastery - HashMaps, Sets & Indexing

Master these 5 senior-level technical interview questions covering CPython hash table internals, HashDoS vulnerability mitigation, database query planner hash joins, and the Python object data model.

---

### Q1: Explain CPython's Compact Dict memory layout introduced in Python 3.6. How does it separate the hash indices array from the entries table, and how does it guarantee insertion order preservation?

#### Answer:

#### 1. Legacy Sparse Hash Table (Python 3.5 and earlier):
Every slot in the hash table was a heavy 24-byte entry containing three 8-byte pointers: `(hash_code, key_pointer, value_pointer)`.
- If the table allocated 8 slots and only stored 3 keys, 5 slots remained empty (`NULL`), wasting $5 \times 24 = 120$ bytes of sparse uninitialized memory.
- Deletions left dummy sentinel markers, and iterating over the dictionary visited slots in arbitrary hash-order, which appeared pseudo-random to developers.

#### 2. Modern Compact Dict Architecture (PEP 468 / Python 3.6+):
CPython splits dictionary storage into two cooperating internal arrays:
1. **`dk_indices` Array (Sparse)**: A sparse table of compact signed integer indices (`int8_t`, `int16_t`, `int32_t`, or `int64_t` depending on table capacity).
   - Unoccupied slots are represented by `-1`.
   - Occupied slots store the integer index pointing into `dk_entries`.
2. **`dk_entries` Array (Dense)**: A contiguous, densely packed array of `PyDictKeyEntry` structs `(me_hash, me_key, me_value)`.
   - New entries are appended sequentially at index `0, 1, 2, ...`.

```
Example: dict with entries {"alpha": 10, "beta": 20}

dk_indices: [ -1,   0,  -1,  -1,   1,  -1,  -1,  -1 ]   (Sparse, tiny bytes)
                    │             │
                    ▼             ▼
dk_entries: [                                            (Dense, contiguous)
  Index 0: { me_hash: 0x4a1, me_key: "alpha", me_value: 10 },
  Index 1: { me_hash: 0x9b3, me_key: "beta",  me_value: 20 }
]
```

#### Why Insertion Order is Guaranteed:
Because incoming key-value pairs are appended to `dk_entries` in chronological order of insertion, dictionary iteration (`for k, v in d.items():`) simply scans the contiguous `dk_entries` array from index 0 to `num_entries - 1`. This yields guaranteed insertion-order traversal with near-100% CPU L1 cache prefetching hits and saves 20% to 25% memory.

---

### Q2: How does CPython resolve hash collisions? Why does it use Open Addressing with Perturbation Probing instead of Separate Chaining or Linear Probing?

#### Answer:

#### 1. Why Not Separate Chaining (Linked Lists)?
Separate chaining (used by Java's `HashMap`) places a linked list at each bucket. In modern hardware:
- Traversing a linked list requires chasing pointers across scattered heap memory, triggering frequent **L1/L2 cache misses**.
- Memory overhead for `next` pointers adds significant RAM amplification.
- Open addressing stores all entries in contiguous buffers, maximizing spatial cache locality.

#### 2. Why Not Simple Linear Probing ($j + 1$)?
In linear probing, if slot $j$ is occupied, the engine checks $j+1, j+2, j+3\dots$
- Linear probing suffers from **Primary Clustering**: contiguous chunks of occupied slots coalesce into large blocks.
- Any key that hashes anywhere into that cluster must probe through the entire cluster, causing collision search times to degrade toward $\mathcal{O}(N)$.

#### 3. CPython's Solution: Open Addressing with Perturbation Probing
CPython computes the probe sequence using this recurrence formula:
$$j = (5 \times j + 1 + \text{perturb}) \pmod{\text{TableSize}}$$
$$\text{perturb} \gg= 5$$

- The formula incorporates the upper bits of the original hash code via `perturb`.
- At each step, `perturb` shifts right by 5 bits, gradually letting the lower bits take over.
- This pseudo-randomly jumps across the table, completely avoiding primary clustering and exploring every slot in the table before repeating.

---

### Q3: What is a HashDoS attack, and how does Python's SipHash algorithm mitigate algorithmic complexity vulnerabilities?

#### Answer:

#### The Attack Mechanism (Algorithmic Complexity Attack):
When web servers parse incoming HTTP POST bodies (e.g. JSON payloads or form data `application/x-www-form-urlencoded`), they instantiate in-memory Hash Maps of the request keys.
- Under normal operation, lookups are average $\mathcal{O}(1)$.
- If an attacker knows the hash algorithm (e.g. standard polynomial hash $h = 31 \times h + c$), they can mathematically precompute thousands of distinct string keys that all yield the **exact same hash code** modulo the table size.
- When the web server receives 50,000 colliding keys, inserting them requires evaluating 50,000 collision probes per key:
  $$\text{Total Comparisons} = \frac{N(N - 1)}{2} \approx 1{,}250{,}000{,}000 \text{ operations}$$
- A single small HTTP request freezes a CPU core at 100% utilization for 30–60 seconds, taking down the backend cluster with minimal attacker bandwidth.

#### Python's Mitigation: SipHash-1-3 and `PYTHONHASHSEED`:
1. Since Python 3.4, CPython utilizes **SipHash-1-3** as the default hash algorithm for strings and bytes.
2. SipHash is a cryptographically robust, pseudorandom keyed hash function.
3. Every time a Python interpreter process starts, it initializes a secret 128-bit random seed (`PYTHONHASHSEED`).
4. Because the seed is randomized per-process in kernel memory, an external attacker cannot precompute colliding keys. Strings that collide in process A will not collide in process B, rendering HashDoS attacks mathematically infeasible.

---

### Q4: Walk through the Build and Probe phases of a Database Hash Join (e.g. in PostgreSQL). When does the query planner choose a Hash Join over a Nested Loop or Sort-Merge Join?

#### Answer:
When executing an equi-join (`SELECT * FROM orders o JOIN users u ON o.user_id = u.id`):

```
                        DATABASE HASH JOIN
   1. BUILD PHASE (Smaller Table)      2. PROBE PHASE (Larger Table)
        [ Users Table: M rows ]              [ Orders Stream: N rows ]
                  │                                      │
                  ▼                                      ▼
     Build In-Memory Hash Table              Probe Hash Table in O(1)
       Key: user_id -> UserRow                           │
                                                         ▼
                                                Matched Joined Row
```

1. **Build Phase**:
   - The query planner selects the smaller relation (Users, $M$ rows).
   - Scans Users and inserts rows into an in-memory hash table keyed by `user_id`.
   - Memory cost is bounded by `work_mem`. If the table exceeds `work_mem`, it spills to a Hybrid Hash Join on disk using multiple hash batches.
2. **Probe Phase**:
   - Streams sequentially through the larger relation (Orders, $N$ rows).
   - For each order, hashes `order.user_id` and probes the in-memory hash table in $\mathcal{O}(1)$ time.
   - If a match is found, emits the combined tuple.
   - Total time: strictly **$\mathcal{O}(M + N)$**.

#### Query Planner Decision Matrix:
- **Nested Loops Join**: Chosen when one table is extremely small ($\le 10$ rows) and the other table has a B-Tree index on the join key ($\mathcal{O}(M \log N)$).
- **Sort-Merge Join**: Chosen when both relations are **already sorted** on the join column (e.g. via an existing clustered index), or when performing range/inequality joins ($<, \le$).
- **Hash Join**: Chosen for large, unsorted datasets performing equi-joins ($=$) where the smaller table fits comfortably within in-memory buffer cache (`work_mem`).

---

### Q5: Explain the `__hash__` and `__eq__` contract in Python. What happens if two equal objects have different hashes, or if two unequal objects have the same hash?

#### Answer:

#### The Mathematical Contract:
$$\text{If } a == b \implies \text{hash}(a) == \text{hash}(b)$$

#### Scenario 1: $a == b$, but $\text{hash}(a) \ne \text{hash}(b)$ (CONTRACT VIOLATION!)
- **Result**: **Catastrophic data loss in HashMaps and Sets**.
- **Explanation**:
  - You insert `a` into a set `s.add(a)`. Python calculates `hash(a)` and places `a` in bucket $X$.
  - Later, you query `b in s` where `b == a`.
  - Python calculates `hash(b)` and inspects bucket $Y$ (since $\text{hash}(a) \ne \text{hash}(b)$).
  - Bucket $Y$ is empty! Python returns `False`, even though an "equal" object exists in the set!
  - You can insert both `a` and `b` into the set simultaneously, violating the fundamental set uniqueness guarantee.

#### Scenario 2: $a \ne b$, but $\text{hash}(a) == \text{hash}(b)$ (VALID HASH COLLISION)
- **Result**: **Completely legal; handled by collision resolution**.
- **Explanation**:
  - Because 64-bit integers cannot uniquely represent infinite strings or objects, the Pigeonhole Principle guarantees hash collisions will occur.
  - When $a$ and $b$ hash to the same bucket:
    1. Python inspects the bucket. The hashes match.
    2. Python invokes the **`__eq__` comparison**: `if a == b: return True`.
    3. Because $a \ne b$, the equality check returns `False`.
    4. Python executes its perturbation probing formula to find the next open bucket slot.
  - Performance degrades slightly during collisions, but correctness is 100% preserved.
