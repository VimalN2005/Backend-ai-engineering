# Day 24: Practical Hands-On DSA Challenges

Master Tree BFS Level Order Batching, DFS Path Finding, and Perspective Views with these 3 production-level challenges.

---

## Challenge 1: Binary Tree Vertical Order Traversal (Column-Based Partitioning)

### Objective
In data visualization renderers and hierarchical organizational charting, nodes must be partitioned and displayed in distinct vertical columns from left to right.

Given the root of a binary tree, return the **vertical order traversal** of its nodes' values.
For each node at position `(row, col)`:
- Its left child is at `(row + 1, col - 1)`.
- Its right child is at `(row + 1, col + 1)`.
- The root is at `(0, 0)`.

Columns must be returned from left to right (smallest `col` to largest `col`). Within each column, nodes must be sorted from top to bottom (smallest `row` to largest `row`). If two nodes share the exact same `(row, col)`, sort them by node value in ascending order.

### Requirements
1. **Time Complexity**: $\mathcal{O}(N \log N)$ due to sorting within vertical columns.
2. **Space Complexity**: $\mathcal{O}(N)$ for the BFS queue and coordinate hash map.
3. **BFS Coordinate Invariant**:
   - Enqueue `(node, row, col)` tuples into a FIFO queue.
   - Use a hash map `column_map: Dict[int, List[Tuple[int, int]]]` mapping `col -> [(row, val), ...]`.
   - Track `min_col` and `max_col` to iterate through columns in order without sorting keys.
4. **Test Cases**:
   - `[3, 9, 20, None, None, 15, 7]` $\to$ `[[9], [3, 15], [20], [7]]`
   - `[1, 2, 3, 4, 5, 6, 7]` $\to$ `[[4], [2], [1, 5, 6], [3], [7]]`
   - `[]` $\to$ `[]`

---

## Challenge 2: Populating Next Right Pointers in Each Node ($\mathcal{O}(1)$ Auxiliary Space)

### Objective
In tree-based cache hierarchies and DOM renderers, navigating between sibling nodes at the same level without traversing back up to the parent is a frequent operation.

Given a binary tree where each node has an additional pointer `next` (initialized to `None`):
```python
class Node:
    def __init__(self, val, left=None, right=None, next=None):
        self.val = val
        self.left = left
        self.right = right
        self.next = next
```
Populate each `next` pointer to point to its next right node on the same level. If there is no next right node, the `next` pointer should be set to `None`.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$ — every node is processed once.
2. **Space Complexity**: Strictly $\mathcal{O}(1)$ auxiliary space. (A naive BFS with a queue consumes $\mathcal{O}(N)$ memory; you must achieve $\mathcal{O}(1)$ by using the `next` pointers of the already-processed parent level as a linked list!).
3. **Algorithm Design**:
   - Treat the current level as a singly linked list connected by `next` pointers.
   - Use a dummy sentinel node `dummy_child` to build the linked list for the *next* level down.
   - Traverse the current level horizontally using `curr = curr.next`, appending non-null children to `dummy_child.next`.
   - Once the current level is exhausted, advance to `dummy_child.next` and reset the dummy sentinel.
4. **Test Cases**:
   - Input: `[1, 2, 3, 4, 5, None, 7]`
     - Level 0: `1 -> None`
     - Level 1: `2 -> 3 -> None`
     - Level 2: `4 -> 5 -> 7 -> None`

---

## Challenge 3: Path Sum II - All Root-to-Leaf Target Paths (DFS Backtracking)

### Objective
In compliance auditing and financial transaction validation, finding all end-to-end execution paths that sum to a specific threshold is required to detect anomalous account transfers.

Given the root of a binary tree and an integer `target_sum`, return **all root-to-leaf paths** where the sum of the node values in the path equals `target_sum`. Each path should be returned as a list of node values, not node references. (A leaf is a node with no children).

### Requirements
1. **Time Complexity**: $\mathcal{O}(N)$ where $N$ is the number of nodes (proportional to output paths).
2. **Space Complexity**: $\mathcal{O}(H)$ auxiliary call-stack and path memory where $H$ is the tree height.
3. **Backtracking Invariant**:
   - Maintain a single mutable list `current_path`.
   - When visiting a node: `current_path.append(node.val)`.
   - If the node is a leaf and `curr_sum == target_sum`: record a shallow copy `result.append(list(current_path))`.
   - When ascending up the call stack: pop the current node `current_path.pop()`.
4. **Test Cases**:
   - Input: `[5, 4, 8, 11, None, 13, 4, 7, 2, None, None, 5, 1]`, `target_sum = 22`
     - Output: `[[5, 4, 11, 2], [5, 8, 4, 5]]`
   - Input: `[1, 2, 3]`, `target_sum = 5`
     - Output: `[]`
   - Input: `[1, 2]`, `target_sum = 0`
     - Output: `[]`
