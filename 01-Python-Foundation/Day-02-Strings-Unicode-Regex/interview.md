# Day 02: Technical Interview Questions & In-Depth Engineering Answers

These questions are frequently asked in Mid-to-Senior Python Backend, Distributed Systems, and Performance Engineering interviews:

---

### Q1: How does PEP 393 (Flexible String Representation) optimize memory in CPython?
**Answer:**
Prior to Python 3.3, CPython builds were configured as either "narrow" (UCS-2, 2 bytes per char, unable to represent characters outside the Basic Multilingual Plane directly) or "wide" (UCS-4, 4 bytes per char, which wasted immense amounts of RAM on pure ASCII strings).

**PEP 393** introduced an adaptive memory model:
- When a string is allocated, CPython checks the maximum code point in that string.
- If all code points are $\le 255$ (`U+00FF`), CPython uses **1 byte per character** (`Latin-1`).
- If all code points are $\le 65,535$ (`U+FFFF`), CPython uses **2 bytes per character** (`UCS-2`).
- If any code point is $> 65,535$ (e.g. emojis or rare math symbols up to `U+10FFFF`), CPython promotes the **entire string to 4 bytes per character** (`UCS-4`).

**Engineering Implication:**
This design keeps ASCII strings minimal (1 byte/char) while allowing $\mathcal{O}(1)$ random access (`s[i]`) without multibyte UTF-8 scanning. However, injecting a single 4-byte character (like an emoji) into a large ASCII text expands the entire buffer by 4x.

---

### Q2: Why are Python strings immutable, and what are the specific benefits for dictionaries and concurrency?
**Answer:**
Python strings are immutable for three architectural reasons:
1. **Cached Hash Values**: Python dictionaries and sets are implemented as hash tables. Because strings cannot change after creation, their hash code (`__hash__`) is computed on first use and permanently cached inside the `PyASCIIObject` C struct. Subsequent dictionary lookups check this precomputed integer hash in $\mathcal{O}(1)$ time without recalculating string bytes.
2. **Thread Safety**: Immutable strings can be shared across multiple OS threads without mutex locks or synchronization overhead, since concurrent readers cannot corrupt internal state.
3. **Security & Stability**: Strings represent critical references such as file descriptors, database connection URIs, and authentication tokens. Immutability guarantees that passing a string into an external library cannot modify the caller's value via side effects.

---

### Q3: What is the architectural difference between `re.match()`, `re.search()`, and `re.fullmatch()`?
**Answer:**
- **`re.match(pattern, string)`**: Anchors the match strictly at the **beginning** of the string (equivalent to `^pattern`). If the first character does not match, it returns `None` immediately.
- **`re.search(pattern, string)`**: Scans **forward through the entire string** looking for the first location where the regex matches.
- **`re.fullmatch(pattern, string)`**: Requires the **entire string** from start to finish to match the pattern (equivalent to `^pattern$`).

**Production Gotcha:**
Junior engineers often use `re.match()` to validate user inputs (e.g., verifying an alphanumeric username). If `re.match(r"[a-zA-Z0-9]+", "admin; DROP TABLE users;")` is called, it returns a successful match object on `"admin"`, leaving the dangerous payload undetected. Input validation must always use `re.fullmatch()`.

---

### Q4: What is Regular Expression Denial of Service (ReDoS), and how can it be prevented in backend APIs?
**Answer:**
Python's standard `re` module uses a **backtracking NFA (Non-deterministic Finite Automaton)** engine. When a regex pattern contains nested quantifiers with overlapping sub-patterns (e.g., `(a+)+$` or `(a|aa)+$`), an input consisting of many `a`'s followed by a non-matching character (e.g., `"aaaaaaaaaaaaaaaaaaaaX"`) forces the engine to explore all possible branching paths.

The time complexity explodes from linear $\mathcal{O}(N)$ to exponential $\mathcal{O}(2^N)$. A short string of 30 characters can freeze a Python worker thread for minutes, pegging the CPU core at 100%.

**Mitigation Strategies:**
1. **Avoid Nested Quantifiers**: Never nest `+` or `*` within another repeated group.
2. **Input Length Limits**: Validate `len(input_str) < 256` before applying regex.
3. **Alternative Linear Engines**: Use the `google-re2` Python wrapper, which guarantees $\mathcal{O}(N)$ linear time by using a DFA (Deterministic Finite Automaton) that forbids backtracking.

---

### Q5: Why is `"".join(list_of_strings)` asymptotically faster than repeated `+=` string concatenation?
**Answer:**
Because Python strings are immutable, executing `result += item` inside a loop cannot resize the existing buffer in place. For each iteration $i$, CPython must:
1. Allocate a brand new memory block on the heap of size $\text{len}(result) + \text{len}(item)$.
2. Copy all characters from the old `result` into the new memory block.
3. Free the old `result` object.

Summing the copy operations across $N$ strings results in $1 + 2 + 3 + \dots + N = \frac{N(N+1)}{2} = \mathcal{O}(N^2)$ time complexity.

In contrast, `str.join(iterable)` performs **two linear passes**:
1. First pass iterates through the collection to sum the exact total character lengths.
2. CPython makes a **single heap allocation** for the entire combined size.
3. Second pass performs high-speed C-level memory copies (`memcpy`) directly into the final buffer.
This guarantees $\mathcal{O}(N)$ linear time and minimal memory fragmentation.
