# Day 02: Common Mistakes & Antipatterns (Strings, Unicode & Regex)

Here are the 7 most damaging pitfalls backend engineers encounter when working with text processing, Unicode encodings, and regular expressions:

---

### 1. Repeated String Concatenation (`+=`) Inside Loops
- ❌ **The Mistake:**
  ```python
  csv_buffer = ""
  for row in database_records:  # Say 50,000 records
      csv_buffer += f"{row.id},{row.name},{row.amount}\n"
  ```
- ⚠️ **The Problem:** Because Python strings are immutable, each `+=` iteration allocates a new string object and copies all previously accumulated characters. This turns an $\mathcal{O}(N)$ operation into an $\mathcal{O}(N^2)$ CPU and memory disaster, grinding the process to a halt under load.
- ✅ **The Correction:**
  ```python
  rows = [f"{row.id},{row.name},{row.amount}\n" for row in database_records]
  csv_buffer = "".join(rows)  # Allocates memory ONCE; O(N) linear time
  ```

---

### 2. Catastrophic Backtracking in Regular Expressions (ReDoS)
- ❌ **The Mistake:**
  ```python
  import re
  # Intended to validate grouped words:
  pattern = re.compile(r"^([a-zA-Z0-9]+)+$")
  ```
- ⚠️ **The Problem:** The nested quantifier `([a-zA-Z0-9]+)+` causes exponential backtracking when given a near-match string like `"aaaaaaaaaaaaaaaaaaaaaaa!"`. The Python regex engine tests millions of branch combinations, locking the worker thread at 100% CPU and causing a Denial of Service.
- ✅ **The Correction:** Avoid nested quantifiers or make them atomic/possessive:
  ```python
  pattern = re.compile(r"^[a-zA-Z0-9]+$")
  ```

---

### 3. Calling `re.compile()` Inside Request Handler Functions
- ❌ **The Mistake:**
  ```python
  @app.get("/users/search")
  def search_users(query: str):
      # Recompiling regex on EVERY incoming HTTP request!
      regex = re.compile(r"^[a-zA-Z0-9_]{3,16}$")
      if not regex.match(query):
          raise HTTPException(status_code=400)
  ```
- ⚠️ **The Problem:** Regex compilation incurs AST building and opcode generation overhead. While Python maintains a small LRU cache for regexes, compiling inside hot loops or route handlers adds avoidable latency.
- ✅ **The Correction:**
  Compile once at the module or class level:
  ```python
  USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,16}$")

  @app.get("/users/search")
  def search_users(query: str):
      if not USERNAME_REGEX.match(query):
          raise HTTPException(status_code=400)
  ```

---

### 4. Forgetting Raw String Notation (`r"..."`) in Regex Patterns
- ❌ **The Mistake:**
  ```python
  # Intended to match a literal Windows path or regex word boundary:
  pattern = re.compile("\b[A-Z]+\b")
  ```
- ⚠️ **The Problem:** In standard Python string literals, `"\b"` is parsed as the ASCII **backspace** character (`\x08`), NOT the regex word boundary!
- ✅ **The Correction:** Always prefix regex patterns with `r`:
  ```python
  pattern = re.compile(r"\b[A-Z]+\b")
  ```

---

### 5. Blindly Mixing `bytes` and `str`
- ❌ **The Mistake:**
  ```python
  payload = b"user_id=42"
  query = "SELECT * FROM users WHERE " + payload  # TypeError!
  ```
- ⚠️ **The Problem:** In Python 3, `bytes` (raw 8-bit octets) and `str` (Unicode text) are strictly incompatible. Concatenating or comparing them raises a `TypeError` or silently returns `False`.
- ✅ **The Correction:** Always decode bytes at the system I/O boundary using explicit encoding:
  ```python
  text_payload = payload.decode("utf-8")
  query = f"SELECT * FROM users WHERE {text_payload}"
  ```

---

### 6. Comparing Passwords or Tokens with `==` (Timing Attacks)
- ❌ **The Mistake:**
  ```python
  def verify_webhook_signature(provided_signature: str, calculated_signature: str) -> bool:
      return provided_signature == calculated_signature  # VULNERABLE!
  ```
- ⚠️ **The Problem:** Standard string equality (`==`) compares characters one by one and aborts on the very first mismatch. An attacker can measure response latency in microseconds to infer characters one by one.
- ✅ **The Correction:**
  Always use constant-time comparison:
  ```python
  import hmac
  def verify_webhook_signature(provided_signature: str, calculated_signature: str) -> bool:
      return hmac.compare_digest(provided_signature, calculated_signature)
  ```

---

### 7. Comparing Unicode Strings Without Normalization
- ❌ **The Mistake:**
  ```python
  user_input = "café"     # Composed form: U+00E9
  db_record = "cafe\u0301" # Decomposed form: 'e' + combining accent
  print(user_input == db_record)  # False! Visual match, binary mismatch!
  ```
- ⚠️ **The Problem:** Unicode allows the same visual glyph to be represented as a single precomposed character or as a base character plus combining accents.
- ✅ **The Correction:**
  Normalize to NFC or NFKC before saving or comparing:
  ```python
  import unicodedata
  norm_input = unicodedata.normalize("NFC", user_input)
  norm_db = unicodedata.normalize("NFC", db_record)
  print(norm_input == norm_db)  # True!
  ```
