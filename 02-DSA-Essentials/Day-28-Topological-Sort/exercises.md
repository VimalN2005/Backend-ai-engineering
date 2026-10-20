# Practice Exercises: Topological Sort & DAG Dependency Engines

Mastering topological sort requires internalizing three canonical patterns:
1. **Curriculum & Package Prerequisite Resolution (Course Schedule II)** — Kahn's BFS with cycle detection.
2. **Lexicographical Graph Reconstruction (Alien Dictionary)** — Lexical string comparison to deduce character graph ordering.
3. **Minimum Pipeline Completion Time (Parallel Courses III)** — Dynamic programming over topological sort (Critical Path Method).

---

## Challenge 1: Course Schedule II (LeetCode 210 - Medium)

### Problem Statement
There are a total of `numCourses` courses you have to take, labeled from `0` to `numCourses - 1`. You are given an array `prerequisites` where `prerequisites[i] = [a_i, b_i]` indicates that you must take course $b_i$ first if you want to take course $a_i$.

Return the **ordering of courses** you should take to finish all courses. If there are many valid answers, return any of them. If it is impossible to finish all courses, return an **empty array**.

### Constraints
- $1 \le \text{numCourses} \le 2000$
- $0 \le \text{prerequisites.length} \le \text{numCourses} \times (\text{numCourses} - 1)$
- `prerequisites[i].length == 2`
- $0 \le a_i, b_i < \text{numCourses}$
- $a_i \ne b_i$
- All pairs $[a_i, b_i]$ are distinct.

### Optimal Solution (Kahn's BFS Algorithm)

```python
from collections import defaultdict, deque
from typing import List

def find_order(num_courses: int, prerequisites: List[List[int]]) -> List[int]:
    """
    Computes valid course completion sequence using Kahn's algorithm.
    
    Time Complexity: O(V + E)
    Space Complexity: O(V + E)
    """
    adj = defaultdict(list)
    in_degree = [0] * num_courses

    # Edge: prereq (b) -> course (a)
    for course, prereq in prerequisites:
        adj[prereq].append(course)
        in_degree[course] += 1

    # Initialize queue with all courses having zero prerequisites
    queue = deque([c for c in range(num_courses) if in_degree[c] == 0])
    order: List[int] = []

    while queue:
        curr = queue.popleft()
        order.append(curr)

        for dependent in adj[curr]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Invariant: If order does not include all courses, a cycle exists
    if len(order) != num_courses:
        return []

    return order


# Verification
if __name__ == "__main__":
    n = 4
    prereqs = [[1, 0], [2, 0], [3, 1], [3, 2]]
    # 0 -> 1, 0 -> 2, 1 -> 3, 2 -> 3
    result = find_order(n, prereqs)
    assert result == [0, 1, 2, 3] or result == [0, 2, 1, 3]
    print("[PASS] Challenge 1: Course Schedule II verified.")
```

---

## Challenge 2: Alien Dictionary (LeetCode 269 - Hard)

### Problem Statement
There is a new alien language that uses the English alphabet. However, the order of the letters is unknown to you.

You are given a list of strings `words` from the alien language's dictionary, where the strings are **claimed to be sorted lexicographically** by the rules of this new language.

Derive the order of letters in this language, and return it. If the given rules are inconsistent (contain cycles or prefix violations), return `""`. If there are multiple valid letter orderings, return any of them.

### Constraints
- $1 \le \text{words.length} \le 100$
- $1 \le \text{words}[i].\text{length} \le 100$
- `words[i]` consists of only lowercase English letters.

### Optimal Solution (Graph Construction + Kahn's Algorithm)

```python
from collections import defaultdict, deque
from typing import Dict, List, Set

def alien_order(words: List[str]) -> str:
    """
    Deduces character order from lexicographically sorted alien words.
    
    ALGORITHM:
    1. Extract all unique characters.
    2. Compare adjacent words (w1, w2) to find the first differing character:
       w1[j] must precede w2[j] -> directed edge w1[j] -> w2[j].
    3. Prefix Check: If w1 starts with w2 and len(w1) > len(w2) (e.g. ['apple', 'app']),
       the dictionary is invalid. Return "".
    4. Run Kahn's algorithm to compute topological order.
    
    Time Complexity: O(C) where C is total characters across all words.
    Space Complexity: O(U + E) where U is unique characters (<= 26).
    """
    # Step 1: Collect unique characters and initialize in-degrees
    in_degree: Dict[str, int] = {c: 0 for word in words for c in word}
    adj: Dict[str, Set[str]] = defaultdict(set)

    # Step 2: Compare adjacent words
    for i in range(len(words) - 1):
        w1, w2 = words[i], words[i + 1]
        min_len = min(len(w1), len(w2))

        # Check prefix violation: e.g. "abc" comes before "ab" is INVALID
        if len(w1) > len(w2) and w1.startswith(w2):
            return ""

        for j in range(min_len):
            if w1[j] != w2[j]:
                c1, c2 = w1[j], w2[j]
                if c2 not in adj[c1]:
                    adj[c1].add(c2)
                    in_degree[c2] += 1
                break  # Only the first differing character establishes ordering

    # Step 3: Kahn's Algorithm
    queue = deque([char for char, deg in in_degree.items() if deg == 0])
    result_chars: List[str] = []

    while queue:
        curr = queue.popleft()
        result_chars.append(curr)

        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # If cycle exists, some characters remain unresolved
    if len(result_chars) != len(in_degree):
        return ""

    return "".join(result_chars)


# Verification
if __name__ == "__main__":
    alien_words = ["wrt", "wrf", "er", "ett", "rftt"]
    # wrt vs wrf -> t -> f
    # wrf vs er  -> w -> e
    # er vs ett  -> r -> t
    # ett vs rftt -> e -> r
    # Order: w -> e -> r -> t -> f
    res = alien_order(alien_words)
    assert res == "wertf", f"Expected wertf, got {res}"
    print("[PASS] Challenge 2: Alien Dictionary verified.")
```

---

## Challenge 3: Parallel Courses III (LeetCode 2050 - Hard)

### Problem Statement
You are given an integer $n$, which indicates that there are $n$ courses labeled from $1$ to $n$. You are also given a 2D integer array `relations` where `relations[j] = [prevCourse_j, nextCourse_j]` denotes that course `prevCourse_j` must be completed before `nextCourse_j` (prerequisite relationship). Furthermore, you are given a 0-indexed integer array `time` where `time[i]` denotes how many months it takes to complete the $(i + 1)$-th course.

You may start taking a course at any time once the prerequisites are met. Any number of courses can be taken at the same time.

Return the **minimum number of months needed** to complete all the courses.

### Constraints
- $1 \le n \le 5 \times 10^4$
- $0 \le \text{relations.length} \le \min(n \times (n - 1) / 2, 5 \times 10^4)$
- `relations[j].length == 2`
- $1 \le \text{prevCourse}_j, \text{nextCourse}_j \le n$
- $\text{time.length} == n$
- $1 \le \text{time}[i] \le 10^4$
- The prerequisite graph is guaranteed to be a DAG.

### Optimal Solution (Dynamic Programming over Topological Order)

```python
from collections import defaultdict, deque
from typing import List

def minimum_time(n: int, relations: List[List[int]], time: List[int]) -> int:
    """
    Calculates the minimum time to complete all courses via Critical Path Method.
    
    ALGORITHM:
    Let max_time[i] be the earliest time node i can finish.
    max_time[child] = max(max_time[child], max_time[parent] + time[child]).
    
    Time Complexity: O(V + E)
    Space Complexity: O(V + E)
    """
    adj = defaultdict(list)
    in_degree = [0] * (n + 1)

    for u, v in relations:
        adj[u].append(v)
        in_degree[v] += 1

    # max_finish_time[i] stores the completion time of course i
    max_finish_time = [0] * (n + 1)
    queue = deque()

    # Enqueue courses with no prerequisites
    for i in range(1, n + 1):
        if in_degree[i] == 0:
            queue.append(i)
            max_finish_time[i] = time[i - 1]

    while queue:
        curr = queue.popleft()
        curr_finish = max_finish_time[curr]

        for neighbor in adj[curr]:
            # Update earliest completion time for neighbor
            candidate_finish = curr_finish + time[neighbor - 1]
            if candidate_finish > max_finish_time[neighbor]:
                max_finish_time[neighbor] = candidate_finish

            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return max(max_finish_time)


# Verification
if __name__ == "__main__":
    courses = 3
    rel = [[1, 3], [2, 3]]
    t = [3, 2, 5]
    # Course 1 takes 3, Course 2 takes 2 (run in parallel -> finish at t=3)
    # Course 3 takes 5 (runs from t=3 to t=8)
    # Total time: 8
    assert minimum_time(courses, rel, t) == 8
    print("[PASS] Challenge 3: Parallel Courses III verified.")
```
