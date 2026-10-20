# Day 28 Rapid Revision: Topological Sort & Build Systems

A condensed 5-minute reference sheet for topological sorting, DAG invariants, and build pipeline orchestration.

---

## 1. Algorithmic Decision Matrix

| Problem Goal | Optimal Algorithm | Primary Data Structure | Key Invariant |
|---|---|---|---|
| **Standard Dependency Order** | **Kahn's Algorithm** | `collections.deque` + in-degree array | Nodes with `in_degree == 0` have zero unmet prerequisites |
| **AST / Compiler Code Gen** | **DFS Post-Order** | Call stack / 3-color array | Reverse of post-order guarantees dependencies appear first |
| **Reproducible Deterministic Build**| **Kahn's with Min-Heap**| `heapq` priority queue | Min-heap guarantees deterministic tie-breaking among ready tasks |
| **Parallel Execution Waves** | **Kahn's Wavefront** | `len(queue)` level-slicing | All nodes in current wave share zero mutual dependencies |
| **Pipeline Critical Path (CPM)** | **DP over Topo-Sort** | Distance array + topological scan | Longest path in DAG is computable in linear $O(V + E)$ time |
| **Cycle Extraction Diagnostic** | **Residual 3-Color DFS**| Call stack on residual nodes | Nodes with `in_degree > 0` contain all circular loops |

---

## 2. Core Reusable Code Templates

### Template 1: Kahn's Algorithm (BFS In-Degree Peeling)
```python
from collections import defaultdict, deque

def kahn_topological_sort(num_nodes: int, edges: list[tuple[int, int]]) -> list[int] | None:
    adj = defaultdict(list)
    in_degree = [0] * num_nodes

    for u, v in edges:  # Directed edge: u -> v (u precedes v)
        adj[u].append(v)
        in_degree[v] += 1

    queue = deque([i for i in range(num_nodes) if in_degree[i] == 0])
    order = []

    while queue:
        curr = queue.popleft()
        order.append(curr)
        for nxt in adj[curr]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    # Invariant: Cycle exists if length is less than total nodes
    return order if len(order) == num_nodes else None
```

### Template 2: Parallel Wavefront Slicing
```python
def parallel_execution_waves(num_nodes: int, edges: list[tuple[int, int]]) -> list[list[int]] | None:
    adj = defaultdict(list)
    in_degree = [0] * num_nodes
    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    queue = deque([i for i in range(num_nodes) if in_degree[i] == 0])
    stages = []
    processed = 0

    while queue:
        wave_size = len(queue)
        current_wave = []
        for _ in range(wave_size):
            curr = queue.popleft()
            current_wave.append(curr)
            processed += 1
            for nxt in adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        stages.append(current_wave)

    return stages if processed == num_nodes else None
```

### Template 3: Critical Path Method (CPM)
```python
def critical_path_duration(num_nodes: int, edges: list[tuple[int, int]], duration: list[float]) -> float:
    # Requires a valid topological order
    topo_order = kahn_topological_sort(num_nodes, edges)
    if topo_order is None:
        raise ValueError("Cannot compute critical path on cyclic graph!")

    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)

    # dist[v] = maximum cumulative time to finish node v
    dist = list(duration)

    for u in topo_order:
        for v in adj[u]:
            cand = dist[u] + duration[v]
            if cand > dist[v]:
                dist[v] = cand

    return max(dist)
```

---

## 3. Non-Negotiable Invariants & Rules

1. **The DAG Axiom**: A graph has a topological ordering **if and only if** it is a Directed Acyclic Graph (zero cycles).
2. **Edge Direction Rule**: Directed edge $A \to B$ means **$A$ is a prerequisite for $B$**. Never invert this into "B depends on A" without reversing the pointer direction!
3. **Cycle Invariant in Kahn's**: Always check `len(order) == num_nodes`. Never return an incomplete list without raising an error.
4. **Wavefront Concurrency Invariant**: All nodes residing in the queue at the start of an iteration have `in_degree == 0` and can be dispatched across worker threads in parallel.
5. **Deterministic Builds**: When deterministic output order is required across cluster nodes, use a `heapq` (Min-Heap) instead of `collections.deque`.

---

## 4. Complexity Quick Reference Table

| Operation | Kahn's Algorithm | DFS Post-Order | Kahn's with Min-Heap |
|---|---|---|---|
| **Topological Sort** | $O(V + E)$ | $O(V + E)$ | $O((V + E) \log V)$ |
| **Cycle Detection** | $O(1)$ check at end | $O(V + E)$ (Back edge) | $O(1)$ check at end |
| **Parallel Stage Batching**| $O(V + E)$ | Complex post-pass | $O((V + E) \log V)$ |
| **Critical Path Method** | $O(V + E)$ | $O(V + E)$ | $O((V + E) \log V)$ |
| **Auxiliary Memory** | $O(V)$ in-degree array | $O(V)$ call stack | $O(V)$ min-heap |
