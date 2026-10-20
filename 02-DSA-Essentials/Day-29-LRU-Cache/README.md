# Day 29: Core System DSA — LRU Cache Implementation (HashMap + Doubly Linked List)

Caching is the primary architectural tool used to bridge the orders-of-magnitude latency gap between volatile CPU memory (nanoseconds) and persistent disk/network storage (milliseconds). 

In enterprise backend engineering and AI infrastructure, caching accelerates database query resolution, API gateway responses, session management, and Large Language Model (LLM) semantic prompt retrieval.

Because physical RAM is finite and expensive, every in-memory cache requires an **Eviction Policy** to discard items when capacity is reached. Among all eviction strategies, **Least Recently Used (LRU)** is the industry standard due to its exploitation of **temporal locality**.

---

## 1. The Caching Latency Hierarchy

```
+-----------------------------------------------------------------------------------+
|                        THE SYSTEM LATENCY SPECTRUM                                |
+-----------------------------------------------------------------------------------+
| L1 / L2 CPU Cache       | 0.5 - 7 ns     | On-chip silicon                        |
| RAM (Main Memory)       | ~100 ns        | Local DDR5 memory (In-Memory Cache)    |
| Redis / Memcached       | ~0.5 - 2 ms    | Local network TCP roundtrip            |
| NVMe SSD Storage        | ~10 - 100 us   | Local non-volatile flash disk          |
| Relational Database     | ~5 - 50 ms     | PostgreSQL disk read, index scan, locks|
| LLM Inference API       | ~500 - 5000 ms | Model weights forward pass             |
+-----------------------------------------------------------------------------------+
```

An in-memory LRU cache operating in RAM delivers a **10,000x to 50,000x latency reduction** compared to a persistent database query.

---

## 2. The Cache Eviction Dilemma

```
+--------------------+--------------------------------------------------------------+
| Eviction Policy    | Decision Rule & Trade-Off                                    |
+--------------------+--------------------------------------------------------------+
| FIFO (First In)    | Evicts the oldest inserted item. Ignores usage frequency.     |
| LIFO (Last In)     | Evicts the newest item. Pathological for sequential access.  |
| LFU (Least Freq)   | Evicts item with lowest access count. Vulnerable to past bias|
| LRU (Least Recent) | Evicts item not accessed for longest duration. Optimal balance|
| ARC (Adaptive)     | Dynamically tunes between LRU and LFU (patented / complex).  |
+--------------------+--------------------------------------------------------------+
```

### The LRU Invariant
The **temporal locality principle** states that data accessed recently is disproportionately likely to be accessed again in the near future.
- When capacity $C$ is exhausted and a new key must be inserted, the cache must evict the **Least Recently Used** item.
- Every read (`get`) or write (`put`) marks the targeted item as the **Most Recently Used (MRU)**.

---

## 3. The Algorithmic Conundrum: Why Single Data Structures Fail

To achieve production-grade caching, both `get(key)` and `put(key, value)` **must execute in strict $O(1)$ time**.

```
+------------------------+--------------------+--------------------+--------------------+
| Data Structure         | Lookup by Key      | Update Recency     | Evict Oldest (LRU) |
+------------------------+--------------------+--------------------+--------------------+
| Array / List           | O(N) linear search | O(N) shift elements| O(1) at end        |
| Standard Hash Table    | O(1) average       | Impossible (no order)| O(N) search      |
| Singly Linked List     | O(N) linear search | O(N) cannot unlink | O(1) at head       |
| Queue / Stack          | O(N) search        | O(N) cannot reposition| O(1) pop        |
| HashMap + Doubly List  | O(1) guaranteed    | O(1) direct unlink | O(1) pop tail      |
+------------------------+--------------------+--------------------+--------------------+
```

### Why Singly Linked List Fails
To unlink a node from the middle of a linked list, you need access to its **predecessor** (`prev.next = curr.next`). In a singly linked list, finding `prev` requires an $O(N)$ traversal from the head, destroying the $O(1)$ requirement.

---

## 4. The Hybrid Architecture: HashMap + Doubly Linked List

The solution pairs two complementary data structures into a unified engine:

```
                  HASHMAP (Key -> Node Pointer)
                 +-----+-----+-----+-----+
                 | "A" | "B" | "C" | "D" |
                 +--+--+--+--+--+--+--+--+
                    |     |     |     |
      +-------------+     |     |     +-------------+
      |                   |     |                   |
      v                   v     v                   v
+------------+       +-------+-------+       +------------+
| HEAD DUMMY | <---> | [A:1] | [B:2] | <---> | TAIL DUMMY |
+------------+       +-------+-------+       +------------+
  (Most Recent)                               (Least Recent)
```

1. **Hash Table (`dict[Key, Node]`)**: Maps arbitrary keys to the direct memory reference (pointer) of their corresponding node in the Doubly Linked List. Provides **$O(1)$ Key Lookup**.
2. **Doubly Linked List (`Node: prev, next, key, value`)**: Maintains chronological access order. With a direct pointer to any node, it can be unlinked and moved to the head in **$O(1)$ Time**.

---

## 5. The Architectural Power of Sentinel Dummy Nodes

A common source of production bugs and null-pointer exceptions in linked list implementations is handling boundary edge cases:
- Inserting into an empty list.
- Evicting the single remaining node.
- Updating the head node.
- Updating the tail node.

### Sentinel Invariant
By maintaining two permanently allocated, immutable sentinel nodes:
- `head` (pseudo-head representing Most Recently Used side).
- `tail` (pseudo-tail representing Least Recently Used side).

```
Initial Empty State:
[ head ] <=======> [ tail ]
head.next = tail
tail.prev = head
```

**Zero null-checks are required.** Every valid data node always has a non-null `prev` and a non-null `next`.

---

## 6. Anatomy of the DLL Node: Why Store `key` Inside the Node?

A common beginner mistake is designing the node as:
```python
class Node:
    def __init__(self, val):
        self.val = val
        self.prev = None
        self.next = None
```

### Why `key` Storage is Mandatory for Eviction
When the cache reaches capacity, the node to evict is `tail.prev`.
1. We detach `tail.prev` from the Doubly Linked List in $O(1)$ time.
2. Next, we **must delete its entry from the HashMap**.
3. If the node only holds `value`, the HashMap cannot find the corresponding key without an exhaustive $O(N)$ scan of all dictionary keys!
4. By embedding `key` inside `Node(key, value)`, the cache executes:
   ```python
   del self.lookup[lru_node.key]  # O(1) deletion from hashmap
   ```

---

## 7. Step-by-Step Operation Mechanics

```
--------------------------------------------------------------------------------
OPERATION: get(key)
--------------------------------------------------------------------------------
1. Check if key in lookup:
   - If False -> Return -1 / None (Cache Miss).
2. If True (Cache Hit):
   - Retrieve node pointer from lookup[key].
   - Detach node from its current position:
       node.prev.next = node.next
       node.next.prev = node.prev
   - Insert node directly after dummy head (mark Most Recently Used):
       node.next = head.next
       node.prev = head
       head.next.prev = node
       head.next = node
   - Return node.value.

--------------------------------------------------------------------------------
OPERATION: put(key, value)
--------------------------------------------------------------------------------
1. If key in lookup:
   - Update node.value = value.
   - Detach node and move to head (mark MRU).
2. If key NOT in lookup:
   - Create new Node(key, value).
   - Insert new node directly after head.
   - Add lookup[key] = new_node.
   - If len(lookup) > capacity:
       - Identify LRU node: lru = tail.prev.
       - Detach lru from DLL.
       - Delete lookup[lru.key].
```

---

## 8. Python `collections.OrderedDict` Internals

Python provides `collections.OrderedDict`, which implements a dictionary whose keys maintain insertion and mutation order using an internal doubly linked list written in C.

```python
from collections import OrderedDict

class LRUCacheOrderedDict:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key)  # Moves to MRU position in O(1)
        return self.cache[key]

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)  # Pops FIFO (LRU) in O(1)
```

### When to Build a Custom DLL vs Use `OrderedDict`
- Use `OrderedDict` for simple, single-threaded in-process caching where standard eviction suffices.
- Build a **Custom DLL LRU** when you need:
  1. Fine-grained concurrency controls (sharded locks, reader-writer locks).
  2. Integrated Time-To-Live (TTL) expiration timestamps per node.
  3. Dynamic memory byte-size budgeting rather than item count.
  4. Custom telemetry hooks (cache hits, misses, eviction callbacks).

---

## 9. Advanced Production Concerns

### 1. Cache Stampede (Thundering Herd)
When a high-traffic key expires or is evicted, hundreds of concurrent threads may simultaneously encounter a cache miss, bombarding the database with identical queries.
- **Defense**: Use a **SingleFlight (Mutex / Promise)** pattern where only the first thread executes the database query while other concurrent threads wait and subscribe to the result.

### 2. Lock Contention & Sharded LRU
In multi-threaded backends, acquiring a single global lock on every `get()` (because `get` mutates the linked list order!) creates severe lock contention.
- **Defense**: **Sharded (Striped) LRU Cache**. Partition the keyspace into $K$ independent sub-caches (e.g. $K=16$) using `hash(key) % K`, each protected by an independent lock.

### 3. Redis Approximated LRU
Redis is an in-memory datastore managing millions of keys. Maintaining a true Doubly Linked List requires two 64-bit pointers (16 bytes) per key, consuming hundreds of megabytes of overhead.
- **Redis Strategy**: **Random Sample Approximated LRU**. Redis stores a 24-bit timestamp in each object. On eviction, it samples $K$ random keys (default $K=5$) and evicts the one with the oldest idle time. This matches exact LRU performance within 1% while consuming zero extra pointer memory.

---

## 10. 7 Non-Negotiable Rules of Production Cache Engineering

1. **Always Use Sentinel Dummy Nodes**: Never manage raw `head` and `tail` pointers without sentinels; eliminating edge-case conditionals prevents off-by-one and null-pointer bugs.
2. **Store `key` Inside the Node**: A node must know its own key so that when `tail.prev` is evicted, it can delete its entry from the hash table in $O(1)$ time.
3. **Move on `get()`**: A read is not passive in an LRU cache; it must promote the accessed node to the MRU position.
4. **Thread-Safe Mutation**: Because `get()` modifies the linked list, read operations require write locking unless a lock-free or sharded cache architecture is used.
5. **Set an Upper Capacity Bound**: Never create an unbounded in-memory cache. Unbounded caches are the #1 cause of `OutOfMemory (OOM)` container crashes in microservices.
6. **Decouple Eviction from TTL**: Expired items (TTL) and evicted items (LRU capacity) are distinct failure modes. Ensure TTL checks happen before serving stale data.
7. **Instrument Telemetry**: Always track Cache Hit Ratio ($\frac{\text{Hits}}{\text{Hits} + \text{Misses}}$) to ensure the cache is actually protecting downstream services.

---

## 11. Comprehensive Complexity Matrix

| Operation | Custom DLL + HashMap | `collections.OrderedDict` | Naive Python `list` |
|---|---|---|---|
| **`get(key)`** | $O(1)$ | $O(1)$ | $O(N)$ |
| **`put(key, value)`** | $O(1)$ | $O(1)$ | $O(N)$ |
| **Recency Update** | $O(1)$ (Pointer swap) | $O(1)$ (`move_to_end`) | $O(N)$ (Array shift) |
| **LRU Eviction** | $O(1)$ (`tail.prev`) | $O(1)$ (`popitem(last=False)`) | $O(1)$ (`pop(0)`) |
| **Space Overhead** | $O(C)$ | $O(C)$ | $O(C)$ |
| **Memory per Node** | ~64 bytes (Obj + 2 ptrs) | Optimized C-struct | Low (contiguous array) |
