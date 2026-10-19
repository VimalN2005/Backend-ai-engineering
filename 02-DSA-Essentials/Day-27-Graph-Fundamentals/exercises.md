# Practice Exercises: Graph Traversals & Search Algorithms

Mastering graphs requires internalizing three canonical patterns:
1. **Deep Object Graph Duplication (Clone Graph)** — Handling cycles, references, and pointer preservation via HashMaps.
2. **Implicit Grid Graph Traversal (Number of Islands)** — Connected components on 2D matrices using iterative BFS/DFS.
3. **Shortest Transformation Sequence (Word Ladder)** — Unweighted shortest path using BFS on dynamic pattern-matching graphs.

---

## Challenge 1: Clone Graph (LeetCode 133 - Medium)

### Problem Statement
Given a reference of a node in a **connected undirected graph**, return a **deep copy** (clone) of the graph.
Each node in the graph contains a value (`int`) and a list (`List[Node]`) of its neighbors.

### Constraints
- The number of nodes in the graph is in the range $[0, 100]$.
- $1 \le \text{Node.val} \le 100$
- `Node.val` is unique for each node.
- There are no repeated edges and no self-loops.

### Optimal Solution (Iterative BFS with Clone Map)

```python
from collections import deque
from typing import Dict, List, Optional

class Node:
    def __init__(self, val: int = 0, neighbors: Optional[List["Node"]] = None):
        self.val = val
        self.neighbors = neighbors if neighbors is not None else []

def clone_graph(node: Optional[Node]) -> Optional[Node]:
    """
    Creates a deep copy of a graph with cycle preservation.
    
    ALGORITHM: BFS with Clone Mapping Dictionary.
    We maintain a hashmap `cloned_map: {original_node: cloned_node}`.
    When exploring neighbors, if a cloned counterpart exists, reuse it;
    otherwise, create it and enqueue the original node for future neighbor wiring.
    
    Time Complexity: O(V + E)
    Space Complexity: O(V) for the hashmap and queue.
    """
    if not node:
        return None

    # Map original node to its cloned copy
    cloned_map: Dict[Node, Node] = {node: Node(node.val)}
    queue = deque([node])

    while queue:
        curr = queue.popleft()

        for neighbor in curr.neighbors:
            if neighbor not in cloned_map:
                # Clone neighbor and enqueue original for processing
                cloned_map[neighbor] = Node(neighbor.val)
                queue.append(neighbor)
            # Wire cloned neighbor into current cloned node's adjacency list
            cloned_map[curr].neighbors.append(cloned_map[neighbor])

    return cloned_map[node]


# Verification
if __name__ == "__main__":
    n1 = Node(1)
    n2 = Node(2)
    n3 = Node(3)
    n1.neighbors = [n2, n3]
    n2.neighbors = [n1, n3]
    n3.neighbors = [n1, n2]

    cloned_n1 = clone_graph(n1)
    assert cloned_n1 is not None and cloned_n1 is not n1
    assert cloned_n1.val == 1
    assert len(cloned_n1.neighbors) == 2
    assert cloned_n1.neighbors[0].val in {2, 3}
    assert cloned_n1.neighbors[0] is not n2  # Must be a distinct deep copy!
    print("[PASS] Challenge 1: Clone Graph verified.")
```

---

## Challenge 2: Number of Islands (LeetCode 200 - Medium)

### Problem Statement
Given an $m \times n$ 2D binary grid `grid` which represents a map of `'1'`s (land) and `'0'`s (water), return the **number of islands**.

An island is surrounded by water and is formed by connecting adjacent lands horizontally or vertically. You may assume all four edges of the grid are all surrounded by water.

### Constraints
- $m == \text{grid.length}$
- $n == \text{grid}[i].\text{length}$
- $1 \le m, n \le 300$
- `grid[i][j]` is `'0'` or `'1'`.

### Optimal Solution (Iterative BFS Sink Method)

```python
from collections import deque
from typing import List

def num_islands(grid: List[List[str]]) -> int:
    """
    Counts connected components of '1's in a 2D matrix.
    
    ALGORITHM: In-place BFS Matrix Sink.
    When a '1' is encountered, increment island count and trigger a BFS
    that sinks all connected '1's to '0's in-place, eliminating auxiliary visited sets.
    
    Time Complexity: O(M * N)
    Space Complexity: O(min(M, N)) queue memory in worst-case.
    """
    if not grid or not grid[0]:
        return 0

    rows, cols = len(grid), len(grid[0])
    island_count = 0

    def bfs_sink(r: int, c: int):
        queue = deque([(r, c)])
        grid[r][c] = "0"  # Sink immediately upon discovery

        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]

        while queue:
            curr_r, curr_c = queue.popleft()
            for dr, dc in directions:
                nr, nc = curr_r + dr, curr_c + dc
                if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == "1":
                    grid[nr][nc] = "0"  # Mark sunk BEFORE pushing to queue!
                    queue.append((nr, nc))

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == "1":
                island_count += 1
                bfs_sink(r, c)

    return island_count


# Verification
if __name__ == "__main__":
    test_grid = [
        ["1", "1", "0", "0", "0"],
        ["1", "1", "0", "0", "0"],
        ["0", "0", "1", "0", "0"],
        ["0", "0", "0", "1", "1"],
    ]
    assert num_islands(test_grid) == 3
    print("[PASS] Challenge 2: Number of Islands verified.")
```

---

## Challenge 3: Word Ladder (LeetCode 127 - Hard)

### Problem Statement
A **transformation sequence** from word `beginWord` to word `endWord` using a dictionary `wordList` is a sequence of words `beginWord -> s1 -> s2 -> ... -> sk` such that:
1. Every adjacent pair of words differs by exactly one letter.
2. Every $s_i$ for $1 \le i \le k$ is in `wordList`. (Note that `beginWord` does not need to be in `wordList`.)
3. $s_k == \text{endWord}$.

Given two words, `beginWord` and `endWord`, and a dictionary `wordList`, return the **number of words in the shortest transformation sequence**, or `0` if no such sequence exists.

### Constraints
- $1 \le \text{beginWord.length} \le 10$
- `endWord.length == beginWord.length`
- $1 \le \text{wordList.length} \le 5000$
- All words consist of lowercase English letters.
- `beginWord != endWord`

### Optimal Solution (BFS with Intermediate Wildcard Pre-Indexing)

```python
from collections import defaultdict, deque
from typing import List

def ladder_length(begin_word: str, end_word: str, word_list: List[str]) -> int:
    """
    Finds the minimum transformation steps from begin_word to end_word.
    
    ALGORITHM: BFS with Wildcard Pattern Buckets.
    Instead of comparing every word against every other word in O(N^2 * L),
    we index words by their wildcard patterns (e.g. 'hot' -> '*ot', 'h*t', 'ho*').
    Looking up valid 1-edit neighbors is O(L) instead of O(N).
    
    Time Complexity: O(N * L^2) where N is word count, L is word length.
    Space Complexity: O(N * L^2) for pattern dictionary and visited sets.
    """
    if end_word not in word_list:
        return 0

    word_len = len(begin_word)
    # Pre-index words into wildcard pattern buckets
    # e.g., '*og' -> ['cog', 'dog', 'log']
    patterns: Dict[str, List[str]] = defaultdict(list)
    for word in word_list:
        for i in range(word_len):
            pattern = word[:i] + "*" + word[i + 1:]
            patterns[pattern].append(word)

    # Queue stores: (current_word, level_step_count)
    queue = deque([(begin_word, 1)])
    visited = {begin_word}

    while queue:
        curr_word, steps = queue.popleft()

        if curr_word == end_word:
            return steps

        # Check all L possible intermediate wildcard representations
        for i in range(word_len):
            pattern = curr_word[:i] + "*" + curr_word[i + 1:]
            for neighbor in patterns[pattern]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, steps + 1))
            # Optimization: Empty pattern bucket once processed to avoid re-visiting
            patterns[pattern] = []

    return 0


# Verification
if __name__ == "__main__":
    start = "hit"
    target = "cog"
    words = ["hot", "dot", "dog", "lot", "log", "cog"]
    # Path: hit -> hot -> dot -> dog -> cog (length: 5)
    assert ladder_length(start, target, words) == 5
    print("[PASS] Challenge 3: Word Ladder verified.")
```
