# Day 29 Rapid Revision: LRU Cache Implementation

A condensed 5-minute reference sheet for LRU Cache pointer mechanics, sentinel node patterns, and concurrency invariants.

---

## 1. Algorithmic Decision Matrix

| Cache Architecture | Optimal Use Case | Primary Advantage | Limitation / Trade-Off |
|---|---|---|---|
| **Manual DLL + HashMap** | Custom cache engines (TTL, byte budgets, custom eviction hooks) | Strict $O(1)$, full internal control | Manual pointer management |
| **`collections.OrderedDict`**| Standard single-threaded Python backend services | Highly optimized in C; concise (15 lines) | Limited customization |
| **Sharded (Striped) LRU** | Multi-threaded backend services (FastAPI, Gunicorn) | Mitigates lock contention by factor of $N$ | Hashing overhead |
| **Approximated LRU (Redis)**| High-density distributed cache ($>10^7$ keys) | Zero pointer RAM overhead (~24 bytes/key saved) | Eviction is statistically approximate |
| **LFU Cache (Least Freq)** | Static catalog / read-heavy hot objects | Protects frequent items from transient scans | Vulnerable to stale historical frequency |

---

## 2. Core Reusable Code Templates

### Template 1: From-Scratch Manual LRU Cache (Sentinel Nodes)
```python
class Node:
    __slots__ = ("key", "val", "prev", "next")
    def __init__(self, key, val):
        self.key, self.val = key, val
        self.prev = self.next = None

class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.lookup = {}
        # Sentinels eliminate null-checks
        self.head, self.tail = Node(0, 0), Node(0, 0)
        self.head.next = self.tail
        self.tail.prev = self.head

    def _remove(self, node: Node) -> None:
        node.prev.next = node.next
        node.next.prev = node.prev

    def _insert_head(self, node: Node) -> None:
        node.prev = self.head
        node.next = self.head.next
        self.head.next.prev = node
        self.head.next = node

    def get(self, key: int) -> int:
        if key not in self.lookup:
            return -1
        node = self.lookup[key]
        self._remove(node)
        self._insert_head(node)  # Move to MRU
        return node.val

    def put(self, key: int, value: int) -> None:
        if key in self.lookup:
            node = self.lookup[key]
            node.val = value
            self._remove(node)
            self._insert_head(node)
        else:
            new_node = Node(key, value)
            self.lookup[key] = new_node
            self._insert_head(new_node)
            if len(self.lookup) > self.capacity:
                lru = self.tail.prev
                self._remove(lru)
                del self.lookup[lru.key]
```

### Template 2: Python `OrderedDict` Standard Implementation
```python
from collections import OrderedDict

class LRUCacheOrderedDict:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key)  # O(1) move to MRU
        return self.cache[key]

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)  # O(1) pop LRU from front
```

---

## 3. Non-Negotiable Invariants & Rules

1. **`get()` is a Mutating Write**: In an LRU cache, `get()` **must** promote the node to the head. It is not passive.
2. **Always Store `key` in the Node**: When evicting `tail.prev`, the node must supply its key so it can be deleted from the HashMap in $O(1)$ time without an $O(N)$ reverse search.
3. **Sentinels are Mandatory**: Always initialize immutable `head` and `tail` dummy nodes. This guarantees every node has non-null `prev` and `next`, eliminating 8 edge-case branches.
4. **Capacity Bounds**: Never create an unbounded cache. Always enforce an upper bound to prevent Out-Of-Memory container kills.
5. **Thread Safety**: Because `get()` modifies pointers, it requires write synchronization (`RLock`) in multithreaded environments.

---

## 4. Complexity Quick Reference Table

| Operation | Time Complexity | Space Complexity | Internal Action |
|---|---|---|---|
| **`get(key)` (Hit)** | $O(1)$ | $O(1)$ | Hash lookup + unhook node + insert at head |
| **`get(key)` (Miss)**| $O(1)$ | $O(1)$ | Hash lookup returns None |
| **`put(key)` (Update)**| $O(1)$ | $O(1)$ | Update value + move to head |
| **`put(key)` (Insert)**| $O(1)$ | $O(1)$ | Allocate node + insert head + pop tail if full |
| **Total Cache Footprint**| — | $O(C)$ | Size proportional to capacity $C$ |
