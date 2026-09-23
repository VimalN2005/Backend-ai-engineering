# Day 03: Practical Hands-On Exercises

Complete these three production-level challenges to master Python collections, memory optimization, and time complexity.

---

## Exercise 1: Inverted Search Index Builder (Easy-Medium)

### Problem Statement:
Search engines and log aggregators use **Inverted Indexes** to find documents containing specific keywords in $\mathcal{O}(1)$ time.
Write a function `build_inverted_index(documents: Dict[int, str]) -> Dict[str, Set[int]]` that accepts a mapping of `document_id -> text_content` and returns an index mapping each unique lowercased word to the `set` of document IDs where it appears.

### Starter Code:
```python
import re
from typing import Dict, Set
from collections import defaultdict

def build_inverted_index(documents: Dict[int, str]) -> Dict[str, Set[int]]:
    """
    Builds an inverted index mapping lowercased tokens to sets of document IDs.
    """
    inverted_index: Dict[str, Set[int]] = defaultdict(set)
    
    # TODO: Tokenize words using re.findall(r"\b[a-zA-Z0-9_]+\b", ...),
    # convert to lowercase, and populate inverted_index.
    
    return dict(inverted_index)

# Test Verification:
docs = {
    101: "FastAPI is a modern web framework for Python",
    102: "Python backend engineering requires deep DSA knowledge",
    103: "FastAPI and Django are popular Python web frameworks"
}

index = build_inverted_index(docs)
print("Documents containing 'fastapi':", index.get("fastapi")) # Expected: {101, 103}
print("Documents containing 'python':", index.get("python"))   # Expected: {101, 102, 103}
```

---

## Exercise 2: Microservice RBAC Permission Analyzer (Medium)

### Problem Statement:
In Role-Based Access Control (RBAC), users are granted multiple roles, and each role has a set of permissions.
Write a class `RBACEngine` that:
1. Registers roles with their permission sets.
2. Resolves effective permissions for a user with multiple roles using set union (`|`).
3. Compares two users to find:
   - Shared permissions (Intersection `&`).
   - Permissions unique to User A (Difference `-`).
   - Permissions present in only one user, not both (Symmetric Difference `^`).

### Starter Code:
```python
from typing import Dict, Set, List

class RBACEngine:
    def __init__(self):
        self.role_permissions: Dict[str, Set[str]] = {}

    def register_role(self, role: str, permissions: Set[str]) -> None:
        self.role_permissions[role] = permissions

    def get_effective_permissions(self, user_roles: List[str]) -> Set[str]:
        # TODO: Compute union of all permissions across assigned roles
        pass

    def compare_users(self, user_a_roles: List[str], user_b_roles: List[str]) -> Dict[str, Set[str]]:
        # TODO: Compute shared, unique_to_a, unique_to_b, and exclusive_either
        pass
```

---

## Exercise 3: Circular Ring Buffer with Fixed Capacity (Advanced)

### Problem Statement:
In high-throughput logging and streaming telemetry, you often need a fixed-size ring buffer that stores the last $K$ events. When capacity is exceeded, the oldest event is overwritten in $\mathcal{O}(1)$ time without shifting elements or reallocating memory.
Implement a `RingBuffer` class without using `collections.deque`. Use an internal pre-allocated fixed-size `list` and modular arithmetic (`index % capacity`).

### Starter Code:
```python
from typing import Any, List, Optional

class RingBuffer:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self.buffer: List[Optional[Any]] = [None] * capacity
        self.head = 0  # Write pointer
        self.size = 0

    def append(self, item: Any) -> None:
        """Appends an item in O(1), overwriting the oldest item if full."""
        # TODO: Implement ring append logic using modulo arithmetic
        pass

    def get_all(self) -> List[Any]:
        """Returns all elements in chronological order (oldest to newest)."""
        # TODO: Reconstruct elements in correct FIFO order
        pass
```
