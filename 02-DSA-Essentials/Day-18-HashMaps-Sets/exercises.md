# Day 18: Practical Hands-On DSA Challenges

Master HashMaps, Hash Sets, and In-Memory Indexing Architectures with these 3 production-level challenges.

---

## Challenge 1: Subarray Sums Divisible by K ($\mathcal{O}(N)$ Time)

### Objective
In financial ledger auditing and batch transaction aggregation, backend engines must detect contiguous transaction blocks whose net monetary amount is evenly divisible by a reconciliation batch size $K$ (e.g. divisible by 100 cents or \$10.00).

Implement `subarrays_div_by_k(nums: list[int], k: int) -> int` that returns the count of non-empty continuous subarrays whose sum is divisible by $k$.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$.
2. **Space Complexity**: $\mathcal{O}(K)$ (stores frequency of remainders modulo $K$).
3. **Mathematical Remainder Invariant**:
   - If two prefix sums $P[i]$ and $P[j]$ leave the exact same remainder when divided by $k$:
     $$P[i] \equiv P[j] \pmod k \implies (P[j] - P[i]) \equiv 0 \pmod k$$
   - Therefore, any pair of prefix sums with identical remainders forms a subarray divisible by $k$!
4. **Negative Modulo Normalization**:
   - In Python, negative modulo `(-1) % 5` equals `4`. Ensure consistent positive remainder mapping:
     $$\text{rem} = ((\text{running\_sum} \pmod k) + k) \pmod k$$
5. **Combinatorics or In-Flight Addition**:
   - If a remainder has appeared $C$ times previously, encountering it again immediately adds $C$ new valid subarrays to the total count.
6. **Edge Cases**:
   - `nums` containing all negative numbers (e.g. `[-1, 2, 9]`, `k = 2`).
   - Single-element arrays.

---

## Challenge 2: Production LRU Cache with HashMap + Doubly Linked List

### Objective
In high-throughput microservices, database query caches and LLM embedding caches must discard the Least Recently Used (LRU) entry when reaching memory limits.

Implement a production-grade `LRUCache` class from scratch **without using `collections.OrderedDict`**:

```python
class LRUCache:
    def __init__(self, capacity: int) -> None:
        """Initializes the LRU cache with positive size capacity."""
        ...

    def get(self, key: int) -> int:
        """
        Returns the value of the key if it exists, otherwise -1.
        Moves the accessed key to the head of the cache (most recently used).
        """
        ...

    def put(self, key: int, value: int) -> None:
        """
        Updates the value of the key if present, or inserts key-value pair.
        If cache exceeds capacity, evicts the least recently used key.
        """
        ...
```

### Requirements
1. **Time Complexity**: Both `.get()` and `.put()` must execute in **strictly $\mathcal{O}(1)$ time**.
2. **Data Structure Architecture**:
   - A Hash Map (`dict`) mapping `key -> Node` for $\mathcal{O}(1)$ pointer lookup.
   - A Doubly Linked List with dummy `head` and `tail` sentinel nodes to avoid boundary null-pointer checks during node removal and insertion.
3. **Validation**:
   - Verify that accessing an existing key via `.get()` promotes it to MRU (preventing its eviction on the next `.put()`).
   - Verify proper eviction of the LRU node when capacity is exceeded.

---

## Challenge 3: Multi-Index Entity Cache with Cascade Invalidation

### Objective
In multi-tenant microservices, user entities are queried across heterogeneous dimensions: by unique `user_id`, by unique `email`, or by collection filter `tenant_id`.

Implement an in-memory entity cache `MultiIndexUserCache`:

```python
@dataclass(frozen=True)
class UserProfile:
    user_id: str
    email: str
    tenant_id: str
    display_name: str

class MultiIndexUserCache:
    def __init__(self) -> None:
        ...

    def insert(self, user: UserProfile) -> None:
        """Inserts user into all 3 indices in O(1) time."""
        ...

    def get_by_id(self, user_id: str) -> Optional[UserProfile]:
        """Resolves user by primary ID in O(1) time."""
        ...

    def get_by_email(self, email: str) -> Optional[UserProfile]:
        """Resolves user by email in O(1) time."""
        ...

    def get_by_tenant(self, tenant_id: str) -> List[UserProfile]:
        """Returns all users belonging to tenant_id in O(K) where K = tenant users."""
        ...

    def delete(self, user_id: str) -> bool:
        """
        Atomically purges user from all 3 indices in O(1) time,
        preventing orphaned email or tenant pointers.
        """
        ...
```

### Requirements
1. **Index Consistency**:
   - Primary Index: `user_id -> UserProfile`.
   - Secondary Unique Index: `email -> user_id`.
   - Secondary Group Index: `tenant_id -> Set[user_id]`.
2. **Atomic Invalidation**:
   - When `.delete(user_id)` is called, the entity must be completely purged from `_by_id`, `_by_email`, and removed from `_by_tenant` set.
3. **Thread-Safety**: Protect index mutations with `threading.Lock`.

---

## Solution Guidelines & Architecture Hints
- For Challenge 1, remember the base case `remainder_counts = {0: 1}` (a prefix sum of 0 has remainder 0).
- For Challenge 2, dummy `head` and `tail` nodes eliminate `if self.head is None` edge cases during list splicing.
- For Challenge 3, ensure updating an existing user's email cleanly evicts the old email pointer from the secondary index.
