# Day 27 Rapid Revision: Graph Fundamentals & Traversals

A condensed 5-minute reference sheet for graph representations, traversal invariants, and cycle detection mechanics.

---

## 1. Algorithmic Decision Matrix

| Problem Goal | Optimal Traversal | Primary Data Structure | Key Invariant |
|---|---|---|---|
| **Shortest Path (Unweighted)** | **BFS** | `collections.deque` + `parent` dict | Explores in non-decreasing hop count |
| **Connected Components** | **BFS or DFS** | `visited` set + outer loop over all nodes | Traversal visits all reachable nodes in component |
| **Cycle in Undirected Graph** | **DFS** | Recursion or stack + `parent` pointer | `v in visited and v != parent` implies cycle |
| **Cycle in Directed Graph** | **DFS (3-Color)**| Recursion / stack + `color` dict (0, 1, 2) | Edge pointing to `GRAY` node is a Back Edge |
| **Deep Graph Search** | **Iterative DFS** | Explicit `stack = [root]` | Prevents Python `RecursionError` |
| **Level-by-Level Metrics** | **BFS** | `deque([(root, 0)])` or inner `len(queue)` | Concentric wavefront expansion |
| **Bipartite Verification** | **BFS (2-Color)** | `color` map (`0` and `1`) | Bipartite iff graph has zero odd-length cycles |

---

## 2. Core Reusable Code Templates

### Template 1: BFS Shortest Path with Predecessor Reconstruction
```python
from collections import deque

def bfs_shortest_path(adj: dict[str, list[str]], start: str, target: str) -> list[str] | None:
    if start == target:
        return [start]
    visited = {start}
    queue = deque([start])
    parent = {start: None}

    while queue:
        curr = queue.popleft()
        if curr == target:
            # Reconstruct path backwards
            path = []
            step = target
            while step is not None:
                path.append(step)
                step = parent[step]
            return path[::-1]

        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)  # INVARIANT: Mark visited immediately upon enqueue!
                parent[neighbor] = curr
                queue.append(neighbor)
    return None
```

### Template 2: Safe Iterative DFS (C-Stack Overflow Proof)
```python
def dfs_iterative(adj: dict[str, list[str]], start: str) -> list[str]:
    visited = set()
    order = []
    stack = [start]

    while stack:
        curr = stack.pop()
        if curr in visited:
            continue
        visited.add(curr)
        order.append(curr)

        for neighbor in reversed(adj.get(curr, [])):
            if neighbor not in visited:
                stack.append(neighbor)
    return order
```

### Template 3: Directed Cycle Detection (Three-Color State Marking)
```python
def has_cycle_directed(all_nodes: list[str], adj: dict[str, list[str]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in all_nodes}

    def dfs(u: str) -> bool:
        color[u] = GRAY  # In active stack (ancestor)
        for v in adj.get(u, []):
            if color[v] == GRAY:
                return True  # Back edge found -> Cycle!
            if color[v] == WHITE:
                if dfs(v):
                    return True
        color[u] = BLACK  # Exiting call frame
        return False

    for node in all_nodes:
        if color[node] == WHITE:
            if dfs(node):
                return True
    return False
```

### Template 4: Undirected Cycle Detection (Parent Pointer)
```python
def has_cycle_undirected(all_nodes: list[str], adj: dict[str, list[str]]) -> bool:
    visited = set()

    def dfs(u: str, parent: str | None) -> bool:
        visited.add(u)
        for v in adj.get(u, []):
            if v not in visited:
                if dfs(v, u):
                    return True
            elif v != parent:
                return True  # Non-parent visited neighbor -> Cycle!
        return False

    for node in all_nodes:
        if node not in visited:
            if dfs(node, None):
                return True
    return False
```

---

## 3. Non-Negotiable Invariants & Rules

1. **Mark Visited Upon Enqueue**: Never wait until `queue.popleft()` to add a node to `visited`. In dense grids or graphs, this causes exponential duplicate enqueues and memory exhaustion.
2. **Always Use `collections.deque`**: `list.pop(0)` is $O(N)$ because Python shifts all remaining array pointers. `deque.popleft()` is $O(1)$.
3. **Directed Graphs Require 3 States**: Simple visited sets cause false positive cycles on diamond dependencies ($A \to B \to D$ and $A \to C \to D$). Only back edges to nodes actively in the call stack (`GRAY`) denote cycles.
4. **Iterate All Disconnected Components**: Real-world service meshes, networks, and graphs are often disconnected. Wrap traversals in an outer loop iterating through `all_nodes`.
5. **Never Store Full Paths in Queue**: Enqueueing tuples `(node, path_list)` consumes $O(V^2)$ memory copying lists. Store scalar `parent[child] = curr` pointers and backtrack once.

---

## 4. Complexity Quick Reference Table

| Operation | Adjacency List Time | Adjacency Matrix Time | Space Complexity |
|---|---|---|---|
| **BFS Traversal** | $O(V + E)$ | $O(V^2)$ | $O(V)$ (queue + visited) |
| **DFS Traversal** | $O(V + E)$ | $O(V^2)$ | $O(V)$ (stack + visited) |
| **Shortest Path (Unweighted)** | $O(V + E)$ | $O(V^2)$ | $O(V)$ (parent map) |
| **Directed Cycle Detection** | $O(V + E)$ | $O(V^2)$ | $O(V)$ (color map) |
| **Undirected Cycle Detection**| $O(V + E)$ | $O(V^2)$ | $O(V)$ (visited set) |
| **Connected Components** | $O(V + E)$ | $O(V^2)$ | $O(V)$ |
| **Bipartite Check (2-Color)** | $O(V + E)$ | $O(V^2)$ | $O(V)$ (color map) |
