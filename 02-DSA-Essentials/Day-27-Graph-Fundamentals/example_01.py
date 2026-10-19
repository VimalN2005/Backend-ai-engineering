"""
Day 27: Graph Fundamentals — Adjacency List, BFS & DFS Traversals.

Covers core graph representations and traversal algorithms:
1. Adjacency List Graph construction (directed & undirected).
2. BFS Traversal & Shortest Path with predecessor reconstruction.
3. DFS Traversal: Recursive vs Stack-based Iterative (recursion-safe).
4. Cycle Detection in Undirected Graphs (Parent pointer technique).
5. Cycle Detection in Directed Graphs (Three-color state marking).
6. Connected Components in undirected networks.
7. Bipartite Graph Verification (2-coloring).
8. High-Volume Benchmark: deque.popleft() O(1) vs list.pop(0) O(N) in BFS.
"""

from collections import defaultdict, deque
import time
from typing import Dict, List, Optional, Set, Tuple


# =====================================================================
# 1. GRAPH REPRESENTATION: ADJACENCY LIST
# =====================================================================

class Graph:
    """
    Production-grade Adjacency List Graph.
    
    WHY ADJACENCY LIST?
    In backend infrastructure (service meshes, dependency DAGs, social networks),
    graphs are sparse (|E| << |V|^2). An Adjacency List requires O(|V| + |E|) memory,
    compared to O(|V|^2) for an Adjacency Matrix, avoiding gigabytes of wasted RAM.
    """

    def __init__(self, directed: bool = False):
        self.directed = directed
        self.adj: Dict[str, List[str]] = defaultdict(list)
        self.nodes: Set[str] = set()

    def add_node(self, node: str) -> None:
        self.nodes.add(node)
        if node not in self.adj:
            self.adj[node] = []

    def add_edge(self, u: str, v: str) -> None:
        """Adds a directed edge u -> v, or bidirectional u <-> v if undirected."""
        self.add_node(u)
        self.add_node(v)
        self.adj[u].append(v)
        if not self.directed:
            self.adj[v].append(u)

    def neighbors(self, u: str) -> List[str]:
        return self.adj.get(u, [])


# =====================================================================
# 2. BREADTH-FIRST SEARCH (BFS) & SHORTEST PATH
# =====================================================================

def bfs_traversal(graph: Graph, start: str) -> List[str]:
    """
    Performs standard BFS level-order traversal from a start node.
    
    WHY DEQUE?
    collections.deque provides O(1) popleft(). Using list.pop(0) causes an O(N)
    memory shift on every element, degrading BFS from O(V + E) to O(V^2 + E).
    """
    if start not in graph.nodes:
        return []

    visited: Set[str] = {start}
    queue: deque[str] = deque([start])
    traversal_order: List[str] = []

    while queue:
        curr = queue.popleft()
        traversal_order.append(curr)

        for neighbor in graph.neighbors(curr):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return traversal_order


def bfs_shortest_path(graph: Graph, start: str, target: str) -> Optional[List[str]]:
    """
    Finds the shortest path (minimum edge hops) between start and target.
    
    WHY BFS GUARANTEES SHORTEST PATH IN UNWEIGHTED GRAPHS:
    BFS explores nodes in strictly non-decreasing order of distance from start
    (level 0, then level 1, then level 2...). The first time target is reached,
    it is guaranteed to be via the shortest possible edge sequence.
    """
    if start not in graph.nodes or target not in graph.nodes:
        return None
    if start == target:
        return [start]

    visited: Set[str] = {start}
    queue: deque[str] = deque([start])
    parent: Dict[str, Optional[str]] = {start: None}

    found = False
    while queue:
        curr = queue.popleft()
        if curr == target:
            found = True
            break

        for neighbor in graph.neighbors(curr):
            if neighbor not in visited:
                visited.add(neighbor)
                parent[neighbor] = curr
                queue.append(neighbor)

    if not found:
        return None

    # Reconstruct path backwards from target using parent pointers
    path = []
    step: Optional[str] = target
    while step is not None:
        path.append(step)
        step = parent[step]
    return path[::-1]


# =====================================================================
# 3. DEPTH-FIRST SEARCH (DFS): RECURSIVE & ITERATIVE
# =====================================================================

def dfs_recursive(graph: Graph, start: str) -> List[str]:
    """Standard recursive DFS traversal."""
    visited: Set[str] = set()
    order: List[str] = []

    def _dfs(u: str):
        visited.add(u)
        order.append(u)
        for v in graph.neighbors(u):
            if v not in visited:
                _dfs(v)

    if start in graph.nodes:
        _dfs(start)
    return order


def dfs_iterative(graph: Graph, start: str) -> List[str]:
    """
    Iterative DFS using an explicit LIFO call stack.
    
    WHY ITERATIVE DFS IN PRODUCTION PYTHON?
    Python enforces a default recursion limit (sys.getrecursionlimit() == 1000).
    Deep dependency graphs or microservice trees easily exceed 1,000 hops,
    crashing recursive implementations with RecursionError. An iterative stack
    resides in heap memory and can handle millions of nodes.
    """
    if start not in graph.nodes:
        return []

    visited: Set[str] = set()
    order: List[str] = []
    stack: List[str] = [start]

    while stack:
        curr = stack.pop()
        if curr in visited:
            continue
        visited.add(curr)
        order.append(curr)

        # Reverse neighbors to match traditional recursive left-to-right order
        for neighbor in reversed(graph.neighbors(curr)):
            if neighbor not in visited:
                stack.append(neighbor)

    return order


# =====================================================================
# 4. CYCLE DETECTION: UNDIRECTED VS DIRECTED
# =====================================================================

def has_cycle_undirected(graph: Graph) -> bool:
    """
    Detects cycles in an undirected graph using DFS and parent pointers.
    
    WHY PARENT POINTER IS REQUIRED:
    In an undirected graph, edge (u, v) also exists as (v, u).
    Looking at v from u would falsely appear as a cycle unless we explicitly
    verify that v is not the immediate parent from which u was visited.
    """
    visited: Set[str] = set()

    def _dfs(u: str, parent: Optional[str]) -> bool:
        visited.add(u)
        for v in graph.neighbors(u):
            if v not in visited:
                if _dfs(v, u):
                    return True
            elif v != parent:
                # Found visited neighbor that is NOT immediate parent -> CYCLE!
                return True
        return False

    for node in graph.nodes:
        if node not in visited:
            if _dfs(node, None):
                return True
    return False


def has_cycle_directed(graph: Graph) -> bool:
    """
    Detects cycles in a directed graph using Three-Color State Marking.
    
    WHY SIMPLE VISITED SET FAILS IN DIRECTED GRAPHS:
    In a directed DAG, two paths can converge to a common node (e.g. A->B->D and A->C->D).
    D is visited twice, but there is NO cycle.
    
    THREE-COLOR INVARIANT:
    - WHITE (0): Unvisited node.
    - GRAY (1) : Node is currently in the active recursion call stack (ancestor).
    - BLACK (2): Node and all its descendants are fully explored.
    A cycle exists iff an edge points to a GRAY node (Back Edge).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {node: WHITE for node in graph.nodes}

    def _dfs(u: str) -> bool:
        color[u] = GRAY  # Mark active in current call path
        for v in graph.neighbors(u):
            if color[v] == GRAY:
                return True  # Back edge to ancestor -> Cycle!
            if color[v] == WHITE:
                if _dfs(v):
                    return True
        color[u] = BLACK  # Exiting call path: completely resolved
        return False

    for node in graph.nodes:
        if color[node] == WHITE:
            if _dfs(node):
                return True
    return False


# =====================================================================
# 5. CONNECTED COMPONENTS & BIPARTITE VERIFICATION
# =====================================================================

def find_connected_components(graph: Graph) -> List[List[str]]:
    """
    Extracts all maximal connected subgraphs in an undirected network.
    Used for network partition and cluster isolation analysis.
    """
    visited: Set[str] = set()
    components: List[List[str]] = []

    for node in graph.nodes:
        if node not in visited:
            component = []
            queue = deque([node])
            visited.add(node)

            while queue:
                curr = queue.popleft()
                component.append(curr)
                for neighbor in graph.neighbors(curr):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(component)

    return components


def is_bipartite(graph: Graph) -> bool:
    """
    Checks if graph can be partitioned into two sets such that no two adjacent
    nodes share the same set (2-colorable).
    
    Invariant: A graph is bipartite iff it contains no odd-length cycles.
    """
    color: Dict[str, int] = {}

    for node in graph.nodes:
        if node not in color:
            queue = deque([node])
            color[node] = 0

            while queue:
                curr = queue.popleft()
                curr_color = color[curr]

                for neighbor in graph.neighbors(curr):
                    if neighbor not in color:
                        color[neighbor] = 1 - curr_color
                        queue.append(neighbor)
                    elif color[neighbor] == curr_color:
                        return False  # Same color conflict -> Not bipartite

    return True


# =====================================================================
# 6. UNIT TESTS & VERIFICATION
# =====================================================================

def run_unit_tests():
    print("=" * 70)
    print("RUNNING UNIT TESTS FOR GRAPH ALGORITHMS")
    print("=" * 70)

    # 1. Test BFS Shortest Path
    g_undirected = Graph(directed=False)
    g_undirected.add_edge("A", "B")
    g_undirected.add_edge("A", "C")
    g_undirected.add_edge("B", "D")
    g_undirected.add_edge("C", "E")
    g_undirected.add_edge("D", "F")
    g_undirected.add_edge("E", "F")

    path = bfs_shortest_path(g_undirected, "A", "F")
    assert path is not None and len(path) == 4, f"Expected path length 4, got {path}"
    assert path[0] == "A" and path[-1] == "F"
    print("[PASS] BFS shortest path verified.")

    # 2. Test DFS Traversal (Recursive & Iterative)
    rec_order = dfs_recursive(g_undirected, "A")
    iter_order = dfs_iterative(g_undirected, "A")
    assert set(rec_order) == set(iter_order) == {"A", "B", "C", "D", "E", "F"}
    print("[PASS] DFS recursive and iterative traversals verified.")

    # 3. Test Cycle Detection (Undirected)
    # g_undirected currently has a cycle: A-B-D-F-E-C-A
    assert has_cycle_undirected(g_undirected) is True

    g_tree = Graph(directed=False)
    g_tree.add_edge("1", "2")
    g_tree.add_edge("1", "3")
    g_tree.add_edge("2", "4")
    assert has_cycle_undirected(g_tree) is False
    print("[PASS] Undirected cycle detection verified.")

    # 4. Test Cycle Detection (Directed 3-Color)
    g_dag = Graph(directed=True)
    g_dag.add_edge("A", "B")
    g_dag.add_edge("A", "C")
    g_dag.add_edge("B", "D")
    g_dag.add_edge("C", "D")  # Diamond structure: NOT a cycle!
    assert has_cycle_directed(g_dag) is False, "Diamond DAG falsely reported as cycle!"

    g_cyclic = Graph(directed=True)
    g_cyclic.add_edge("A", "B")
    g_cyclic.add_edge("B", "C")
    g_cyclic.add_edge("C", "A")  # Circular dependency
    assert has_cycle_directed(g_cyclic) is True
    print("[PASS] Directed 3-color cycle detection verified.")

    # 5. Test Connected Components
    g_clusters = Graph(directed=False)
    g_clusters.add_edge("c1_a", "c1_b")
    g_clusters.add_edge("c2_a", "c2_b")
    g_clusters.add_node("isolated_c3")
    components = find_connected_components(g_clusters)
    assert len(components) == 3
    print("[PASS] Connected components isolation verified.")

    # 6. Test Bipartite Verification
    g_bipartite = Graph(directed=False)
    g_bipartite.add_edge("u1", "v1")
    g_bipartite.add_edge("u1", "v2")
    g_bipartite.add_edge("u2", "v1")
    assert is_bipartite(g_bipartite) is True

    g_triangle = Graph(directed=False)
    g_triangle.add_edge("1", "2")
    g_triangle.add_edge("2", "3")
    g_triangle.add_edge("3", "1")  # Odd cycle of length 3 -> NOT bipartite
    assert is_bipartite(g_triangle) is False
    print("[PASS] Bipartite 2-coloring verified.")
    print("All graph algorithm unit tests passed successfully!\n")


# =====================================================================
# 7. HIGH-VOLUME BENCHMARK: DEQUE.POPLEFT() VS LIST.POP(0)
# =====================================================================

def run_performance_benchmark():
    print("=" * 70)
    print("HIGH-VOLUME BENCHMARK: DEQUE.POPLEFT() O(1) VS LIST.POP(0) O(N)")
    print("=" * 70)

    # Construct a linear chain graph of 20,000 nodes to test BFS FIFO popping
    nodes_count = 20_000
    print(f"Building linear pipeline graph with {nodes_count:,} vertices...")
    chain_graph = Graph(directed=True)
    for i in range(nodes_count - 1):
        chain_graph.add_edge(str(i), str(i + 1))

    # Benchmark 1: collections.deque.popleft() -> O(1) per pop
    start_time = time.perf_counter()
    visited_deque = {"0"}
    queue_deque = deque(["0"])
    deque_visited_count = 0

    while queue_deque:
        curr = queue_deque.popleft()
        deque_visited_count += 1
        for nxt in chain_graph.neighbors(curr):
            if nxt not in visited_deque:
                visited_deque.add(nxt)
                queue_deque.append(nxt)

    deque_duration_ms = (time.perf_counter() - start_time) * 1000

    # Benchmark 2: Python list.pop(0) -> O(N) memory copy per pop
    # On 20,000 nodes, 20,000 * 20,000 shifts takes multiple seconds.
    # We benchmark on first 4,000 nodes and mathematically project 20,000.
    sample_size = 4_000
    start_time = time.perf_counter()
    sample_list = list(range(sample_size))
    while sample_list:
        sample_list.pop(0)
    sample_duration_ms = (time.perf_counter() - start_time) * 1000
    projected_list_ms = sample_duration_ms * ((nodes_count / sample_size) ** 2)

    speedup = projected_list_ms / deque_duration_ms if deque_duration_ms > 0 else float("inf")

    print(f"Nodes Processed in BFS:           {nodes_count:,}")
    print(f"collections.deque.popleft() O(1): {deque_duration_ms:.2f} ms")
    print(f"Python list.pop(0) Projected O(N):{projected_list_ms:.2f} ms")
    print(f"Performance Speedup:              {speedup:,.1f}x faster!")
    print("=" * 70)


if __name__ == "__main__":
    run_unit_tests()
    run_performance_benchmark()
