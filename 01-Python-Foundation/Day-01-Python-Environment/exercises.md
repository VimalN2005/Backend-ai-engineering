# Day 01: Practical Hands-On Exercises

Complete these three coding challenges to master Python environment mechanics, bytecode inspection, and memory reference behavior.

---

## Exercise 1: Bytecode Opcode Counter (Easy)

### Problem Statement:
Using Python's built-in `dis` module, implement a function that accepts any callable, disassembles its compiled bytecode, and returns a dictionary summarizing the frequency of each opcode instruction used.

### Starter Code:
```python
import dis
from typing import Callable, Dict

def count_opcodes(target_func: Callable) -> Dict[str, int]:
    """
    Analyzes a callable and returns a dictionary with opcode names as keys
    and their frequency of occurrence as values.
    """
    opcode_counts: Dict[str, int] = {}
    
    # TODO: Iterate over dis.get_instructions(target_func) and count occurrences
    
    return opcode_counts

# Verification:
def sample_logic(a: int, b: int) -> int:
    result = a + b
    return result * 2

# Expected output should show counts for instructions like:
# 'LOAD_FAST', 'BINARY_OP', 'STORE_FAST', 'RETURN_VALUE', etc.
print(count_opcodes(sample_logic))
```

---

## Exercise 2: Shallow vs. Deep Identity Comparator (Medium)

### Problem Statement:
Write an inspection function that takes two nested data structures (lists or dictionaries) and returns a detailed identity audit:
1. Are the root memory addresses identical (`a is b`)?
2. Are the nested elements sharing identical memory references?
3. Are the underlying values equal (`a == b`)?

### Starter Code:
```python
from typing import Any, Dict

def inspect_nested_identity(obj_a: Any, obj_b: Any) -> Dict[str, Any]:
    """
    Returns an analysis dictionary reporting:
    - 'root_is_identical': bool
    - 'values_equal': bool
    - 'nested_references_shared': bool
    """
    # TODO: Implement identity, equality, and nested reference checks
    pass
```

### Verification Criteria:
- Passing `orig = [1, [2, 3]]` and `shallow = orig.copy()` must return:
  `root_is_identical: False`, `nested_references_shared: True`.
- Passing `deep = copy.deepcopy(orig)` must return both as `False`.

---

## Exercise 3: CLI Environment Lockfile Auditor (Production Practical)

### Problem Statement:
Build a production-grade utility function that parses a `requirements.txt` file and validates dependency locking:
1. Identify all pinned dependencies (containing exact version specification `==`).
2. Identify all unpinned or loosely pinned dependencies (e.g., packages without `==`), which could introduce breaking changes in production.

### Starter Code:
```python
from pathlib import Path
from typing import List, Tuple

def audit_requirements_file(file_path: Path) -> Tuple[List[str], List[str]]:
    """
    Scans a requirements.txt file.
    Returns:
      (valid_pinned_packages, unpinned_or_invalid_packages)
    """
    pinned: List[str] = []
    unpinned: List[str] = []
    
    # TODO: Read lines, strip comments (#), and verify '==' presence
    
    return pinned, unpinned
```
