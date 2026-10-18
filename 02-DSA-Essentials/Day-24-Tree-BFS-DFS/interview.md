# Day 24: Senior Backend Interview Mastery - Tree BFS & DFS Architectures

Master these 5 senior-level technical interview questions covering memory scaling trade-offs, level-order batch barriers in workflow schedulers, prefix-sum subpath optimizations, radial graph transformations, and constant-space level linking.

---

### Q1: Compare the space complexity and hardware memory profiles of Tree BFS vs. Tree DFS on wide trees vs. deep trees. When is each optimal in microservice systems?

#### Answer:

The choice between BFS and DFS in production systems is rarely an aesthetic choice; it is driven primarily by **spatial memory constraints** and **hardware cache behavior**:

```
BALANCED BINARY TREE (N nodes, Height H = log2 N):
Level 0:       ( 1 )                  <-- 1 node
Level 1:     ( 2 ) ( 3 )              <-- 2 nodes
Level 2:   (4) (5) (6) (7)            <-- 4 nodes
...
Level H: [ N / 2 leaf nodes ]         <-- Max Width = N / 2 nodes!
```

#### 1. Balanced Tree Memory Profiles:
- **BFS (Queue Storage)**:
  - Memory is bounded by the **Maximum Tree Width**.
  - In a balanced binary tree, the last level contains exactly $\frac{N + 1}{2} = \mathcal{O}(N)$ nodes.
  - For $N = 10{,}000{,}000$ nodes, the BFS queue must hold $5{,}000{,}000$ pointers simultaneously in RAM (~40 MB of pointer addresses plus queue metadata).
- **DFS (Stack Storage)**:
  - Memory is bounded by the **Maximum Tree Height**.
  - For a balanced tree, height $H = \log_2(10{,}000{,}000) \approx 24$ frames.
  - The call stack consumes only 24 activation records (< 12 KB).
  - **Verdict on Balanced Trees**: DFS is vastly superior in memory footprint ($\mathcal{O}(\log N)$ vs $\mathcal{O}(N)$).

#### 2. Skewed / Degenerate Tree Memory Profiles:
- When a tree degenerates into a linked list ($H = N$):
  - **BFS**: The queue contains at most **1 node** at any given moment ($\mathcal{O}(1)$ memory!).
  - **DFS**: The call stack grows to $N$ frames, crashing with CPython's `RecursionError` unless rewritten iteratively.
  - **Verdict on Skewed Trees**: BFS operates in constant $\mathcal{O}(1)$ memory, while recursive DFS crashes.

#### Production Architecture Recommendation:
- Use **DFS** when traversing balanced, wide trees (e.g. organizational directories, JSON document trees, ASTs) to conserve memory.
- Use **BFS** when searching for the shortest path, calculating network proximity / blast radius, or when the tree is deep and unbalanced to prevent call stack exhaustion.

---

### Q2: How does the "Level-Order Snapshotting" invariant (`level_size = len(queue)`) guarantee deterministic batch processing in distributed task schedulers? What happens if you iterate `for node in queue:`?

#### Answer:

In distributed task schedulers (e.g. Apache Airflow, Temporal, Celery Canvas), dependencies form a tree or DAG where all tasks at level $L$ must complete before level $L + 1$ can be dispatched.

#### 1. Why `for node in queue:` Fails:
In Python, if you write:
```python
# FAILS AT RUNTIME:
for node in queue:
    queue.append(node.left)  # Raises RuntimeError: deque mutated during iteration!
```
CPython's internal iterator guards against mutating a `collections.deque` during an active `for-in` loop, immediately crashing with `RuntimeError`.

#### 2. The Snapshotting Barrier Invariant:
```python
while queue:
    level_size = len(queue)  # Fixed integer snapshot!
    for _ in range(level_size):
        node = queue.popleft()
        # Enqueue children for level L + 1
```
- At the start of the outer while loop, the queue holds **only** the nodes of level $L$.
- Storing `level_size = len(queue)` creates an immutable execution barrier.
- Exactly `level_size` nodes are popped and processed as a coherent concurrent batch.
- Any children enqueued during this step are appended to the tail and belong strictly to level $L + 1$.
- This guarantees clean temporal separation between dependency layers without requiring secondary temporary lists or explicit timestamps.

---

### Q3: How does the Prefix Sum Hash Map optimization in "Path Sum III" reduce time complexity from $\mathcal{O}(N^2)$ to $\mathcal{O}(N)$? Explain the backtracking requirement.

#### Answer:

The problem asks for the count of all downward paths in a binary tree summing to `target_sum`.

#### 1. Naive Quadratic Approach ($\mathcal{O}(N^2)$):
The brute-force solution runs a secondary DFS from every single node in the tree:
$$\sum_{u \in V} \mathcal{O}(\text{Size of Subtree at } u) = \mathcal{O}(N^2) \text{ on skewed trees}$$
This repeats path sum calculations millions of times across overlapping subtrees.

#### 2. The Linear $\mathcal{O}(N)$ Prefix Sum Optimization:
We adapt the classic array prefix-sum identity to a tree branch:
- Let $S(u)$ be the cumulative sum from the root down to the current node $u$.
- If there exists an ancestor node $a$ on the path from root to $u$ whose cumulative sum is $S(a)$, then the subpath from $a.\text{child}$ down to $u$ has sum:
  $$\text{Subpath Sum} = S(u) - S(a)$$
- We want $\text{Subpath Sum} = \text{target\_sum}$, which rearranges to:
  $$S(a) = S(u) - \text{target\_sum}$$
- By maintaining a hash map `prefix_map: Dict[int, int]` tracking the counts of prefix sums seen so far along the current path, we can query `prefix_map[S(u) - target_sum]` in **strict $\mathcal{O}(1)$ time**!

#### 3. Why Backtracking is Non-Negotiable:
Trees branch into disjoint left and right subtrees:
```
           ( Root: Sum = 10 )
             /             \
    ( Left: Sum = 15 )    ( Right: Sum = 22 )
```
When DFS completes the left subtree and ascends back to Root before entering the right subtree, the prefix sum `15` **does NOT exist on the path to the right subtree**!
If we fail to execute:
```python
prefix_map[curr_sum] -= 1
```
The right branch would inherit the left branch's prefix sums, creating false-positive phantom paths across unrelated sibling branches.

---

### Q4: How is "All Nodes Distance K in Binary Tree" resolved in production, and how do you prevent infinite cycles when augmenting unidirectional trees with parent pointers?

#### Answer:

In standard binary trees, navigation is strictly unidirectional: parents point to children (`left`, `right`), but children have no pointer to their parent. A radial search of distance $K$ requires moving both **downward** into subtrees and **upward** into ancestor branches.

#### 1. The Two-Phase Transformation:
1. **Phase 1: Graph Expansion**:
   Perform a single pass (BFS or DFS) to populate a parent mapping:
   ```python
   parent_map: Dict[TreeNode, Optional[TreeNode]] = {root: None}
   ```
   This effectively transforms the directed binary tree into an unweighted, undirected graph where each node has up to 3 neighbors: `[node.left, node.right, parent_map[node]]`.

2. **Phase 2: Multi-Directional Radial BFS**:
   Initialize a BFS queue starting at `(target_node, distance=0)`.
   Expand radially outward one hop at a time. All nodes dequeued at `distance == K` form the exact answer set.

#### 2. The Infinite Cycle Hazard:
In a normal tree, cycles are impossible. However, adding parent pointers introduces a **2-node undirected cycle** between every parent and child:
$$\text{parent} \longleftrightarrow \text{child}$$
If node `A` pops from the queue and enqueues its child `B`, then in the next iteration node `B` inspects its parent and enqueues `A` again.
Without protection, BFS enters an infinite ping-pong oscillation: $A \to B \to A \to B \dots$

#### Remediation:
Maintain a `visited: Set[TreeNode]` initialized with `{target_node}`. Only enqueue neighbors that are **not** present in `visited`. This preserves strict $\mathcal{O}(N)$ time and prevents infinite loops.

---

### Q5: In "Populating Next Right Pointers" (LeetCode 117), how can level-order traversal be implemented in strictly $\mathcal{O}(1)$ auxiliary space without a FIFO queue?

#### Answer:

Standard level-order traversal uses a FIFO queue, consuming $\mathcal{O}(W) = \mathcal{O}(N)$ memory. We can achieve **strictly $\mathcal{O}(1)$ auxiliary space** by exploiting the pointers we have already established:

```
Level L (Already linked):     [Node A] ─────► [Node B] ─────► None
                                /   \           /   \
Level L + 1 (To be linked):   [C] ──► [D] ──► [E] ──► [F] ──► None
```

#### The Architecture:
1. **The Parent Level is a Linked List**:
   Assume that the current level $L$ has already had its `next` pointers populated. We can traverse level $L$ horizontally just like a singly linked list using `curr = curr.next`!
2. **Sentinel Dummy Node for the Child Level**:
   Create a dummy node `dummy_child = Node(0)` and a tracking pointer `tail = dummy_child`.
3. **Stitching the Child Level**:
   As `curr` traverses horizontally across level $L$:
   - If `curr.left`: `tail.next = curr.left`, `tail = tail.next`.
   - If `curr.right`: `tail.next = curr.right`, `tail = tail.next`.
4. **Advancing to the Next Level**:
   When `curr` reaches `None`, the child level has been completely stitched together!
   Set `curr = dummy_child.next` and reset `dummy_child.next = None`.
5. **Space Complexity**:
   Because the links are formed directly using the node's existing `next` pointer fields and a few local pointer variables, auxiliary space is **strictly $\mathcal{O}(1)$**.
