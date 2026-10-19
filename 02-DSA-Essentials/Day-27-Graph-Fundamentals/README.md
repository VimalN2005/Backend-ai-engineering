# Day 27: Graph Fundamentals — Adjacency List, BFS & DFS Traversals

A graph $G = (V, E)$ is the most expressive non-linear data structure in computer science. While arrays, trees, and linked lists model strict linear or hierarchical relationships, graphs model arbitrary networks, cyclic dependencies, and many-to-many topologies.

In backend and AI engineering, graphs power distributed service meshes (Istio/Envoy), CI/CD build execution engines (Bazel, Make), workflow orchestrators (Apache Airflow DAGs, Temporal), database query planners, distributed tracing blast-radius analyzers, neural network computation graphs (PyTorch `torch.fx`), and enterprise knowledge graphs.

---

## 1. Graphs in Modern Backend & AI Systems

```
+-----------------------------------------------------------------------------------+
|                        ENTERPRISE GRAPH TOPOLOGIES                                |
+-----------------------------------------------------------------------------------+
| 1. Microservice Service Mesh: Services calling services (traffic flow, circuits)  |
| 2. CI/CD Build Dependency Graph: Modules needing compilation (Bazel / Cargo)      |
| 3. Data Lineage & Orchestration: Airflow DAGs, dbt models, Kafka streaming pipes   |
| 4. Distributed Tracing: Jaeger / OpenTelemetry span dependency causality graphs   |
| 5. Neural Network Architecture: PyTorch computational graph (forward / backward)  |
| 6. Social & Fraud Detection: User-account-device money transfer clusters (Neo4j)  |
+-----------------------------------------------------------------------------------+
```

1. **Microservice Service Meshes (Istio, Linkerd)**: Services are vertices; RPC/gRPC calls are directed edges. Traversal algorithms compute cascading outage blast radii and detect circular RPC deadlocks.
2. **Data Pipeline DAGs (Apache Airflow, Prefect, dbt)**: Tasks are vertices; execution dependencies are directed acyclic edges. Cycle detection is mandatory during DAG parsing to prevent infinite scheduling stalls.
3. **AI Computation Graphs (PyTorch, TensorFlow, TensorRT)**: Tensors and operators form directed graphs where backward automatic differentiation traverses the reverse graph using topological sort and DFS.
4. **Security & IAM Permission Trees**: Roles, users, and resources form authorization graphs where reachability analysis validates zero-trust access boundaries.

---

## 2. Graph Taxonomy & Formal Definitions

A graph consists of a set of vertices (nodes) $V$ and a set of edges (connections) $E$:
$$G = (V, E), \quad |V| = \text{number of vertices}, \quad |E| = \text{number of edges}$$

```
+----------------------+------------------------------------------------------------+
| Taxonomy             | Key Invariant & Architectural Meaning                      |
+----------------------+------------------------------------------------------------+
| Directed (Digraph)   | Edges have direction: (u, v) != (v, u). E.g., RPC caller.  |
| Undirected           | Edges are bidirectional: (u, v) == (v, u). E.g., Network.  |
| Weighted             | Edges carry cost/latency/bandwidth scalar weights.         |
| Unweighted           | All edges have uniform cost (BFS finds shortest path).     |
| Cyclic               | Contains at least one path starting and ending at node u.  |
| Acyclic (DAG)        | Directed graph with zero cycles (schedulable workflows).   |
| Dense                | |E| approaches |V|^2 (almost all node pairs connected).    |
| Sparse               | |E| << |V|^2 (typically |E| = O(|V|); 99% of backend nets).|
+----------------------+------------------------------------------------------------+
```

---

## 3. Graph Representation Architectures

How a graph is stored in memory dictates the algorithmic time and space complexity of every traversal.

```
       (0) -------- (1)
        |          / |
        |        /   |
        |      /     |
        |    /       |
       (2) -------- (3)

1. Adjacency Matrix:
       0  1  2  3
    0 [0, 1, 1, 0]
    1 [1, 0, 1, 1]
    2 [1, 1, 0, 1]
    3 [0, 1, 1, 0]

2. Adjacency List:
    0: [1, 2]
    1: [0, 2, 3]
    2: [0, 1, 3]
    3: [1, 2]
```

### Representation Comparison

```
+-----------------------------------+--------------------+--------------------+--------------------+
| Dimension                         | Adjacency Matrix   | Adjacency List     | CSR (Compressed)   |
+-----------------------------------+--------------------+--------------------+--------------------+
| Space Complexity                  | O(|V|^2)           | O(|V| + |E|)       | O(|V| + |E|)       |
| Check Edge (u, v)                 | O(1)               | O(deg(u))          | O(log(deg(u)))     |
| Iterate Neighbors of u            | O(|V|)             | O(deg(u))          | O(deg(u))          |
| Add Edge                          | O(1)               | O(1)               | O(|E|) (rebuild)   |
| Add Vertex                        | O(|V|^2) (resize)  | O(1)               | O(|V| + |E|)       |
| Hardware Cache Locality           | High (flat array)  | Low (pointers/obj) | Maximum (compact)  |
| Production Fit                    | Small/Dense graphs | 99% Backend APIs   | High-perf GNNs/DBs |
+-----------------------------------+--------------------+--------------------+--------------------+
```

### Why Adjacency List is the Production Backend Standard
Real-world systems are overwhelmingly **sparse**:
- A social network with $10^8$ users ($|V| = 10^8$) where each user has 200 friends ($|E| \approx 2 \times 10^{10}$).
- Adjacency Matrix would require $(10^8)^2 \times 1 \text{ byte} = 10^{16} \text{ bytes} = 10 \text{ Petabytes}$ of RAM!
- Adjacency List requires only $O(|V| + |E|) \approx 200 \times 10^8 \text{ entries} \approx \text{few Gigabytes}$.

---

## 4. Graph Construction in Python

```python
from collections import defaultdict
from typing import Dict, List, Set

class Graph:
    """
    Production-grade Directed/Undirected Adjacency List Graph.
    Uses defaultdict(list) for dynamic sparse node registration.
    """
    def __init__(self, directed: bool = False):
        self.directed = directed
        self.adj: Dict[str, List[str]] = defaultdict(list)
        self.nodes: Set[str] = set()

    def add_node(self, u: str) -> None:
        self.nodes.add(u)
        if u not in self.adj:
            self.adj[u] = []

    def add_edge(self, u: str, v: str) -> None:
        self.add_node(u)
        self.add_node(v)
        self.adj[u].append(v)
        if not self.directed:
            self.adj[v].append(u)

    def neighbors(self, u: str) -> List[str]:
        return self.adj.get(u, [])
```

---

## 5. Breadth-First Search (BFS) & Shortest Path Invariant

BFS explores the graph in concentric wavefronts (levels) away from the source vertex.

```
       Level 0:           [ Source ]
                             |
       Level 1:      +-------+-------+
                     |               |
                  [ A ]           [ B ]
                     |               |
       Level 2:   +--+--+         +--+--+
                  |     |         |     |
                [ C ] [ D ]     [ E ] [ F ]
```

### The Unweighted Shortest Path Theorem
In an unweighted graph (or a graph where all edges have identical positive weight), **Breadth-First Search is guaranteed to discover every reachable vertex $v$ along the shortest possible path (minimum edge count) from source $s$.**

#### Formal Proof Invariant:
1. When node $u$ is at distance $d(s, u) = k$, all of its unvisited neighbors $v$ are at distance $k + 1$.
2. Because a FIFO queue processes nodes in strictly non-decreasing order of level ($k$ before $k + 1$), a node is popped at level $k$ if and only if no path of length $< k$ exists to that node.
3. Therefore, the first time $v$ is added to the `visited` set during BFS, its distance from $s$ is minimal.

```python
from collections import deque
from typing import Dict, List, Optional

def bfs_shortest_path(adj: Dict[str, List[str]], start: str, target: str) -> Optional[List[str]]:
    """Computes minimum-hop shortest path using BFS with predecessor tracking."""
    if start == target:
        return [start]

    visited = {start}
    queue = deque([start])
    parent: Dict[str, Optional[str]] = {start: None}

    while queue:
        curr = queue.popleft()
        if curr == target:
            # Reconstruct path by walking backwards from target
            path = []
            step: Optional[str] = target
            while step is not None:
                path.append(step)
                step = parent[step]
            return path[::-1]

        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                parent[neighbor] = curr
                queue.append(neighbor)

    return None  # Target unreachable
```

---

## 6. Depth-First Search (DFS) & Edge Classification

DFS dives deep along each branch before backtracking. It can be implemented recursively or iteratively using an explicit LIFO stack.

### Edge Classification in DFS Trees
During DFS traversal of a directed graph, every edge $(u, v)$ falls into one of four categories:
1. **Tree Edge**: Explores an unvisited vertex $v$. Part of the DFS forest.
2. **Back Edge**: Points from $u$ to an ancestor vertex $v$ currently in the active recursion call stack. **A back edge indicates a CYCLE!**
3. **Forward Edge**: Points from $u$ to a descendant $v$ that has already been fully explored.
4. **Cross Edge**: Points from $u$ to a vertex $v$ that is neither an ancestor nor a descendant (in a previously explored subtree).

```
          (Root)
          /    \
  Tree   /      \ Tree
        v        v
       (A)      (B)
        |        |
  Tree  |        |
        v        |
       (C) ---->+  (Cross Edge)
        |
        +-----> (Root)  (Back Edge -> CYCLE DETECTED!)
```

---

## 7. Cycle Detection: Undirected vs Directed Graphs

The mechanics of cycle detection differ fundamentally between directed and undirected graphs:

### Undirected Graph Cycle Detection (Parent Pointer Technique)
In an undirected graph, edge $(u, v)$ implies edge $(v, u)$. Traversing back to the immediate parent is NOT a cycle.
- A cycle exists if and only if we encounter an already-visited neighbor $v$ such that $v \ne \text{parent}(u)$.

```python
def has_cycle_undirected_dfs(adj: Dict[str, List[str]], start: str) -> bool:
    visited = set()

    def dfs(u: str, parent: Optional[str]) -> bool:
        visited.add(u)
        for v in adj.get(u, []):
            if v not in visited:
                if dfs(v, u):
                    return True
            elif v != parent:
                # Encountered visited node that is not our parent -> CYCLE
                return True
        return False

    return dfs(start, None)
```

### Directed Graph Cycle Detection (Three-Color State Marking)
In a directed graph, a node may be visited multiple times across different branches without a cycle (e.g., diamond dependency: $A \to B \to D$ and $A \to C \to D$).
We must track nodes currently in the **active recursion stack**.

```
State 0 (WHITE): Node is unvisited.
State 1 (GRAY) : Node is currently in the active recursion stack (processing ancestors).
State 2 (BLACK): Node and all its descendants are completely processed and verified.
```

**Theorem**: A directed graph contains a cycle if and only if DFS encounters an edge $(u, v)$ where $v$ is **GRAY** (Back Edge).

```python
def has_cycle_directed(adj: Dict[str, List[str]], all_nodes: List[str]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in all_nodes}

    def dfs(u: str) -> bool:
        color[u] = GRAY  # In recursion stack
        for v in adj.get(u, []):
            if color[v] == GRAY:
                return True  # Back edge detected: Cycle!
            if color[v] == WHITE:
                if dfs(v):
                    return True
        color[u] = BLACK  # Exiting recursion stack
        return False

    for node in all_nodes:
        if color[node] == WHITE:
            if dfs(node):
                return True
    return False
```

---

## 8. Connected Components

- **Undirected Graphs**: A connected component is a maximal subgraph where every pair of vertices is connected by a path. Found by running BFS/DFS from every unvisited node.
- **Directed Graphs (Strongly Connected Components - SCC)**: Every vertex in the SCC is reachable from every other vertex in the SCC. Solved via Tarjan's or Kosaraju's algorithms ($O(V + E)$).

---

## 9. Bipartite Graph Verification (2-Coloring)

A graph is bipartite if its vertices can be partitioned into two disjoint sets $U$ and $V$ such that every edge connects a node in $U$ to a node in $V$ (no edges within the same set).
- **Theorem**: A graph is bipartite if and only if it contains **no odd-length cycles**.
- **Algorithm**: BFS/DFS 2-coloring. If any neighbor already has the same color as the current node, the graph is not bipartite.

---

## 10. Iterative DFS vs Recursion Limits in Python

Python enforces a default recursion stack limit (`sys.getrecursionlimit() == 1000`).
In enterprise systems, call graphs or dependency trees can exceed $50,000$ nodes. Recursive DFS will crash with `RecursionError: maximum recursion depth exceeded`.

### Production Rule: Use Iterative DFS with Explicit LIFO Stack

```python
def dfs_iterative(adj: Dict[str, List[str]], start: str) -> List[str]:
    """Iterative DFS safe against recursion stack overflows on deep graphs."""
    visited = set()
    order = []
    stack = [start]

    while stack:
        curr = stack.pop()
        if curr in visited:
            continue
        visited.add(curr)
        order.append(curr)

        # Push unvisited neighbors onto stack
        for neighbor in reversed(adj.get(curr, [])):
            if neighbor not in visited:
                stack.append(neighbor)

    return order
```

---

## 11. Graph Traversal Complexity Analysis

$$\text{Time Complexity: } O(|V| + |E|)$$
- Every vertex is visited at most once: $O(|V|)$.
- Every edge is inspected:
  - In directed graphs: each directed edge is examined once: $O(|E|)$.
  - In undirected graphs: each undirected edge is examined twice: $O(2|E|) = O(|E|)$.
- Total: $O(|V| + |E|)$.

$$\text{Space Complexity: } O(|V|)$$
- The `visited` set stores at most $|V|$ vertices.
- The queue (BFS) or stack (DFS) holds at most $|V|$ vertices in the worst case (e.g., star graph or line graph).

---

## 12. 7 Non-Negotiable Rules of Production Graph Engineering

1. **Always Use `collections.deque` for BFS**: Calling `list.pop(0)` is $O(N)$ because it shifts all remaining elements in memory, degrading BFS from $O(V + E)$ to $O(V^2 + E)$.
2. **Defend Against Recursion Limits**: In Python, write deep graph traversals using an explicit iterative stack (`list.pop()`) rather than recursion.
3. **Use 3-Coloring for Directed Cycles**: Simple 2-state `visited` sets produce false positive cycles on directed graphs whenever two paths merge into a shared sink node.
4. **Always Iterate Over Disconnected Components**: Real-world service meshes and networks are rarely fully connected. Wrap traversals in an outer loop iterating over all nodes in $V$.
5. **Never Mutate Graph During Traversal**: Adding or deleting edges while iterating through an adjacency list leads to `RuntimeError: dictionary changed size during iteration` or skipped nodes.
6. **Store Predecessors for Path Reconstruction**: Never store entire path arrays inside queue tuples `(node, [path])`—this incurs $O(V^2)$ memory copying. Store scalar `parent[child] = curr` pointers.
7. **Normalize Node Identifiers**: Strip leading/trailing whitespaces, enforce lowercase, or use typed UUIDs for vertices to prevent split-brain node representations.

---

## 13. Comprehensive Complexity & Operation Matrix

| Operation | Adjacency Matrix | Adjacency List | Space Overhead | Real-World Application |
|---|---|---|---|---|
| **Graph Construction** | $O(V^2)$ | $O(V + E)$ | Matrix: $O(V^2)$, List: $O(V + E)$ | Ingesting network topologies |
| **BFS Traversal** | $O(V^2)$ | $O(V + E)$ | $O(V)$ (queue + visited) | Shortest hop path, level scan |
| **DFS Traversal** | $O(V^2)$ | $O(V + E)$ | $O(V)$ (stack + visited) | Topological sort, reachability |
| **Cycle Detection (Directed)** | $O(V^2)$ | $O(V + E)$ | $O(V)$ (3-color state) | Circular RPC & Airflow DAG validation |
| **Cycle Detection (Undirected)**| $O(V^2)$ | $O(V + E)$ | $O(V)$ (parent pointers) | Network bridge/loop detection |
| **Connected Components** | $O(V^2)$ | $O(V + E)$ | $O(V)$ | Network partition / cluster isolation |
| **Bipartite Check (2-Color)** | $O(V^2)$ | $O(V + E)$ | $O(V)$ (color map) | Resource compatibility matching |
