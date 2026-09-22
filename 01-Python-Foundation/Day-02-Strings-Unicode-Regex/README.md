# Day 02: Strings, Unicode, Regular Expressions & Slicing Mechanics

---

## 1. Definition
A **string** in Python (`str`) is an **immutable sequence of Unicode code points**. Unlike languages such as C, where strings are null-terminated byte arrays (`char*`), CPython strings are sophisticated high-level objects governed by **PEP 393 (Flexible String Representation)**. Python 3 strictly separates binary data (`bytes`) from textual data (`str`), requiring explicit encoding (converting text to bytes) and decoding (converting bytes to text).

---

## 2. Why? (Problem it Solves)
- **String Immutability**:
  1. **Dictionary Key Hashability**: Dictionaries and Sets rely on hash tables ($\mathcal{O}(1)$ lookups). Because strings cannot mutate after creation, their hash value (`__hash__`) is computed once and cached permanently inside the `PyASCIIObject` structure.
  2. **Thread Safety**: Multiple execution threads can read the same string object concurrently without requiring mutex locks, eliminating data races.
  3. **Security**: Strings carry sensitive credentials, database queries, and file paths. Immutability guarantees that passing a string into a function cannot secretly alter the caller's value.
- **Unicode Overhaul (PEP 393)**:
  Python 2 mixed raw bytes with ASCII strings, leading to ubiquitous `UnicodeDecodeError` crashes in production. Python 3 guarantees that all strings represent pure Unicode text, while PEP 393 dynamically selects the most compact byte-width representation (1, 2, or 4 bytes per character) to drastically conserve memory.

---

## 3. How? (Under the Hood / Working Principle)

### 3.1 PEP 393 Flexible String Representation
CPython does not use a fixed 4-byte (UTF-32) buffer for every character. Instead, when a string is allocated, CPython inspects the highest Unicode code point in that string and chooses the minimal representation:

| Character Kind | Max Code Point | Bytes per Character | Example Content | C Struct Used |
| :--- | :--- | :--- | :--- | :--- |
| **1-byte (Latin-1 / ASCII)** | `U+00FF` (255) | 1 byte | `"backend123"` | `PyASCIIObject` / `PyCompactUnicodeObject` |
| **2-byte (UCS-2)** | `U+FFFF` (65,535) | 2 bytes | `"Python भाषा"` (Devanagari, Cyrillic) | `PyCompactUnicodeObject` |
| **4-byte (UCS-4)** | `U+10FFFF` (1,114,111) | 4 bytes | `"FastAPI 🚀"` (Emojis, Mathematical symbols) | `PyCompactUnicodeObject` |

> **Memory Implication**: A 1,000,000-character ASCII string consumes ~1 MB of RAM. If you append a single emoji (`🚀`) to it, CPython promotes the **entire** string to UCS-4, instantly expanding memory consumption to ~4 MB!

### 3.2 Slicing Mechanics (`[start:stop:step]`)
String slicing creates a **shallow copy** of the sliced segment. CPython calculates indices using pointer arithmetic:
```text
index = start + (step * iteration)
```
- Negative indices: Converted internally as `length + index`.
- Slicing does not mutate the original string; it allocates a brand new `PyUnicodeObject` on the heap.

### 3.3 Regular Expression Engine (`re` module)
Python's regex engine uses a **Non-deterministic Finite Automaton (NFA)** with backtracking.
1. `re.compile(pattern)` translates the regex string into an internal bytecode sequence.
2. The regex virtual machine attempts to match input characters against states.
3. If a branch fails, the engine **backtracks** to the last decision point and tests alternate paths.

---

## 4. Syntax & Basic Contract
```python
import re

# Slicing syntax: [start:stop:step]
text = "Enterprise-Backend"
prefix = text[0:10]        # "Enterprise"
reversed_text = text[::-1] # "dnekcaB-esirpretnE"

# Modern string formatting (f-strings - fastest due to compile-time bytecode)
status_code = 200
message = f"Status: {status_code} | Health: OK"

# Regular expression pattern compilation
pattern = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
is_valid = bool(pattern.match("dev@production.io"))
```

---

## 5. Example 1: Conceptual Walkthrough
Understanding memory allocation in string concatenation vs. `join`:

```python
# ANTIPATTERN: O(N^2) time complexity
# In each iteration, a brand new string object is allocated in heap memory,
# and all previous characters are copied over!
result = ""
for item in ["auth", "users", "tokens", "v1"]:
    result += "/" + item  # 4 separate allocations + repeated character copying

# PRODUCTION PATTERN: O(N) time complexity
# Pre-calculates the exact total buffer size needed, allocates memory ONCE,
# and copies all parts into the allocated buffer.
parts = ["auth", "users", "tokens", "v1"]
result = "/" + "/".join(parts)
```

---

## 6. Example 2: Edge Cases & Gotchas

### Gotcha A: Unicode Normalization (NFC vs. NFD)
Two strings can look visually identical on screen yet fail equality tests due to different Unicode compositions:
```python
import unicodedata

# Composed form (NFC): Single code point for 'é' (U+00E9)
s1 = "\u00e9" 

# Decomposed form (NFD): 'e' (U+0065) + Combining Acute Accent (U+0301)
s2 = "e\u0301"

print(s1 == s2)  # False!
# Fix: Normalize before comparison or database lookup:
print(unicodedata.normalize("NFC", s1) == unicodedata.normalize("NFC", s2))  # True!
```

### Gotcha B: Catastrophic Backtracking (ReDoS)
Poorly constructed regex patterns with nested quantifiers (e.g., `(a+)+$`) cause the NFA engine to attempt exponential ($2^N$) match combinations on failing inputs, locking the CPU core at 100%:
```python
import re
# Dangerous pattern:
bad_regex = re.compile(r"^(a+)+$")
# Testing bad_regex.match("aaaaaaaaaaaaaaaaaaaaaaaaaaaaax") will freeze your server!
```

---

## 7. Production-Grade Example
A high-throughput PII (Personally Identifiable Information) Redaction Engine used in backend API middleware to mask sensitive logs before dispatching to Datadog/CloudWatch.
*(See complete runnable code in [example_02.py](example_02.py))*

```python
import re

class PIIRedactor:
    """Production utility to scrub credentials, emails, and credit cards from logs."""
    
    PATTERNS = {
        "EMAIL": re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),
        "CARD": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        "BEARER_TOKEN": re.compile(r"Bearer\s+([A-Za-z0-9\-_\.]+)", re.IGNORECASE)
    }

    @classmethod
    def redact(cls, log_payload: str) -> str:
        scrubbed = cls.PATTERNS["EMAIL"].sub("[EMAIL_REDACTED]", log_payload)
        scrubbed = cls.PATTERNS["CARD"].sub("[CARD_REDACTED]", scrubbed)
        scrubbed = cls.PATTERNS["BEARER_TOKEN"].sub("Bearer [TOKEN_REDACTED]", scrubbed)
        return scrubbed
```

---

## 8. Common Mistakes & Antipatterns
- ❌ **Mistake**: Using string concatenation (`+=`) in a loop to construct large HTTP response bodies or CSV exports.
  - ✅ **Correction**: Collect chunks in a `list` and use `"".join(chunks)`.
- ❌ **Mistake**: Calling `re.match()` or `re.sub()` with raw string patterns inside a hot request loop without pre-compiling.
  - ✅ **Correction**: Compile regex patterns once at module level using `re.compile()`.
- ❌ **Mistake**: Forgetting raw string notation `r"..."` in regex, resulting in Python escape sequence collisions (e.g., `"\b"` interpreted as ASCII backspace instead of regex word boundary).
- ❌ **Mistake**: Comparing user passwords or security tokens with `==` instead of `hmac.compare_digest()`, introducing timing attack vulnerabilities.

---

## 9. Performance & Complexity
- **Length Lookup (`len(s)`)**: $\mathcal{O}(1)$ time. The character count is stored directly in the `PyASCIIObject` header struct.
- **Slicing (`s[a:b]`)**: $\mathcal{O}(K)$ time and space, where $K = b - a$. Slicing allocates a new string.
- **Format Benchmark**:
  - `f"{a}_{b}"` ➔ **Fastest** (Compiled directly into `FORMAT_VALUE` and `BUILD_STRING` opcodes).
  - `"{}_{}".format(a, b)` ➔ ~25% slower (Method lookup overhead).
  - `"%s_%s" % (a, b)` ➔ Legacy C-style tuple packing.
- **Hash Computation**: $\mathcal{O}(N)$ on first call, $\mathcal{O}(1)$ on all subsequent calls (cached in object header).

---

## 10. Security Implications
1. **ReDoS (Regular Expression Denial of Service)**: Attackers send crafted inputs designed to trigger catastrophic backtracking in unanchored regexes, starving the Python process of CPU cycles.
2. **Timing Attacks**: Comparing secret hashes/tokens using `str.__eq__` (`==`) aborts on the first mismatched character. Attackers can deduce secrets by measuring microsecond response timing differences. Always use `hmac.compare_digest()`.
3. **Unicode Homoglyph Attacks**: Characters from different alphabets (e.g., Cyrillic 'а' `U+0430` vs. Latin 'a' `U+0061`) render identically but have distinct hashes and memory representations. Always normalize incoming authentication identifiers using `unicodedata.normalize("NFKC", input_str)`.

---

## 11. When to Use?
- **Pre-compiled Regex (`re.compile`)**: When matching against repeated streams of text, API route parameters, or log pipelines.
- **`str.partition()` / `str.split(..., maxsplit=1)`**: When parsing `key=value` headers or configuration pairs; significantly faster than running regex.
- **`str.join()`**: Whenever assembling 3 or more string segments dynamically.

---

## 12. When NOT to Use?
- **Do NOT use Regex for HTML/XML Parsing**: Regex cannot handle arbitrary nesting and recursion. Use `BeautifulSoup` or `lxml`.
- **Do NOT use `str` for Binary Files**: Always use `bytes` or `bytearray` when reading image, video, audio, or encrypted network buffers to avoid Unicode decoding failures.

---

## 13. Top Interview Questions
1. *How does PEP 393 optimize string memory usage in CPython?*
2. *Why are strings immutable in Python, and how does this affect dictionary performance?*
3. *What is the difference between `re.match()`, `re.search()`, and `re.fullmatch()`?*
4. *What causes catastrophic backtracking in regular expressions, and how do you protect a backend API against it?*
5. *Why is `"".join(list_of_strings)` asymptotically faster than repeated `+=` concatenation?*

---

## 14. Practice Problems
1. Implement a high-speed HTTP access log parser that extracts IP, timestamp, HTTP verb, URL path, and status code using compiled regex named groups.
2. Build a memory benchmark comparing `+=` loop concatenation against `"".join()` for 100,000 strings, tracking memory deltas via `tracemalloc`.
3. Create a Unicode security sanitizer that detects homoglyph collisions between usernames.

---

## 15. 5-Minute Revision Notes
- Python 3 strings are immutable sequences of Unicode code points.
- **PEP 393**: Automatically selects Latin-1 (1B), UCS-2 (2B), or UCS-4 (4B) per string based on the highest code point.
- Adding a single 4-byte character (like an emoji) quadruples the memory consumption of an ASCII string.
- `len(s)` is $\mathcal{O}(1)$ because string length is stored in the C struct header.
- Always use `"".join()` instead of `+=` inside loops to avoid $\mathcal{O}(N^2)$ memory reallocation.
- Always use raw strings (`r"..."`) with `re.compile()` to avoid escape character collisions and recompilation overhead.
- Always use `hmac.compare_digest()` for security-sensitive token/password comparisons to prevent timing attacks.
