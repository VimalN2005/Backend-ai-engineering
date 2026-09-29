# Day 08: Common File I/O & Serialization Antipatterns

Avoid these 7 production bugs when writing backend I/O and data processing pipelines.

---

### 1. Omitting `encoding="utf-8"` (The Platform Locale Trap)
- **Root Cause**: Omitting the `encoding` parameter defaults to `locale.getpreferredencoding()`. On Windows, this defaults to `cp1252` or `Windows-1250`. On minimal Docker containers, it can revert to ASCII.
- **Consequence**: When reading or writing non-ASCII characters (e.g., emojis `🚀`, Chinese characters, or accented text), the application crashes with `UnicodeDecodeError` or silently corrupts dataset bytes.

```python
# ❌ FATAL ANTIPATTERN: Uses host system default encoding
with open("dataset.jsonl", "r") as f:
    data = f.read()

# ✅ PRODUCTION PATTERN: Explicitly declare UTF-8 everywhere
with open("dataset.jsonl", "r", encoding="utf-8") as f:
    data = f.read()
```

---

### 2. Slurping Unbounded Files with `f.read()` or `f.readlines()`
- **Root Cause**: `f.read()` copies the entire file contents into a single monolithic string in Python memory. `f.readlines()` allocates a list containing every line string.
- **Consequence**: When processing a 10 GB training corpus or server access log on an 8 GB RAM server, the operating system's Out-Of-Memory (OOM) killer terminates the Python process abruptly.

```python
# ❌ FATAL ANTIPATTERN: Allocates 10 GB of heap objects for 10 GB file
with open("large_corpus.jsonl", "r", encoding="utf-8") as f:
    lines = f.readlines()
    for line in lines:
        process(line)

# ✅ PRODUCTION PATTERN: Stream line-by-line in O(1) constant memory
with open("large_corpus.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        process(line.strip())
```

---

### 3. Path Concatenation with Raw Strings (Path Traversal Vulnerability)
- **Root Cause**: Using `f"{base_dir}/{user_filename}"` or `os.path.join` without canonical validation.
- **Consequence**: A user submitting `../../etc/passwd` or `..\..\Windows\System32\drivers\etc\hosts` can traverse out of the sandbox and read or overwrite arbitrary system files.

```python
# ❌ FATAL ANTIPATTERN: Vulnerable to path traversal
def get_user_file(filename: str):
    return open(f"/var/app/uploads/{filename}", "rb")

# ✅ PRODUCTION PATTERN: Modern Pathlib canonical sandbox validation
from pathlib import Path

def get_user_file(filename: str):
    base_dir = Path("/var/app/uploads").resolve()
    target_path = (base_dir / filename).resolve()
    
    if not target_path.is_relative_to(base_dir):
        raise PermissionError(f"Access denied: {filename} attempts directory traversal")
        
    return open(target_path, "rb")
```

---

### 4. Relying on Manual `f.close()` Instead of Context Managers
- **Root Cause**: Developers open a file, perform operations, and attempt to close it with `f.close()` at the bottom of the function.
- **Consequence**: If an exception occurs before `f.close()`, the file descriptor remains open until CPython's garbage collector reclaims the object. In high-concurrency microservices, this rapidly exhausts the operating system file descriptor limit (`EMFILE: Too many open files`).

```python
# ❌ FATAL ANTIPATTERN: File descriptor leaks if parse_data() throws an exception
f = open("data.csv", "r", encoding="utf-8")
data = parse_data(f)
f.close()

# ✅ PRODUCTION PATTERN: Guaranteed deterministic cleanup with context manager
with open("data.csv", "r", encoding="utf-8") as f:
    data = parse_data(f)
```

---

### 5. Assuming `f.flush()` Guarantees Disk Persistence
- **Root Cause**: Confusing CPython user-space buffering with operating system kernel caching.
- **Consequence**: `f.flush()` only pushes bytes from CPython memory into the OS kernel page cache. If the server loses power or the physical machine crashes immediately after, the dirty pages in RAM are lost forever.

```python
# ❌ RISKY FOR WAL/CHECKPOINTS: Only flushes to RAM cache
with open("wallet_transactions.log", "a", encoding="utf-8") as f:
    f.write("TX_9812_COMMITTED\n")
    f.flush()

# ✅ PRODUCTION PATTERN: Hardware durability via os.fsync()
import os

with open("wallet_transactions.log", "a", encoding="utf-8") as f:
    f.write("TX_9812_COMMITTED\n")
    f.flush()
    os.fsync(f.fileno())  # Forces kernel to sync dirty pages to non-volatile disk
```

---

### 6. Loading Multi-Gigabyte Arrays with `json.load()`
- **Root Cause**: Storing large datasets as a single monolithic JSON list `[ {...}, {...} ]` and deserializing with `json.load(f)`.
- **Consequence**: JSON parsing builds the entire object graph in memory simultaneously. A 2 GB JSON file often balloons to 6–10 GB of Python heap allocations due to `PyObject` overhead.

```python
# ❌ UN-SCALABLE ANTIPATTERN: Full array deserialization
with open("dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)  # OOM crash on multi-gigabyte payloads

# ✅ PRODUCTION PATTERN: Switch to JSONL (JSON Lines) streaming format
with open("dataset.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        record = json.loads(line)
        process(record)
```

---

### 7. CSV Formula Injection (Spreadsheet Macro Execution)
- **Root Cause**: Writing unvalidated user strings into CSV exports.
- **Consequence**: If a user submits an email or name like `=cmd|' /C calc'!A0` or `@SUM(1+1)*cmd|...`, spreadsheet tools (Microsoft Excel, LibreOffice) automatically execute the macro commands when the administrator opens the CSV file.

```python
# ❌ VULNERABLE ANTIPATTERN: Direct CSV writing
import csv

with open("users.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([user_input_name, user_input_email])

# ✅ SECURE PATTERN: Neutralize leading formula trigger characters
def sanitize_csv_cell(value: str) -> str:
    # Prefix dangerous characters with a single quote to force text interpretation
    if str(value).startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return str(value)
```
