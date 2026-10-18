# Day 24: 7 Critical Antipatterns in Tree BFS & DFS Engineering

Tree traversals are foundational to backend workflows, but subtle flaws in queue management, state backtracking, and graph conversions trigger silent data corruption, infinite loops, and catastrophic quadratic slowdowns.

Below are 7 critical antipatterns, their low-level runtime consequences, and enterprise remediations.

---

## 1. Dynamic Queue Mutation During Level Draining (Missing `level_size` Snapshot)

### The Mistake
Iterating over an actively mutating queue without snapshotting `len(queue)`:

```python
# WRONG: Merges all levels into an undifferentiated flat stream!
def level_order_broken(root: Optional[TreeNode]) -> list[list[int]]:
    if not root:
        return []
    queue = deque([root])
    result = []
    while queue:
        level = []
        # BUG: len(queue) grows as children are appended!
        # This while loop never finishes level 0 cleanly!
        while len(queue) > 0:
            node = queue.popleft()
            level.append(node.val)
            if node.left: queue.append(node.left)
            if node.right: queue.append(node.right)
        result.append(level)
    return result
```

### The Consequence
Because children are appended to the same queue during the inner loop, `len(queue)` never drops to zero until the entire tree is exhausted. The entire tree is placed into a single giant level, completely breaking level-by-level batching.

### The Correct Approach
Always capture a snapshot of the queue length in a fixed integer variable *before* the inner loop begins:

```python
# CORRECT: Fixed level snapshot cleanly partitions levels
while queue:
    level_size = len(queue)  # Snapshot fixed level boundary!
    current_level = []
    for _ in range(level_size):
        node = queue.popleft()
        current_level.append(node.val)
        if node.left: queue.append(node.left)
        if node.right: queue.append(node.right)
    result.append(current_level)
```

---

## 2. Using Python `list.pop(0)` for FIFO BFS Queues

### The Mistake
Using a standard Python list as a BFS queue and calling `queue.pop(0)`:

```python
# WRONG: Severe O(N) memory shift on every single node!
def bfs_slow(root: Optional[TreeNode]) -> list[int]:
    queue = [root]  # Standard Python list
    result = []
    while queue:
        node = queue.pop(0)  # C memmove() shifts all remaining N-1 pointers!
        result.append(node.val)
        if node.left: queue.append(node.left)
        if node.right: queue.append(node.right)
    return result
```

### The Consequence
Because Python lists are contiguous C pointer arrays, `pop(0)` invokes C's `memmove()` to shift all remaining $N - 1$ pointers leftward by 8 bytes. For a tree with $N = 100{,}000$ nodes, this causes $\frac{100{,}000^2}{2} = 5{,}000{,}000{,}000$ pointer shifts, causing the worker process to freeze for seconds and trigger timeout alerts.

### The Correct Approach
Always use `collections.deque` for FIFO queues:

```python
# CORRECT: O(1) double-ended queue using 64-pointer block list
from collections import deque

queue = deque([root])
while queue:
    node = queue.popleft()  # Strict O(1) block pointer advance
```

---

## 3. Forgetting to Backtrack Prefix Sum Hash Maps in Tree DFS

### The Mistake
Leaving the current node's running prefix sum in the shared hash map after unwinding the call stack:

```python
# WRONG: State leaks across sibling subtrees!
def path_sum_broken(node, curr_sum, target, prefix_map):
    if not node:
        return 0
    curr_sum += node.val
    count = prefix_map.get(curr_sum - target, 0)
    prefix_map[curr_sum] = prefix_map.get(curr_sum, 0) + 1

    # Recurse
    count += path_sum_broken(node.left, curr_sum, target, prefix_map)
    count += path_sum_broken(node.right, curr_sum, target, prefix_map)

    # BUG: Forgot prefix_map[curr_sum] -= 1!
    # Sibling branches will falsely believe this node is their ancestor!
    return count
```

### The Consequence
The prefix sum accumulated down the left subtree remains in `prefix_map` when the algorithm backtracks and enters the right subtree. The right subtree will match against prefix sums from completely unrelated sibling nodes, producing incorrect phantom path counts.

### The Correct Approach
Always enforce the **Backtracking Invariant**: decrement the hash map counter upon returning from the node's children:

```python
# CORRECT: Backtrack state before returning up the call stack
prefix_map[curr_sum] += 1
count += dfs(node.left, curr_sum)
count += dfs(node.right, curr_sum)
prefix_map[curr_sum] -= 1  # Crucial backtracking cleanup!
```

---

## 4. Omitting the `visited` Set in Radial Distance Queries (Tree to Graph)

### The Mistake
Adding bidirectional parent pointers to nodes and running BFS without tracking visited nodes:

```python
# WRONG: Infinite ping-pong oscillation between child and parent!
def distance_k_broken(target_node, k, parent_map):
    queue = deque([(target_node, 0)])
    # BUG: No visited set!
    while queue:
        curr, dist = queue.popleft()
        # curr enqueues parent... parent enqueues curr... INFINITE LOOP!
        for neighbor in (curr.left, curr.right, parent_map.get(curr)):
            if neighbor:
                queue.append((neighbor, dist + 1))
```

### The Consequence
Because trees are directed downward, normal trees have no cycles. But as soon as you add parent pointers, every node and its parent form a 2-node cycle (`parent <-> child`). Without a `visited` set, BFS perpetually bounces back and forth between parent and child until memory is exhausted.

### The Correct Approach
Always initialize a `visited: Set[TreeNode] = {target_node}` and check `neighbor not in visited` before enqueuing:

```python
# CORRECT: Track visited nodes in bidirectional graph expansions
visited = {target_node}
queue = deque([(target_node, 0)])
while queue:
    curr, dist = queue.popleft()
    for neighbor in (curr.left, curr.right, parent_map.get(curr)):
        if neighbor and neighbor not in visited:
            visited.add(neighbor)
            queue.append((neighbor, dist + 1))
```

---

## 5. Enqueuing Null Child Pointers Blindly into the BFS Queue

### The Mistake
Pushing children without checking if they exist:

```python
# WRONG: Enqueues None, bloating the queue and forcing repetitive null checks!
queue.append(curr.left)
queue.append(curr.right)
```

### The Consequence
For a balanced binary tree, half of all child references are `None`. Blindly pushing `None` doubles queue memory consumption and forces the queue pop logic to handle null pointers defensively.

### The Correct Approach
Guard before pushing to keep the queue clean:

```python
# CORRECT: Only enqueue valid non-null children
if curr.left:
    queue.append(curr.left)
if curr.right:
    queue.append(curr.right)
```

---

## 6. Blindly Using BFS on Wide Trees Without Memory Awareness

### The Mistake
Choosing BFS indiscriminately on massive trees without evaluating maximum tree width:

```python
# SCENARIO: A complete binary tree of depth 24 (16,000,000 nodes)
# Level 23 alone contains 8,388,608 nodes!
```

### The Consequence
In a complete binary tree of $N$ nodes, the bottom level contains approximately $N / 2$ nodes. A BFS queue on $16{,}000{,}000$ nodes must hold $8{,}000{,}000$ pointers in memory simultaneously (~64 MB of pointer RAM plus node wrapping overhead), risking Out-Of-Memory container kills on memory-constrained microservices. In contrast, DFS only holds $H = 24$ frames on the call stack (a few kilobytes).

### The Correct Approach
Choose the traversal algorithm based on tree topology and hardware constraints:
- On **wide trees**, favor **DFS** to bound memory to $\mathcal{O}(H)$.
- On **deep/skewed trees**, favor **BFS** or **Iterative DFS** to avoid call stack overflow.

---

## 7. Modifying Shared Tree State During Concurrent Async Traversals

### The Mistake
Mutating `node.status` or `node.val` directly in place when multiple concurrent asynchronous workers are traversing the same workflow tree:

```python
# WRONG: Race conditions on shared mutable tree nodes!
async def process_task(node: WorkflowTask):
    node.status = TaskStatus.RUNNING  # Unsynchronized write across concurrent coroutines!
    await execute_remote(node)
```

### The Consequence
Multiple async coroutines or worker threads overwrite node state concurrently, causing lost updates, conflicting DAG branch executions, and corrupt workflow audit trails.

### The Correct Approach
Isolate mutable state into a thread-safe dictionary or use an immutable execution context:

```python
# CORRECT: Keep task nodes immutable and record state in an isolated context
class ExecutionContext:
    def __init__(self):
        self._states: dict[str, TaskStatus] = {}
        self._lock = asyncio.Lock()

    async def update_status(self, task_id: str, status: TaskStatus):
        async with self._lock:
            self._states[task_id] = status
```
