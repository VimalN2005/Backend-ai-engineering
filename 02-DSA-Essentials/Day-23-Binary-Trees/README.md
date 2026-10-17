# Day 23: Binary Trees: Recursive Traversals (Inorder, Preorder, Postorder) & AST Compilation

In backend engineering and artificial intelligence systems, hierarchical data cannot be modeled efficiently with linear sequences. From **Abstract Syntax Trees (ASTs)** in SQL query optimizers (PostgreSQL, SQLite) and compiler bytecode generation to **Machine Learning Decision Trees** (Random Forests, XGBoost), **Permission Hierarchies** (RBAC / ABAC), and **DOM / JSON parsers**, Binary Trees form the structural backbone of core systems.

Mastering binary trees requires mastering **Tree Traversal Mechanics** (Preorder, Inorder, Postorder) and understanding how recursive divide-and-conquer unwinds on the physical runtime call stack.

---

## 1. Binary Trees in Backend Architecture & AI Systems

```
1. SQL Query Execution Plan / Compiler AST:
                    [ Filter: AND ]
                   /               \
         [ Comp: status == 200 ]   [ Filter: OR ]
                                  /              \
                    [ Comp: lat < 500 ]    [ Comp: role == 'admin' ]
Bottom-up Postorder evaluation evaluates leaves first, then aggregates boolean truth!

2. Machine Learning Decision Trees (XGBoost / LightGBM):
                    [ Feature: age <= 30 ]
                   /                      \
        [ Feature: income <= 50k ]       [ Leaf: Class = Approve ]
        /                        \
    [ Leaf: Reject ]          [ Leaf: Approve ]
Evaluating an inference sample is an Inorder / Preorder traversal down decision branches!
```

---

## 2. Structural Invariants & Tree Classifications

```
1. Full Binary Tree:             2. Complete Binary Tree:        3. Degenerate (Skewed) Tree:
   Every node has 0 or 2            All levels filled except        Collapses into a linked
   children.                        last (filled left-to-right).    list! O(N) operations!
          ( 1 )                            ( 1 )                            ( 1 )
         /     \                          /     \                            \
       ( 2 )   ( 3 )                    ( 2 )   ( 3 )                        ( 2 )
       /   \                            /   \   /                               \
     ( 4 ) ( 5 )                      ( 4 ) ( 5)( 6 )                           ( 3 )
```

### Height and Balance:
- **Tree Height ($H$)**: The number of edges on the longest path from the root to a leaf.
- **Balanced Tree**: Height is bounded by $\mathcal{O}(\log_2 N)$. Search, insertion, and traversal take $\mathcal{O}(\log N)$ stack frames.
- **Skewed (Degenerate) Tree**: Height equals $N - 1$. Recursion depth becomes $\mathcal{O}(N)$, risking call-stack exhaustion (`RecursionError`).

---

## 3. The `TreeNode` Class & Memory Layout (`__slots__`)

In CPython, an unoptimized Python class creates an instance dictionary (`__dict__`), consuming ~152 bytes per node. By using `__slots__ = ("val", "left", "right")`, we eliminate the dynamic dictionary and drop memory consumption to ~48 bytes:

```python
class TreeNode:
    __slots__ = ("val", "left", "right")

    def __init__(
        self,
        val: int,
        left: Optional[TreeNode] = None,
        right: Optional[TreeNode] = None,
    ) -> None:
        self.val = val
        self.left = left
        self.right = right
```

For an AI tree model containing 1,000,000 nodes, `__slots__` reduces memory from ~152 MB to ~48 MB (a **~68% RAM savings**).

---

## 4. The Anatomy of Tree Recursion & The Call Stack

Every recursive call allocates a **Call Stack Frame** in CPython:
```
Call Stack:
┌──────────────────────────────────────────────┐
│ inorder(node=4)   -> returns [4]             │ <-- Top of Stack (Leaf)
├──────────────────────────────────────────────┤
│ inorder(node=2)   -> calls left (4), then 2  │
├──────────────────────────────────────────────┤
│ inorder(node=1)   -> root waiting for left   │ <-- Base of Stack
└──────────────────────────────────────────────┘
```

- **Base Case**: `if root is None: return`
- **Time Complexity**: $\mathcal{O}(N)$ — every node is visited exactly once.
- **Space Complexity**: $\mathcal{O}(H)$ where $H$ is the height of the tree.
  - Best / Balanced Case: $\mathcal{O}(\log N)$ stack frames.
  - Worst Case (Skewed Tree): $\mathcal{O}(N)$ stack frames.

---

## 5. Preorder Traversal: `Root -> Left -> Right`

In **Preorder Traversal**, the current node is visited **before** its child subtrees:

```
            ( 1 )
           /     \
         ( 2 )   ( 3 )
         /   \
       ( 4 ) ( 5 )

Preorder Visit Sequence: 1 -> 2 -> 4 -> 5 -> 3
```

### Production Use Cases:
1. **Tree Serialization & Deserialization**: Preorder preserving `None` markers uniquely encodes tree structure.
2. **Cloning / Deep Copying**: To clone a tree, create the parent node first, then populate left and right children.
3. **Directory Tree Printing**: Formatting nested file systems (`root/`, then `dirA/`, then `file1`).

---

## 6. Inorder Traversal: `Left -> Root -> Right`

In **Inorder Traversal**, the left subtree is visited first, then the current node, then the right subtree:

```
            ( 4 )
           /     \
         ( 2 )   ( 5 )
         /   \
       ( 1 ) ( 3 )

Inorder Visit Sequence: 1 -> 2 -> 3 -> 4 -> 5
```

### Production Use Cases:
1. **Binary Search Tree (BST) Ordering**: Inorder traversal of any valid BST visits values in **strictly non-decreasing sorted order**.
2. **BST Validation**: Verify whether `A[i] < A[i+1]` holds across consecutive visited nodes.
3. **K-th Smallest / Largest Element**: Early-exit inorder traversal retrieves the $K$-th sorted element in $\mathcal{O}(H + K)$ time.

---

## 7. Postorder Traversal: `Left -> Right -> Root`

In **Postorder Traversal**, both child subtrees are visited **before** the parent node:

```
            ( 1 )
           /     \
         ( 2 )   ( 3 )
         /   \
       ( 4 ) ( 5 )

Postorder Visit Sequence: 4 -> 5 -> 2 -> 3 -> 1
```

### Production Use Cases:
1. **Abstract Syntax Tree (AST) Expression Evaluation**: Leaf operands must be evaluated before applying the parent operator.
2. **Bottom-Up Metrics Calculation**: Computing tree height, diameter, or subtree size requires children results first.
3. **Directory Disk Usage (`du -sh`)**: Total directory size equals sum of child file sizes plus metadata.
4. **Memory Deallocation / Destructors**: In languages with manual memory management (C/C++), child nodes must be freed before freeing the parent node.

---

## 8. Iterative Traversals Using Explicit Stacks

When a tree is deep ($H > 1{,}000$), recursion triggers `RecursionError` in CPython. In production, we convert recursion into iterative loops using an explicit heap stack:

### Iterative Preorder (`Root -> Left -> Right`):
```python
def preorder_iterative(root: Optional[TreeNode]) -> list[int]:
    if not root:
        return []
    result = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node.val)
        # Push RIGHT first so that LEFT is popped and processed first!
        if node.right:
            stack.append(node.right)
        if node.left:
            stack.append(node.left)
    return result
```

### Iterative Inorder (`Left -> Root -> Right`):
```python
def inorder_iterative(root: Optional[TreeNode]) -> list[int]:
    result = []
    stack = []
    curr = root
    while curr or stack:
        # Push all left nodes to stack
        while curr:
            stack.append(curr)
            curr = curr.left
        curr = stack.pop()
        result.append(curr.val)
        curr = curr.right
    return result
```

### Iterative Postorder (`Left -> Right -> Root`):
```python
def postorder_iterative(root: Optional[TreeNode]) -> list[int]:
    if not root:
        return []
    # Trick: Root -> Right -> Left, then reverse result!
    stack = [root]
    traversal = []
    while stack:
        node = stack.pop()
        traversal.append(node.val)
        if node.left:
            stack.append(node.left)
        if node.right:
            stack.append(node.right)
    return traversal[::-1]
```

---

## 9. Morris Traversal: $\mathcal{O}(N)$ Time & $\mathcal{O}(1)$ Auxiliary Space

Can we traverse a binary tree without a call stack and without an explicit heap stack?

**Morris Traversal** uses **Threaded Binary Trees**:
1. If `curr.left` is `None`, visit `curr` and move to `curr.right`.
2. If `curr.left` exists, find the **Inorder Predecessor** (rightmost node of the left subtree).
   - If `predecessor.right` is `None`: establish temporary thread `predecessor.right = curr` and move `curr = curr.left`.
   - If `predecessor.right == curr`: thread already exists! Sever the thread `predecessor.right = None`, visit `curr`, and move `curr = curr.right`.

Runs in strictly $\mathcal{O}(N)$ time and **$\mathcal{O}(1)$ auxiliary memory**!

---

## 10. Reconstructing Binary Trees from Traversals

A single traversal sequence cannot uniquely identify a binary tree because it does not record boundary shapes:

```
Tree A:   1             Tree B:     1
           \                       /
            2                     2
Both have Preorder: [1, 2]!
```

### Uniqueness Theorem:
A binary tree with unique values can be **uniquely reconstructed** if and only if we are given:
1. **Inorder + Preorder**, OR
2. **Inorder + Postorder**

- **Why Inorder is Required**: The root is identified by the first element of Preorder (or last element of Postorder). Inorder then splits the remaining elements into the **Left Subtree** and **Right Subtree**.

```python
def build_tree(preorder: list[int], inorder: list[int]) -> Optional[TreeNode]:
    inorder_index_map = {val: idx for idx, val in enumerate(inorder)}
    pre_iter = iter(preorder)

    def helper(left_in: int, right_in: int) -> Optional[TreeNode]:
        if left_in > right_in:
            return None
        root_val = next(pre_iter)
        root = TreeNode(root_val)
        mid = inorder_index_map[root_val]

        # Left subtree must be constructed before right subtree!
        root.left = helper(left_in, mid - 1)
        root.right = helper(mid + 1, right_in)
        return root

    return helper(0, len(inorder) - 1)
```

---

## 11. Subtree Aggregations & Divide-and-Conquer

### Maximum Depth of Binary Tree:
$$\text{Depth}(u) = 1 + \max(\text{Depth}(u.\text{left}), \text{Depth}(u.\text{right}))$$

### Diameter of Binary Tree:
The longest path between any two nodes in a tree (may or may not pass through the root):
$$\text{Longest Path Passing Through } u = \text{Depth}(u.\text{left}) + \text{Depth}(u.\text{right})$$
$$\text{Global Diameter} = \max_{u \in V}(\text{Longest Path Through } u)$$

---

## 12. Lowest Common Ancestor (LCA)

The Lowest Common Ancestor of nodes $p$ and $q$ is the deepest node that has both $p$ and $q$ as descendants.

```python
def lowest_common_ancestor(root: Optional[TreeNode], p: TreeNode, q: TreeNode) -> Optional[TreeNode]:
    if not root or root == p or root == q:
        return root

    left = lowest_common_ancestor(root.left, p, q)
    right = lowest_common_ancestor(root.right, p, q)

    # If both left and right return non-null, root is the LCA!
    if left and right:
        return root
    return left if left else right
```

---

## 13. Production Architecture: AST Query Compilation Engine

In production API gateways and ORM query builders (e.g. Django Q objects, SQLAlchemy expressions), client filter queries are parsed into an Abstract Syntax Tree (AST):

```
Query: (status == 200 AND response_time < 300) OR tenant == "enterprise"
```

A Postorder Bottom-Up recursive engine evaluates:
1. Leaves: resolve predicates against incoming HTTP request telemetry.
2. Binary operators (`AND`, `OR`): compute boolean operations over child results.
3. Root: returns boolean routing decision in sub-microsecond time.

---

## 14. 7 Golden Invariant Rules for Binary Tree Engineering

1. **Always Handle `root is None` First**: The base case of any tree traversal must guard against null references.
2. **Use `__slots__` on Tree Nodes**: Reduces memory allocation by 68% in high-volume graph/tree indexes.
3. **Never Recompute Subtree Depths**: Avoid $\mathcal{O}(N^2)$ antipatterns by combining depth calculation and validation in a single bottom-up pass.
4. **Mind the CPython Recursion Limit**: If tree height can exceed 1,000, use iterative stack traversal or `sys.setrecursionlimit()`.
5. **Preorder Left-First Stack Ordering**: In iterative preorder, push `right` child first so that `left` child is on top of the stack.
6. **Inorder Index Map for Reconstructions**: Always precompute `{val: idx}` hash map to avoid $\mathcal{O}(N)$ linear slicing inside tree builders.
7. **Preserve Subtree Separation**: Sever temporary threads in Morris traversal to avoid corrupting tree invariants.

---

## 15. Complexity Reference Matrix

| Algorithm / Traversal | Time Complexity | Auxiliary Space (Balanced) | Auxiliary Space (Skewed) | Key Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Recursive Preorder** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Call stack `Root -> Left -> Right` |
| **Recursive Inorder** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Call stack `Left -> Root -> Right` |
| **Recursive Postorder** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Call stack `Left -> Right -> Root` |
| **Iterative Inorder** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Explicit LIFO stack with leftmost dive |
| **Morris Traversal** | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Threaded Binary Tree pointers |
| **Build Tree (Pre + In)**| $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Hash map + recursive partitioning |
| **Max Depth / Diameter** | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Postorder bottom-up aggregation |
| **Lowest Common Ancestor**| $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ | Postorder divide-and-conquer |
