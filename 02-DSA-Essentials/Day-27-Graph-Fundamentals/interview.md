# Senior Backend & AI Systems Interview: Graph Fundamentals

This document details the top 5 high-impact interview questions asked by Tier-1 tech companies (Google, Meta, Uber, Netflix) regarding graph representations, distributed traversals, and cycle detection mechanics.

---

### Question 1: How do Adjacency Matrix, Adjacency List, and Compressed Sparse Row (CSR) compare in production systems?

#### The Question
"In high-performance graph systems (e.g., Graph Neural Networks, Neo4j, or large-scale routing engines), why don't we always use an Adjacency Matrix or a simple Adjacency List? When is Compressed Sparse Row (CSR) required?"

#### Candidate Answer
The selection of graph representation is governed by **graph sparsity** ($|E| \ll |V|^2$) and **hardware memory hierarchy** (CPU cache line utilization):

| Dimension | Adjacency Matrix | Adjacency List (Linked/Hash) | Compressed Sparse Row (CSR) |
|---|---|---|---|
| **Space Complexity** | $O(|V|^2)$ | $O(|V| + |E|)$ | $O(|V| + |E|)$ (Minimal constant factor) |
| **Edge Lookup $(u, v)$** | $O(1)$ | $O(\text{deg}(u))$ | $O(\log(\text{deg}(u)))$ via binary search |
| **Iterate Neighbors** | $O(|V|)$ | $O(\text{deg}(u))$ | $O(\text{deg}(u))$ |
| **Memory Layout** | Flat 2D array | Fragmented heap pointers | 2 or 3 flat, contiguous 1D arrays |
| **Cache Miss Rate** | Low, but massive footprint | **High** (pointer chasing across RAM) | **Optimal** (pre-fetchable contiguous memory) |
| **Dynamic Mutations** | $O(1)$ edge toggles | $O(1)$ appends | Expensive ($O(|E|)$ rebuild) |

#### How CSR Works
CSR stores a static graph using two flat 1D arrays:
1. `col_indices`: A flat array of all target neighbors concatenated together.
2. `row_ptr`: An array of size $|V| + 1$ where `row_ptr[u]` gives the starting index of node $u$'s neighbors in `col_indices`, and `row_ptr[u + 1]` gives the end index.
   - Neighbors of node $u$: `col_indices[row_ptr[u] : row_ptr[u + 1]]`.

#### Production Recommendation:
- **Backend Microservice APIs / Dynamic Graphs**: Use **Adjacency List** (HashMaps with list/set values) because edges are inserted/removed dynamically.
- **AI / Deep Learning (PyG, DGL, Graph Neural Networks)**: Use **CSR / CSC** tensors on GPUs to eliminate pointer chasing and maximize memory bandwidth during matrix multiplications and message passing.

---

### Question 2: Why does BFS guarantee shortest path in unweighted graphs, and why does it fail on weighted graphs?

#### The Question
"Provide the formal inductive invariant showing why BFS finds the shortest path in unweighted graphs. Why does this guarantee collapse if edges have arbitrary positive weights?"

#### Candidate Answer

#### The Unweighted Invariant
In BFS, the queue maintains a strict monotonicity invariant:
$$\text{dist}(s, Q[i]) \le \text{dist}(s, Q[i+1]) \le \text{dist}(s, Q[i]) + 1$$
At any point during execution, the queue contains vertices from at most two consecutive distance levels ($k$ and $k+1$).
1. **Base Case**: At $t=0$, level 0 contains only $\{s\}$ with distance 0.
2. **Inductive Step**: When popping node $u$ at level $k$, all unvisited neighbors $v$ are added to the back of the queue at level $k+1$.
3. Since the queue is FIFO, no node at level $k+1$ can be popped before all nodes at level $k$ are exhausted.
4. Hence, the first time target $T$ is discovered, the number of edges from $s$ to $T$ must be minimal.

#### Why BFS Collapses on Weighted Graphs
BFS equates **path length** (number of edge hops) with **path cost** (sum of edge weights):
- Suppose path $A \to B$ has 1 hop with weight 100.
- Path $A \to C \to B$ has 2 hops with weights $1 + 1 = 2$.
- BFS discovers $B$ via $A \to B$ first (1 hop) and marks $B$ as visited, returning cost 100. It misses the 2-hop path with cost 2.
- **Solution**: On weighted graphs with non-negative weights, we replace the FIFO queue with a Min-Priority Queue (Dijkstra’s Algorithm), sorting by cumulative path cost rather than hop count.

---

### Question 3: Why does cycle detection differ between Directed and Undirected graphs?

#### The Question
"Why does checking `if neighbor in visited and neighbor != parent` work for undirected graphs, but completely fail for directed graphs? What mathematical state is required for directed cycle detection?"

#### Candidate Answer

#### The Undirected Case: Parent Filtering
In an undirected graph, every edge $(u, v)$ is bidirectional. Traversing $u \to v$ means $u$ is in $v$'s adjacency list:
- If we didn't track `parent`, every single edge would be misclassified as a cycle of length 2 ($u \to v \to u$).
- If $v$ has already been visited and $v \ne \text{parent}$, we have arrived at an already-explored tree node through a different branch, which guarantees a cycle.

#### The Directed Case: Diamond Convergence
In a directed graph:
- Edge $(u, v)$ does not imply $(v, u)$.
- Two independent paths can converge to a shared child node:
  $$A \to B \to D \quad \text{and} \quad A \to C \to D$$
- Node $D$ is reached twice. If we only track a 2-state `visited` set, reaching $D$ the second time will falsely trigger a cycle alert, even though the graph is a completely valid Directed Acyclic Graph (DAG).

#### The 3-Coloring Solution
A cycle in a directed graph exists if and only if there is a **Back Edge** pointing to an ancestor currently in the active execution call stack.
We partition vertices into three disjoint states:
1. **WHITE (0)**: Unvisited.
2. **GRAY (1)**: Currently in active recursion stack (ancestors of current node).
3. **BLACK (2)**: Fully processed; all reachable descendants have been verified acyclic.
- If we see an edge pointing to a **GRAY** node $\longrightarrow$ **Cycle Detected!**
- If we see an edge pointing to a **BLACK** node $\longrightarrow$ **Safe Cross/Forward Edge** (no cycle).

---

### Question 4: How do distributed graph processing engines (Pregel, GraphX) scale beyond a single machine?

#### The Question
"When a graph has billions of vertices and trillions of edges (e.g., Google search index or Facebook social graph), it cannot fit into a single machine's RAM. How does distributed graph computing work?"

#### Candidate Answer
Distributed graph processing abandons traditional centralized BFS/DFS in favor of the **Bulk Synchronous Parallel (BSP)** model, pioneered by Google's **Pregel** and Apache Spark **GraphX**:

#### 1. "Think Like a Vertex" Programming Model
Instead of a global master traversing the graph:
- Every vertex $v$ executes user-defined logic concurrently in discrete **supersteps**.
- During superstep $S$:
  1. Vertex $v$ receives messages sent to it from its incoming neighbors during superstep $S-1$.
  2. Vertex $v$ computes its new local state (e.g. `min_distance = min(messages)`).
  3. Vertex $v$ sends messages along its outgoing edges for superstep $S+1$.
  4. If no state change occurs, $v$ votes to halt.
- The global computation terminates when all vertices are halted and no messages are in flight.

#### 2. Graph Partitioning: Edge-Cut vs Vertex-Cut
How the graph is sharded across cluster worker nodes determines network RPC traffic:
- **Edge-Cut**: Vertices are distributed across machines; edges crossing machines require network RPCs. Works well for low-degree, uniform graphs.
- **Vertex-Cut (Used in Power-Law Networks)**: In social/web graphs, hub nodes (e.g., celebrity accounts) have millions of edges. Sharding by edge-cut overloads the machine holding the hub vertex. Vertex-cut shards the *edges* across machines and replicates the vertex across partitions as mirrors, synchronizing state with minimal network bottlenecks.

---

### Question 5: How do you handle deep graph traversals in Python without crashing the interpreter?

#### The Question
"Python's default recursion limit is 1,000 frames. If you are traversing an enterprise call dependency graph with 50,000 nodes, how should you architect your code, and what are the trade-offs of `sys.setrecursionlimit()`?"

#### Candidate Answer

#### The Pitfall of `sys.setrecursionlimit()`
Calling `sys.setrecursionlimit(100000)` is an antipattern in production:
1. Python function calls allocate a C-stack frame in operating system memory.
2. The OS C-stack has a fixed size (typically 1MB to 8MB per thread).
3. If deep recursion exhausts the OS C-stack, the Python process crashes instantly with a **Segmentation Fault (`SIGSEGV`)**, which cannot be caught by any Python `try/except` block and immediately kills the production container.

#### The 3 Production-Grade Solutions

1. **Iterative Stack Architecture (Standard Recommendation)**:
   - Use an explicit Python list on the heap (`stack = [root]`, `stack.pop()`).
   - Memory is bounded only by available system RAM (Gigabytes), not the tiny C-stack (Megabytes).
   ```python
   def dfs_iterative(adj, root):
       visited, stack = set(), [root]
       while stack:
           curr = stack.pop()
           if curr in visited: continue
           visited.add(curr)
           for nbr in reversed(adj.get(curr, [])):
               if nbr not in visited: stack.append(nbr)
   ```

2. **Trampolining (Generator DFS)**:
   - Yielding subroutines back to a central trampoline loop that flattens call frames into a single loop.

3. **Breadth-First Search (BFS)**:
   - If the task only requires reachability, connected components, or shortest path, replace DFS with queue-based BFS (`collections.deque`), which naturally operates on the heap.
