# Day 23: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Binary Trees, Traversal Orders, AST Evaluation, and Call Stack Mechanics for backend engineers.

---

## 1. Traversal Order Decision Matrix

| Traversal | Sequence Order | Primary Intuition | Production Backend Use Cases |
| :--- | :--- | :--- | :--- |
| **Preorder** | `Root -> Left -> Right` | Process parent before children | Tree serialization, cloning/copying, directory listings (`tree`) |
| **Inorder** | `Left -> Root -> Right` | Non-decreasing sorted order for BST | BST validation, sorted iteration, finding $K$-th smallest element |
| **Postorder** | `Left -> Right -> Root` | Process children before parent | AST expression evaluation, tree height/diameter, memory deallocation |

---

## 2. Essential Code Templates

### Template A: Iterative Inorder Traversal ($\mathcal{O}(N)$ Time, Explicit Stack)
```python
def inorder_iterative(root: Optional[TreeNode]) -> list[int]:
    result = []
    stack = []
    curr = root

    while curr or stack:
        # Dive leftward
        while curr:
            stack.append(curr)
            curr = curr.left
        curr = stack.pop()
        result.append(curr.val)
        curr = curr.right

    return result
```

### Template B: Reconstruct Binary Tree from Preorder & Inorder ($\mathcal{O}(N)$ Time)
```python
def build_tree(preorder: list[int], inorder: list[int]) -> Optional[TreeNode]:
    inorder_map = {val: idx for idx, val in enumerate(inorder)}
    pre_iter = iter(preorder)

    def helper(in_left: int, in_right: int) -> Optional[TreeNode]:
        if in_left > in_right:
            return None
        val = next(pre_iter)
        node = TreeNode(val)
        mid = inorder_map[val]
        # Left subtree must precede right to match preorder consumption!
        node.left = helper(in_left, mid - 1)
        node.right = helper(mid + 1, in_right)
        return node

    return helper(0, len(inorder) - 1)
```

### Template C: Lowest Common Ancestor (LCA)
```python
def lowest_common_ancestor(root: Optional[TreeNode], p: TreeNode, q: TreeNode) -> Optional[TreeNode]:
    if not root or root == p or root == q:
        return root

    left = lowest_common_ancestor(root.left, p, q)
    right = lowest_common_ancestor(root.right, p, q)

    if left and right:
        return root  # LCA split point
    return left if left else right
```

---

## 3. The Reversal Invariant for Iterative Postorder

```
Preorder:         Root -> Left  -> Right
Reversed Push:    Root -> Right -> Left
Reverse Output:   Left -> Right -> Root  <-- Postorder!
```

---

## 4. 5 Non-Negotiable Binary Tree Rules

1. **Always Handle `root is None` First**: Guard against `AttributeError` by making `if not root: return ...` the first check in recursion.
2. **Use `__slots__ = ("val", "left", "right")`**: Cuts node memory from ~152 bytes to ~48 bytes (68% savings).
3. **Never Recompute Heights Inside Recursion**: Combine depth checks and validation in a single bottom-up postorder pass to avoid $\mathcal{O}(N^2)$ degradation.
4. **Precompute Inorder Indices for Reconstructions**: Always build a `{val: index}` hash map to keep tree construction strictly $\mathcal{O}(N)$.
5. **Path Bifurcation Invariant**: When computing maximum path sums, update the global maximum with both branches, but return only **one** branch to the parent.

---

## 5. Complexity Quick Reference Table

| Algorithm | Best Time | Worst Time | Auxiliary Space (Balanced) | Auxiliary Space (Skewed) |
| :--- | :--- | :--- | :--- | :--- |
| **Recursive Traversals** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ (Call stack) |
| **Iterative Traversals** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ (Heap stack) |
| **Morris Traversal** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| **Reconstruct (Pre + In)** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **Max Depth / Diameter** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ |
| **LCA in Binary Tree** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ |
| **Max Path Sum (LC 124)** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | $\mathcal{O}(\log N)$ | $\mathcal{O}(N)$ |
