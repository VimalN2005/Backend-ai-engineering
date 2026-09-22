# Day 02: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or coding sessions:

---

- ⚡ **PEP 393 Memory Hierarchy:**
  - 1-byte (`Latin-1` / ASCII $\le 255$): 48-byte header + 1 byte/char.
  - 2-byte (`UCS-2` $\le 65,535$): 72-byte header + 2 bytes/char.
  - 4-byte (`UCS-4` $\le 1,114,111$): 72-byte header + 4 bytes/char.
  - *Gotcha:* A single emoji promotes the whole string buffer to 4 bytes per character.

- ⚡ **Immutability Benefits:**
  - `__hash__` is computed once and permanently cached inside `PyASCIIObject`.
  - Guarantees $\mathcal{O}(1)$ dictionary key lookups.
  - Thread-safe without locks; eliminates memory data races across threads.

- ⚡ **Concatenation vs. Join:**
  - `+=` in loops ➔ $\mathcal{O}(N^2)$ time (continuous reallocation and re-copying).
  - `"".join(seq)` ➔ $\mathcal{O}(N)$ time (pre-allocates total buffer once + C `memcpy`).

- ⚡ **Regular Expressions Best Practices:**
  - Always compile at module/class scope (`re.compile`) to avoid repeated parsing overhead.
  - Always use raw strings (`r"..."`) to avoid backslash escaping bugs (`\b` word boundary vs backspace).
  - Use `re.fullmatch()` for input validation; never rely solely on `re.match()`.

- ⚡ **Security Protocols:**
  - **ReDoS Prevention:** Eliminate nested quantifiers `(a+)+` to prevent exponential $\mathcal{O}(2^N)$ backtracking.
  - **Constant-Time Comparison:** Never compare secrets with `==`. Use `hmac.compare_digest()` to eliminate timing attack vectors.
  - **Unicode Normalization:** Always normalize incoming text with `unicodedata.normalize("NFC", text)` before hashing or comparing.
