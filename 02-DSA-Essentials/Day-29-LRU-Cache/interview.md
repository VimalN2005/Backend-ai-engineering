# Senior Backend & AI Systems Interview: LRU Cache & In-Memory Systems

This document details the top 5 high-impact interview questions asked by Tier-1 tech companies (Google, Meta, Amazon, Netflix, Stripe) regarding in-memory cache design, pointer mechanics, and eviction trade-offs.

---

### Question 1: Why does pairing a HashMap with a Doubly Linked List achieve $O(1)$ time, and why does a Singly Linked List fail?

#### The Question
"To implement an LRU cache, both `get(key)` and `put(key, value)` must run in strict $O(1)$ time. Why is a HashMap alone insufficient? Why does a Singly Linked List fail to deliver $O(1)$, making a Doubly Linked List mandatory?"

#### Candidate Answer
An LRU cache requires two distinct capabilities operating concurrently in $O(1)$ time:
1. **Instant Key Lookup**: Finding whether an item exists and reading its value.
2. **Instant Recency Repositioning & Eviction**: Removing an item from its current position and splicing it to the head, and evicting the oldest item from the tail.

#### Why Single Structures Fail:
- **HashMap alone**: Delivers $O(1)$ key lookup, but has zero awareness of chronological access order. Identifying the oldest key requires scanning all $N$ keys ($O(N)$ time).
- **Array / Dynamic List**: Can maintain order, but moving an element from the middle to the front requires shifting $O(N)$ elements in memory.
- **Singly Linked List**:
  - With a pointer to a node `curr`, you can insert after it in $O(1)$.
  - However, to **unlink** `curr` (which is required whenever `get()` promotes an accessed node), you must update its predecessor's pointer:
    $$\text{prev.next} = \text{curr.next}$$
  - In a Singly Linked List, a node has no backward pointer to `prev`. The only way to find `prev` is to traverse the entire list from `head`, costing $O(N)$ time!

#### The Doubly Linked List Invariant:
In a Doubly Linked List, every node stores both `next` and `prev`. Given a direct node reference from the HashMap:
```python
# Unlinking a node takes strict O(1) time:
curr.prev.next = curr.next
curr.next.prev = curr.prev
```
The HashMap maps `key -> NodePointer` in $O(1)$ average time, and the DLL unlinks and inserts the node at the head in $O(1)$ guaranteed time.

---

### Question 2: What architectural purpose do Sentinel Dummy Nodes serve, and how do they prevent bugs?

#### The Question
"In your LRU cache implementation, you used dummy `head` and `tail` sentinel nodes. Why not just maintain raw `self.head` and `self.tail` pointers pointing directly to the first and last data nodes?"

#### Candidate Answer
Sentinel dummy nodes are an **invariant-preserving architectural pattern** that eliminates boundary conditionals:

#### Without Sentinels (Raw Pointers):
Every list mutation must handle up to 8 distinct edge cases:
1. Inserting into an empty list (`head = tail = new_node`).
2. Inserting into a 1-element list.
3. Removing the head node (`head = head.next; head.prev = None`).
4. Removing the tail node (`tail = tail.prev; tail.next = None`).
5. Removing the only node left (`head = tail = None`).
6. Moving a node that is already at the head.
7. Moving a node that is currently at the tail.

In production multithreaded systems, nested `if head is None` or `if node == self.tail` checks create high cyclomatic complexity, race condition windows, and frequent `NullPointerException` / `AttributeError` crashes.

#### With Sentinels (`head <-> tail`):
The list is initialized as:
$$\text{head.next} = \text{tail} \quad \text{and} \quad \text{tail.prev} = \text{head}$$
- The list is **never empty**. There are always at least two nodes in memory.
- Every valid data node is guaranteed to have a non-null `node.prev` and a non-null `node.next`.
- Insertion is always:
  ```python
  node.prev = head
  node.next = head.next
  head.next.prev = node
  head.next = node
  ```
- Unlinking is always:
  ```python
  node.prev.next = node.next
  node.next.prev = node.prev
  ```
Zero `if/else` boundary branches exist in the core algorithm, yielding faster CPU branch prediction and bug-free execution.

---

### Question 3: How do LRU, LFU, and Adaptive Replacement Cache (ARC) compare in production workloads?

#### The Question
"What are the operational trade-offs between LRU and LFU? What is the 'Scan Resistance' problem, and how does ARC solve it?"

#### Candidate Answer

| Eviction Policy | Core Metric | Primary Strength | Critical Vulnerability |
|---|---|---|---|
| **LRU** (Least Recently Used) | Recency of access ($T_{\text{last}}$) | Adapts rapidly to shifting hot-key trends; low $O(1)$ overhead. | **Vulnerable to batch scans** (a sequential table dump flushes entire cache). |
| **LFU** (Least Frequently Used) | Frequency of access ($F_{\text{count}}$) | Protects long-term popular items from transient bursts. | **Cache Pollution / Stale Frequency**: Historical hot items stay forever even when no longer used. |
| **ARC** (Adaptive Replacement Cache) | Dynamic balance of Recency & Frequency | Self-tuning; resists scans while protecting high-frequency keys. | Complex implementation; higher memory footprint (tracks 4 lists). |

#### The Scan-Resistance Problem:
In pure LRU, if a background cron job runs `SELECT * FROM large_table` scanning 100,000 cold rows, those cold rows will flood the cache, evicting the genuinely hot working set.
- **Modern Solution**: Multi-Queue Caching (e.g. **2Q** or **W-TinyLFU** used in Java's Caffeine Cache). Newly inserted items enter a probationary LRU window. They are only promoted to the main protected cache if they are accessed a second time, rendering the cache immune to sequential scans.

---

### Question 4: Why does a single-lock LRU suffer from lock contention, and how is it solved in production?

#### The Question
"In a standard multithreaded web application (e.g. FastAPI / Go web server), why does an in-memory LRU cache with a global mutex become a CPU bottleneck, even if 95% of requests are reads (`get`)?"

#### Candidate Answer

#### The Hidden Write in `get()`
In standard data structures (like a concurrent HashMap), read operations do not modify internal pointers. Multiple reader threads can execute concurrently using a Read-Write Lock (`shared_mutex`).
- **In an LRU Cache, every `get()` is a write!**
- Accessing key $K$ requires detaching its node and moving it to the head of the Doubly Linked List.
- Therefore, every `get()` requires an **Exclusive Write Lock**. On a 64-core server handling 100,000 req/sec, all 64 cores serialize on that single lock, causing CPU core stalling and massive latency spikes.

#### Production Solutions:

1. **Sharded (Striped) LRU Cache**:
   - Divide the keyspace into $N$ independent sub-caches (e.g. $N=16$ or $N=64$).
   - Route keys via `shard_id = hash(key) % N`.
   - Each shard has its own independent lock. Contention is reduced by a factor of $N$.

2. **Read Buffering (Batched Async Recency)**:
   - Used by Caffeine and modern high-concurrency caches.
   - When a thread calls `get()`, it does NOT immediately mutate the linked list.
   - Instead, it appends the accessed key into a thread-local ring buffer.
   - A background drain thread or periodic batch worker flushes the ring buffer and updates the linked list in bulk, allowing `get()` to execute lock-free.

---

### Question 5: How does Redis implement LRU, and why doesn't it use a Doubly Linked List?

#### The Question
"Redis supports `allkeys-lru` and `volatile-lru` eviction policies. However, Salvatore Sanfilippo (creator of Redis) famously refused to use a Doubly Linked List for LRU. How does Redis implement LRU, and why?"

#### Candidate Answer

#### Why Redis Avoids True Doubly Linked Lists:
1. **Memory Overhead**:
   - A standard 64-bit Doubly Linked List node requires two 8-byte pointers (`prev` and `next`) = 16 bytes.
   - Adding internal C struct padding and allocator headers raises overhead to ~24–32 bytes per key.
   - In a Redis instance caching 50 million keys, the linked list pointers alone consume **1.2 to 1.6 Gigabytes of RAM** just to maintain access order!
2. **Write Amplification**:
   - Every read command (`GET`) would mutate pointers, causing cache line invalidations across CPU cores and triggering dirty memory pages in OS COW (Copy-On-Write) fork mechanisms.

#### The Redis Approximated LRU Algorithm:
Instead of maintaining a continuous linked list:
1. Every Redis object header stores a compact **24-bit timestamp** (`lruclock`), indicating the last time the object was accessed.
2. When memory limit (`maxmemory`) is exceeded:
   - Redis does **not** look for the absolute global oldest key.
   - Instead, it selects a random sample of $K$ keys (default $K=5$, configurable via `maxmemory-samples`).
   - It computes the idle time for each of the $K$ keys: $\text{idle} = \text{current\_time} - \text{lruclock}$.
   - It evicts the single key with the largest idle time among the sample.
3. In Redis 3.0+, an **eviction pool** (a 16-slot sorted array of candidate keys) was added to persist the best candidates across eviction cycles.
4. **Result**: With $K=10$, Redis's approximated LRU is mathematically indistinguishable from true LRU (within 1% precision), while saving gigabytes of memory and eliminating pointer mutations on reads.
