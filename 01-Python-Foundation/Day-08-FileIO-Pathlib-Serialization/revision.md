# Day 08: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before technical interviews and architectural design reviews:

---

- ⚡ **The UTF-8 Mandate:**
  - Always declare `encoding="utf-8"` in `open()`.
  - Never rely on system default locales (`cp1252` on Windows, ASCII in minimal containers) to prevent silent decoding corruption.

- ⚡ **The Three-Layer Storage Pyramid:**
  - `f.write()` $\to$ CPython User Buffer (in RAM).
  - `f.flush()` $\to$ OS Kernel Page Cache (dirty RAM pages).
  - `os.fsync(f.fileno())` $\to$ Non-Volatile Physical Storage (NVMe/SSD). Mandatory for WALs and checkpoints.

- ⚡ **Path Traversal Sandboxing:**
  - Never concatenate user input into paths with string formatting.
  - Resolve canonical path: `target = (base / user_input).resolve()`.
  - Assert containment: `target.is_relative_to(base.resolve())`.

- ⚡ **Memory-Bounded Streaming:**
  - Line streaming: `for line in f:` maintains $\mathcal{O}(1)$ RAM.
  - Binary chunk streaming: `for chunk in iter(lambda: f.read(64*1024), b""): yield chunk`.
  - Never call `f.read()` or `f.readlines()` on unbounded production files.

- ⚡ **Atomic File Updates:**
  - Write to temp file in the **same directory**: `tempfile.NamedTemporaryFile(dir=dest.parent)`.
  - Flush user buffers and fsync: `f.flush(); os.fsync(f.fileno())`.
  - Atomically swap pointers: `os.replace(temp_path, dest_path)`. Guarantees readers never observe partially written files.

- ⚡ **JSON vs. JSONL in AI Systems:**
  - Standard JSON: Requires full-file AST parsing in RAM. Fails on multi-gigabyte datasets.
  - JSONL (JSON Lines): One standalone JSON object per line. Constant $\mathcal{O}(1)$ memory streaming, instant append (`a` mode), trivial worker chunking, and fault isolation.

- ⚡ **Memory-Mapped Files (`mmap`):**
  - Maps disk files directly into the virtual address space.
  - Enables zero-copy binary pattern searching without allocating heap strings.
  - Ideal for multi-gigabyte static files, embeddings, and cross-process shared memory.
