# Critical Antipatterns & Production Traps in Topological Sorting

Topological sorting and DAG task execution are central to build systems, data pipelines, and workflow orchestrators. Subtle logic errors in edge direction, in-degree updates, or cycle detection can lead to deadlocks, data corruption, or silent infinite loops. Below are the top 7 mistakes observed in production environments.

---

### Mistake 1: Edge Direction Inversion ("Depends On" vs "Enables")

#### Bad Pattern
Inverting the edge direction when parsing task manifests. For example, in Airflow or Make, `Task B depends on Task A` means $A$ must complete before $B$ can begin ($A \to B$). Inverting this creates $B \to A$:

```python
# INCORRECT: Treating "B depends on A" as an edge from B to A
def build_graph_inverted(prerequisites: list[tuple[str, str]]):
    adj = {}
    in_degree = {}
    for task, prereq in prerequisites:  # "task depends on prereq"
        adj[task].append(prereq)        # BUG: Directed edge should be prereq -> task!
        in_degree[prereq] += 1
```

#### Correct Pattern
Always direct edges from the **prerequisite** (dependency) to the **dependent** (consumer):
$$A \longrightarrow B \quad (\text{Task } A \text{ enables Task } B)$$

```python
# CORRECT: Prerequisite points to dependent
def build_graph_correct(prerequisites: list[tuple[str, str]]):
    adj = defaultdict(list)
    in_degree = defaultdict(int)
    for task, prereq in prerequisites:
        adj[prereq].append(task)  # prereq enables task
        in_degree[task] += 1
        in_degree.setdefault(prereq, 0)
```

#### Why It Breaks
If edge direction is inverted, the topological sort will output the terminal tasks *first* and root setup tasks *last*. In a database migration engine, this attempts to run child foreign-key migrations before the parent table even exists!

---

### Mistake 2: Missing Cycle Detection & Returning Truncated Orderings

#### Bad Pattern
Blindly returning the output array of Kahn's algorithm without comparing its length against the total number of vertices:

```python
from collections import deque

# INCORRECT: Returns truncated list on cyclic graphs without raising errors
def kahn_blind(num_nodes: int, edges: list[tuple[int, int]]) -> list[int]:
    adj = defaultdict(list)
    in_degree = [0] * num_nodes
    for u, v in edges:
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

    return order  # BUG: If a cycle exists, order contains only a subset of nodes!
```

#### Correct Pattern
Enforce the Kahn Invariant: A valid topological sort **must** contain all $|V|$ vertices:

```python
# CORRECT: Enforces completeness invariant
def kahn_safe(num_nodes: int, edges: list[tuple[int, int]]) -> list[int]:
    # ... (same setup) ...
    if len(order) != num_nodes:
        raise ValueError(
            f"Cycle detected in graph! Only {len(order)} of {num_nodes} nodes resolved."
        )
    return order
```

#### Why It Breaks
If nodes $A$ and $B$ form a circular deadlock ($A \to B \to A$) alongside an independent node $C$, Kahn's algorithm will resolve $C$ and terminate with `order = [C]`. A caller relying on `len(order)` will execute $C$ and silently discard $A$ and $B$, causing silent task omission in production ETLs.

---

### Mistake 3: Modifying Graph Structure Instead of Tracking In-Degree Counters

#### Bad Pattern
Physically deleting nodes or edges from adjacency dictionaries during traversal:

```python
# INCORRECT: Modifying dictionary structure during traversal is O(V^2) or causes runtime errors
def topo_in_place_destructive(adj: dict[int, list[int]]):
    while adj:
        # Find node with no incoming edges by scanning all values! O(V * E)
        sources = [u for u in adj if not any(u in targets for targets in adj.values())]
        if not sources:
            break
        curr = sources[0]
        del adj[curr]  # Destroys original graph metadata
```

#### Correct Pattern
Maintain a decoupled `in_degree` integer lookup array:

```python
# CORRECT: Decrementing scalar counters in O(1) preserves graph immutability
def topo_counter(num_nodes: int, edges: list[tuple[int, int]]):
    in_degree = [0] * num_nodes
    for u, v in edges:
        in_degree[v] += 1
    # Decrement in_degree[v] without mutating adjacency structure
```

---

### Mistake 4: Non-Deterministic Task Ordering in Distributed Compilers

#### Bad Pattern
Using unordered sets to store ready nodes in a build scheduler:

```python
# INCORRECT: Set iteration order is non-deterministic across Python runs (hash randomization)
def kahn_non_deterministic(num_nodes: int, edges: list[tuple[int, int]]):
    # ...
    ready_set = {i for i in range(num_nodes) if in_degree[i] == 0}
    order = []
    while ready_set:
        curr = ready_set.pop()  # Arbitrary, non-deterministic order!
        order.append(curr)
        # ...
```

#### Correct Pattern
Use a Min-Heap (`heapq`) or sort initial inputs to guarantee byte-for-byte reproducibility:

```python
import heapq

# CORRECT: Deterministic tie-breaking ensures identical build artifacts
def kahn_deterministic(num_nodes: int, edges: list[tuple[int, int]]):
    # ...
    min_heap = [i for i in range(num_nodes) if in_degree[i] == 0]
    heapq.heapify(min_heap)
    order = []
    while min_heap:
        curr = heapq.heappop(min_heap)
        order.append(curr)
        # ...
```

#### Why It Breaks
Python enables `PYTHONHASHSEED` randomization by default. If two compilation runs execute in different orders, generated binaries or intermediate symbol caches may differ, causing cache invalidations across distributed build clusters (e.g. Bazel / sccache).

---

### Mistake 5: Failing to Handle Self-Loops ($u \to u$)

#### Bad Pattern
Assuming cycles always involve at least two distinct nodes:

```python
# INCORRECT: Fails if an edge points directly to itself
def add_edge_naive(adj, in_degree, u, v):
    adj[u].append(v)
    in_degree[v] += 1  # If u == v, node v's in-degree is permanently >= 1!
```

#### Correct Pattern
Validate and reject self-loops at the graph ingestion boundary:

```python
# CORRECT: Immediate validation guard
def add_edge_validated(adj, in_degree, u, v):
    if u == v:
        raise ValueError(f"Self-loop detected: Task '{u}' cannot depend on itself!")
    adj[u].append(v)
    in_degree[v] += 1
```

---

### Mistake 6: Recursive DFS Post-Order Stack Overflow on Deep Pipelines

#### Bad Pattern
Using recursive DFS for DAGs with thousands of chained sequential tasks:

```python
# INCORRECT: Crashes with RecursionError when pipeline chain exceeds 1,000 tasks
def dfs_topo_recursive(u, adj, visited, order):
    visited.add(u)
    for v in adj[u]:
        if v not in visited:
            dfs_topo_recursive(v, adj, visited, order)  # RecursionError!
    order.append(u)
```

#### Correct Pattern
Use Kahn's Algorithm (BFS) or an explicit heap-allocated iterative stack:

```python
# CORRECT: Kahn's algorithm uses collections.deque on the heap (unlimited depth)
def kahn_unlimited_depth(num_nodes: int, edges: list[tuple[int, int]]):
    # Operating on the heap, Kahn's algorithm easily handles 1,000,000+ nodes
    ...
```

---

### Mistake 7: Uncoordinated Parallel Wavefronts Causing Race Conditions

#### Bad Pattern
In asynchronous task runners, dispatching child tasks immediately whenever any parent completes without atomic in-degree synchronization:

```python
# BUGGY CONCURRENT DISPATCH:
async def on_parent_completed(parent_id):
    for child in adj[parent_id]:
        in_degree[child] -= 1  # Race condition if multiple parents complete concurrently!
        if in_degree[child] == 0:
            asyncio.create_task(run(child))  # child may be launched MULTIPLE times!
```

#### Correct Pattern
Protect in-degree decrements with thread/async locks, or use centralized queue dispatching:

```python
# CORRECT: Centralized or thread-safe atomic in-degree transitions
async def on_parent_completed_safe(parent_id, lock):
    async with lock:
        for child in adj[parent_id]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                # Exactly one parent triggers the dispatch
                asyncio.create_task(run(child))
```
