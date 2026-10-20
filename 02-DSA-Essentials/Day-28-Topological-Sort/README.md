# Day 28: Topological Sort — Dependency Resolution & Build Systems

Topological Sort is the foundational algorithm for ordering directed dependencies. Given a directed graph $G = (V, E)$, a **topological ordering** is a linear permutation of all vertices such that for every directed edge $(u, v) \in E$, vertex $u$ appears strictly before vertex $v$ in the sequence.

```
       [ Core Config ] (A)
          /        \
         v          v
   (B) [ Auth ]   [ Database ] (C)
         \          /
          v        v
      [ API Gateway ] (D)

Valid Topological Order: [ A, B, C, D ]  or  [ A, C, B, D ]
Invalid Ordering:        [ A, D, B, C ]  (D executed before its dependency B!)
```

In modern backend and AI systems, topological sort orchestrates compilation pipelines (Bazel, Cargo), package dependency managers (pip, npm), data warehouse transformations (dbt, Apache Airflow), database schema migrations (Django, Alembic), and automatic differentiation engines in deep learning frameworks (PyTorch, JAX).

---

## 1. The Fundamental Prerequisite: The DAG Invariant

**Theorem**: A directed graph $G$ admits a topological ordering **if and only if** $G$ is a **Directed Acyclic Graph (DAG)**.

```
+-----------------------------------------------------------------------------------+
|                            THE DAG INVARIANT                                      |
+-----------------------------------------------------------------------------------+
| 1. If G contains a cycle (u -> v -> ... -> u):                                    |
|    - u must appear before v (because of edge u -> v).                             |
|    - v must appear before u (because of the path v -> ... -> u).                  |
|    - This creates an impossible logical contradiction: u < v and v < u.           |
| 2. Therefore, any graph with circular dependencies has ZERO topological sorts.    |
| 3. Topological sorting is simultaneously a linear sorter AND a cycle detector.    |
+-----------------------------------------------------------------------------------+
```

---

## 2. Real-World Applications in Backend & AI Infrastructure

```
+-----------------------------------------------------------------------------------+
|                        ENTERPRISE TOPOLOGICAL APPLICATIONS                        |
+-----------------------------------------------------------------------------------+
| 1. Build Systems (Bazel, Make, Cargo, Gradle): Compile dependencies before linking|
| 2. Package Managers (pip, npm, apt, Cargo): Install library dependencies safely   |
| 3. Workflow Orchestration (Airflow, Temporal, dbt): Schedule ETL task stages      |
| 4. Database Migrations (Django, Alembic): Apply parent migrations before children |
| 5. Neural Network Autodiff (PyTorch, JAX): Reverse topological tape execution     |
| 6. Spreadsheet Calculation Engines: Recompute dependent formulas dynamically     |
+-----------------------------------------------------------------------------------+
```

1. **Build Execution Engines (Bazel, Ninja, Make)**: Source files must be compiled into object files before being linked into shared libraries or binaries. Bazel builds an in-memory action graph and traverses it topologically.
2. **Package Dependency Resolvers (pip, npm, Cargo)**: Resolving transitive dependency trees (e.g. `fastapi` requires `pydantic` and `starlette`). The package manager installs leaves first, walking up to root applications.
3. **Data Pipeline DAGs (Apache Airflow, dbt, Prefect)**: In data warehouses, raw ingestion models (`stg_orders`) must run before dimensional models (`dim_customers`), which must run before reporting marts (`fct_revenue`).
4. **Database Migration Trees (Django, Flyway, Alembic)**: When multiple developers create migrations, Django constructs a migration graph using dependencies (`dependencies = [('users', '0001_initial')]`). Topological sort applies migrations in exact foreign-key order.
5. **Deep Learning Computation Graphs (PyTorch, TensorFlow)**: The forward pass builds a computation graph. The backward pass executes a **reverse topological sort** from the loss node back to model weights to compute gradients.

---

## 3. The In-Degree Concept

In a directed graph:
- **In-Degree ($\text{deg}^-(v)$)**: The number of directed edges entering vertex $v$. Represents the number of **unmet prerequisites** node $v$ must wait for before it can execute.
- **Out-Degree ($\text{deg}^+(v)$)**: The number of directed edges leaving vertex $v$. Represents downstream dependents relying on $v$.
- **Source Node**: A vertex with $\text{deg}^-(v) = 0$. Has zero unmet dependencies and is immediately ready for execution.
- **Sink Node**: A vertex with $\text{deg}^+(v) = 0$. Has no downstream dependents.

```
       (A) [In: 0] ---> (B) [In: 1] ---> (D) [In: 2]
        |                                   ^
        +-------------> (C) [In: 1] --------+

At start: Node A has In-Degree 0 (Ready to execute immediately).
When A completes: In-Degree of B drops from 1 -> 0, C drops from 1 -> 0.
Both B and C become ready!
```

---

## 4. Algorithm 1: Kahn's Algorithm (BFS In-Degree Peeling)

Proposed by Arthur Kahn in 1962, this is the most widely used topological sorting algorithm in production engineering because it naturally supports **parallel stage batching**.

### The Kahn Invariant
1. Compute the in-degree of every vertex in $V$.
2. Enqueue all source vertices ($\text{deg}^-(v) = 0$) into a FIFO queue.
3. While the queue is not empty:
   - Dequeue vertex $u$ and append it to the topological order list.
   - For every outgoing neighbor $v$ of $u$:
     - Decrement $v$'s in-degree: $\text{deg}^-(v) \leftarrow \text{deg}^-(v) - 1$.
     - If $\text{deg}^-(v) == 0$, all of $v$'s prerequisites have been satisfied; enqueue $v$.
4. **Cycle Invariant**: If the length of the topological order list is less than $|V|$, the graph contains at least one **directed cycle** (the remaining nodes have mutual deadlocks where in-degrees never reach 0).

```python
from collections import defaultdict, deque
from typing import Dict, List, Optional

def kahn_topological_sort(num_nodes: int, edges: List[List[int]]) -> Optional[List[int]]:
    """
    Kahn's Algorithm for Topological Sort.
    Time Complexity: O(V + E)
    Space Complexity: O(V + E)
    Returns: Valid topological ordering, or None if a cycle is detected.
    """
    adj = defaultdict(list)
    in_degree = [0] * num_nodes

    # Step 1: Build adjacency list and calculate in-degrees
    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    # Step 2: Enqueue all nodes with in-degree 0
    queue = deque([node for node in range(num_nodes) if in_degree[node] == 0])
    topo_order = []

    # Step 3: Process nodes
    while queue:
        curr = queue.popleft()
        topo_order.append(curr)

        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Step 4: Cycle validation
    if len(topo_order) != num_nodes:
        return None  # Cycle detected!

    return topo_order
```

---

## 5. Algorithm 2: DFS Post-Order Reversal (Tarjan's Approach)

Developed by Robert Tarjan, this approach relies on Depth-First Search and the call-stack backtracking order.

### The Post-Order Invariant
In a DAG, if we run DFS on node $u$, all reachable descendants of $u$ will finish their DFS execution **before** $u$ finishes its execution.
- Therefore, if we record nodes in **post-order** (when their DFS call completes and backtracks), every node $u$ appears **after** all of its downstream dependencies.
- **Reversing** the post-order sequence yields a valid topological sort!

```python
def dfs_topological_sort(num_nodes: int, edges: List[List[int]]) -> Optional[List[int]]:
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * num_nodes
    post_order = []

    def dfs(u: int) -> bool:
        color[u] = GRAY  # Mark active in call stack
        for v in adj[u]:
            if color[v] == GRAY:
                return True  # Back edge detected: Cycle!
            if color[v] == WHITE:
                if dfs(v):
                    return True
        color[u] = BLACK  # Exiting call frame
        post_order.append(u)
        return False

    for node in range(num_nodes):
        if color[node] == WHITE:
            if dfs(node):
                return None  # Cycle detected

    return post_order[::-1]  # Reverse post-order
```

---

## 6. Architectural Comparison: Kahn's BFS vs DFS Post-Order

```
+-----------------------------------+--------------------+--------------------+
| Dimension                         | Kahn's (BFS-Based) | DFS Post-Order     |
+-----------------------------------+--------------------+--------------------+
| Core Data Structure               | Queue + In-Degree  | Call Stack/Colors  |
| Cycle Detection Mechanism         | len(order) != |V|  | Back Edge (GRAY)   |
| Parallel Stage Execution (Waves)  | Native (O(1))      | Complex post-proc  |
| Memory Access Pattern             | Sequential array   | Pointer call-stack |
| Deep Graph Safety                 | Safe on heap       | Recursion limits   |
| Lexicographical Ordering          | Native (Min-Heap)  | Unnatural          |
| Real-Time Dynamic Task Enqueue    | Supported natively | Requires rebuild   |
| Production Standard In Backend    | Airflow / Bazel    | AST Compilers      |
+-----------------------------------+--------------------+--------------------+
```

---

## 7. Parallel Stage Batching (Wavefront Scheduling)

In distributed build systems and task runners (e.g., Bazel, GitHub Actions matrix, Airflow), we do not execute tasks one by one. We execute **waves** of independent tasks concurrently across worker clusters.

### Wavefront Generation via Kahn's Level Slicing
At each iteration of Kahn's algorithm, all nodes currently in the queue have $\text{deg}^-(v) = 0$. **They have zero mutual dependencies and can all be executed in parallel!**

```
Wave 0:  [ Build Frontend Assets ]    [ Compile C++ Backend ]   [ Run DB Migrations ]
                      \                        /
                       \                      /
Wave 1:                 [ Run Integration Tests ]
                                    |
Wave 2:                       [ Deploy Canary ]
```

```python
def parallel_execution_stages(num_nodes: int, edges: List[List[int]]) -> List[List[int]]:
    """Partitions tasks into discrete parallel execution stages (waves)."""
    adj = defaultdict(list)
    in_degree = [0] * num_nodes
    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    queue = deque([n for n in range(num_nodes) if in_degree[n] == 0])
    stages: List[List[int]] = []

    while queue:
        # All nodes in queue right now can run simultaneously
        current_wave_size = len(queue)
        current_stage = []

        for _ in range(current_wave_size):
            curr = queue.popleft()
            current_stage.append(curr)

            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        stages.append(current_stage)

    return stages
```

---

## 8. Critical Path Method (CPM) & Longest Path in DAG

In a build pipeline, every task $u$ has a known duration $T(u)$.
- What is the **minimum possible total time** to complete the entire pipeline?
- Which sequence of tasks forms the **Critical Path** (the bottleneck path where any delay directly delays the entire build)?

In a general graph, finding the longest simple path is NP-Hard. However, **in a DAG, the longest path can be solved in linear time $O(V + E)$ using Topological Sort and Dynamic Programming.**

```
Let dist[v] be the maximum time to reach node v from any source.
For each node u in Topological Order:
    For each neighbor v of u:
        dist[v] = max(dist[v], dist[u] + duration[v])
```

---

## 9. Lexicographical / Deterministic Topological Sort

In reproducible compilation (e.g. Debian reproducible builds, NixOS, Bazel remote caching), builds must be bit-for-bit deterministic. If multiple nodes are ready at the same time, we must break ties predictably.
- **Solution**: Replace the FIFO `collections.deque` in Kahn's algorithm with a **Min-Heap (`heapq`)**.
- Every pop yields the lexicographically smallest available node.
- Complexity: $O(V \log V + E \log V)$.

---

## 10. Dynamic Cycle Diagnostics: Extracting the Cycle

When Kahn's algorithm detects a cycle (`len(topo_order) < |V|`), it tells you that a cycle exists, but it does **not** identify which tasks caused it.
In production engines, returning `Error: Cycle detected` without naming the circular loop frustrates developers.

### Cycle Extraction Algorithm
All nodes with $\text{deg}^-(v) > 0$ after Kahn's finishes are either inside a cycle or downstream of a cycle.
1. Filter vertices to those with `in_degree[v] > 0`.
2. Pick any such vertex and run a DFS tracking active recursion ancestors (`GRAY` state).
3. The moment a back edge is hit, slice the call stack from the target ancestor to the current node to extract the exact circular path (e.g. `['auth-service', 'billing-service', 'auth-service']`).

---

## 11. 7 Non-Negotiable Rules of Production DAG Engineering

1. **Explicit In-Degree Validation**: Never run task schedulers on unvalidated DAG inputs. Run Kahn's validation during configuration loading before dispatching worker threads.
2. **Defend Against Self-Loops**: An edge from $u \to u$ immediately creates a cycle of length 1. Ensure edge sanitization rejects $u == v$.
3. **Use Queue Batching for Wavefronts**: Use `len(queue)` level-slicing to schedule independent tasks across thread/process pools simultaneously.
4. **Reproducible Builds Require Min-Heaps**: In distributed compilation systems, break ties deterministically using priority queues to prevent non-deterministic cache misses.
5. **Report Exact Cycle Traces**: When a cycle is detected, extract and log the exact circular dependency loop for developer diagnostics.
6. **Support Multiple Independent Components**: Real-world DAGs often contain disconnected sub-pipelines. Always initialize all nodes with in-degree 0 into the starting queue.
7. **Isolate Dynamic DAG Mutations**: If tasks dynamically spawn child tasks at runtime (e.g. dynamic Airflow task mapping), increment parent in-degrees and synchronize with atomic concurrency primitives.

---

## 12. Comprehensive Complexity Matrix

| Operation | Kahn's Algorithm | DFS Post-Order | Kahn's with Min-Heap |
|---|---|---|---|
| **Graph Construction** | $O(V + E)$ | $O(V + E)$ | $O(V + E)$ |
| **Topological Sort** | $O(V + E)$ | $O(V + E)$ | $O((V + E) \log V)$ |
| **Cycle Detection** | $O(1)$ check at end | $O(V + E)$ (via back-edge) | $O(1)$ check at end |
| **Parallel Stage Partition**| $O(V + E)$ native | Requires depth DP | $O((V + E) \log V)$ |
| **Space Overhead** | $O(V)$ in-degree array | $O(V)$ recursion stack | $O(V)$ min-heap |
| **Determinism** | Dependent on edge order | Dependent on node order| Strictly deterministic |
