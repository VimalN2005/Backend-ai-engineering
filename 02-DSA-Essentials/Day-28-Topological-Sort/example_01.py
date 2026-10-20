"""
Day 28: Topological Sort — Dependency Resolution & Build Systems.

Covers core topological sorting algorithms and dependency resolution invariants:
1. Kahn's Algorithm (BFS-based in-degree peeling).
2. DFS Post-Order Reversal (3-color cycle detection).
3. Lexicographical / Deterministic Topological Sort (Min-Heap priority queue).
4. Parallel Execution Wavefront Generation (Level-by-level stage slicing).
5. Cycle Extraction Diagnostic (Pinpointing circular dependency paths).
6. High-Volume Benchmark: Kahn's BFS vs DFS Post-Order on 20,000 nodes.
"""

from collections import defaultdict, deque
import heapq
import time
from typing import Dict, List, Optional, Set, Tuple


# =====================================================================
# 1. KAHN'S ALGORITHM (BFS IN-DEGREE PEELING)
# =====================================================================

def kahn_topological_sort(num_nodes: int, edges: List[Tuple[int, int]]) -> Optional[List[int]]:
    """
    Standard Kahn's Algorithm for Topological Sorting.
    
    WHY IN-DEGREE MATTERS:
    In-degree represents the exact count of unfulfilled prerequisites for a task.
    When in-degree reaches 0, all incoming dependencies have completed, making the
    task immediately safe to schedule.
    
    Time Complexity: O(V + E)
    Space Complexity: O(V + E)
    Returns: Valid linear ordering, or None if a cycle exists.
    """
    adj: Dict[int, List[int]] = defaultdict(list)
    in_degree = [0] * num_nodes

    # Build adjacency list and compute in-degree for every vertex
    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    # Queue holds all vertices with zero unmet prerequisites
    queue: deque[int] = deque([node for node in range(num_nodes) if in_degree[node] == 0])
    topo_order: List[int] = []

    while queue:
        curr = queue.popleft()
        topo_order.append(curr)

        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Invariant: If topo_order contains fewer than num_nodes, a cycle exists
    if len(topo_order) != num_nodes:
        return None  # Cycle detected

    return topo_order


# =====================================================================
# 2. DFS POST-ORDER REVERSAL (TARJAN'S APPROACH)
# =====================================================================

def dfs_topological_sort(num_nodes: int, edges: List[Tuple[int, int]]) -> Optional[List[int]]:
    """
    DFS-based Topological Sort using Post-Order Reversal.
    
    WHY POST-ORDER REVERSAL WORKS:
    In a DAG, when we run DFS on node u, all downstream dependencies reachable from u
    will complete their DFS calls and append themselves to post_order BEFORE u finishes.
    Reversing this list guarantees every parent appears before its children.
    
    Time Complexity: O(V + E)
    Space Complexity: O(V + E)
    """
    adj: Dict[int, List[int]] = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * num_nodes
    post_order: List[int] = []

    def _dfs(u: int) -> bool:
        color[u] = GRAY  # In active recursion stack
        for v in adj[u]:
            if color[v] == GRAY:
                return True  # Back edge: Cycle detected!
            if color[v] == WHITE:
                if _dfs(v):
                    return True
        color[u] = BLACK  # Node and all its descendants fully resolved
        post_order.append(u)
        return False

    for node in range(num_nodes):
        if color[node] == WHITE:
            if _dfs(node):
                return None  # Graph contains a cycle

    # Reverse post-order yields topological sort
    return post_order[::-1]


# =====================================================================
# 3. LEXICOGRAPHICAL / DETERMINISTIC TOPOLOGICAL SORT
# =====================================================================

def lexicographical_topological_sort(num_nodes: int, edges: List[Tuple[int, int]]) -> Optional[List[int]]:
    """
    Computes the lexicographically smallest topological sort using a Min-Heap.
    
    WHY REPRODUCIBLE DETERMINISM MATTERS:
    In build systems (e.g. Bazel, Nix, Cargo) and compiler intermediate representations,
    build outputs must be byte-for-byte identical across runs to ensure build caching.
    A Min-Heap guarantees deterministic tie-breaking among concurrent ready nodes.
    
    Time Complexity: O(V log V + E log V)
    Space Complexity: O(V + E)
    """
    adj: Dict[int, List[int]] = defaultdict(list)
    in_degree = [0] * num_nodes

    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    # Min-Heap stores available nodes (in-degree == 0)
    min_heap: List[int] = [node for node in range(num_nodes) if in_degree[node] == 0]
    heapq.heapify(min_heap)

    topo_order: List[int] = []

    while min_heap:
        curr = heapq.heappop(min_heap)
        topo_order.append(curr)

        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                heapq.heappush(min_heap, neighbor)

    if len(topo_order) != num_nodes:
        return None

    return topo_order


# =====================================================================
# 4. PARALLEL EXECUTION WAVEFRONT BATCHING
# =====================================================================

def parallel_execution_waves(num_nodes: int, edges: List[Tuple[int, int]]) -> Optional[List[List[int]]]:
    """
    Partitions tasks into discrete parallel execution stages (waves).
    
    WHY WAVEFRONT BATCHING:
    All nodes in the queue at the start of a wave have in_degree == 0.
    They share ZERO mutual dependencies and can be dispatched across worker
    threads or cluster nodes concurrently without lock contention.
    
    Returns: List of stages, where each stage is a list of node IDs.
    """
    adj: Dict[int, List[int]] = defaultdict(list)
    in_degree = [0] * num_nodes

    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    queue: deque[int] = deque([node for node in range(num_nodes) if in_degree[node] == 0])
    stages: List[List[int]] = []
    processed_count = 0

    while queue:
        wave_size = len(queue)
        current_wave: List[int] = []

        # Process all tasks currently at in-degree 0 in this wave
        for _ in range(wave_size):
            curr = queue.popleft()
            current_wave.append(curr)
            processed_count += 1

            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        stages.append(current_wave)

    if processed_count != num_nodes:
        return None  # Cycle detected

    return stages


# =====================================================================
# 5. CYCLE DIAGNOSTIC EXTRACTOR
# =====================================================================

def extract_cycle_diagnostic(num_nodes: int, edges: List[Tuple[int, int]]) -> Optional[List[int]]:
    """
    Extracts the exact sequence of vertices forming a circular dependency loop.
    Crucial for developer error messaging in CI/CD pipeline parsing.
    """
    adj: Dict[int, List[int]] = defaultdict(list)
    in_degree = [0] * num_nodes

    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1

    # Run Kahn's peeling to filter out all acyclic prefixes
    queue = deque([n for n in range(num_nodes) if in_degree[n] == 0])
    while queue:
        curr = queue.popleft()
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # All nodes with in_degree > 0 are inside or downstream of cycles
    cyclic_nodes = {n for n in range(num_nodes) if in_degree[n] > 0}
    if not cyclic_nodes:
        return None  # No cycle exists

    # Run DFS on cyclic nodes to extract exact back edge path
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in cyclic_nodes}
    call_stack: List[int] = []
    cycle_found: List[int] = []

    def _dfs_trace(u: int) -> bool:
        color[u] = GRAY
        call_stack.append(u)

        for v in adj[u]:
            if v in cyclic_nodes:
                if color[v] == GRAY:
                    # Found back edge: Extract cycle slice from call stack
                    idx = call_stack.index(v)
                    cycle_found.extend(call_stack[idx:] + [v])
                    return True
                if color[v] == WHITE:
                    if _dfs_trace(v):
                        return True

        call_stack.pop()
        color[u] = BLACK
        return False

    for node in cyclic_nodes:
        if color[node] == WHITE:
            if _dfs_trace(node):
                return cycle_found

    return None


# =====================================================================
# 6. UNIT TESTS & VERIFICATION
# =====================================================================

def run_unit_tests():
    print("=" * 70)
    print("RUNNING UNIT TESTS FOR TOPOLOGICAL SORT ALGORITHMS")
    print("=" * 70)

    # Test 1: Diamond DAG
    # 0 -> 1, 0 -> 2, 1 -> 3, 2 -> 3
    diamond_edges = [(0, 1), (0, 2), (1, 3), (2, 3)]
    kahn_order = kahn_topological_sort(4, diamond_edges)
    dfs_order = dfs_topological_sort(4, diamond_edges)

    assert kahn_order is not None and len(kahn_order) == 4
    assert dfs_order is not None and len(dfs_order) == 4
    assert kahn_order[0] == 0 and kahn_order[-1] == 3
    assert dfs_order[0] == 0 and dfs_order[-1] == 3
    print("[PASS] Diamond DAG topological sort verified.")

    # Test 2: Cycle Detection & Diagnostic
    # 0 -> 1 -> 2 -> 0 (Cycle), 3 -> 4 (Independent)
    cyclic_edges = [(0, 1), (1, 2), (2, 0), (3, 4)]
    assert kahn_topological_sort(5, cyclic_edges) is None
    assert dfs_topological_sort(5, cyclic_edges) is None

    cycle_trace = extract_cycle_diagnostic(5, cyclic_edges)
    assert cycle_trace is not None
    assert cycle_trace[0] == cycle_trace[-1]  # Closed loop
    assert set(cycle_trace).issuperset({0, 1, 2})
    print(f"[PASS] Cycle detected and diagnosed: {cycle_trace}")

    # Test 3: Lexicographical Determinism
    # 0 -> 2, 0 -> 1 (Both 1 and 2 ready after 0; 1 must be picked first)
    # 3 has no deps (0 and 3 ready at start; 0 must be picked first)
    lex_edges = [(0, 2), (0, 1)]
    lex_order = lexicographical_topological_sort(4, lex_edges)
    # Ready at start: 0, 3. Min is 0.
    # After 0: 1, 2, 3 ready. Min is 1.
    # After 1: 2, 3 ready. Min is 2.
    # After 2: 3 ready.
    assert lex_order == [0, 1, 2, 3], f"Expected [0, 1, 2, 3], got {lex_order}"
    print("[PASS] Lexicographical Min-Heap determinism verified.")

    # Test 4: Parallel Execution Waves
    # Stage 0: 0, 4 (both in-degree 0)
    # Stage 1: 1, 2
    # Stage 2: 3
    wave_edges = [(0, 1), (0, 2), (1, 3), (2, 3), (4, 1)]
    waves = parallel_execution_waves(5, wave_edges)
    assert waves is not None
    assert len(waves) == 3
    assert set(waves[0]) == {0, 4}
    assert set(waves[1]) == {1, 2}
    assert set(waves[2]) == {3}
    print(f"[PASS] Parallel execution wavefront batching verified: {len(waves)} waves.")
    print("All unit tests passed successfully!\n")


# =====================================================================
# 7. HIGH-VOLUME PERFORMANCE BENCHMARK
# =====================================================================

def run_performance_benchmark():
    print("=" * 70)
    print("HIGH-VOLUME BENCHMARK: KAHN'S BFS VS DFS POST-ORDER")
    print("=" * 70)

    import random
    random.seed(42)

    num_nodes = 20_000
    num_edges = 60_000

    print(f"Synthesizing large DAG: {num_nodes:,} nodes, {num_edges:,} directed edges...")
    edges: List[Tuple[int, int]] = []
    # Enforcing u < v guarantees acyclicity
    for _ in range(num_edges):
        u = random.randint(0, num_nodes - 2)
        v = random.randint(u + 1, num_nodes - 1)
        edges.append((u, v))

    # Benchmark Kahn's Algorithm (BFS)
    start_time = time.perf_counter()
    kahn_result = kahn_topological_sort(num_nodes, edges)
    kahn_duration_ms = (time.perf_counter() - start_time) * 1000

    # Benchmark DFS Post-Order
    start_time = time.perf_counter()
    dfs_result = dfs_topological_sort(num_nodes, edges)
    dfs_duration_ms = (time.perf_counter() - start_time) * 1000

    # Benchmark Parallel Wavefront Batching
    start_time = time.perf_counter()
    waves = parallel_execution_waves(num_nodes, edges)
    wave_duration_ms = (time.perf_counter() - start_time) * 1000

    assert kahn_result is not None and len(kahn_result) == num_nodes
    assert dfs_result is not None and len(dfs_result) == num_nodes
    assert waves is not None

    print(f"Kahn's BFS Topological Sort:      {kahn_duration_ms:.2f} ms")
    print(f"DFS Post-Order Topological Sort:  {dfs_duration_ms:.2f} ms")
    print(f"Parallel Wave Generation:         {wave_duration_ms:.2f} ms ({len(waves):,} waves)")
    print("=" * 70)


if __name__ == "__main__":
    run_unit_tests()
    run_performance_benchmark()
