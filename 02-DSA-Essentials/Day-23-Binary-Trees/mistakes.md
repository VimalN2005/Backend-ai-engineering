# Day 23: 7 Critical Antipatterns in Binary Tree Engineering

Binary trees are the cornerstone of hierarchical systems, AST compilers, and database indexes. However, subtle bugs in recursive termination, call stack limits, and traversal orders frequently cause stack overflow crashes, quadratic performance degradation, and corrupted data structures.

Below are 7 critical antipatterns, their low-level runtime consequences, and enterprise remediations.

---

## 1. Omitting the Base Case for `root is None`

### The Mistake
Assuming a node's children are always non-null before recursing, or forgetting the `None` base guard:

```python
# WRONG: Crashes with AttributeError on leaves or empty trees!
def sum_tree_broken(root: Optional[TreeNode]) -> int:
    # BUG: If root is None, accessing root.val raises AttributeError!
    if not root.left and not root.right:
        return root.val
    return root.val + sum_tree_broken(root.left) + sum_tree_broken(root.right)
```

### The Consequence
When `root.left` is `None` but `root.right` is populated, calling `sum_tree_broken(root.left)` passes `None` into the next frame. The next frame attempts `if not root.left`, immediately raising `AttributeError: 'NoneType' object has no attribute 'left'`, crashing the service.

### The Correct Approach
Always handle `root is None` as the very first line of any recursive tree algorithm:

```python
# CORRECT: Universal base case guard
def sum_tree_correct(root: Optional[TreeNode]) -> int:
    if root is None:
        return 0
    return root.val + sum_tree_correct(root.left) + sum_tree_correct(root.right)
```

---

## 2. Unhandled Stack Overflow on Degenerate (Skewed) Trees

### The Mistake
Assuming all binary trees are balanced with $\mathcal{O}(\log N)$ depth:

```python
# FLAWED ASSUMPTION: Believing call stack depth is always small
def traverse_naive(root: Optional[TreeNode]) -> list[int]:
    if not root:
        return []
    return traverse_naive(root.left) + [root.val] + traverse_naive(root.right)
```

### The Consequence
If items were inserted into a binary tree in sorted order without balancing (e.g. `1 -> 2 -> 3 -> ... -> 2000`), the tree collapses into a degenerate linked list with height $H = N$. When $N \ge 1{,}000$, CPython's default recursion limit is exceeded, raising:
`RecursionError: maximum recursion depth exceeded while calling a Python object`.

### The Correct Approach
For deep or unbounded trees, use **Iterative Traversal with an Explicit Heap-Allocated Stack**:

```python
# CORRECT: Explicit stack scales safely to millions of nodes
def inorder_iterative(root: Optional[TreeNode]) -> list[int]:
    result = []
    stack = []
    curr = root
    while curr or stack:
        while curr:
            stack.append(curr)
            curr = curr.left
        curr = stack.pop()
        result.append(curr.val)
        curr = curr.right
    return result
```

---

## 3. Redundant Subtree Traversals Causing Quadratic $\mathcal{O}(N^2)$ Complexity

### The Mistake
Calling a depth-calculating helper function repeatedly inside a recursive predicate:

```python
# WRONG: Catastrophic O(N^2) complexity!
def is_balanced_naive(root: Optional[TreeNode]) -> bool:
    if not root:
        return True
    
    # max_depth() traverses the ENTIRE left and right subtrees (O(N) work!)
    left_h = max_depth(root.left)
    right_h = max_depth(root.right)

    if abs(left_h - right_h) > 1:
        return False

    # Recursively calls is_balanced on children, repeating depth scans!
    return is_balanced_naive(root.left) and is_balanced_naive(root.right)
```

### The Consequence
For each of the $N$ nodes, `max_depth()` traverses down to the leaves. On a balanced tree, this does $\sum \mathcal{O}(\text{size of subtree}) = \mathcal{O}(N \log N)$ work; on a skewed tree, it degrades to $\mathcal{O}(N^2)$. For $N = 100{,}000$, this requires $10{,}000{,}000{,}000$ operations, freezing the backend.

### The Correct Approach
Use **Bottom-Up Postorder Traversal with Sentinel Return**: compute depth and balance simultaneously. Return `-1` immediately if an unbalanced subtree is discovered:

```python
# CORRECT: Guaranteed strict O(N) single-pass bottom-up validation
def is_balanced_optimal(root: Optional[TreeNode]) -> bool:
    def check_depth(node: Optional[TreeNode]) -> int:
        if not node:
            return 0
        left = check_depth(node.left)
        if left == -1:
            return -1  # Propagate unbalance early
        right = check_depth(node.right)
        if right == -1:
            return -1  # Propagate unbalance early

        if abs(left - right) > 1:
            return -1  # Current node is unbalanced!

        return 1 + max(left, right)

    return check_depth(root) != -1
```

---

## 4. Reversing Push Order in Iterative Preorder Traversal

### The Mistake
Pushing the left child onto the explicit stack before the right child:

```python
# WRONG: Produces Root -> Right -> Left instead of Root -> Left -> Right!
def preorder_broken(root: Optional[TreeNode]) -> list[int]:
    if not root:
        return []
    stack = [root]
    result = []
    while stack:
        node = stack.pop()
        result.append(node.val)
        # BUG: Pushing left first means right is at the top of the LIFO stack!
        if node.left:
            stack.append(node.left)
        if node.right:
            stack.append(node.right)
    return result
```

### The Consequence
Because stacks are Last-In, First-Out (LIFO), whatever is pushed *last* is popped *first*. Pushing `left` and then `right` causes `right` to be popped and processed before `left`, producing the inverted sequence `Root -> Right -> Left`.

### The Correct Approach
Always push `right` **first**, so that `left` is on top of the stack and gets popped first:

```python
# CORRECT: Push right first so left is popped first
if node.right:
    stack.append(node.right)
if node.left:
    stack.append(node.left)
```

---

## 5. Using `inorder.index(val)` Inside Recursive Tree Reconstruction

### The Mistake
Searching for the root index in the inorder array using linear scan inside recursive partition steps:

```python
# WRONG: O(N) list.index() inside recursion triggers O(N^2) total runtime!
def build_tree_slow(preorder: list[int], inorder: list[int]) -> Optional[TreeNode]:
    if not preorder or not inorder:
        return None
    root_val = preorder[0]
    root = TreeNode(root_val)
    
    # BUG: inorder.index() scans up to N elements linearly!
    mid = inorder.index(root_val)

    root.left = build_tree_slow(preorder[1:mid+1], inorder[:mid])
    root.right = build_tree_slow(preorder[mid+1:], inorder[mid+1:])
    return root
```

### The Consequence
For $N = 50{,}000$ nodes, `inorder.index()` and list slicing `preorder[1:mid+1]` create copies and scan memory on every recursive level, taking 10+ seconds and allocating gigabytes of intermediate memory.

### The Correct Approach
Precompute an in-memory hash map `{val: index}` in $\mathcal{O}(N)$ once before recursion, and pass boundary index pointers instead of slicing arrays:

```python
# CORRECT: O(1) hash map lookups achieve strict O(N) total construction
def build_tree_optimal(preorder: list[int], inorder: list[int]) -> Optional[TreeNode]:
    inorder_map = {val: idx for idx, val in enumerate(inorder)}
    pre_iter = iter(preorder)

    def helper(left: int, right: int) -> Optional[TreeNode]:
        if left > right:
            return None
        val = next(pre_iter)
        node = TreeNode(val)
        mid = inorder_map[val]
        node.left = helper(left, mid - 1)
        node.right = helper(mid + 1, right)
        return node

    return helper(0, len(inorder) - 1)
```

---

## 6. Corrupting Tree Invariants via Unsevered Morris Threads

### The Mistake
Failing to sever temporary right predecessor pointers in Morris Traversal:

```python
# WRONG: Forgetting pred.right = None leaves cyclic graph!
if pred.right is None:
    pred.right = curr
    curr = curr.left
else:
    # BUG: Forgot to sever pred.right = None!
    result.append(curr.val)
    curr = curr.right
```

### The Consequence
The temporary thread pointing from the predecessor back to `curr` remains intact. The binary tree is now permanently corrupted into a cyclic directed graph. Any subsequent standard traversal or serializer enters an infinite loop, freezing the service.

### The Correct Approach
Always restore tree topology by resetting `pred.right = None`:

```python
# CORRECT: Sever thread before moving to right child
pred.right = None
result.append(curr.val)
curr = curr.right
```

---

## 7. Memory Overhead from Omitting `__slots__` on Tree Nodes

### The Mistake
Defining tree nodes without `__slots__` in high-volume graph or tree indexes:

```python
# WRONG: ~152 bytes per node due to dynamic __dict__
class TreeNodeNaive:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None
```

### The Consequence
In AI decision forests, AST compilers, or database indexes with 5,000,000 nodes, unoptimized instances consume ~760 MB of RAM compared to ~240 MB for `__slots__` nodes. The extra ~520 MB of memory triggers heavy garbage collection overhead and cache misses.

### The Correct Approach
Always specify `__slots__` for node models:

```python
# CORRECT: ~48 bytes per node (68% memory savings)
class TreeNode:
    __slots__ = ("val", "left", "right")

    def __init__(self, val: int, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
```
