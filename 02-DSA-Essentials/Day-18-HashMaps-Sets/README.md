# Day 18: HashMaps & Sets: Fast Lookups, Collision Handling & Anagram Groups

In backend and AI systems engineering, **Hash Tables** (implemented in Python as `dict` and `set`) are arguably the most heavily utilized data structures. From in-memory caches (Redis, Memcached), database indexes, routing tables, and session stores to embedding token lookup tables in LLM tokenizers, high-performance computing relies on the promise of **average $\mathcal{O}(1)$ time complexity**.

However, treating HashMaps as "magic constant-time black boxes" leads to critical outages: memory bloat from sparse hash tables, hash collision denial-of-service (HashDoS), and accidental $\mathcal{O}(N^2)$ regressions. Understanding how hash tables work at the C-level in CPython is essential for senior backend engineers.

---

## 1. The HashMap & Set Foundations in Backend Architecture

A Hash Table maps arbitrary keys (strings, integers, UUIDs, tuples) to values by computing a deterministic integer hash:
$$\text{Index} = \text{hash}(\text{key}) \pmod{\text{TableSize}}$$

```
  Key: "user:1042" ──► [ Hash Function: SipHash ] ──► Hash: 0x7a3f81e9
                                                                │
                                                    Modulo Table Size (8)
                                                                ▼
                                                          Bucket Index: 5
                                                                │
                                                                ▼
                                              Entries Table [ Index 5 ] ──► Value
```

In relational databases and microservices:
- **Foreign Key Indexing**: In-memory joins pair rows in $\mathcal{O}(1)$ per record.
- **Session Management**: Session tokens resolve to user profiles in sub-millisecond lookups.
- **Deduplication Pipelines**: Stream processing drops duplicate messages across Kafka topics in $\mathcal{O}(1)$ time.

---

## 2. CPython Dict Internals: The Compact Dict Architecture

Prior to Python 3.6, CPython implemented dictionaries as a **sparse hash table**. Every bucket slot was a 24-byte entry `(hash, key, value)`. If the table had 8 slots and only 3 entries, 5 slots held empty memory, wasting 20% to 40% of RAM.

### The Modern Compact Dict (PEP 468 / Python 3.6+)
CPython splits the dictionary into two distinct internal arrays:
1. **Indices Array (`dk_indices`)**: A sparse array of small integers (e.g. `int8_t`, `int16_t`) representing hash buckets.
2. **Entries Array (`dk_entries`)**: A densely packed contiguous array of actual `(hash, key, value)` structs.

```
Example: dict with 3 items ("tim": 29, "bob": 34, "ana": 22)

Indices Array (Sparse, small bytes):
Index:     0      1      2      3      4      5      6      7
Value:   [ -1,    1,   -1,    0,   -1,   -1,    2,   -1 ]
                  │           │                 │
                  ▼           ▼                 ▼
Entries Array (Dense, tightly packed in insertion order):
Index 0: hash("tim"), key="tim", value=29
Index 1: hash("bob"), key="bob", value=34
Index 2: hash("ana"), key="ana", value=22
```

### Architectural Benefits:
1. **20%–25% Memory Reduction**: The sparse table only stores tiny byte indices (`-1`, `0`, `1`...) rather than heavy 24-byte entry pointers.
2. **Guaranteed Insertion Order Preservation**: Because `dk_entries` appends new keys sequentially, iterating over a Python dict iterates through `dk_entries` in exact insertion order!

---

## 3. Hash Functions & Collision Resolution: Open Addressing with Perturbation

### Hash Function: SipHash-1-3
Python uses **SipHash** for strings and bytes. SipHash incorporates a per-process randomized secret seed (`PYTHONHASHSEED`) generated when the Python interpreter boots. This prevents **HashDoS attacks** where malicious clients send thousands of strings tailored to collide at the same bucket, degrading lookup times from $\mathcal{O}(1)$ to $\mathcal{O}(N)$.

### Collision Resolution: Open Addressing with Perturbation
When two distinct keys hash to the identical bucket index:
$$\text{hash}(\text{key}_1) \equiv \text{hash}(\text{key}_2) \pmod{\text{TableSize}}$$

Instead of using **Separate Chaining** (linked lists at each bucket, which suffers from pointer chasing and L1 cache misses), CPython uses **Open Addressing** with a specialized pseudo-random probing formula:

$$j = (5 \times j + 1 + \text{perturb}) \pmod{\text{TableSize}}$$
$$\text{perturb} \gg= 5$$

Why not simple linear probing ($j + 1$)?
- Linear probing causes **primary clustering**: contiguous blocks of occupied slots grow, causing progressively longer probe chains.
- CPython's perturbation formula mixes the high-order bits of the hash code into the probe sequence, scattering probed slots across the table and eliminating clustering.

---

## 4. Load Factor, Resizing Dynamics & Amortized $\mathcal{O}(1)$

The **Load Factor** ($\alpha$) is the ratio of occupied slots to total table capacity:
$$\alpha = \frac{\text{Number of Entries}}{\text{Table Capacity}}$$

- CPython enforces a strict threshold: when the table becomes **$\frac{2}{3}$ full (66.7%)**, it automatically resizes.
- **Resizing Mechanics**:
  - The table allocates a new indices array of double (or quadruple) size.
  - Active entries are re-indexed into the new table.
  - Expired / deleted "dummy" slots are purged.
- **Time Complexity**:
  - Resizing requires an $\mathcal{O}(N)$ sweep.
  - However, because doubling occurs exponentially ($8 \to 16 \to 32 \to 64 \dots$), the resizing cost spread across $N$ insertions is:
    $$\text{Amortized Cost} = \frac{\mathcal{O}(N)}{N} = \mathcal{O}(1)$$
  - Each individual insertion is **amortized $\mathcal{O}(1)$**.

---

## 5. Hash Sets Internals (`set` and `frozenset`)

A Python `set` is internally implemented using an engine almost identical to `dict`, with two simplifications:
- The `entries` table only stores `(hash, key)`. No value pointer is allocated.
- A dummy sentinel object (`<dummy>`) marks deleted entries during open addressing probing so search chains are not prematurely broken.

### Mathematical Set Algebra Complexity:
- Membership Testing (`x in S`): **Average $\mathcal{O}(1)$**, Worst-case $\mathcal{O}(N)$.
- Set Intersection (`S & T`): $\mathcal{O}(\min(|S|, |T|))$ — Python iterates through the smaller set and probes the larger set.
- Set Union (`S | T`): $\mathcal{O}(|S| + |T|)$.
- Set Difference (`S - T`): $\mathcal{O}(|S|)$.

---

## 6. Mutability, Hashability & Python Data Model (`__hash__` & `__eq__`)

An object can only be used as a dictionary key or set element if it is **hashable**.

### The Hashability Contract
1. **Immutability**: An object's hash value must never change during its entire lifetime.
2. **Equality Invariant**:
   $$\text{If } a == b \implies \text{hash}(a) == \text{hash}(b)$$
   *(Note: The reverse is not required; two unequal objects may have the same hash due to collisions).*

```python
class TenantUserKey:
    def __init__(self, tenant_id: str, user_id: str):
        self.tenant_id = tenant_id
        self.user_id = user_id
        
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TenantUserKey):
            return False
        return (self.tenant_id, self.user_id) == (other.tenant_id, other.user_id)
        
    def __hash__(self) -> int:
        # Hash must combine all fields evaluated in __eq__
        return hash((self.tenant_id, self.user_id))
```

> [!CAUTION]
> If a class implements `__eq__` without implementing `__hash__`, Python automatically sets `__hash__ = None`, rendering instances unhashable. If you mutate any attribute evaluated by `__hash__` after inserting it into a dictionary, the object is stranded in the wrong bucket and becomes **unretrievable**!

---

## 7. Pattern 1: Frequency Counters & Cardinality Tracking

Tracking frequencies of tokens, IP addresses, or database error codes is foundational:
```python
from collections import Counter
log_counts = Counter(error_stream)
top_5_errors = log_counts.most_common(5)  # O(N log K) via heapq internally
```

In LLM token pipelines, frequency counters track vocabulary distributions and detect hallucinated token repetition loops.

---

## 8. Pattern 2: Canonical Representation & Group Anagrams

Given a list of strings, group all anagrams together (e.g. `["eat", "tea", "tan", "ate", "nat", "bat"]` $\to$ `[["bat"], ["nat", "tan"], ["ate", "eat", "tea"]]`).

```
                CANONICAL HASH KEY STRATEGIES
          Strategy A: Sorted Key            Strategy B: Frequency Tuple
          "eat" -> tuple(sorted("eat"))      "eat" -> [1,0,0,0,1,0,...]
          Key: ('a', 'e', 't')              Key: (1, 0, 0, ..., 1, ...)
          Cost: O(K log K) per string       Cost: O(K) per string
```

### Optimal Frequency Tuple Key ($\mathcal{O}(N \times K)$ Time):
```python
from collections import defaultdict

def group_anagrams(strs: list[str]) -> list[list[str]]:
    groups = defaultdict(list)
    for word in strs:
        count = [0] * 26
        for char in word:
            count[ord(char) - ord('a')] += 1
        # Convert list to immutable hashable tuple
        groups[tuple(count)].append(word)
    return list(groups.values())
```
For long strings (e.g., DNA sequences, large documents), avoiding $\mathcal{O}(K \log K)$ sorting provides substantial speedups.

---

## 9. Pattern 3: Prefix Sums + Hash Map (Subarray Sum Equals K)

When an array contains **both positive and negative numbers**, Sliding Window fails because expanding the window is not monotonic. Prefix sums combined with a Hash Map solve this in strictly $\mathcal{O}(N)$ time:

```
Array:      [  3,   4,   7,  -2,   2,   1,   4  ],  Target K = 7
Prefix Sums: 0   3   7  14  12  14  15  19

Formula: Sum(i, j) = Prefix[j] - Prefix[i - 1] = K
         ==> Prefix[i - 1] = Prefix[j] - K
```

```python
def subarray_sum_k(nums: list[int], k: int) -> int:
    prefix_counts = {0: 1}  # Base case: empty prefix has sum 0
    running_sum = 0
    total_matches = 0
    
    for x in nums:
        running_sum += x
        complement = running_sum - k
        if complement in prefix_counts:
            total_matches += prefix_counts[complement]
            
        prefix_counts[running_sum] = prefix_counts.get(running_sum, 0) + 1
        
    return total_matches
```

---

## 10. Pattern 4: Hash Set Sequence Boundary Expansion

Given an unsorted array of integers, find the length of the longest consecutive sequence in $\mathcal{O}(N)$ time without sorting ($\mathcal{O}(N \log N)$).

```
Nums: [ 100,  4,  200,  1,  3,  2 ]

Convert to Set: { 1, 2, 3, 4, 100, 200 }
Key Invariant: An element is the START of a sequence IF AND ONLY IF (x - 1) is NOT in set!
- 100: (99 not in set) -> Start! Check 101 (no) -> Len = 1
- 4:   (3 is in set)   -> Skip! Not a sequence start
- 1:   (0 not in set)  -> Start! Check 2, 3, 4 -> Len = 4 (Max!)
```

```python
def longest_consecutive(nums: list[int]) -> int:
    num_set = set(nums)
    max_streak = 0
    
    for x in num_set:
        # Only evaluate if x is the beginning of a sequence
        if (x - 1) not in num_set:
            current_num = x
            current_streak = 1
            
            while (current_num + 1) in num_set:
                current_num += 1
                current_streak += 1
                
            max_streak = max(max_streak, current_streak)
            
    return max_streak
```
Because interior numbers are skipped in $\mathcal{O}(1)$, each element is traversed at most twice across the entire execution. Time complexity is strictly **$\mathcal{O}(N)$**.

---

## 11. Pattern 5: Dual Hash Map Bi-directional Mappings

Used in compiler tokenizers and API routing (e.g. *Isomorphic Strings*):
- We must prove a **one-to-one bijection** between characters of string $S$ and string $T$.
- A single hash map mapping $S \to T$ fails if multiple distinct characters in $S$ map to the same character in $T$.
- Maintaining two maps ($S \to T$ and $T \to S$) guarantees a strict bijection in $\mathcal{O}(N)$ time.

---

## 12. Backend Application 1: In-Memory Multi-Tenant Session Store

High-performance authentication systems require two competing capabilities:
1. Validate incoming bearer token $\to$ User session in $\mathcal{O}(1)$ time.
2. Invalidate **all active sessions** for a specific user (e.g. after password reset) in $\mathcal{O}(1)$ time.

### Dual-Index Architecture:
- `token_to_session: Dict[Token, SessionData]` (Direct authentication index).
- `user_to_tokens: Dict[UserID, Set[Token]]` (Reverse cascade index).

When logging out a user across all devices, the backend retrieves `user_to_tokens[user_id]`, deletes every token from `token_to_session`, and clears the set in $\mathcal{O}(K)$ where $K$ is the user's active devices (typically $\le 5$), avoiding a full database table scan.

---

## 13. Backend Application 2: In-Memory Database Hash Joins

When microservices join data from two upstream APIs (e.g. 5,000 Orders and 1,000 Customers):
- **Nested Loops Join**: $5{,}000 \times 1{,}000 = 5{,}000{,}000$ iterations ($\mathcal{O}(M \times N)$).
- **Hash Join**:
  1. **Build Phase**: Build a hash map of Customers keyed by `customer_id` ($\mathcal{O}(M)$).
  2. **Probe Phase**: Iterate over Orders, querying the hash table in $\mathcal{O}(1)$ ($\mathcal{O}(N)$).
  Total time: $\mathcal{O}(M + N)$ linear execution.

---

## 14. Backend Application 3: Event Deduplication & Rolling TTL Caches

In webhook ingestion engines (Stripe, GitHub, Twilio), webhooks can be retried multiple times:
- Use an in-memory Hash Table with timestamps:
  `seen_events: Dict[event_id, timestamp]`
- Upon arrival, check `if event_id in seen_events: return 200_ALREADY_PROCESSED`.
- Periodically prune entries older than 24 hours to prevent memory exhaustion.

---

## 15. Complexity Comparison Matrix

| Data Structure / Pattern | Search | Insert | Delete | Space Complexity | Ordered? | Cache Locality |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CPython Dict / Set** | $\mathcal{O}(1)$ avg / $\mathcal{O}(N)$ worst | $\mathcal{O}(1)$ avg | $\mathcal{O}(1)$ avg | $\mathcal{O}(N)$ | Yes (Insertion order) | Medium (Compact indices) |
| **Binary Search Tree** | $\mathcal{O}(\log N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Yes (Key sort order) | Low (Pointer node hops) |
| **Contiguous Array (`list`)** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ append | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Yes (Index order) | **Near 100% L1 Hits** |
| **Trie (Prefix Tree)** | $\mathcal{O}(L)$ string len | $\mathcal{O}(L)$ | $\mathcal{O}(L)$ | $\mathcal{O}(N \times L \times \Sigma)$ | Lexicographical | Low |

---

## Summary Checklist for Day 18
- [x] Understand CPython's Compact Dict memory layout (`dk_indices` + `dk_entries`).
- [x] Understand why Python uses SipHash with randomized seeds to prevent HashDoS.
- [x] Master collision resolution via open addressing with perturbation probing.
- [x] Implement the `__hash__` and `__eq__` contract for custom domain classes.
- [x] Solve Group Anagrams using frequency tuple keys in $\mathcal{O}(N \times K)$ time.
- [x] Solve Subarray Sum Equals K using Prefix Sums + Hash Map on arrays with negative numbers.
- [x] Master the sequence start invariant `(x - 1) not in S` for Longest Consecutive Sequence.
- [x] Architect dual-indexed in-memory session stores for $\mathcal{O}(1)$ validation and cascade invalidation.
