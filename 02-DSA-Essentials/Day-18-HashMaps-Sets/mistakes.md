# Day 18: 7 Critical Antipatterns in HashMaps, Sets & Hash Tables

HashMaps and Sets provide the foundation of fast backend indexing and caching. However, subtle misunderstandings regarding object hashability, memory layout, and hash table probing lead to catastrophic production failures. Below are 7 common antipatterns, their consequences, and enterprise remediations.

---

## 1. Implementing `__eq__` Without `__hash__` (or Vice Versa)

### The Mistake
Overriding `__eq__` in custom domain models without defining `__hash__`:
```python
# WRONG: Python automatically sets __hash__ = None when __eq__ is overridden!
class UserSessionKey:
    def __init__(self, user_id: str, device_id: str):
        self.user_id = user_id
        self.device_id = device_id
        
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserSessionKey):
            return False
        return (self.user_id, self.device_id) == (other.user_id, other.device_id)

# Later in cache logic:
cache = {}
key = UserSessionKey("u_101", "dev_ios")
cache[key] = "session_data"  # CRASH: TypeError: unhashable type: 'UserSessionKey'
```

### The Consequence
Under Python's data model, if a class overrides `__eq__` without defining `__hash__`, Python treats the object as unhashable to prevent equal objects from having different hash codes. Attempting to use it as a dict key or set element raises an immediate runtime `TypeError`.

### The Correct Approach
Either implement both methods adhering to the equality-hash contract, or use a frozen dataclass:
```python
# CORRECT OPTION A: Frozen dataclass (automatically generates __eq__ and __hash__)
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class UserSessionKey:
    user_id: str
    device_id: str

# CORRECT OPTION B: Manual implementation
class UserSessionKeyManual:
    def __init__(self, user_id: str, device_id: str):
        self.user_id = user_id
        self.device_id = device_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserSessionKeyManual):
            return False
        return (self.user_id, self.device_id) == (other.user_id, other.device_id)

    def __hash__(self) -> int:
        return hash((self.user_id, self.device_id))
```

---

## 2. Mutating an Object After Inserting It as a Set or Dict Key

### The Mistake
Modifying an attribute of a key after inserting it into a hash table:
```python
# WRONG: Strands the key in an unreachable bucket!
class MutableEntity:
    def __init__(self, entity_id: int):
        self.entity_id = entity_id

    def __hash__(self) -> int:
        return hash(self.entity_id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MutableEntity) and self.entity_id == other.entity_id

entity = MutableEntity(42)
registry = {entity: "Active Database Record"}

# Later in business logic:
entity.entity_id = 99  # Key attribute mutated!

# Now lookup fails:
print(entity in registry)  # False! Key is permanently lost!
```

### The Consequence
When `entity.entity_id = 99` is set, `hash(entity)` changes. When querying `entity in registry`, Python hashes the modified object to a **different bucket index** where it finds nothing! The object remains stuck in the old bucket indefinitely, causing silent data corruption and memory leaks.

### The Correct Approach
Keys must be strictly immutable. Enforce immutability via tuples, `namedtuple`, or frozen dataclasses.

---

## 3. Using `list` for Membership Lookups Inside Loops ($\mathcal{O}(N^2)$ Trap)

### The Mistake
Checking membership against a Python `list` inside an iteration:
```python
# WRONG: Silent O(N^2) quadratic explosion in production!
def filter_unprocessed_events(event_ids: list[str], processed_ids: list[str]) -> list[str]:
    unprocessed = []
    for eid in event_ids:
        if eid not in processed_ids:  # O(M) linear scan for EVERY element!
            unprocessed.append(eid)
    return unprocessed
```

### The Consequence
In CPython, `x in my_list` performs a sequential linear scan from index 0 to $M - 1$. For $N = 50{,}000$ incoming events and $M = 50{,}000$ processed events, this loop executes $2{,}500{,}000{,}000$ comparisons, hanging the microservice for minutes!

### The Correct Approach
Convert the lookup collection to a `set` upfront for **$\mathcal{O}(1)$ average lookups**:
```python
# CORRECT: O(N + M) linear execution
def filter_unprocessed_events(event_ids: list[str], processed_ids: list[str]) -> list[str]:
    processed_set = set(processed_ids)  # O(M) one-time hash table construction
    return [eid for eid in event_ids if eid not in processed_set]  # O(1) per check
```

---

## 4. Sorting Long Strings for Group Anagrams Instead of Frequency Tuples

### The Mistake
Sorting long text tokens to generate anagram grouping keys:
```python
# SUBOPTIMAL: O(N * K log K) overhead on long tokens
def group_anagrams_slow(strs: list[str]):
    groups = defaultdict(list)
    for s in strs:
        key = "".join(sorted(s))  # O(K log K) sorting per word!
        groups[key].append(s)
    return list(groups.values())
```

### The Consequence
When grouping documents, DNA base sequences, or cryptographic hashes where token length $K$ is large ($K = 1{,}000$), $K \log K$ sorting dominates execution time.

### The Correct Approach
Use an immutable 26-integer frequency tuple for strictly **$\mathcal{O}(K)$ linear key computation**:
```python
# OPTIMAL: O(N * K) time with zero string sorting
def group_anagrams_fast(strs: list[str]):
    groups = defaultdict(list)
    for s in strs:
        counts = [0] * 26
        for ch in s:
            counts[ord(ch) - ord('a')] += 1
        groups[tuple(counts)].append(s)
    return list(groups.values())
```

---

## 5. Forgetting the Base Case `{0: 1}` in Subarray Sum Equals K

### The Mistake
Initializing the prefix sums frequency map as empty:
```python
# WRONG: Misses any subarray that sums to K starting from index 0!
def subarray_sum_broken(nums: list[int], k: int) -> int:
    prefix_counts = {}  # BUG: Missing base case {0: 1}!
    running_sum = 0
    total = 0
    for x in nums:
        running_sum += x
        if (running_sum - k) in prefix_counts:
            total += prefix_counts[running_sum - k]
        prefix_counts[running_sum] = prefix_counts.get(running_sum, 0) + 1
    return total
```

### The Consequence
For `nums = [3, 4]`, $k = 7$:
- At index 0 ($x = 3$): `running_sum = 3`. Complement $3 - 7 = -4$ not found.
- At index 1 ($x = 4$): `running_sum = 7`. Complement $7 - 7 = 0$ is NOT in `prefix_counts`!
- The algorithm returns `0` instead of `1` because the entire array `[3, 4]` starts from index 0.

### The Correct Approach
Always initialize `prefix_counts = {0: 1}` to account for subarrays starting at the very beginning of the array:
```python
# CORRECT: Base case records that an empty prefix has a sum of 0
prefix_counts = {0: 1}
```

---

## 6. Expanding Every Element in Longest Consecutive Sequence ($\mathcal{O}(N^2)$ Trap)

### The Mistake
Checking for forward consecutive numbers on every single element in the set:
```python
# WRONG: Degenerates to O(N^2) quadratic time on descending or contiguous sequences!
def longest_consecutive_naive(nums: list[int]) -> int:
    num_set = set(nums)
    max_len = 0
    for x in num_set:
        curr = x
        streak = 1
        while (curr + 1) in num_set:  # Re-scans forward for EVERY element!
            curr += 1
            streak += 1
        max_len = max(max_len, streak)
    return max_len
```

### The Consequence
For `nums = [1, 2, 3, ..., 10000]`:
- Element 1 scans 10,000 steps.
- Element 2 scans 9,999 steps.
- Element 3 scans 9,998 steps.
Total operations: $\frac{N(N+1)}{2} = \mathcal{O}(N^2)$, defeating the entire purpose of using a Hash Set.

### The Correct Approach
Only initiate the forward scan if `x` is the **absolute beginning of a sequence**:
```python
# CORRECT: Strictly O(N) linear time
for x in num_set:
    if (x - 1) not in num_set:  # O(1) guard clause skips interior elements!
        curr = x
        streak = 1
        while (curr + 1) in num_set:
            curr += 1
            streak += 1
        max_len = max(max_len, streak)
```

---

## 7. Vulnerability to HashDoS Attacks via Deterministic Hashing

### The Mistake
Exposing internal hash table keys derived directly from untrusted client HTTP inputs using weak, non-cryptographic hash functions:
```python
# DANGEROUS: If an attacker crafts collisions, hash table degenerates to O(N) linked list!
def custom_insecure_hash(s: str) -> int:
    h = 0
    for char in s:
        h = (31 * h + ord(char)) & 0xFFFFFFFF
    return h
```

### The Consequence
An attacker crafts a payload of 100,000 JSON keys that all map to the identical bucket index. Inserting them causes 100,000 open-addressing collision probes per key, spiking CPU to 100% and taking down the web server cluster.

### The Correct Approach
Rely strictly on Python's built-in `hash()` function (which implements SipHash-1-3 with per-process randomized `PYTHONHASHSEED`), or use keyed HMACs for externally sourced keys.

---

## Summary Checklist
- [x] Always implement both `__hash__` and `__eq__` on custom classes (or use `@dataclass(frozen=True)`).
- [x] Never mutate an object after using it as a dictionary or set key.
- [x] Replace `item in my_list` with `item in my_set` for $\mathcal{O}(1)$ lookups.
- [x] Use frequency tuples $\mathcal{O}(K)$ instead of string sorting $\mathcal{O}(K \log K)$ for long anagram tokens.
- [x] Always seed `prefix_counts = {0: 1}` in Subarray Sum Equals K.
- [x] Add the `(x - 1) not in num_set` guard clause for Longest Consecutive Sequence.
- [x] Rely on SipHash to prevent HashDoS algorithmic complexity attacks.
