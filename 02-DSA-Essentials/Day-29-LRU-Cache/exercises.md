# Practice Exercises: Advanced Caching & O(1) Data Structures

Mastering in-memory systems requires moving beyond basic LRU into multi-index $O(1)$ architectures:
1. **Frequency-Based Eviction (LFU Cache)** — Dual HashMap + Frequency Doubly Linked Lists in strict $O(1)$ time.
2. **Frequency Bucket Matrix (All O(1) Data Structure)** — Linked list of frequency buckets with $O(1)$ min/max tracking.
3. **Dual-Tiered TTL In-Memory Store** — Active janitor eviction thread with passive read invalidation.

---

## Challenge 1: LFU (Least Frequently Used) Cache (LeetCode 460 - Hard)

### Problem Statement
Design and implement a data structure for a **Least Frequently Used (LFU)** cache.

Implement the `LFUCache` class:
- `LFUCache(int capacity)`: Initializes the object with the `capacity` of the data structure.
- `int get(int key)`: Gets the value of the `key` if the key exists in the cache. Otherwise, returns `-1`.
- `void put(int key, int value)`: Update the value of the `key` if present, or inserts the key if not already present. When the cache reaches its `capacity`, it should invalidate and remove the **least frequently used** key before inserting a new item. For this problem, when there is a **tie** (two or more keys with the same frequency), the **least recently used** key should be invalidated.

Both `get` and `put` must run in **$O(1)$ average time complexity**.

### Constraints
- $1 \le \text{capacity} \le 10^4$
- $0 \le \text{key} \le 10^5$
- $0 \le \text{value} \le 10^9$
- At most $2 \times 10^5$ calls will be made to `get` and `put`.

### Optimal Solution (Dual HashMap + Frequency DLL)

```python
from collections import defaultdict
from typing import Dict, Optional

class LFUNode:
    __slots__ = ("key", "val", "freq", "prev", "next")

    def __init__(self, key: int, val: int):
        self.key = key
        self.val = val
        self.freq = 1
        self.prev: Optional["LFUNode"] = None
        self.next: Optional["LFUNode"] = None


class DoublyLinkedList:
    """Internal DLL managing nodes sharing the exact same frequency."""

    def __init__(self):
        self.head = LFUNode(0, 0)
        self.tail = LFUNode(0, 0)
        self.head.next = self.tail
        self.tail.prev = self.head
        self.size = 0

    def add_to_head(self, node: LFUNode) -> None:
        node.prev = self.head
        node.next = self.head.next
        self.head.next.prev = node
        self.head.next = node
        self.size += 1

    def remove_node(self, node: LFUNode) -> None:
        p = node.prev
        n = node.next
        p.next = n
        n.prev = p
        self.size -= 1

    def pop_tail(self) -> LFUNode:
        lru = self.tail.prev
        self.remove_node(lru)
        return lru

    def is_empty(self) -> bool:
        return self.size == 0


class LFUCache:
    """
    LFU Cache executing all operations in strict O(1) time.
    
    Data Structures:
    1. node_map: key -> LFUNode
    2. freq_map: frequency_int -> DoublyLinkedList of nodes with that frequency
    3. min_freq: Tracks the lowest active frequency in the cache.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.node_map: Dict[int, LFUNode] = {}
        self.freq_map: Dict[int, DoublyLinkedList] = defaultdict(DoublyLinkedList)
        self.min_freq = 0

    def _increment_freq(self, node: LFUNode) -> None:
        """Promotes node from freq list to freq + 1 list."""
        old_freq = node.freq
        self.freq_map[old_freq].remove_node(node)

        # If min_freq list is now empty and was equal to old_freq, advance min_freq
        if self.freq_map[old_freq].is_empty():
            del self.freq_map[old_freq]
            if self.min_freq == old_freq:
                self.min_freq += 1

        node.freq += 1
        self.freq_map[node.freq].add_to_head(node)

    def get(self, key: int) -> int:
        if key not in self.node_map:
            return -1
        node = self.node_map[key]
        self._increment_freq(node)
        return node.val

    def put(self, key: int, value: int) -> None:
        if self.capacity <= 0:
            return

        if key in self.node_map:
            node = self.node_map[key]
            node.val = value
            self._increment_freq(node)
        else:
            if len(self.node_map) >= self.capacity:
                # Evict LRU node from min_freq list
                lfu_list = self.freq_map[self.min_freq]
                evicted = lfu_list.pop_tail()
                del self.node_map[evicted.key]
                if lfu_list.is_empty():
                    del self.freq_map[self.min_freq]

            new_node = LFUNode(key, value)
            self.node_map[key] = new_node
            self.freq_map[1].add_to_head(new_node)
            self.min_freq = 1  # Brand new item always has freq = 1


# Verification
if __name__ == "__main__":
    lfu = LFUCache(2)
    lfu.put(1, 1)
    lfu.put(2, 2)
    assert lfu.get(1) == 1     # 1 freq=2, 2 freq=1
    lfu.put(3, 3)              # evicts 2 (lowest freq=1)
    assert lfu.get(2) == -1
    assert lfu.get(3) == 3     # 3 freq=2, 1 freq=2
    lfu.put(4, 4)              # tie: both 1 and 3 have freq=2. 1 accessed earlier -> evicts 1
    assert lfu.get(1) == -1
    assert lfu.get(3) == 3
    assert lfu.get(4) == 4
    print("[PASS] Challenge 1: LFU Cache verified.")
```

---

## Challenge 2: All O(1) Data Structure (LeetCode 432 - Hard)

### Problem Statement
Design a data structure to store the strings' count with the ability to return the strings with minimum and maximum counts.

Implement the `AllOne` class:
- `AllOne()`: Initializes the object.
- `inc(String key)`: Increments the count of the string `key` by `1`. If `key` does not exist in the data structure, insert it with count `1`.
- `dec(String key)`: Decrements the count of the string `key` by `1`. If the count of `key` reaches `0`, remove it from the data structure.
- `getMaxKey()`: Returns one of the keys with the maximal count. If no element exists, return an empty string `""`.
- `getMinKey()`: Returns one of the keys with the minimal count. If no element exists, return an empty string `""`.

**Each function must run in $O(1)$ average time complexity.**

### Optimal Solution (Doubly Linked List of Frequency Buckets)

```python
from typing import Dict, Optional, Set

class BucketNode:
    """A node representing a unique count, containing a set of all keys with that count."""
    __slots__ = ("count", "keys", "prev", "next")

    def __init__(self, count: int):
        self.count = count
        self.keys: Set[str] = set()
        self.prev: Optional["BucketNode"] = None
        self.next: Optional["BucketNode"] = None


class AllOne:
    """
    Maintains a sorted Doubly Linked List of frequency buckets.
    - head.next has the MINIMUM count.
    - tail.prev has the MAXIMUM count.
    """

    def __init__(self):
        self.key_bucket_map: Dict[str, BucketNode] = {}
        # Sentinel dummy nodes
        self.head = BucketNode(0)
        self.tail = BucketNode(0)
        self.head.next = self.tail
        self.tail.prev = self.head

    def _insert_after(self, curr: BucketNode, new_node: BucketNode) -> None:
        new_node.prev = curr
        new_node.next = curr.next
        curr.next.prev = new_node
        curr.next = new_node

    def _remove_bucket(self, bucket: BucketNode) -> None:
        p = bucket.prev
        n = bucket.next
        p.next = n
        n.prev = p

    def inc(self, key: str) -> None:
        if key not in self.key_bucket_map:
            # Check if bucket with count=1 exists directly after head
            if self.head.next.count == 1:
                target_bucket = self.head.next
            else:
                target_bucket = BucketNode(1)
                self._insert_after(self.head, target_bucket)
            target_bucket.keys.add(key)
            self.key_bucket_map[key] = target_bucket
        else:
            curr_bucket = self.key_bucket_map[key]
            nxt_count = curr_bucket.count + 1
            if curr_bucket.next.count == nxt_count:
                target_bucket = curr_bucket.next
            else:
                target_bucket = BucketNode(nxt_count)
                self._insert_after(curr_bucket, target_bucket)

            target_bucket.keys.add(key)
            self.key_bucket_map[key] = target_bucket
            curr_bucket.keys.remove(key)
            if not curr_bucket.keys:
                self._remove_bucket(curr_bucket)

    def dec(self, key: str) -> None:
        if key not in self.key_bucket_map:
            return

        curr_bucket = self.key_bucket_map[key]
        curr_bucket.keys.remove(key)

        if curr_bucket.count == 1:
            del self.key_bucket_map[key]
        else:
            prev_count = curr_bucket.count - 1
            if curr_bucket.prev.count == prev_count:
                target_bucket = curr_bucket.prev
            else:
                target_bucket = BucketNode(prev_count)
                self._insert_after(curr_bucket.prev, target_bucket)

            target_bucket.keys.add(key)
            self.key_bucket_map[key] = target_bucket

        if not curr_bucket.keys:
            self._remove_bucket(curr_bucket)

    def getMaxKey(self) -> str:
        if self.tail.prev == self.head:
            return ""
        # Return any key in the maximum bucket (tail.prev)
        return next(iter(self.tail.prev.keys))

    def getMinKey(self) -> str:
        if self.head.next == self.tail:
            return ""
        # Return any key in the minimum bucket (head.next)
        return next(iter(self.head.next.keys))


# Verification
if __name__ == "__main__":
    all_one = AllOne()
    all_one.inc("hello")
    all_one.inc("hello")
    assert all_one.getMaxKey() == "hello"
    assert all_one.getMinKey() == "hello"
    all_one.inc("world")
    assert all_one.getMaxKey() == "hello"  # count=2
    assert all_one.getMinKey() == "world"  # count=1
    print("[PASS] Challenge 2: All O(1) Data Structure verified.")
```

---

## Challenge 3: In-Memory Key-Value Store with TTL & Active Cleanup

### Problem Statement
Build a thread-safe in-memory key-value cache supporting `set(key, val, ttl_seconds)` and `get(key)`. In addition to passive eviction on read, implement a **background janitor worker thread** that periodically samples and purges expired keys to prevent memory bloat when keys are written but never subsequently read.

```python
import threading
import time
from typing import Any, Dict, Optional

class ActiveTTLCache:
    def __init__(self, cleanup_interval_seconds: float = 1.0):
        self._store: Dict[str, tuple[Any, Optional[float]]] = {}
        self._lock = threading.Lock()
        self._running = True
        self._cleanup_interval = cleanup_interval_seconds
        self._thread = threading.Thread(target=self._janitor_loop, daemon=True)
        self._thread.start()

    def set(self, key: str, val: Any, ttl: Optional[float] = None):
        expires_at = (time.time() + ttl) if ttl else None
        with self._lock:
            self._store[key] = (val, expires_at)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._store:
                return None
            val, expires_at = self._store[key]
            if expires_at and time.time() >= expires_at:
                del self._store[key]
                return None
            return val

    def _janitor_loop(self):
        """Active background cleaner that purges dead keys."""
        while self._running:
            time.sleep(self._cleanup_interval)
            now = time.time()
            with self._lock:
                expired_keys = [k for k, (v, exp) in self._store.items() if exp and now >= exp]
                for k in expired_keys:
                    del self._store[k]

    def stop(self):
        self._running = False


# Verification
if __name__ == "__main__":
    cache = ActiveTTLCache(cleanup_interval_seconds=0.1)
    cache.set("ephemeral", "data", ttl=0.05)
    assert cache.get("ephemeral") == "data"
    time.sleep(0.2)
    assert cache.get("ephemeral") is None
    cache.stop()
    print("[PASS] Challenge 3: Active TTL Cache verified.")
```
