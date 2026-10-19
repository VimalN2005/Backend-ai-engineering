# Critical Antipatterns & Production Traps in Graph Algorithms

Graph processing is prone to subtle algorithmic bugs, memory leaks, and performance collapses. Below are the top 7 mistakes observed in backend microservice meshes, data pipeline orchestrators, and technical interview submissions.

---

### Mistake 1: Marking Nodes as `visited` on Dequeue Instead of Enqueue

#### Bad Pattern
Delaying the addition of a vertex to the `visited` set until it is popped from the BFS queue:

```python
from collections import deque

# INCORRECT: Node marked visited upon popping from queue
def bfs_bad(adj: dict[str, list[str]], start: str):
    visited = set()
    queue = deque([start])

    while queue:
        curr = queue.popleft()
        visited.add(curr)  # TOO LATE!

        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                queue.append(neighbor)  # neighbor can be enqueued hundreds of times!
```

#### Correct Pattern
Always mark the vertex as `visited` at the exact moment it is pushed into the queue:

```python
from collections import deque

# CORRECT: Immediate visited marking prevents duplicate enqueues
def bfs_good(adj: dict[str, list[str]], start: str):
    visited = {start}
    queue = deque([start])

    while queue:
        curr = queue.popleft()

        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)  # INVARIANT: Mark visited immediately upon discovery
                queue.append(neighbor)
```

#### Why It Breaks
In a dense graph or grid where multiple nodes connect to the same neighbor, that neighbor will be pushed into the queue by *every* adjacent node before it ever gets popped.
- In a grid of $1000 \times 1000$, a single vertex can be enqueued up to 4 times per wave, causing the queue size to explode exponentially, exhausting RAM and triggering `MemoryError`.

---

### Mistake 2: Using Python `list.pop(0)` for BFS Queues

#### Bad Pattern
Using a standard Python list as a FIFO queue:

```python
# INCORRECT: O(N) memory shift per popleft degrades BFS to O(V^2 + E)
def bfs_slow(adj: dict[str, list[str]], start: str):
    queue = [start]
    visited = {start}
    while queue:
        curr = queue.pop(0)  # O(K) memory shift where K is current queue size!
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
```

#### Correct Pattern
Always use `collections.deque`:

```python
from collections import deque

# CORRECT: O(1) amortized popleft from doubly-linked block list
def bfs_fast(adj: dict[str, list[str]], start: str):
    queue = deque([start])
    visited = {start}
    while queue:
        curr = queue.popleft()  # O(1) guaranteed
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
```

#### Why It Breaks
In CPython, `list` is a contiguous dynamic array. Calling `list.pop(0)` removes the 0th pointer and shifts all remaining $N-1$ pointers one slot to the left in memory.
On 20,000 nodes, `list.pop(0)` causes hundreds of millions of memory movements, running **75x to 1,000x slower** than `deque.popleft()`.

---

### Mistake 3: Using a Simple Visited Set for Directed Cycle Detection

#### Bad Pattern
Using a 2-state boolean `visited` set to detect cycles in a directed graph:

```python
# INCORRECT: Flags diamonds and convergent DAG paths as false cycles
def has_cycle_directed_broken(adj: dict[str, list[str]], start: str) -> bool:
    visited = set()

    def dfs(u: str) -> bool:
        if u in visited:
            return True  # BUG: Falsely flags diamond convergence as cycle!
        visited.add(u)
        for v in adj.get(u, []):
            if dfs(v):
                return True
        return False

    return dfs(start)

# Counterexample: A -> B -> D and A -> C -> D (Diamond DAG)
# Path 1 visits A, B, D. Path 2 visits C, then reaches D.
# D is in visited -> Returns True (FALSE POSITIVE)!
```

#### Correct Pattern
Use **Three-Color State Marking** (White = unvisited, Gray = in current call stack, Black = fully explored):

```python
# CORRECT: Only a back edge to a GRAY node (active in stack) is a true cycle
def has_cycle_directed_correct(adj: dict[str, list[str]], all_nodes: list[str]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in all_nodes}

    def dfs(u: str) -> bool:
        color[u] = GRAY  # In active stack
        for v in adj.get(u, []):
            if color[v] == GRAY:
                return True  # True cycle (Back Edge)
            if color[v] == WHITE:
                if dfs(v):
                    return True
        color[u] = BLACK  # Exited stack
        return False

    for node in all_nodes:
        if color[node] == WHITE:
            if dfs(node):
                return True
    return False
```

#### Why It Breaks
A directed graph can have multiple independent paths leading to the same terminal node without any cycle. The 2-state approach cannot distinguish between a **cross/forward edge** in a DAG and an actual **back edge** creating a circular loop.

---

### Mistake 4: Treating the Parent Edge as a Cycle in Undirected Graphs

#### Bad Pattern
In an undirected graph, checking `if v in visited: return True` without filtering out the immediate parent:

```python
# INCORRECT: Every undirected edge (u, v) is falsely treated as a cycle of length 2
def has_cycle_undirected_broken(adj: dict[str, list[str]], start: str) -> bool:
    visited = set()

    def dfs(u: str) -> bool:
        visited.add(u)
        for v in adj.get(u, []):
            if v in visited:
                return True  # BUG: v could be the parent node we just came from!
            if dfs(v):
                return True
        return False

    return dfs(start)
```

#### Correct Pattern
Pass the `parent` vertex and verify `v != parent`:

```python
# CORRECT: Exclude immediate parent from cycle verification
def has_cycle_undirected_correct(adj: dict[str, list[str]], start: str) -> bool:
    visited = set()

    def dfs(u: str, parent: str | None) -> bool:
        visited.add(u)
        for v in adj.get(u, []):
            if v not in visited:
                if dfs(v, u):
                    return True
            elif v != parent:
                return True  # Visited neighbor that is not parent -> TRUE CYCLE
        return False

    return dfs(start, None)
```

#### Why It Breaks
In an undirected graph, adding edge $A - B$ adds $B$ to $A$'s adjacency list and $A$ to $B$'s adjacency list. When traversing $A \to B$, checking $B$'s neighbors immediately sees $A$. Without checking `v != parent`, every undirected tree is misclassified as cyclic.

---

### Mistake 5: Recursion Limit Crash on Deep Graph Call Stacks

#### Bad Pattern
Using recursive DFS for large production graphs:

```python
# DANGEROUS IN PYTHON: Crashes on call chains > 1000 nodes
def dfs_recursive(adj: dict[str, list[str]], curr: str, visited: set):
    visited.add(curr)
    for neighbor in adj.get(curr, []):
        if neighbor not in visited:
            dfs_recursive(adj, neighbor, visited)  # RecursionError: maximum recursion depth exceeded
```

#### Correct Pattern
Use **Iterative DFS** with an explicit heap-allocated Python list as a LIFO stack:

```python
# PRODUCTION SAFE: Operates across 1,000,000+ vertices without recursion limits
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

#### Why It Breaks
Python’s C-stack limits recursion to 1,000 frames by default to protect the interpreter. Deep linear microservice call graphs or AST trees will instantly crash worker threads in production.

---

### Mistake 6: Assuming Graphs are Always Fully Connected

#### Bad Pattern
Running BFS or DFS from a single arbitrary starting node and assuming all vertices in the graph were processed:

```python
# INCORRECT: Fails to process disconnected subgraphs or isolated nodes
def count_all_nodes(adj: dict[str, list[str]], arbitrary_start: str) -> int:
    visited = set()
    queue = deque([arbitrary_start])
    visited.add(arbitrary_start)

    while queue:
        curr = queue.popleft()
        for n in adj.get(curr, []):
            if n not in visited:
                visited.add(n)
                queue.append(n)

    return len(visited)  # Misses all other connected components!
```

#### Correct Pattern
Always wrap the traversal in an outer loop over all nodes:

```python
# CORRECT: Guarantees full coverage across all components
def traverse_entire_graph(all_nodes: list[str], adj: dict[str, list[str]]):
    visited = set()
    components = []

    for node in all_nodes:
        if node not in visited:
            comp = []
            queue = deque([node])
            visited.add(node)
            while queue:
                curr = queue.popleft()
                comp.append(curr)
                for neighbor in adj.get(curr, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(comp)

    return components
```

---

### Mistake 7: Storing Entire Path History Tuples Inside the BFS Queue

#### Bad Pattern
Enqueueing the full accumulated path list at each step:

```python
# INCORRECT: O(V^2) memory consumption and heavy copy overhead
def bfs_path_expensive(adj: dict[str, list[str]], start: str, target: str):
    queue = deque([(start, [start])])  # Allocates new list copy on every push!
    visited = {start}

    while queue:
        curr, path = queue.popleft()
        if curr == target:
            return path
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))  # O(K) copy per neighbor!
```

#### Correct Pattern
Store scalar predecessor pointers (`parent[child] = curr`) and reconstruct backwards only when the target is reached:

```python
# CORRECT: O(1) memory per queue entry, O(V) path reconstruction at the end
def bfs_path_optimal(adj: dict[str, list[str]], start: str, target: str):
    queue = deque([start])
    visited = {start}
    parent = {start: None}

    while queue:
        curr = queue.popleft()
        if curr == target:
            break
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                parent[neighbor] = curr
                queue.append(neighbor)

    if target not in parent:
        return None

    path = []
    step = target
    while step is not None:
        path.append(step)
        step = parent[step]
    return path[::-1]
```
