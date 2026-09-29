# Day 08: Hands-On Production Exercises

Master real-world file I/O, atomic storage, and streaming serialization by implementing these 3 production challenges.

---

### Exercise 1: Atomic Rotating File Writer with Gzip Archiving (Medium)
Build a class `AtomicRotatingWriter(filepath: Path, max_bytes: int = 1_000_000, backup_count: int = 3)` that:
1. Appends log records or telemetry lines to the active log file using standard buffering.
2. Checks file size on each write. When the active file exceeds `max_bytes`:
   - Flushes and fsyncs the current file.
   - Rotates existing backup files (`app.log.2.gz` -> `app.log.3.gz`, `app.log.1.gz` -> `app.log.2.gz`).
   - Atomically compresses the current active file into `app.log.1.gz` using `gzip.open` and removes the uncompressed file.
   - Creates a new active file cleanly.

```python
from pathlib import Path
import gzip
import os

class AtomicRotatingWriter:
    def __init__(self, filepath: Path, max_bytes: int = 1_000_000, backup_count: int = 3) -> None:
        self.filepath = Path(filepath)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        # TODO: Initialize file handle
        
    def write_line(self, record: str) -> None:
        # TODO: Implement atomic check, rotation, and gzip compression
        pass
```

---

### Exercise 2: Memory-Bounded JSONL Validator & Quarantine Filter (Medium)
When ingesting datasets for LLM fine-tuning, training runs will fail mid-way if a single record contains missing keys, invalid types, or corrupted JSON.

Implement `validate_and_quarantine_jsonl(input_file: Path, valid_output: Path, quarantine_output: Path, required_keys: set[str]) -> dict`:
1. Stream `input_file` line-by-line without loading more than one record into memory.
2. If a line is valid JSON and contains all `required_keys`:
   - Append to `valid_output`.
3. If a line is invalid JSON or lacks required keys:
   - Append to `quarantine_output` with the reason and line number annotated.
4. Return a summary dict: `{"total": int, "valid": int, "quarantined": int, "elapsed_seconds": float}`.

---

### Exercise 3: High-Speed Memory-Mapped Needle Scanner (`mmap`) (Advanced)
Build a high-performance log analysis tool `scan_log_mmap(filepath: Path, search_pattern: bytes) -> list[dict]`:
1. Map the target file using `mmap.mmap(access=mmap.ACCESS_READ)`.
2. Find all occurrences of `search_pattern` without converting the entire file or individual lines into Python `str` objects.
3. For each match found:
   - Determine the byte start and end offset of the line containing the match.
   - Decode and extract ONLY that specific matched line.
   - Return a list of records: `[{"byte_offset": int, "line_text": str}, ...]`.
