"""
Day 23: Example 01 - Core Binary Tree Traversals, Reconstruction & Stack Mechanics

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In compiler construction, SQL query optimizers (PostgreSQL, SQLite), and ML decision
tree evaluation (XGBoost, Random Forests), trees represent hierarchical execution plans.

Traversing trees requires understanding the call stack mechanics:
- Preorder (Root -> Left -> Right): Used for tree serialization, cloning, and prefix syntax trees.
- Inorder (Left -> Root -> Right): Retrieves sorted data from Binary Search Trees (BSTs).
- Postorder (Left -> Right -> Root): Used for bottom-up AST expression evaluation, computing
  subtree metrics (depth, diameter, size), and memory deallocation.

Furthermore, on deep or skewed trees (H > 1,000), recursive traversals crash with CPython's
`RecursionError`. Production engines convert recursive traversals to iterative heap-allocated
stacks to guarantee memory safety.

This module implements:
1. `TreeNode`: Compact tree node with `__slots__`.
2. Recursive Traversals: Preorder, Inorder, Postorder.
3. Iterative Traversals: Preorder, Inorder, Postorder using explicit LIFO stacks.
4. Morris Traversal: O(N) time and O(1) auxiliary space traversal using threaded binary trees.
5. `build_tree`: O(N) tree reconstruction from Preorder and Inorder traversal arrays.
6. `max_depth` & `tree_diameter`: Postorder bottom-up subtree aggregations.
7. Performance benchmark: Call-stack recursion vs explicit heap-allocated stack on 10,000 nodes.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Dict, Iterator, List, Optional, Tuple


# ============================================================================
# 1. COMPACT TREENODE DEFINITION USING __SLOTS__
# ============================================================================

class TreeNode:
    """
    Binary tree node optimized for memory density using __slots__.
    Eliminates instance __dict__ to drop per-node overhead from ~152 bytes to ~48 bytes.
    """
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

    def __repr__(self) -> str:
        return f"TreeNode({self.val})"


# Utility to build tree from level-order list (LeetCode format: [1, 2, 3, None, 4])
def build_tree_from_list(values: List[Optional[int]]) -> Optional[TreeNode]:
    """Constructs a binary tree from a level-order serialized array."""
    if not values or values[0] is None:
        return None

    root = TreeNode(values[0])
    queue: deque[TreeNode] = deque([root])
    i = 1
    n = len(values)

    while queue and i < n:
        curr = queue.popleft()

        # Left child
        if i < n and values[i] is not None:
            curr.left = TreeNode(values[i])  # type: ignore
            queue.append(curr.left)
        i += 1

        # Right child
        if i < n and values[i] is not None:
            curr.right = TreeNode(values[i])  # type: ignore
            queue.append(curr.right)
        i += 1

    return root


# ============================================================================
# 2. RECURSIVE TRAVERSALS (PREORDER, INORDER, POSTORDER)
# ============================================================================

def preorder_recursive(root: Optional[TreeNode]) -> List[int]:
    """Preorder: Root -> Left -> Right"""
    result: List[int] = []

    def traverse(node: Optional[TreeNode]) -> None:
        if not node:
            return
        result.append(node.val)
        traverse(node.left)
        traverse(node.right)

    traverse(root)
    return result


def inorder_recursive(root: Optional[TreeNode]) -> List[int]:
    """Inorder: Left -> Root -> Right"""
    result: List[int] = []

    def traverse(node: Optional[TreeNode]) -> None:
        if not node:
            return
        traverse(node.left)
        result.append(node.val)
        traverse(node.right)

    traverse(root)
    return result


def postorder_recursive(root: Optional[TreeNode]) -> List[int]:
    """Postorder: Left -> Right -> Root"""
    result: List[int] = []

    def traverse(node: Optional[TreeNode]) -> None:
        if not node:
            return
        traverse(node.left)
        traverse(node.right)
        result.append(node.val)

    traverse(root)
    return result


# ============================================================================
# 3. ITERATIVE TRAVERSALS USING EXPLICIT HEAP STACKS
# ============================================================================

def preorder_iterative(root: Optional[TreeNode]) -> List[int]:
    """
    Iterative Preorder using an explicit LIFO stack.
    
    WHY RIGHT CHILD PUSHED FIRST:
    A stack is LIFO (Last-In, First-Out). Pushing node.right before node.left
    ensures that node.left is popped and evaluated before node.right.
    """
    if not root:
        return []

    result: List[int] = []
    stack: List[TreeNode] = [root]

    while stack:
        curr = stack.pop()
        result.append(curr.val)

        if curr.right:
            stack.append(curr.right)
        if curr.left:
            stack.append(curr.left)

    return result


def inorder_iterative(root: Optional[TreeNode]) -> List[int]:
    """
    Iterative Inorder using an explicit LIFO stack.
    
    INVARIANT:
    Traverse as far left as possible, pushing every node onto the stack.
    When a None is encountered, pop the top node (the leftmost available),
    record its value, and step to its right child.
    """
    result: List[int] = []
    stack: List[TreeNode] = []
    curr: Optional[TreeNode] = root

    while curr or stack:
        while curr:
            stack.append(curr)
            curr = curr.left

        curr = stack.pop()
        result.append(curr.val)
        curr = curr.right

    return result


def postorder_iterative(root: Optional[TreeNode]) -> List[int]:
    """
    Iterative Postorder using modified reverse traversal:
    Process Root -> Right -> Left, then reverse the collected result array.
    """
    if not root:
        return []

    traversal: List[int] = []
    stack: List[TreeNode] = [root]

    while stack:
        curr = stack.pop()
        traversal.append(curr.val)

        # Push left first so right is popped first (Root -> Right -> Left)
        if curr.left:
            stack.append(curr.left)
        if curr.right:
            stack.append(curr.right)

    # Reversing Root -> Right -> Left gives Left -> Right -> Root!
    return traversal[::-1]


# ============================================================================
# 4. MORRIS INORDER TRAVERSAL (O(N) TIME, O(1) AUXILIARY SPACE)
# ============================================================================

def morris_inorder_traversal(root: Optional[TreeNode]) -> List[int]:
    """
    Traverses binary tree in O(N) time and O(1) auxiliary space using Threaded Binary Trees.
    
    MECHANISM:
    Temporarily uses the null right pointer of the inorder predecessor to point back
    to the current node, eliminating both call stack and heap stack memory.
    Severing threads restores the original tree structure.
    """
    result: List[int] = []
    curr: Optional[TreeNode] = root

    while curr:
        if curr.left is None:
            # No left subtree: visit curr and step right
            result.append(curr.val)
            curr = curr.right
        else:
            # Find inorder predecessor (rightmost node in left subtree)
            pred = curr.left
            while pred.right and pred.right is not curr:
                pred = pred.right

            if pred.right is None:
                # Establish temporary thread back to curr
                pred.right = curr
                curr = curr.left
            else:
                # Thread already exists: sever thread, visit curr, step right
                pred.right = None
                result.append(curr.val)
                curr = curr.right

    return result


# ============================================================================
# 5. RECONSTRUCT TREE FROM PREORDER & INORDER (O(N) TIME)
# ============================================================================

def build_tree_preorder_inorder(preorder: List[int], inorder: List[int]) -> Optional[TreeNode]:
    """
    Reconstructs a unique binary tree from its preorder and inorder traversals.
    
    WHY PRECOMPUTING INORDER INDICES MATTERS:
    Using `inorder.index(val)` inside recursion triggers an O(N) scan at every node,
    resulting in quadratic O(N^2) complexity. Precomputing a hash map `{val: idx}`
    achieves strictly optimal O(N) construction.
    """
    inorder_map: Dict[int, int] = {val: idx for idx, val in enumerate(inorder)}
    pre_iter: Iterator[int] = iter(preorder)

    def helper(in_left: int, in_right: int) -> Optional[TreeNode]:
        if in_left > in_right:
            return None

        root_val = next(pre_iter)
        root = TreeNode(root_val)
        mid_idx = inorder_map[root_val]

        # Left subtree must be built before right subtree to match preorder sequence!
        root.left = helper(in_left, mid_idx - 1)
        root.right = helper(mid_idx + 1, in_right)
        return root

    return helper(0, len(inorder) - 1)


# ============================================================================
# 6. SUBTREE AGGREGATIONS: DEPTH & DIAMETER
# ============================================================================

def max_depth(root: Optional[TreeNode]) -> int:
    """Computes maximum depth of a binary tree in O(N) time and O(H) space."""
    if not root:
        return 0
    return 1 + max(max_depth(root.left), max_depth(root.right))


def tree_diameter(root: Optional[TreeNode]) -> int:
    """
    Computes the diameter (longest path between any two nodes in terms of edges).
    
    BOTTOM-UP POSTORDER PATTERN:
    At each node, the longest path passing through that node is depth(left) + depth(right).
    We track the global maximum diameter while returning the depth up the call stack.
    """
    max_diam = 0

    def get_depth(node: Optional[TreeNode]) -> int:
        nonlocal max_diam
        if not node:
            return 0

        left_d = get_depth(node.left)
        right_d = get_depth(node.right)

        # Path through this node in terms of edges
        max_diam = max(max_diam, left_d + right_d)

        return 1 + max(left_d, right_d)

    get_depth(root)
    return max_diam


# ============================================================================
# 7. VERIFICATION SUITE & PERFORMANCE BENCHMARK
# ============================================================================

def run_tests() -> None:
    """Validates traversals, reconstruction, and tree properties."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR BINARY TREE ALGORITHMS")
    print("======================================================================")

    # Construct test tree:
    #         1
    #       /   \
    #      2     3
    #     / \
    #    4   5
    tree = build_tree_from_list([1, 2, 3, 4, 5])

    # 1. Preorder: 1 -> 2 -> 4 -> 5 -> 3
    assert preorder_recursive(tree) == [1, 2, 4, 5, 3]
    assert preorder_iterative(tree) == [1, 2, 4, 5, 3]
    print("[PASS] Preorder (Recursive & Iterative) verified.")

    # 2. Inorder: 4 -> 2 -> 5 -> 1 -> 3
    assert inorder_recursive(tree) == [4, 2, 5, 1, 3]
    assert inorder_iterative(tree) == [4, 2, 5, 1, 3]
    assert morris_inorder_traversal(tree) == [4, 2, 5, 1, 3]
    print("[PASS] Inorder (Recursive, Iterative & Morris) verified.")

    # 3. Postorder: 4 -> 5 -> 2 -> 3 -> 1
    assert postorder_recursive(tree) == [4, 5, 2, 3, 1]
    assert postorder_iterative(tree) == [4, 5, 2, 3, 1]
    print("[PASS] Postorder (Recursive & Iterative) verified.")

    # 4. Tree Reconstruction
    pre_arr = [3, 9, 20, 15, 7]
    in_arr = [9, 3, 15, 20, 7]
    reconstructed = build_tree_preorder_inorder(pre_arr, in_arr)
    assert preorder_iterative(reconstructed) == pre_arr
    assert inorder_iterative(reconstructed) == in_arr
    print("[PASS] Tree Reconstruction from Preorder & Inorder verified.")

    # 5. Depth and Diameter
    assert max_depth(tree) == 3
    assert tree_diameter(tree) == 3  # Path: 4 -> 2 -> 1 -> 3 (3 edges)
    print("[PASS] Maximum Depth and Tree Diameter verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks recursion vs explicit stack on deep skewed trees."""
    print("======================================================================")
    print("BENCHMARK: CALL-STACK RECURSION VS EXPLICIT HEAP STACK ON DEEP TREES")
    print("======================================================================")

    # Build a deep skewed tree of 5,000 nodes (1 -> 2 -> 3 -> ... -> 5000)
    tree_depth = 5_000
    root = TreeNode(0)
    curr = root
    for i in range(1, tree_depth):
        curr.right = TreeNode(i)
        curr = curr.right

    print(f"Constructed deep degenerate tree with {tree_depth:,} levels.")

    # Increase recursion limit safely for the test
    original_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(tree_depth + 500)

    try:
        # Benchmark Recursive Inorder
        start_rec = time.perf_counter()
        res_rec = inorder_recursive(root)
        elapsed_rec = time.perf_counter() - start_rec
        print(f"Recursive Inorder (Call Stack):     {elapsed_rec * 1000:8.2f} ms")

        # Benchmark Iterative Inorder
        start_iter = time.perf_counter()
        res_iter = inorder_iterative(root)
        elapsed_iter = time.perf_counter() - start_iter
        print(f"Iterative Inorder (Explicit Stack): {elapsed_iter * 1000:8.2f} ms")

        # Benchmark Morris Inorder (O(1) Space)
        start_morris = time.perf_counter()
        res_morris = morris_inorder_traversal(root)
        elapsed_morris = time.perf_counter() - start_morris
        print(f"Morris Inorder (O(1) Space):        {elapsed_morris * 1000:8.2f} ms")

        assert res_rec == res_iter == res_morris
        print("\n[PASS] All traversal outputs match identically across 5,000 deep nodes!")
    finally:
        sys.setrecursionlimit(original_limit)

    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
