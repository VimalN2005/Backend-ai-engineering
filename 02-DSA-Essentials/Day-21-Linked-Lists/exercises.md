# Day 21: Week 3 Milestone Review - 13 Practical DSA Challenges

Welcome to the **Week 3 Milestone Review**! As part of our 7-day milestone cadence, this file contains **13 practical, production-grade challenges** (3 standard challenges + 10 milestone challenges) designed to master Linked Lists, pointer manipulation, and in-memory data structures.

---

## Standard Challenges (Core Mastery)

### Challenge 1: Reverse Linked List in Range ($m$ to $n$)

#### Objective
In transaction audit logs and paginated stream windowing, a subset of records within a range `[left, right]` must be inverted in-place without touching the rest of the chain.

Implement `reverse_between(head: Optional[ListNode], left: int, right: int) -> Optional[ListNode]`.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$ in a single pass.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ auxiliary memory.
3. **Algorithm**:
   - Use a dummy sentinel node to handle the case where `left == 1` (the head is reversed).
   - Traverse to node `left - 1` (`pre`).
   - Sequentially splice each node between `left` and `right` to the front of the sublist.
4. **Test Cases**:
   - `[1, 2, 3, 4, 5]`, `left = 2`, `right = 4` $\to$ `[1, 4, 3, 2, 5]`
   - `[5]`, `left = 1`, `right = 1` $\to$ `[5]`

---

### Challenge 2: Copy List with Random Pointer ($\mathcal{O}(1)$ Auxiliary Space)

#### Objective
In deep graph replication and object state cloning, a linked list where each node contains an additional `random` pointer (which can point to any node in the list or `None`) must be deeply cloned.

Implement `copy_random_list(head: Optional[RandomNode]) -> Optional[RandomNode]`.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ auxiliary space (excluding new cloned nodes). A naive hash map takes $\mathcal{O}(N)$ space; you must use the **Interweaving Node Algorithm**.
3. **Algorithm**:
   - Pass 1: Clone each node and interweave it immediately after its original: `A -> A' -> B -> B' -> C -> C'`.
   - Pass 2: Connect random pointers: `curr.next.random = curr.random.next` (if `curr.random` exists).
   - Pass 3: Decouple original and cloned lists to restore original pointers and return clone head.

---

### Challenge 3: Merge K Sorted Lists via Min-Heap Priority Queue

#### Objective
In distributed database query engines and log aggregators (e.g. merging LSM-tree SSTables in RocksDB or Cassandra), $K$ sorted streams must be merged into a single globally sorted stream.

Implement `merge_k_lists(lists: List[Optional[ListNode]]) -> Optional[ListNode]`.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N \log K)$ where $N$ is total nodes and $K$ is number of lists.
2. **Space Complexity**: $\mathcal{O}(K)$ for the Min-Heap.
3. **Algorithm**:
   - Push the head node of each list into a priority queue: `(node.val, unique_id, node)`.
   - Pop the smallest node, append to result list, and push `popped_node.next` back to heap if non-null.

---

## Milestone Challenges (Week 3 Review)

### Challenge 4: Rotate List by K Positions

#### Objective
Rotate a singly linked list to the right by `k` places.
- Example: `[1, 2, 3, 4, 5]`, `k = 2` $\to$ `[4, 5, 1, 2, 3]`.

#### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$.
2. **Space Complexity**: $\mathcal{O}(1)$.
3. **Algorithm**:
   - Count length $N$ and connect the tail to the head (forming a temporary ring).
   - Compute effective rotations: `effective_k = k % N`.
   - Find the new tail at position `N - effective_k`, break the ring by setting `new_tail.next = None`, and return `new_head`.

---

### Challenge 5: Partition List around Value X

#### Objective
Given head of linked list and value `x`, partition it such that all nodes with values less than `x` come before nodes with values greater than or equal to `x`, preserving original relative order.

#### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$.
2. **Space Complexity**: $\mathcal{O}(1)$.
3. **Algorithm**:
   - Maintain two separate chains: `less_dummy` and `greater_dummy`.
   - Traverse the list: append nodes with `val < x` to `less_tail`, and nodes with `val >= x` to `greater_tail`.
   - Connect `less_tail.next = greater_dummy.next` and `greater_tail.next = None`.

---

### Challenge 6: Odd Even Linked List (Node Parity Grouping)

#### Objective
Group all odd-indexed nodes together followed by even-indexed nodes (referring to node position 1, 2, 3..., not value).
- Example: `[1, 2, 3, 4, 5]` $\to$ `[1, 3, 5, 2, 4]`.

#### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$.
3. **Algorithm**:
   - Maintain `odd` pointer starting at node 1, and `even` starting at node 2.
   - Cache `even_head = head.next`.
   - Advance: `odd.next = even.next`, `odd = odd.next`, `even.next = odd.next`, `even = even.next`.
   - Connect `odd.next = even_head`.

---

### Challenge 7: Intersection of Two Linked Lists

#### Objective
Find the node at which two singly linked lists intersect (share physical memory addresses), or return `None` if they do not intersect.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N + M)$.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ (no hash sets allowed).
3. **Algorithm**:
   - Two pointers `pA = headA` and `pB = headB`.
   - When `pA` reaches end of list A, redirect it to `headB`.
   - When `pB` reaches end of list B, redirect it to `headA`.
   - Both pointers travel exactly $\text{len}(A) + \text{len}(B)$ distance and collide at the intersection node or `None`.

---

### Challenge 8: Swap Nodes in Pairs (Pairwise Reversal)

#### Objective
Given a linked list, swap every two adjacent nodes and return its head.
- Example: `[1, 2, 3, 4]` $\to$ `[2, 1, 4, 3]`.

#### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$.
2. **Space Complexity**: $\mathcal{O}(1)$ iterative.
3. **Algorithm**:
   - Use dummy sentinel node. In each step, swap `first` and `second` using 3 pointer reassignments:
     ```python
     first = prev.next
     second = first.next
     prev.next = second
     first.next = second.next
     second.next = first
     prev = first
     ```

---

### Challenge 9: Reverse Nodes in K-Group

#### Objective
Given the head of a linked list, reverse the nodes of the list `k` at a time, and return the modified list. If the number of nodes is not a multiple of `k`, the left-out nodes at the end should remain as-is.
- Example: `[1, 2, 3, 4, 5]`, `k = 2` $\to$ `[2, 1, 4, 3, 5]`.
- Example: `[1, 2, 3, 4, 5]`, `k = 3` $\to$ `[3, 2, 1, 4, 5]`.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$.
3. **Algorithm**:
   - Pre-check if there are at least `k` nodes remaining.
   - Invert the sub-segment of `k` nodes in-place.
   - Connect previous group's tail to new group head, and update previous pointer.

---

### Challenge 10: Sort List in $\mathcal{O}(N \log N)$ Time and $\mathcal{O}(1)$ Auxiliary Space

#### Objective
Sort a linked list in $\mathcal{O}(N \log N)$ time using constant auxiliary space.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N \log N)$.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ auxiliary (Bottom-Up Iterative Merge Sort). Top-down recursion takes $\mathcal{O}(\log N)$ call-stack space; bottom-up iterative merge sort uses strictly $\mathcal{O}(1)$ space!
3. **Algorithm**:
   - Count total length $N$.
   - For step sizes `1, 2, 4, 8, ... < N`:
     - Split list into sublists of size `step`.
     - Merge pairs of sorted sublists using iterative two-way merge.

---

### Challenge 11: Flatten a Multilevel Doubly Linked List

#### Objective
Given a doubly linked list where in addition to `prev` and `next`, each node may have a `child` pointer to a separate doubly linked list, flatten the list so that all nodes appear in a single-level doubly linked list.

#### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$.
2. **Space Complexity**: $\mathcal{O}(1)$ auxiliary.
3. **Algorithm**:
   - Iterate through list. When a node has a `child`:
     - Find the tail of the child sublist.
     - Splice child sublist between `curr` and `curr.next`.
     - Set `curr.child = None`.

---

### Challenge 12: Design Concurrent Lock-Free Treiber Stack (Atomic CAS Simulation)

#### Objective
Implement an in-memory lock-free LIFO stack that supports concurrent pushes and pops without locking mutexes, using atomic Compare-And-Swap (CAS) semantics.

#### Requirements
1. **Time Complexity**: Amortized $\mathcal{O}(1)$ push and pop under low contention.
2. **Space Complexity**: $\mathcal{O}(N)$ nodes.
3. **Algorithm**:
   - `push(val)`: Create new node with `new_node.next = head`. Attempt CAS to swing `head` to `new_node`. If CAS fails (another thread modified `head`), retry in spin-loop.
   - `pop()`: Read `curr_head = head`. Attempt CAS to swing `head` to `curr_head.next`. Return `curr_head.val`.

---

### Challenge 13: High-Performance LFU (Least Frequently Used) Cache Engine

#### Objective
Design and implement a data structure for a **Least Frequently Used (LFU) cache** where both `get(key)` and `put(key, value)` operate in strict **$\mathcal{O}(1)$ time complexity**.

#### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(1)$ for both `get` and `put`.
2. **Tie-Breaking Rule**: When two or more keys share the minimum access frequency, evict the **Least Recently Used (LRU)** key among them.
3. **Data Structure Architecture**:
   - `key_to_node_map`: Maps `key` to `Node(key, value, freq)`.
   - `freq_to_dlist_map`: Maps `freq: int` to a `DoublyLinkedList` of nodes with that frequency.
   - `min_freq: int`: Tracks the global lowest frequency currently present in the cache.
   - On access: Increment node's frequency, remove from old frequency doubly linked list, add to new frequency list, update `min_freq`.
