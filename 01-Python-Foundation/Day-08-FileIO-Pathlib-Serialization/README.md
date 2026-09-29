# Day 08: File I/O, OS Pathlib, High-Performance Serialization & Memory-Mapped Files

In high-scale enterprise backends and production AI systems, data does not exist solely in memory. Whether ingestion engines are processing 50-gigabyte JSONL datasets for LLM fine-tuning, microservices are streaming multi-gigabyte vector checkpoints, or APIs are handling user file uploads, understanding the mechanics of kernel-level file I/O, OS path resolution, and zero-copy data streaming is paramount.

---

## 1. Operating System File Descriptors & Kernel I/O Lifecycle

At the operating system level, files are accessed through integer handles known as **File Descriptors (FDs)** on POSIX systems or **File Handles** on Windows:

```
+--------------------------------------------------------------------------+
|                              User Space                                  |
|                                                                          |
|   +-----------------------+              +---------------------------+   |
|   |   Python Application  |              |     CPython C Runtime     |   |
|   |   `open("data.txt")`  | ---------->  |   User-space I/O Buffer   |   |
|   +-----------------------+              |   (io.DEFAULT_BUFFER_SIZE)|   |
|                                          +---------------------------+   |
+------------------------------------------------------- | ----------------+
                                                         | `write()` syscall
+------------------------------------------------------- v ----------------+
|                             Kernel Space                                 |
|                                                                          |
|   +-----------------------+              +---------------------------+   |
|   | File Descriptor Table |              |   Page Cache / OS Buffer  |   |
|   |   (fd=3 -> File Object| ---------->  |   (Dirty Pages in RAM)    |   |
|   +-----------------------+              +---------------------------+   |
|                                                        |                 |
|                                                        | `fsync()` / I/O |
+------------------------------------------------------- | ----------------+
                                                         v
                                           +---------------------------+
                                           | Physical Disk (NVMe / SSD)|
                                           +---------------------------+
```

1. **CPython User-Space Buffer**: CPython wraps raw OS file descriptors in high-level stream objects (`_io.BufferedReader`, `_io.BufferedWriter`, or `_io.TextIOWrapper`). Writes to a file first accumulate in user-space memory to minimize costly kernel mode switches.
2. **System Call (`write`)**: When the CPython buffer fills, or when `flush()` is invoked, CPython makes a kernel system call (`write()` on Linux, `WriteFile()` on Windows).
3. **OS Kernel Page Cache**: The kernel copies data into its own kernel page cache. The data is now marked as "dirty pages." At this moment, the write system call returns successfully, **even though the data has not yet reached physical disk storage**.
4. **Physical Persistence (`fsync`)**: The operating system kernel periodically flushes dirty pages to the physical storage device (via the `pdflush`/`flusher` kernel thread). If power fails before this occurs, unwritten cache is lost. Calling `os.fsync(fd)` forces the kernel to immediately flush its cache to non-volatile physical disk.

---

## 2. Text Mode vs. Binary Mode & The UTF-8 Mandate

CPython distinguishes strictly between **Text Streams** (`str` in Python) and **Binary Streams** (`bytes` in Python).

```python
# Text mode: Performs automatic character decoding and newline translation
with open("data.txt", mode="r", encoding="utf-8") as f:
    text: str = f.read()

# Binary mode: Reads raw unaltered bytes directly from disk
with open("data.bin", mode="rb") as f:
    raw_bytes: bytes = f.read()
```

### The Production UTF-8 Mandate
Never omit the `encoding` parameter when opening text files.
- On Windows, the default system encoding is historically `cp1252` or `Windows-1250`.
- On legacy Linux systems, default locales can revert to `ASCII` or `ISO-8859-1`.
- If an application processes Unicode characters (such as emojis `🚀`, non-Latin text, or JSON datasets) without `encoding="utf-8"`, it will crash at runtime with `UnicodeDecodeError` or `UnicodeEncodeError`.

---

## 3. Buffering Strategies & Durability Control

Python's `open()` function accepts a `buffering` argument:

| Buffering Argument | Mode | Behavior | Best Use Case |
| :--- | :--- | :--- | :--- |
| `buffering=-1` (Default) | Text / Binary | Uses system default buffer size (`io.DEFAULT_BUFFER_SIZE`, typically 8,192 bytes / 8 KB). | General file reads and writes. |
| `buffering=0` | **Binary Only** | Unbuffered. Every write call immediately invokes the OS `write()` system call. | High-frequency telemetry, low-latency device communication. |
| `buffering=1` | **Text Only** | Line-buffered. Flushes the buffer automatically whenever a newline character (`\n`) is written. | Log files and interactive command-line utilities. |
| `buffering > 1` | Text / Binary | Custom chunk buffer size in bytes (e.g., `buffering=1024*1024` for 1 MB buffer). | High-throughput bulk file streaming over network storage (NFS/S3). |

### `flush()` vs. `os.fsync()`
- `f.flush()`: Flushes CPython's internal memory buffer into the OS kernel page cache.
- `os.fsync(f.fileno())`: Forces the operating system kernel to synchronously write all modified in-memory pages to the physical disk controller.

---

## 4. Modern Filesystem Operations with `pathlib.Path`

Legacy Python code relied heavily on `os.path` and string manipulations:
```python
# Deprecated Legacy Pattern: Error-prone, brittle across platforms
import os
filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "records.csv")
```

Modern Python backends use **`pathlib.Path`**, an object-oriented, cross-platform standard library module:

```python
from pathlib import Path

# Clean, composable, object-oriented syntax
base_dir = Path(__file__).resolve().parent
filepath = base_dir / "data" / "records.csv"

# Essential Pathlib methods
filepath.exists()          # True if file or directory exists
filepath.is_file()         # True if path exists and is a regular file
filepath.is_dir()          # True if path exists and is a directory
filepath.suffix            # Returns file extension (e.g., '.csv')
filepath.stem              # Returns filename without extension ('records')
filepath.name              # Returns filename with extension ('records.csv')
filepath.parent            # Returns immediate parent directory Path object
filepath.stat().st_size    # File size in bytes
```

---

## 5. Security: Path Traversal Vulnerabilities & Sandboxing

A critical vulnerability in web applications (FastAPI/Django) handling user file uploads or dynamic file downloads is **Path Traversal (Directory Traversal)**.

If a malicious user submits `../../etc/passwd` or `..\..\Windows\System32\cmd.exe`, standard string concatenation allows the path to break out of the intended root directory.

### Secure Path Sandboxing Pattern
```python
from pathlib import Path

def resolve_safe_path(base_directory: Path, user_supplied_filename: str) -> Path:
    # 1. Resolve canonical absolute path (eliminates '..' and symlinks)
    target_path = (base_directory / user_supplied_filename).resolve()
    resolved_base = base_directory.resolve()
    
    # 2. Strict jail validation: Assert target resides inside base directory
    if not target_path.is_relative_to(resolved_base):
        raise PermissionError(f"Security Alert: Path traversal attempt detected: {user_supplied_filename}")
        
    return target_path
```

---

## 6. Atomic File Writes & Data Durability

When writing critical configuration, transaction logs, or model checkpoints, writing directly to the target file is dangerous:
- If the application crashes, the disk runs out of space, or the server loses power midway through `f.write()`, the target file is left in a **corrupted, truncated state**.

### The Production Atomic Write Recipe
1. Write changes to a temporary hidden file in the **same directory** (same filesystem volume).
2. Flush CPython buffers (`f.flush()`).
3. Force physical persistence via `os.fsync(f.fileno())`.
4. Atomically replace the destination file using `os.replace()`.

```python
import os
import tempfile
from pathlib import Path

def atomic_write(filepath: Path, content: str) -> None:
    filepath = Path(filepath)
    # Important: Create temp file in the SAME directory to guarantee atomic rename across the same mount
    temp_dir = filepath.parent
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        temp_path = Path(tf.name)
        tf.write(content)
        tf.flush()
        os.fsync(tf.fileno())  # Ensure disk write cache is flushed
        
    # Atomic rename (POSIX rename / Windows ReplaceFile)
    os.replace(temp_path, filepath)
```

---

## 7. Streaming Large Files vs. Memory Exhaustion

Never load multi-gigabyte files into RAM using `f.read()` or `f.readlines()`:
```python
# FATAL ANTIPATTERN: Consumes 10 GB of RAM on a 10 GB dataset -> Process killed by OS OOM Killer
with open("massive_dataset.jsonl", "r", encoding="utf-8") as f:
    lines = f.readlines()
```

### Production Generator-Based Streaming
Line-by-line streaming utilizes Python's built-in file iterator, reading line by line with $\mathcal{O}(1)$ memory consumption:

```python
from typing import Generator

def stream_lines(filepath: Path) -> Generator[str, None, None]:
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\n")
```

For binary files (chunks of raw bytes):
```python
from typing import Generator

def stream_chunks(filepath: Path, chunk_size: int = 64 * 1024) -> Generator[bytes, None, None]:
    with open(filepath, "rb") as f:
        # Idiomatic CPython two-argument iter pattern: iter(callable, sentinel)
        for chunk in iter(lambda: f.read(chunk_size), b""):
            yield chunk
```

---

## 8. JSON Serialization & Custom Encoders

Python's built-in `json` module provides standard JSON serialization:
- `json.dumps(obj)`: Serializes Python object to in-memory JSON `str`.
- `json.loads(s)`: Deserializes JSON `str` to Python dictionary/list.
- `json.dump(obj, f)`: Serializes Python object directly into a file-like stream.
- `json.load(f)`: Deserializes JSON from a file-like stream directly into Python objects.

### Custom Encoders for Production Types
Standard `json` cannot serialize `datetime`, `UUID`, or custom dataclasses out of the box. Implement a custom serializer:

```python
import json
from datetime import datetime
from uuid import UUID
from dataclasses import is_dataclass, asdict
from typing import Any

def production_json_serializer(obj: Any) -> Any:
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

# Usage:
# payload_str = json.dumps(data, default=production_json_serializer)
```

---

## 9. High-Performance JSON Alternatives: `orjson`

In high-throughput microservices handling tens of thousands of requests per second, standard `json` becomes a major CPU bottleneck.
Libraries like **`orjson`** (written in Rust) provide massive performance benefits:
1. **Speed**: 4x to 10x faster than standard library `json`.
2. **Native Serialization**: Automatically serializes `datetime`, `dataclasses`, and `UUID` without requiring custom converter functions.
3. **Direct Bytes Output**: Produces `bytes` directly, eliminating intermediate Python `str` allocations before network transfer.

---

## 10. JSONL (JSON Lines) for AI & Big Data Pipelines

In AI systems, massive datasets (LLM fine-tuning corpora, vector embeddings, evaluation logs) are stored in **JSONL (JSON Lines)** format:
- Each line in the file is a complete, standalone valid JSON object followed by a newline `\n`.

### Why JSONL Dominates AI Engineering:
1. **Append-Friendly**: New training examples can be appended to the end of the file in $\mathcal{O}(1)$ time without reading or rewriting the file.
2. **Chunkable & Parallelizable**: Files can be split across worker processes by line offsets without parsing the entire file structure.
3. **Low Memory Overhead**: Files can be processed line by line in constant memory $\mathcal{O}(1)$, preventing Out-Of-Memory (OOM) crashes on 100 GB training datasets.

---

## 11. CSV Serialization & Delimiter Handling

Python's built-in `csv` module handles tabular data with automated quoting and dialect handling:

```python
import csv
from pathlib import Path

# DictWriter: Enforces column mapping consistency
fieldnames = ["user_id", "email", "role"]
with open("users.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    writer.writerow({"user_id": 101, "email": "alice@corp.internal", "role": "admin"})
```

> **Security Note (CSV Injection / Formula Injection)**: If user-generated strings start with `=, +, -, @`, spreadsheet programs (Excel, Google Sheets) execute them as executable formulas upon opening. Always sanitize or quote untrusted text before writing to CSVs.

---

## 12. Memory-Mapped Files (`mmap`) for Zero-Copy I/O

The `mmap` module allows Python to map a file directly into the application's **virtual address space**:

```
+-------------------------------------------------------------+
|                     Virtual Memory Space                    |
|                                                             |
|   +-----------------------------------------------------+   |
|   |         mmap object (Byte-addressable memory)        |   |
|   +-----------------------------------------------------+   |
+------------------------------ | ----------------------------+
                                | Demand Paging (Kernel Page Faults)
+------------------------------ v ----------------------------+
|                       OS Page Cache                         |
|                                                             |
|   +-------------+  +-------------+  +-------------+         |
|   | Page 0 (4K) |  | Page 1 (4K) |  | Page 2 (4K) |         |
|   +-------------+  +-------------+  +-------------+         |
+------------------------------ | ----------------------------+
                                | DMA (Direct Memory Access)
+------------------------------ v ----------------------------+
|                  Physical Disk / SSD Drive                  |
+-------------------------------------------------------------+
```

### When to Use `mmap`:
1. **Zero-Copy Searching**: Search for binary patterns or strings in a 10 GB file without reading the bytes into Python heap memory.
2. **Random Access**: Read arbitrary slices of large binary models or embeddings without seeking or loading full datasets.
3. **Inter-Process Shared Memory**: Multiple processes can map the same file read-only, sharing physical RAM pages managed by the OS kernel.

---

## 13. Secure Temporary Files (`tempfile`)

Never generate temporary filenames manually with random strings. This creates **TOCTOU (Time-Of-Check to Time-Of-Use)** race conditions and symlink injection vulnerabilities.

Always use the **`tempfile`** module:
- `tempfile.NamedTemporaryFile()`: Creates a secure temporary file with a unique name in the system's designated temp folder (`/tmp` or `AppData\Local\Temp`).
- `tempfile.TemporaryDirectory()`: Context manager that safely creates a temporary directory and automatically deletes it (along with all contents) upon exit.

---

## 14. Real-Time Compression Streams

For large-scale log archives and dataset transfers, compress data on the fly using streaming wrappers:
```python
import gzip
from pathlib import Path

# Compress data on-the-fly without intermediate uncompressed disk writes
with gzip.open("training_data.jsonl.gz", "wt", encoding="utf-8") as f:
    f.write('{"prompt": "Hello", "completion": "World"}\n')
```

---

## 15. Summary Architecture: High-Scale AI Ingestion Pipeline

```
[Raw 50GB Dataset on Disk]
           │
           ▼
[Line-by-Line Generator / mmap]  <--- O(1) Memory Bound (< 50 MB RAM)
           │
           ▼
[Fast Deserializer (orjson/json)]
           │
           ▼
[Dynamic Batch Accumulator]      <--- Accumulates 64 items or token ceiling
           │
           ▼
[Embedding Generator / LLM API]
           │
           ▼
[Atomic State Checkpoint File]   <--- os.replace() + os.fsync() durability
```
