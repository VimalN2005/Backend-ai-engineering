# Day 02: Practical Hands-On Exercises

Solve these three production-oriented challenges to master string parsing, regex extraction, and memory profiling.

---

## Exercise 1: Nginx / Apache Access Log Parser (Easy-Medium)

### Problem Statement:
Modern backend observability systems ingest millions of access log lines per hour.
Write a function `parse_access_log(log_line: str)` that uses a single pre-compiled regular expression with **named capture groups** (`(?P<name>...)`) to parse a standard Nginx access log entry into a structured dictionary.

### Sample Input Log Line:
```text
192.168.1.105 - john_doe [23/Sep/2026:14:32:10 +0000] "POST /api/v1/payments HTTP/1.1" 201 4096 "https://app.frontend.com" "Mozilla/5.0"
```

### Starter Code:
```python
import re
from typing import Dict, Optional

# Expected dictionary keys:
# 'ip', 'user', 'timestamp', 'method', 'path', 'protocol', 'status_code', 'response_bytes'

LOG_PATTERN = re.compile(
    # TODO: Write regex pattern with named groups (?P<ip>...), (?P<method>...), etc.
    r""
)

def parse_access_log(log_line: str) -> Optional[Dict[str, str]]:
    """
    Parses a single log line into a dictionary. Returns None if line does not match.
    """
    match = LOG_PATTERN.match(log_line)
    if not match:
        return None
    return match.groupdict()

# Test Verification:
sample = '192.168.1.105 - john_doe [23/Sep/2026:14:32:10 +0000] "POST /api/v1/payments HTTP/1.1" 201 4096 "https://app.frontend.com" "Mozilla/5.0"'
result = parse_access_log(sample)
print(result)
```

---

## Exercise 2: Tokenizer & URL Slug Generator (Medium)

### Problem Statement:
Backend CMS and e-commerce APIs need SEO-friendly URL slugs (e.g. from article titles).
Write a function `generate_slug(title: str, max_length: int = 50) -> str` that:
1. Strips leading and trailing whitespace.
2. Converts Unicode accented characters to their closest ASCII equivalents (e.g. `Crème brûlée` ➔ `Creme brulee`).
3. Converts to lowercase.
4. Replaces all non-alphanumeric characters (spaces, punctuation) with a single hyphen (`-`).
5. Removes duplicate hyphens and truncates safely without cutting a word in half if possible.

### Starter Code:
```python
import re
import unicodedata

def generate_slug(title: str, max_length: int = 50) -> str:
    """
    Generates a clean, normalized, URL-safe slug from any Unicode string.
    """
    # Step 1: Normalize unicode characters (NFKD) and encode to ASCII
    # Step 2: Clean non-alphanumeric characters with regex
    # Step 3: Strip redundant hyphens and truncate to max_length
    pass

# Test Cases:
print(generate_slug("Crème Brûlée: The Ultimate 2026 Recipe!"))
# Expected: "creme-brulee-the-ultimate-2026-recipe"
```

---

## Exercise 3: Unicode Homoglyph & Impersonation Detector (Production Security)

### Problem Statement:
Attackers frequently register usernames that visually mimic admin accounts (e.g., substituting Latin 'a' with Cyrillic 'а', or Latin 'o' with Greek 'ο') to spoof identities.
Write a function `detect_homoglyph_collision(existing_username: str, candidate_username: str) -> bool` that normalizes both inputs (using Unicode NFKC) and flags whether the candidate username attempts an impersonation attack.

### Starter Code:
```python
import unicodedata

def detect_homoglyph_collision(target: str, candidate: str) -> bool:
    """
    Returns True if 'candidate' is a visual or normalized homoglyph collision
    of 'target' while having different raw byte representations.
    """
    # TODO: Implement normalization and cross-script inspection
    pass

# Verification:
admin = "admin"
spoof = "аdmin"  # First letter is Cyrillic small letter 'a' (U+0430)
print("Spoof detected:", detect_homoglyph_collision(admin, spoof))
```
