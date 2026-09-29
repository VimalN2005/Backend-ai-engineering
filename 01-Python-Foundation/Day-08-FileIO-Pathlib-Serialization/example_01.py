"""Day 08: Core File I/O, Pathlib, Custom Serialization, and Memory-Mapped Files.

This module demonstrates the mechanics of Python's I/O subsystems:
1. Object-oriented Pathlib and Path Traversal Jail Security.
2. CPython User Buffer vs OS Page Cache (flush vs fsync).
3. Memory-Bounded Stream Reading (iter with sentinel).
4. Custom JSON Serialization for Production Enterprise Types.
5. Zero-Copy Substring Scanning via Memory-Mapped Files (mmap).
"""

import io
import json
import mmap
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import UUID, uuid4


# =====================================================================
# 1. PATHLIB & PATH TRAVERSAL JAIL SECURITY
# =====================================================================
def demonstrate_pathlib_and_security() -> None:
    print("=" * 65)
    print("1. PATHLIB & PATH TRAVERSAL JAIL SECURITY")
    print("=" * 65)

    # WHY Pathlib?
    # pathlib.Path abstracts away Windows vs POSIX path separator differences (\ vs /),
    # provides immutable path manipulation, and prevents fragile string-splitting bugs.
    sandbox_dir = Path(tempfile.gettempdir()) / "backend_ai_sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Base Sandbox Directory: {sandbox_dir}")

    def safe_resolve(base: Path, user_input: str) -> Path:
        """Resolve a path safely, preventing directory traversal attacks.

        WHY?
        Untrusted user inputs like '../../etc/passwd' or '..\\..\\Windows\\System32'
        can escape the intended directory if concatenated as raw strings.
        Using .resolve() computes the canonical absolute path (resolving all '..'
        and symlinks). We then check .is_relative_to(base) to enforce containment.
        """
        # Resolve canonical target path
        target = (base / user_input).resolve()
        resolved_base = base.resolve()

        if not target.is_relative_to(resolved_base):
            raise PermissionError(
                f"Security Breach Detected: '{user_input}' attempts to escape sandbox '{resolved_base}'"
            )
        return target

    # Valid path test
    safe_path = safe_resolve(sandbox_dir, "uploads/rag_documents/invoice_001.pdf")
    print(f"  [ALLOW] Valid relative path resolved: {safe_path.name}")

    # Malicious traversal test
    try:
        malicious_input = "../../../etc/shadow"
        safe_resolve(sandbox_dir, malicious_input)
    except PermissionError as err:
        print(f"  [BLOCKED] Traversal attempt intercepted: {err}")


# =====================================================================
# 2. CPYTHON USER BUFFER VS OS PAGE CACHE (flush vs fsync)
# =====================================================================
def demonstrate_buffering_and_durability() -> None:
    print("\n" + "=" * 65)
    print("2. BUFFERING STRATEGIES & HARDWARE DURABILITY")
    print("=" * 65)

    # WHY io.DEFAULT_BUFFER_SIZE?
    # Context switches from user space to kernel space are computationally expensive.
    # CPython maintains a default memory buffer (typically 8,192 bytes = 8 KB)
    # in user space to coalesce small writes into larger bulk operations.
    print(f"[*] Default CPython I/O Buffer Size: {io.DEFAULT_BUFFER_SIZE} bytes ({io.DEFAULT_BUFFER_SIZE // 1024} KB)")

    temp_file = Path(tempfile.gettempdir()) / "durability_test.wal"

    # WHY with statement + explicit flush/fsync?
    # 1. f.write() writes ONLY to CPython's in-memory user buffer.
    # 2. f.flush() flushes CPython memory to the OS kernel page cache.
    # 3. os.fsync() forces the OS kernel to flush its dirty pages to physical NVMe/SSD disk.
    with open(temp_file, "w", encoding="utf-8") as f:
        f.write("TX_RECORD: COMMIT_TX_ID=9821034\n")
        f.flush()               # Step 1: User space -> Kernel Page Cache
        os.fsync(f.fileno())    # Step 2: Kernel Page Cache -> Non-Volatile Storage

    print(f"[*] Durable Write Verified on disk: {temp_file} (Size: {temp_file.stat().st_size} bytes)")
    temp_file.unlink(missing_ok=True)


# =====================================================================
# 3. MEMORY-BOUNDED STREAM READING (iter with sentinel)
# =====================================================================
def demonstrate_stream_chunking() -> None:
    print("\n" + "=" * 65)
    print("3. MEMORY-BOUNDED STREAM READING (iter with sentinel)")
    print("=" * 65)

    # Create a synthetic 100 KB binary payload
    source_file = Path(tempfile.gettempdir()) / "synthetic_stream.bin"
    chunk_payload = b"PACKET_HEADER_DATA_1234567890\n" * 1024  # ~31 KB
    source_file.write_bytes(chunk_payload * 3)                   # ~93 KB

    # WHY iter(callable, sentinel)?
    # When processing 10 GB or 50 GB dataset files, calling f.read() or f.readlines()
    # loads the entire file into RAM, triggering the Linux OOM killer.
    # The two-argument iter(lambda: f.read(chunk_size), b"") creates a generator
    # that reads fixed-size byte buffers until f.read() returns the sentinel (b""),
    # maintaining strictly constant O(1) memory overhead.
    def read_in_chunks(filepath: Path, chunk_size: int = 16 * 1024) -> Generator[bytes, None, None]:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                yield chunk

    total_chunks = 0
    total_bytes = 0
    for chunk in read_in_chunks(source_file, chunk_size=32 * 1024):
        total_chunks += 1
        total_bytes += len(chunk)

    print(f"[*] Streamed {total_bytes} bytes across {total_chunks} chunks in O(1) constant memory.")
    source_file.unlink(missing_ok=True)


# =====================================================================
# 4. CUSTOM JSON SERIALIZATION FOR PRODUCTION TYPES
# =====================================================================
@dataclass
class RAGDocumentMetadata:
    doc_id: UUID
    title: str
    tokens: int
    created_at: datetime
    embedding_model: str


def demonstrate_custom_json_serialization() -> None:
    print("\n" + "=" * 65)
    print("4. CUSTOM JSON SERIALIZATION (DATACLASS, UUID, DATETIME)")
    print("=" * 65)

    # WHY a custom serializer function?
    # Python's built-in json module raises TypeError on datetime, UUID, and dataclasses.
    # Instead of converting objects manually everywhere, passing a 'default' handler
    # centralizes normalization logic for enterprise entities.
    def production_json_encoder(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, set):
            return list(obj)
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    record = RAGDocumentMetadata(
        doc_id=uuid4(),
        title="Attention Is All You Need (Transformer Architecture)",
        tokens=14200,
        created_at=datetime.now(timezone.utc),
        embedding_model="text-embedding-3-large",
    )

    serialized_json = json.dumps(record, default=production_json_encoder, indent=2)
    print("[*] Serialized Complex Dataclass to JSON string:")
    print(serialized_json)


# =====================================================================
# 5. ZERO-COPY SCANNING VIA MEMORY-MAPPED FILES (mmap)
# =====================================================================
def demonstrate_memory_mapped_search() -> None:
    print("\n" + "=" * 65)
    print("5. ZERO-COPY SCANNING VIA MEMORY-MAPPED FILES (mmap)")
    print("=" * 65)

    # WHY mmap?
    # Standard file reads copy data from kernel buffer cache into CPython heap memory.
    # mmap maps the file directly into the process's virtual address space.
    # Searching for substrings or regex patterns in a multi-gigabyte log file
    # is executed via direct memory pointer offsets without allocating Python string objects.
    log_file = Path(tempfile.gettempdir()) / "server_access.log"

    # Seed log file
    with open(log_file, "wb") as f:
        f.write(b"[INFO] System initialized\n" * 1000)
        f.write(b"[FATAL] GPU Out Of Memory: CudaAllocError at line 9821\n")
        f.write(b"[INFO] Heartbeat OK\n" * 1000)

    with open(log_file, "r+b") as f:
        # Map the entire file into virtual memory (length=0 maps whole file)
        # ACCESS_READ prevents accidental mutations to the underlying file
        with mmap.mmap(f.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
            print(f"[*] Memory-Mapped File Size: {len(mm)} bytes")

            target_needle = b"[FATAL]"
            offset = mm.find(target_needle)

            if offset != -1:
                # Seek to offset and read line directly from virtual memory
                mm.seek(offset)
                critical_log_line = mm.readline().decode("utf-8").strip()
                print(f"  [MATCH FOUND] Byte Offset {offset}: '{critical_log_line}'")
            else:
                print("  [NOT FOUND] Target pattern does not exist in log file.")

    log_file.unlink(missing_ok=True)


# =====================================================================
# MAIN RUNNER
# =====================================================================
if __name__ == "__main__":
    demonstrate_pathlib_and_security()
    demonstrate_buffering_and_durability()
    demonstrate_stream_chunking()
    demonstrate_custom_json_serialization()
    demonstrate_memory_mapped_search()
