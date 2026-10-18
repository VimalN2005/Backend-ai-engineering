# Day 24: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Tree Breadth-First Search (BFS), Depth-First Search (DFS), Prefix Subpaths, and Radial Graph Conversions for backend engineers.

---

## 1. BFS vs. DFS Architectural Decision Matrix

| Dimension | Breadth-First Search (BFS) | Depth-First Search (DFS) |
| :--- | :--- | :--- |
| **Data Structure** | FIFO Queue (`collections.deque`) | LIFO Stack (Call Stack or explicit list) |
| **Exploration Direction**| Horizontal layer-by-layer | Vertical root-to-leaf paths |
| **Memory Bound** | $\mathcal{O}(\text{Max Width}) = \mathcal{O}(N)$ on balanced trees | $\mathcal{O}(\text{Max Height}) = \mathcal{O}(\log N)$ on balanced trees |
| **Skewed Tree Memory** | $\mathcal{O}(1)$ (Queue holds 1 node) | $\mathcal{O}(N)$ (Stack holds $N$ frames) |
| **Optimal Use Cases** | Shortest path, level batching, blast-radius queries | Backtracking paths, subtree aggregations, serialization |

---

## 2. Essential Production Code Templates

### Template A: Canonical Level-Order Batching ($\mathcal{O}(N)$ Time, $\mathcal{O}(W)$ Space)
```python
from collections import deque

def level_order(root: Optional[TreeNode]) -> list[list[int]]:
    if not root:
        return []
    result = []
    queue = deque([root])

    while queue:
        level_size = len(queue)  # CRITICAL INVARIANT: Snapshot queue length!
        level = []
        for _ in range(level_size):
            node = queue.popleft()  # Strict O(1)
            level.append(node.val)
            if node.left: queue.append(node.left)
            if node.right: queue.append(node.right)
        result.append(level)

    return result
```

### Template B: Path Sum III with Prefix Sum Backtracking ($\mathcal{O}(N)$ Time, $\mathcal{O}(H)$ Space)
```python
from collections import defaultdict

def path_sum_iii(root: Optional[TreeNode], target: int) -> int:
    prefix = defaultdict(int)
    prefix[0] = 1
    total = 0

    def dfs(node: Optional[TreeNode], curr: int) -> None:
        nonlocal total
        if not node:
            return
        curr += node.val
        total += prefix[curr - target]  # Ancestor match in O(1)
        prefix[curr] += 1

        dfs(node.left, curr)
        dfs(node.right, curr)

        prefix[curr] -= 1  # BACKTRACKING INVARIANT!

    dfs(root, 0)
    return total
```

### Template C: Radial Multi-Directional BFS (Nodes Distance K)
```python
def distance_k(root: Optional[TreeNode], target_node: TreeNode, k: int) -> list[int]:
    # 1. Map parents
    parent = {root: None}
    q = deque([root])
    while q:
        curr = q.popleft()
        if curr.left: parent[curr.left] = curr; q.append(curr.left)
        if curr.right: parent[curr.right] = curr; q.append(curr.right)

    # 2. Radial BFS with visited set
    bfs_q = deque([(target_node, 0)])
    visited = {target_node}
    res = []
    while bfs_q:
        node, d = bfs_q.popleft()
        if d == k:
            res.append(node.val)
            continue
        for neighbor in (node.left, node.right, parent.get(node)):
            if neighbor and neighbor not in visited:
                visited.add(neighbor)
                bfs_q.append((neighbor, d + 1))
    return res
```

---

## 3. 5 Non-Negotiable Engineering Rules

1. **Always Snapshot `level_size = len(queue)`**: Never iterate directly over a mutating queue in Python.
2. **Never Use `list.pop(0)`**: Always use `collections.deque.popleft()` to avoid severe $\mathcal{O}(N)$ pointer memory shifts.
3. **Always Backtrack Prefix Sum Maps**: Decrement `prefix[curr] -= 1` when unwinding the call stack to prevent state leakage to sibling branches.
4. **Always Track `visited` in Bidirectional Trees**: Adding parent pointers introduces 2-node cycles; without a `visited` set, BFS loops infinitely.
5. **Guard Children Enqueues**: Check `if node.left:` before enqueuing to keep the queue free of `None` pointers.

---

## 4. Complexity Quick Reference Table

| Algorithm | Time Complexity | Auxiliary Space (Balanced) | Auxiliary Space (Skewed) |
| :--- | :--- | :--- | :--- |
| **Level-Order BFS** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ (Queue width) | $\mathcal{O}(1)$ |
| **Zigzag Level-Order** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ |
| **Right Side View** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ (DFS) | $\mathcal{O}(N)$ |
| **Path Sum III (Prefix)** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ |
| **All Nodes Distance K** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ (Parent map) | $\mathcal{O}(N)$ |
| **Populate Next Pointers** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| **Vertical Order Traversal** | $\mathcal{O}(N \log N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
