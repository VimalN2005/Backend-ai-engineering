# Critical Antipatterns & Production Traps in LRU Caching

LRU caches are foundational components of high-throughput backend services. However, subtle flaws in pointer manipulation, concurrency management, and memory lifecycle can cause silent memory leaks, thread deadlocks, or service crashes. Below are the top 7 mistakes observed in production caching systems.

---

### Mistake 1: Treating `get()` as a Read-Only Passive Operation

#### Bad Pattern
Returning the cached value from the HashMap without moving the node to the head of the Doubly Linked List:

```python
# INCORRECT: get() fails to update recency
def get_broken(self, key):
    if key not in self.lookup:
        return -1
    node = self.lookup[key]
    return node.val  # BUG: Node stays near the tail and will be evicted next!
```

#### Correct Pattern
Every successful `get(key)` must promote the node to the Most Recently Used (MRU) position directly after the dummy head:

```python
# CORRECT: Promotes accessed node to MRU
def get_correct(self, key):
    if key not in self.lookup:
        return -1
    node = self.lookup[key]
    self._move_to_head(node)  # Detach and re-insert after head
    return node.val
```

#### Why It Breaks
If a key is accessed a million times via `get()`, but its recency is never refreshed, the cache treats it as stale. The very next `put()` will evict this ultra-hot key from the tail, causing an immediate database storm.

---

### Mistake 2: Omitting `key` from the Doubly Linked Node

#### Bad Pattern
Storing only `val`, `prev`, and `next` inside the node:

```python
# INCORRECT: Node does not know its own key
class BadNode:
    def __init__(self, val):
        self.val = val
        self.prev = None
        self.next = None
```

#### Correct Pattern
Always store `key` inside the node alongside `val`:

```python
# CORRECT: Node stores key for O(1) reverse HashMap deletion
class GoodNode:
    __slots__ = ("key", "val", "prev", "next")
    def __init__(self, key, val):
        self.key = key
        self.val = val
        self.prev = None
        self.next = None
```

#### Why It Breaks
When capacity is exceeded, the LRU item is popped from the tail: `lru = tail.prev`.
You can unlink `lru` in $O(1)$ time, but you **must also delete it from `self.lookup`**.
If `lru` does not store `key`, the only way to delete it from the HashMap is:
```python
# DISASTROUS: O(N) reverse scan defeats the entire O(1) cache architecture!
for k, v in self.lookup.items():
    if v is lru:
        del self.lookup[k]
        break
```

---

### Mistake 3: Raw Pointer Manipulation Without Sentinel Dummy Nodes

#### Bad Pattern
Managing raw `head` and `tail` pointers without sentinels, requiring endless conditional branches:

```python
# DANGEROUS: High probability of NullPointerException / AttributeError
def add_to_head_fragile(self, node):
    if self.head is None:
        self.head = node
        self.tail = node
    else:
        node.next = self.head
        self.head.prev = node
        self.head = node
    # Repeated conditionals needed for remove_node, pop_tail, update, etc.
```

#### Correct Pattern
Use permanent **Sentinel Dummy Nodes** (`head` and `tail`):

```python
# ROBUST: Zero null checks across all operations
def __init__(self, capacity):
    self.head = Node("__HEAD__", None)
    self.tail = Node("__TAIL__", None)
    self.head.next = self.tail
    self.tail.prev = self.head

def _insert_head(self, node):
    node.prev = self.head
    node.next = self.head.next
    self.head.next.prev = node
    self.head.next = node
```

#### Why It Breaks
In a raw pointer implementation, edge cases like "evicting the last item in a 1-item cache" or "updating the head" require 6 to 8 nested conditional branches. A single missed check corrupts the linked list into an infinite loop or triggers `AttributeError: 'NoneType' object has no attribute 'next'`.

---

### Mistake 4: Unbounded In-Memory Caches (The OOM Trap)

#### Bad Pattern
Using a plain Python dictionary without capacity limits or eviction policies:

```python
# INCORRECT: Memory grows unbounded until process is killed by OS OOM-Killer
GLOBAL_CACHE = {}

def get_user_profile(user_id: str):
    if user_id not in GLOBAL_CACHE:
        GLOBAL_CACHE[user_id] = fetch_from_db(user_id)
    return GLOBAL_CACHE[user_id]
```

#### Correct Pattern
Always set an explicit upper capacity bound and evict items:

```python
# CORRECT: Bounded memory footprint with deterministic eviction
GLOBAL_CACHE = ManualLRUCache(capacity=50_000)
```

#### Why It Breaks
Under sustained traffic or automated scraping, `GLOBAL_CACHE` accumulates millions of distinct keys. In containerized environments (Kubernetes pods), hitting the memory cgroup limit triggers an instant `SIGKILL` (OOMKilled exit code 137).

---

### Mistake 5: Assuming Reads are Thread-Safe Without Locks

#### Bad Pattern
Using an LRU cache across multiple threads without locking `get()`:

```python
# DANGEROUS: Race conditions during pointer updates
class ThreadUnsafeCache:
    def get(self, key):
        if key in self.lookup:
            node = self.lookup[key]
            self._move_to_head(node)  # MUTATES prev/next POINTERS!
            return node.val
```

#### Correct Pattern
Protect both `get()` and `put()` with reentrant locks or reader-writer synchronization:

```python
import threading

class ThreadSafeCache:
    def __init__(self, capacity):
        self._lock = threading.RLock()
        # ...

    def get(self, key):
        with self._lock:  # Lock is mandatory because get() mutates pointers
            if key not in self.lookup:
                return None
            node = self.lookup[key]
            self._move_to_head(node)
            return node.val
```

#### Why It Breaks
Unlike standard dictionaries where reads are passive, in an LRU cache **`get()` is a write operation** that updates four pointers (`node.prev.next`, `node.next.prev`, etc.). Two concurrent `get()` calls on adjacent nodes will corrupt pointers, creating orphaned nodes or circular reference loops.

---

### Mistake 6: Cache Stampede (Thundering Herd) on Hot Key Eviction

#### Bad Pattern
When a hot key is evicted or expires, all incoming threads simultaneously query the database:

```python
# INCORRECT: If key expires, 500 concurrent requests all miss and query DB simultaneously
def fetch_product(product_id: str):
    val = cache.get(product_id)
    if val is None:
        val = query_slow_database(product_id)  # DB crashes under 500 parallel queries!
        cache.put(product_id, val)
    return val
```

#### Correct Pattern
Use the **SingleFlight (Mutex / Promise)** pattern to coalesce concurrent misses into a single database fetch:

```python
import threading

_locks = defaultdict(threading.Lock)

def fetch_product_safe(product_id: str):
    val = cache.get(product_id)
    if val is not None:
        return val

    # Acquire per-key lock so only ONE thread queries the database
    with _locks[product_id]:
        # Double-check cache inside lock
        val = cache.get(product_id)
        if val is not None:
            return val

        val = query_slow_database(product_id)
        cache.put(product_id, val)
        return val
```

---

### Mistake 7: Conflating TTL Expiration with LRU Capacity Eviction

#### Bad Pattern
Assuming that having an LRU capacity replaces the need for TTL, or vice versa:
- An LRU cache with no TTL stores stale data indefinitely if capacity is large.
- A TTL-only cache with no capacity bounds exhausts RAM if burst traffic arrives within the TTL window.

#### Correct Architectural Standard
Implement a **Multi-Tiered Cache**:
1. **TTL (Time-To-Live)** defines **data freshness** (e.g. invalidate user token after 15 minutes).
2. **LRU Capacity** defines **memory safety** (e.g. keep at most 10,000 items in RAM).
3. Evaluate TTL passively on `get()`, and enforce LRU actively on `put()`.
