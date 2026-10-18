"""
Day 24: Example 01 - Core Tree BFS & DFS Algorithms, Prefix Subpaths & Serialization

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In distributed backend architectures, trees represent:
1. Workflow Execution DAGs (Airflow, Temporal): BFS executes tasks level-by-level in parallel
   worker pools, ensuring all parent dependencies are fulfilled before downstream tasks trigger.
2. Authorization and Security Scopes: DFS paths determine whether a client holds sufficient
   hierarchical privileges or satisfies compliance constraints.
3. Microservice Network Proximity: Radial BFS determines blast radius and finds all service
   mesh nodes within K network hops.

Crucially, in BFS traversals, using `list.pop(0)` causes an O(N) memory shift per node,
freezing backend servers on large trees. Using `collections.deque.popleft()` maintains strict
O(1) time complexity, yielding thousands-fold speedups.

This module implements:
1. `level_order_traversal`: Level-by-level batch processing using queue snapshotting.
2. `zigzag_level_order`: Alternating spiral level order traversal.
3. `right_side_view`: Perspective visibility using both BFS and DFS.
4. `path_sum_subpaths`: O(N) arbitrary subpath sum counter using prefix sum hash maps + backtracking.
5. `distance_k_nodes`: Radial BFS finding all nodes at distance K via parent pointer mapping.
6. `serialize_bfs` & `deserialize_bfs`: Lossless level-order serialization and reconstruction.
7. High-volume benchmark: `collections.deque.popleft()` vs `list.pop(0)` on 50,000 nodes.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. COMPACT TREENODE DEFINITION USING __SLOTS__
# ============================================================================

class TreeNode:
    """
    Binary tree node optimized for minimal memory footprint using __slots__.
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
# 2. LEVEL ORDER & ZIGZAG BFS TRAVERSAL
# ============================================================================

def level_order_traversal(root: Optional[TreeNode]) -> List[List[int]]:
    """
    Performs level-by-level batch traversal using FIFO queue snapshotting.
    
    Time Complexity:  O(N) - Every node is enqueued and dequeued once.
    Space Complexity: O(W) where W is the maximum tree width (up to N / 2 nodes).
    """
    if not root:
        return []

    result: List[List[int]] = []
    queue: deque[TreeNode] = deque([root])

    while queue:
        level_size = len(queue)  # Snapshot current level's node count
        current_level: List[int] = []

        for _ in range(level_size):
            node = queue.popleft()
            current_level.append(node.val)

            if node.left:
                queue.append(node.left)
            if node.right:
                queue.append(node.right)

        result.append(current_level)

    return result


def zigzag_level_order(root: Optional[TreeNode]) -> List[List[int]]:
    """
    Traverses tree in alternating spiral order (Left -> Right, then Right -> Left).
    """
    if not root:
        return []

    result: List[List[int]] = []
    queue: deque[TreeNode] = deque([root])
    left_to_right = True

    while queue:
        level_size = len(queue)
        level_nodes: deque[int] = deque()

        for _ in range(level_size):
            node = queue.popleft()
            if left_to_right:
                level_nodes.append(node.val)
            else:
                level_nodes.appendleft(node.val)

            if node.left:
                queue.append(node.left)
            if node.right:
                queue.append(node.right)

        result.append(list(level_nodes))
        left_to_right = not left_to_right

    return result


# ============================================================================
# 3. RIGHT SIDE VIEW (BFS & DFS PERSPECTIVES)
# ============================================================================

def right_side_view_bfs(root: Optional[TreeNode]) -> List[int]:
    """
    Returns values visible when viewing tree from the right side using BFS.
    Invariant: The last element in each level batch is the rightmost visible node.
    """
    if not root:
        return []

    result: List[int] = []
    queue: deque[TreeNode] = deque([root])

    while queue:
        level_size = len(queue)
        for i in range(level_size):
            node = queue.popleft()
            # If it's the last element of the level, record it
            if i == level_size - 1:
                result.append(node.val)

            if node.left:
                queue.append(node.left)
            if node.right:
                queue.append(node.right)

    return result


def right_side_view_dfs(root: Optional[TreeNode]) -> List[int]:
    """
    Returns right side view using DFS (Root -> Right -> Left).
    Memory is bounded by tree height O(H) rather than tree width O(W).
    """
    result: List[int] = []

    def dfs(node: Optional[TreeNode], depth: int) -> None:
        if not node:
            return
        if depth == len(result):
            result.append(node.val)
        dfs(node.right, depth + 1)  # Visit right branch first!
        dfs(node.left, depth + 1)

    dfs(root, 0)
    return result


# ============================================================================
# 4. PATH SUM III: LINEAR O(N) PREFIX SUM WITH BACKTRACKING
# ============================================================================

def path_sum_subpaths(root: Optional[TreeNode], target_sum: int) -> int:
    """
    Finds the number of downward paths in the tree whose sum equals target_sum.
    Paths do not need to start at the root or end at a leaf.
    
    WHY PREFIX SUM HASH MAP:
    Testing all subpaths starting at every node takes quadratic O(N^2) time.
    By maintaining a running prefix sum hash map {sum: count}, we check if an
    ancestor had sum (curr_sum - target_sum) in O(1) time, achieving linear O(N) runtime.
    
    CRITICAL INVARIANT:
    Must backtrack `prefix_map[curr_sum] -= 1` when returning up the call stack
    to avoid corrupting counts for sibling branches!
    """
    prefix_map: Dict[int, int] = defaultdict(int)
    prefix_map[0] = 1  # Base case: empty path
    total_paths = 0

    def dfs(node: Optional[TreeNode], curr_sum: int) -> None:
        nonlocal total_paths
        if not node:
            return

        curr_sum += node.val

        # Check for matching ancestor prefix sum
        total_paths += prefix_map[curr_sum - target_sum]

        # Register current prefix sum
        prefix_map[curr_sum] += 1

        # Recurse on children
        dfs(node.left, curr_sum)
        dfs(node.right, curr_sum)

        # Backtracking invariant: remove current sum before ascending
        prefix_map[curr_sum] -= 1

    dfs(root, 0)
    return total_paths


# ============================================================================
# 5. ALL NODES DISTANCE K (RADIAL BFS VIA GRAPH CONVERSION)
# ============================================================================

def distance_k_nodes(root: Optional[TreeNode], target_val: int, k: int) -> List[int]:
    """
    Finds all nodes located at exact radial distance K from the node with target_val.
    
    ALGORITHM:
    1. First pass: Build parent mapping {node: parent} to treat tree as an undirected graph.
    2. Locate target node pointer.
    3. Second pass: Radial multi-directional BFS starting from target node.
    """
    if not root:
        return []

    parent_map: Dict[TreeNode, Optional[TreeNode]] = {}
    target_node: Optional[TreeNode] = None

    # Step 1: Map parent pointers and find target node
    queue: deque[TreeNode] = deque([root])
    parent_map[root] = None

    while queue:
        curr = queue.popleft()
        if curr.val == target_val:
            target_node = curr

        if curr.left:
            parent_map[curr.left] = curr
            queue.append(curr.left)
        if curr.right:
            parent_map[curr.right] = curr
            queue.append(curr.right)

    if not target_node:
        return []

    # Step 2: Radial BFS from target_node
    bfs_queue: deque[Tuple[TreeNode, int]] = deque([(target_node, 0)])
    visited: Set[TreeNode] = {target_node}
    result: List[int] = []

    while bfs_queue:
        curr, dist = bfs_queue.popleft()

        if dist == k:
            result.append(curr.val)
            continue  # All remaining items at distance k will be collected
        elif dist > k:
            break

        # Explore 3 neighbors: left, right, and parent
        neighbors = [curr.left, curr.right, parent_map.get(curr)]
        for neighbor in neighbors:
            if neighbor and neighbor not in visited:
                visited.add(neighbor)
                bfs_queue.append((neighbor, dist + 1))

    return result


# ============================================================================
# 6. LOSSLESS LEVEL-ORDER SERIALIZATION & DESERIALIZATION
# ============================================================================

def serialize_bfs(root: Optional[TreeNode]) -> str:
    """
    Serializes a binary tree into a comma-delimited level-order string.
    Encodes null children as '#' tokens.
    """
    if not root:
        return ""

    tokens: List[str] = []
    queue: deque[Optional[TreeNode]] = deque([root])

    while queue:
        curr = queue.popleft()
        if curr:
            tokens.append(str(curr.val))
            queue.append(curr.left)
            queue.append(curr.right)
        else:
            tokens.append("#")

    # Trim trailing null tokens for compactness
    while tokens and tokens[-1] == "#":
        tokens.pop()

    return ",".join(tokens)


def deserialize_bfs(data: str) -> Optional[TreeNode]:
    """
    Reconstructs a binary tree from a level-order serialized string.
    """
    if not data:
        return None

    tokens = data.split(",")
    root = TreeNode(int(tokens[0]))
    queue: deque[TreeNode] = deque([root])
    idx = 1
    total_tokens = len(tokens)

    while queue and idx < total_tokens:
        curr = queue.popleft()

        # Reconstruct Left Child
        if idx < total_tokens and tokens[idx] != "#":
            curr.left = TreeNode(int(tokens[idx]))
            queue.append(curr.left)
        idx += 1

        # Reconstruct Right Child
        if idx < total_tokens and tokens[idx] != "#":
            curr.right = TreeNode(int(tokens[idx]))
            queue.append(curr.right)
        idx += 1

    return root


# ============================================================================
# 7. VERIFICATION SUITE & COMPREHENSIVE BENCHMARK
# ============================================================================

def run_tests() -> None:
    """Validates edge cases and standard fixtures across all algorithms."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR TREE BFS & DFS ALGORITHMS")
    print("======================================================================")

    # Construct test tree:
    #         3
    #       /   \
    #      9     20
    #           /  \
    #          15   7
    tree = build_tree_from_list([3, 9, 20, None, None, 15, 7])

    # 1. Level Order Traversal
    lo = level_order_traversal(tree)
    assert lo == [[3], [9, 20], [15, 7]], f"Unexpected level order: {lo}"
    print("[PASS] level_order_traversal verified.")

    # 2. Zigzag Level Order
    zz = zigzag_level_order(tree)
    assert zz == [[3], [20, 9], [15, 7]], f"Unexpected zigzag order: {zz}"
    print("[PASS] zigzag_level_order verified.")

    # 3. Right Side View
    assert right_side_view_bfs(tree) == [3, 20, 7]
    assert right_side_view_dfs(tree) == [3, 20, 7]
    print("[PASS] right_side_view (both BFS and DFS) verified.")

    # 4. Path Sum III (Prefix Sum Subpaths)
    # Tree: [10, 5, -3, 3, 2, None, 11, 3, -2, None, 1], target = 8
    # Paths: 5 -> 3, 5 -> 2 -> 1, -3 -> 11 (Total = 3 paths)
    ps_tree = build_tree_from_list([10, 5, -3, 3, 2, None, 11, 3, -2, None, 1])
    assert path_sum_subpaths(ps_tree, 8) == 3
    print("[PASS] path_sum_subpaths prefix sum backtracking verified.")

    # 5. All Nodes Distance K
    # Tree: [3, 5, 1, 6, 2, 0, 8, None, None, 7, 4], target = 5, K = 2
    # Nodes at distance 2 from 5: [7, 4, 1]
    dist_tree = build_tree_from_list([3, 5, 1, 6, 2, 0, 8, None, None, 7, 4])
    k_nodes = distance_k_nodes(dist_tree, 5, 2)
    assert sorted(k_nodes) == [1, 4, 7], f"Expected [1, 4, 7], got {k_nodes}"
    print("[PASS] distance_k_nodes radial BFS verified.")

    # 6. Serialization & Deserialization
    serialized = serialize_bfs(tree)
    reconstructed = deserialize_bfs(serialized)
    assert level_order_traversal(reconstructed) == [[3], [9, 20], [15, 7]]
    print("[PASS] serialize_bfs and deserialize_bfs verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Demonstrates O(N) collections.deque.popleft() vs O(N^2) list.pop(0) on BFS."""
    print("======================================================================")
    print("HIGH-VOLUME BENCHMARK: COLLECTIONS.DEQUE VS LIST.POP(0) ON 50,000 NODES")
    print("======================================================================")

    data_size = 50_000
    print(f"Dataset Size: {data_size:,} nodes in simulated wide BFS queue.")

    # Benchmark collections.deque.popleft() - Strict O(1)
    queue_deque = deque(range(data_size))
    start_deque = time.perf_counter()
    while queue_deque:
        _ = queue_deque.popleft()
    elapsed_deque = time.perf_counter() - start_deque
    print(f"collections.deque.popleft() (O(1)):  {elapsed_deque * 1000:8.2f} ms")

    # Benchmark list.pop(0) - O(N) memory shift per element
    queue_list = list(range(data_size))
    start_list = time.perf_counter()
    # Benchmark only first 5,000 pops to avoid freezing the terminal
    bench_limit = 5_000
    for _ in range(bench_limit):
        _ = queue_list.pop(0)
    elapsed_list = time.perf_counter() - start_list
    # Extrapolate for full dataset
    projected_list_time = (elapsed_list / bench_limit) * data_size

    speedup = projected_list_time / elapsed_deque if elapsed_deque > 0 else float("inf")

    print(f"list.pop(0) [Projected for {data_size:,}]:   {projected_list_time * 1000:8.2f} ms")
    print(f"Speedup Factor with collections.deque:     {speedup:8.1f}x faster!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
