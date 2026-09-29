# Day 08: Top 5 Technical Interview Questions

---

### Question 1: What is the difference between CPython user-space buffering, OS page cache, and physical storage? Why is `os.fsync()` necessary?

#### Expected Answer:
When writing data in Python using `f.write("data")`:
1. **User-Space Buffer (`_io.BufferedWriter`)**: Python buffers writes in memory (typically 8 KB via `io.DEFAULT_BUFFER_SIZE`) to reduce expensive CPU context switches into kernel space.
2. **OS Page Cache**: Calling `f.flush()` pushes bytes across the user-space/kernel boundary via a `write()` syscall into the operating system's kernel page cache. The OS marks these memory pages as "dirty." The syscall returns immediately, but the data is still in volatile RAM.
3. **Physical Storage**: To guarantee that data survives power failure, kernel panics, or sudden hardware termination, `os.fsync(f.fileno())` must be called. This issues an explicit flush command to the disk controller, forcing dirty pages to non-volatile physical storage (NVMe/SSD).

In databases (WAL write-ahead logs), distributed checkpoints, and financial transaction engines, omitting `os.fsync()` risks silent data corruption upon unexpected server restarts.

---

### Question 2: How does `mmap` achieve zero-copy I/O, and when should you choose `mmap` over standard chunked streaming?

#### Expected Answer:
Standard file reads involve copying data twice:
1. Disk $\to$ OS Kernel Page Cache (via DMA).
2. OS Kernel Page Cache $\to$ CPython User Heap Memory (via `read()` syscall).

`mmap` uses the OS virtual memory manager to map the file's disk blocks directly into the process's virtual address space:
- **Zero-Copy**: The application accesses file contents via direct memory pointers without copying bytes into Python `str` or `bytes` heap objects.
- **Demand Paging**: The operating system kernel pages blocks into RAM only when accessed via page faults, and automatically evicts clean pages when memory pressure occurs.

**When to use `mmap`**:
- Performing fast random-access lookups or binary needle searches across multi-gigabyte static files (e.g., embedding indices, binary model weights).
- Sharing read-only data across multiple worker processes without memory duplication (Copy-On-Write / shared pages).

**When to avoid `mmap`**:
- Sequential streaming of small files (overhead of memory mapping table entries exceeds benefits).
- Highly dynamic, frequently resized files (changing file length requires re-mapping).

---

### Question 3: How do you prevent Path Traversal vulnerabilities in Python file-handling backends?

#### Expected Answer:
Path Traversal occurs when user-supplied filenames contain directory escape sequences (`../` or `..\`) that resolve outside the intended root directory.

#### Antipattern:
Using string checks like `if ".." in filename` is fragile because URL encoding (`%2e%2e%2f`), Unicode normalization, or mixed separators can bypass naive checks.

#### Production Solution:
1. Resolve the canonical absolute path using `Path(target).resolve()`, which resolves all relative dots and symlinks.
2. Resolve the canonical base directory using `Path(base_dir).resolve()`.
3. Assert containment using `target.is_relative_to(base_dir)`.

```python
target = (base_dir / user_input).resolve()
if not target.is_relative_to(base_dir.resolve()):
    raise PermissionError("Path traversal detected")
```

---

### Question 4: Why has JSONL (JSON Lines) become the universal standard over JSON arrays for AI systems and big data pipelines?

#### Expected Answer:
A standard JSON file containing an array of records `[ {...}, {...} ]` requires:
1. **Monolithic Parsing**: The parser must read the entire file into memory before building the AST and Python object graph. A 10 GB dataset creates an immediate Out-Of-Memory (OOM) crash.
2. **Brittle Corruption**: If a single byte is corrupted at line 500,000, the entire JSON document fails parsing (`JSONDecodeError`).
3. **Expensive Appends**: Adding a new record requires seeking to the end, removing the closing `]`, appending the comma and item, and rewriting the closing bracket ($\mathcal{O}(N)$ file rewrite).

**Advantages of JSONL**:
1. **Constant Memory Streaming**: Lines can be streamed one by one in strictly $\mathcal{O}(1)$ RAM.
2. **Fault Isolation**: A corrupted line can be caught, quarantined, and skipped without discarding the remaining 999,999 valid records.
3. **High-Speed Appends**: New items are appended to disk in $\mathcal{O}(1)$ time (`open(..., "a")`).
4. **Trivial Parallelism**: Large files can be chunked by line boundaries across distributed Ray/Celery worker nodes.

---

### Question 5: How does atomic file replacement work in Python (`os.replace`), and why must the temporary file be on the same filesystem?

#### Expected Answer:
`os.replace(src, dst)` performs an atomic rename at the OS kernel level (using the `rename()` system call on POSIX and `SetFileInformationByHandle` / `ReplaceFile` on Windows):
- At no point during the replacement does `dst` disappear or appear in a partially written state. Concurrent readers will either see the old version of the file or the completely written new version.

#### The Filesystem Limitation:
`os.replace` can only guarantee atomicity if `src` and `dst` reside on the **same filesystem mount / drive partition**:
- Within the same filesystem, an atomic rename simply updates directory entry pointers (inodes / MFT records).
- Across different filesystems (e.g., from `/tmp` on a root mount to `/data` on a separate EBS volume), an atomic rename is impossible because the OS must perform a full physical copy followed by a delete, which is not atomic and raises `OSError: [Errno 18] Invalid cross-device link`.
- Therefore, always create temporary swap files in the **same directory** as the destination (`tempfile.NamedTemporaryFile(dir=dest_path.parent)`).
