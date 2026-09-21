# Day 01: Practical Hands-on Exercises

इन तीनों समस्याओं को हल करके अपने Python इंटरनल्स और एनवायरनमेंट कॉन्सेप्ट्स को टेस्ट करें।

---

## Exercise 1: Bytecode Opcode Counter (Easy)

### Problem Statement:
Python के `dis` मॉड्यूल का उपयोग करके एक ऐसा फ़ंक्शन बनाएं जो किसी भी दिए गए फ़ंक्शन को एनालाइज़ करे और यह बताए कि उसमें कुल कितने और कौन-से बाइटकोड इंस्ट्रक्शन्स (`opcodes`) इस्तेमाल हुए हैं।

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
    
    # TODO: Use dis.get_instructions(target_func) to iterate and count
    
    return opcode_counts

# Verification:
def sample_logic(a: int, b: int) -> int:
    result = a + b
    return result * 2

# Output should show counts of: LOAD_FAST, BINARY_OP, STORE_FAST, RETURN_VALUE, etc.
print(count_opcodes(sample_logic))
```

---

## Exercise 2: Shallow vs Deep Identity Comparator (Medium)

### Problem Statement:
एक ऐसा फ़ंक्शन लिखें जो दो नेस्टेड डेटा स्ट्रक्चर्स (Lists या Dicts) को लेता है और यह रिपोर्ट करता है:
1. क्या दोनों का रूट मेमोरी एड्रेस समान है (`a is b`)?
2. क्या अंदरूनी ऑब्जेक्ट्स (नेस्टेड एलीमेंट्स) का मेमोरी एड्रेस समान है?
3. क्या दोनों के मान समान हैं (`a == b`)?

### Starter Code:
```python
from typing import Any, Dict

def inspect_nested_identity(obj_a: Any, obj_b: Any) -> Dict[str, Any]:
    """
    Returns an analysis dictionary showing:
    - root_is_identical: bool
    - values_equal: bool
    - nested_references_shared: bool
    """
    # TODO: Implement identity and equality verification
    pass
```

### Verification Criteria:
- `orig = [1, [2, 3]]` और `shallow = orig.copy()` पास करने पर `root_is_identical: False` लेकिन `nested_references_shared: True` होना चाहिए।
- `deep = copy.deepcopy(orig)` पास करने पर दोनों `False` होने चाहिए।

---

## Exercise 3: CLI Environment Lockfile Auditor (Production Practical)

### Problem Statement:
एक प्रोडक्शन यूटिलिटी स्क्रिप्ट लिखें जो मौजूदा प्रोजेक्ट के `requirements.txt` को स्कैन करे और चेक करे:
1. क्या कोई अनपिन्ड (unpinned) डिपेंडेंसी है? (उदाहरण: `requests` बिना `==` के, जो प्रोडक्शन में वर्ज़न ब्रेक कर सकती है)।
2. क्या इनसिक्योर/डिप्रीकेटेड पैकेजेस हैं?

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
    pinned = []
    unpinned = []
    
    # TODO: Read lines, strip comments (#), and verify '==' presence
    
    return pinned, unpinned
```
