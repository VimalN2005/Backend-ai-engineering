# Day 24: Tree Breadth-First Search (Level Order) & Depth-First Search (Path Finding, Views, Serialization)

In backend engineering and distributed systems, trees and Directed Acyclic Graphs (DAGs) represent **hierarchical execution workflows**, **organizational permissions**, **task dependencies**, and **nested network topologies**.

To inspect, validate, and execute these structures, software engineers rely on two fundamental search paradigms:
- **Breadth-First Search (BFS / Level Order)**: Explores nodes layer-by-layer horizontally. Used in parallel job scheduling (Airflow / Celery), blast-radius isolation, and shortest-path radial searches.
- **Depth-First Search (DFS / Path Finding)**: Explores nodes vertically down to leaves before backtracking. Used in authorization path validation, prefix-sum subpath queries, and serialization.

Understanding when to choose BFS vs. DFS and how their respective memory profiles scale on wide vs. deep trees is essential for building production-grade infrastructure.

---

## 1. BFS vs. DFS: Architectural Paradigms

```
BREADTH-FIRST SEARCH (Level Order):             DEPTH-FIRST SEARCH (Vertical Paths):
Explores horizontally layer-by-layer.           Explores vertically to the leaf first.

           Level 0:     ( 1 )                                   ( 1 )
                       /     \                                 /     \
           Level 1:  ( 2 )   ( 3 )                           ( 2 )   ( 5 )
                     /   \     \                             /   \     \
           Level 2:( 4 ) ( 5 ) ( 6 )                       ( 3 ) ( 4 ) ( 6 )

Sequence: 1 -> 2, 3 -> 4, 5, 6                  Sequence: 1 -> 2 -> 3 -> 4 -> 5 -> 6
Data Structure: FIFO Queue (collections.deque)  Data Structure: LIFO Call Stack / Heap Stack
Memory Bound: Max Tree Width (O(N) at base)     Memory Bound: Max Tree Height (O(H) = O(log N))
```

---

## 2. Memory Footprint Comparison: Width vs. Height

The choice between BFS and DFS is often dictated by tree topology and hardware memory constraints:

| Metric | Breadth-First Search (BFS) | Depth-First Search (DFS) |
| :--- | :--- | :--- |
| **Data Structure** | FIFO Queue (`collections.deque`) | LIFO Stack (Call Stack or `list`) |
| **Memory Bound** | $\mathcal{O}(\text{Max Width})$ | $\mathcal{O}(\text{Max Height})$ |
| **Balanced Tree ($N$ nodes)**| $\mathcal{O}(N)$ (Bottom level has $N / 2$ nodes) | $\mathcal{O}(\log_2 N)$ (Height is $\log N$) |
| **Skewed Tree ($N$ nodes)**| $\mathcal{O}(1)$ (Width is 1) | $\mathcal{O}(N)$ (Height is $N$) |
| **Best Used For** | Shortest paths, level batching, radial distance | Full path validation, subtree aggregations, serialization |

---

## 3. The Level-Order Batch Processing Invariant

To process nodes level-by-level in distinct batches (e.g. for concurrent execution or level-averaged metrics), we take a snapshot of the queue length:

```python
from collections import deque

def level_order(root: Optional[TreeNode]) -> list[list[int]]:
    if not root:
        return []

    result = []
    queue = deque([root])

    while queue:
        level_size = len(queue)  # CRITICAL INVARIANT: Snapshot queue length!
        current_level = []

        for _ in range(level_size):
            node = queue.popleft()  # Strict O(1) FIFO pop
            current_level.append(node.val)

            if node.left:
                queue.append(node.left)
            if node.right:
                queue.append(node.right)

        result.append(current_level)

    return result
```

### Why Snapshotting `len(queue)` Works:
At the start of the outer while loop, the queue contains **exclusively** the nodes of the current level $L$. Iterating exactly `level_size` times drains level $L$ while pushing children belonging strictly to level $L + 1$.

---

## 4. Zigzag (Spiral) Level Order Traversal

In distributed renderers, hierarchical UI drawers, and alternating schedule dispatchers, nodes are traversed in alternating left-to-right and right-to-left order.

```python
def zigzag_level_order(root: Optional[TreeNode]) -> list[list[int]]:
    if not root:
        return []
    result = []
    queue = deque([root])
    left_to_right = True

    while queue:
        level_size = len(queue)
        level_nodes = deque()

        for _ in range(level_size):
            node = queue.popleft()
            if left_to_right:
                level_nodes.append(node.val)      # Normal append to tail
            else:
                level_nodes.appendleft(node.val)  # Prepend to head for reversed view

            if node.left:
                queue.append(node.left)
            if node.right:
                queue.append(node.right)

        result.append(list(level_nodes))
        left_to_right = not left_to_right  # Flip direction flag

    return result
```

---

## 5. Tree Views: Right Side View & Vertical Coordinates

### Right Side View (What an observer sees from the right):
1. **BFS Approach**: The last element of each level's batch snapshot is the rightmost visible node.
2. **DFS Approach**: Prioritize right child first (`Root -> Right -> Left`). The first node visited at each depth level is added to the result.

```python
# DFS Right Side View: O(N) Time, O(H) Space
def right_side_view_dfs(root: Optional[TreeNode]) -> list[int]:
    result = []
    def dfs(node: Optional[TreeNode], depth: int) -> None:
        if not node:
            return
        # If this is the first node visited at this depth
        if depth == len(result):
            result.append(node.val)
        dfs(node.right, depth + 1)  # Visit right first!
        dfs(node.left, depth + 1)
    dfs(root, 0)
    return result
```

### Vertical Order Traversal (Column-Based Indexing):
Map each node to a 2D coordinate `(col, row)`:
- Root is at `(0, 0)`.
- Left child is at `(col - 1, row + 1)`.
- Right child is at `(col + 1, row + 1)`.
Using BFS with a hash table `defaultdict(list)` groups nodes by column index `col`.

---

## 6. Path Finding with DFS: Prefix Sums in Trees (Path Sum III)

Finding the number of paths in a binary tree that sum to `target_sum` (where paths do not need to start at the root or end at a leaf) can be solved naively in $\mathcal{O}(N^2)$ by testing all subpaths.

### The Linear $\mathcal{O}(N)$ Prefix Sum Optimization:
We adapt the **Two-Sum Prefix Sum Hash Map** pattern from arrays (Day 17/18) to tree DFS:

```
At node u with current running sum S:
We need past ancestor nodes whose prefix sum was: P = S - target_sum
The number of valid subpaths ending at u is: prefix_counts[S - target_sum]
```

```python
def path_sum_iii(root: Optional[TreeNode], target_sum: int) -> int:
    prefix_map = {0: 1}  # Base case: empty path sum
    total_paths = 0

    def dfs(node: Optional[TreeNode], curr_sum: int) -> None:
        nonlocal total_paths
        if not node:
            return

        curr_sum += node.val

        # Check if an ancestor prefix sum exists such that curr_sum - ancestor_sum == target_sum
        total_paths += prefix_map.get(curr_sum - target_sum, 0)

        # Register current prefix sum
        prefix_map[curr_sum] = prefix_map.get(curr_sum, 0) + 1

        # Recurse down children
        dfs(node.left, curr_sum)
        dfs(node.right, curr_sum)

        # BACKTRACKING INVARIANT: Remove current sum before returning up the call stack!
        prefix_map[curr_sum] -= 1

    dfs(root, 0)
    return total_paths
```

---

## 7. Radial Distance Queries: All Nodes Distance K in Binary Tree

To find all nodes at distance $K$ from a given `target` node:
1. **Graph Conversion**: A binary tree only has downward pointers (`left`, `right`). We traverse the tree once (via BFS or DFS) to build a `parent` mapping `{node: parent_node}`.
2. **Multi-Source Radial BFS**:
   - Start a BFS initialized with the `target` node at distance 0.
   - For each node, explore 3 directions: `left`, `right`, and `parent`.
   - Maintain a `visited` set to avoid revisiting nodes and prevent infinite oscillation.
   - Stop when distance reaches $K$.

---

## 8. Serialization Protocols: BFS Level-Order vs. DFS Preorder

In distributed systems, tree structures must be transmitted over the wire:

### BFS Level-Order Serialization (Compact Array):
- Serializes nodes level by level, recording `null` for missing children.
- Example: `[1, 2, 3, null, null, 4, 5]`.
- Reconstructs using a FIFO queue, attaching children to parent nodes in sequence.

### DFS Preorder Serialization (Delimiter String):
- Serializes using `val,left_subtree,right_subtree`.
- Example: `1,2,#,#,3,4,#,#,5,#,#`.
- Reconstructs using an iterator/queue in linear time without index calculation.

---

## 9. CPython `collections.deque` vs. `list` in Production BFS

In CPython:
- `list.pop(0)` invokes C's `memmove()` syscall, shifting $N - 1$ pointers leftward ($\mathcal{O}(N)$).
- A queue processing 100,000 nodes using `list.pop(0)` executes $\frac{100{,}000^2}{2} = 5{,}000{,}000{,}000$ pointer shifts, freezing the process for seconds.
- `collections.deque.popleft()` simply advances a pointer within a 64-element memory block in **strictly $\mathcal{O}(1)$ time**.

---

## 10. Production Architecture: Distributed Task Orchestration DAG

In modern workflow engines (Apache Airflow, Temporal, AWS Step Functions):
- Tasks are arranged as a tree or DAG of dependent jobs.
- **Level-Order BFS Scheduling**:
  1. All independent jobs at level $L$ are dispatched concurrently across a Kubernetes worker pool.
  2. The orchestrator waits for level $L$ completion before advancing to level $L + 1$.
  3. If any job at level $L$ fails, downstream dependent subtrees are safely quarantined.

---

## 11. Boundary Traversal of Binary Tree

The boundary of a binary tree is traversed in counter-clockwise order:
1. **Left Boundary**: From root downwards along left edges (excluding leaves).
2. **Leaf Nodes**: All leaves from left to right (using DFS).
3. **Right Boundary**: From bottom leaves up to root along right edges (excluding leaves and root), collected bottom-up using a stack.

---

## 12. 7 Golden Invariant Rules for Tree BFS & DFS Engineering

1. **Snapshot Queue Length**: Always capture `level_size = len(queue)` before iterating to cleanly separate levels.
2. **Never Use `list.pop(0)`**: Always use `collections.deque.popleft()` for $\mathcal{O}(1)$ FIFO queue performance.
3. **Backtrack Prefix Sum Maps**: In subpath DFS, always decrement `prefix_map[curr_sum] -= 1` when unwinding the stack to prevent leaking state to sibling branches.
4. **Track Visited in Bidirectional Trees**: When adding parent pointers for radial distance searches, always track `visited` to prevent infinite two-node loops.
5. **Guard Double Dereferencing**: Check `if node.left:` before enqueuing to prevent filling the queue with `None`.
6. **Choose Traversal by Topology**: Use DFS on wide trees to save memory; use BFS on deep trees to avoid recursion limits.
7. **Use `__slots__` for Scalability**: Eliminate instance dictionaries to save 68% RAM on millions of tree nodes.

---

## 13. Complexity Reference Matrix

| Algorithm | Time Complexity | Auxiliary Space (Balanced) | Auxiliary Space (Skewed) | Primary Data Structure |
| :--- | :--- | :--- | :--- | :--- |
| **Level-Order BFS** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ (Max width) | $\mathcal{O}(1)$ | `collections.deque` |
| **Zigzag Level-Order** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Dual deques |
| **Right Side View (BFS)** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | Queue + Level snapshot |
| **Right Side View (DFS)** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Call stack (`Root -> Right -> Left`) |
| **Path Sum III (Prefix)** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Hash Map + Backtracking DFS |
| **Nodes Distance K** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Parent Map + Radial BFS |
| **Level-Order Serialize** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Queue |
| **Boundary Traversal** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Left DFS + Leaf DFS + Right Stack |
