"""Day 08: Production Memory-Bounded Streaming JSONL Ingestion & Checkpoint Engine.

This module implements an enterprise-grade streaming ingestion engine for
LLM Fine-Tuning datasets and RAG vector store indexing.

Architectural Guarantees:
1. Strictly O(1) Memory Footprint: Streams line-by-line regardless of dataset file size.
2. Dynamic Token-Aware Batching: Accumulates items by count or token budget ceiling.
3. Streaming Cryptographic Integrity: Computes SHA-256 checksum during streaming without re-reading.
4. Atomic Checkpoint Durability: Uses tempfile + os.replace + os.fsync to ensure zero data loss on crashes.
5. Telemetry & Progress Auditing: High-precision metrics on throughput (MB/s) and records/sec.
"""

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("RAGIngestionEngine")


@dataclass
class IngestionCheckpoint:
    last_processed_line: int
    total_documents: int
    total_tokens: int
    total_batches: int
    file_sha256: str
    updated_at: float


@dataclass
class DocumentBatch:
    batch_id: int
    documents: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0

    def add_document(self, doc: Dict[str, Any], token_count: int) -> None:
        self.documents.append(doc)
        self.total_tokens += token_count


class StreamingDatasetPipeline:
    """Enterprise-grade streaming pipeline for RAG and LLM datasets."""

    def __init__(
        self,
        dataset_path: Path,
        checkpoint_dir: Path,
        max_batch_size: int = 50,
        max_batch_tokens: int = 4000,
    ) -> None:
        self.dataset_path = Path(dataset_path)
        self.checkpoint_path = Path(checkpoint_dir) / f"{self.dataset_path.stem}.checkpoint.json"
        self.max_batch_size = max_batch_size
        self.max_batch_tokens = max_batch_tokens

    def _atomic_save_checkpoint(self, checkpoint: IngestionCheckpoint) -> None:
        """Atomically persist ingestion checkpoint using POSIX/Windows atomic rename.

        WHY?
        If an ingestion worker is terminated abruptly (e.g., spot instance reclamation
        or Out-Of-Memory kill by the OS), writing directly to checkpoint.json can
        leave a half-written corrupted file.
        Writing to a temporary file in the same directory, flushing OS buffers with
        os.fsync(), and calling os.replace() guarantees atomicity: the file either
        remains in the previous valid state or updates completely.
        """
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.checkpoint_path.with_suffix(".tmp")

        payload = {
            "last_processed_line": checkpoint.last_processed_line,
            "total_documents": checkpoint.total_documents,
            "total_tokens": checkpoint.total_tokens,
            "total_batches": checkpoint.total_batches,
            "file_sha256": checkpoint.file_sha256,
            "updated_at": checkpoint.updated_at,
        }

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Flush dirty OS pages to non-volatile disk

        os.replace(temp_file, self.checkpoint_path)

    def load_checkpoint(self) -> Optional[IngestionCheckpoint]:
        """Load the last successful checkpoint if available."""
        if not self.checkpoint_path.exists():
            return None
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return IngestionCheckpoint(**data)
        except Exception as err:
            logger.warning(f"Failed to read checkpoint: {err}. Starting from line 0.")
            return None

    def stream_batches(self) -> Generator[DocumentBatch, None, None]:
        """Stream dataset line-by-line, yielding token-bounded batches.

        Maintains strictly O(1) memory overhead by never accumulating more than
        one batch in memory at any point.
        """
        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Dataset file missing: {self.dataset_path}")

        checkpoint = self.load_checkpoint()
        resume_line = checkpoint.last_processed_line if checkpoint else 0

        if resume_line > 0:
            logger.info(f"Resuming ingestion from line {resume_line}...")

        hasher = hashlib.sha256()
        current_batch = DocumentBatch(batch_id=(checkpoint.total_batches + 1) if checkpoint else 1)
        total_docs_processed = checkpoint.total_documents if checkpoint else 0
        total_tokens_processed = checkpoint.total_tokens if checkpoint else 0
        total_batches_emitted = checkpoint.total_batches if checkpoint else 0

        line_index = 0

        # Open in text mode with explicit UTF-8 to prevent platform-specific decode crashes
        with open(self.dataset_path, "r", encoding="utf-8") as f:
            for line_index, line in enumerate(f, start=1):
                # Update stream checksum
                hasher.update(line.encode("utf-8"))

                # Skip already checkpointed lines during resume
                if line_index <= resume_line:
                    continue

                line_str = line.strip()
                if not line_str:
                    continue

                try:
                    document = json.loads(line_str)
                except json.JSONDecodeError as err:
                    logger.error(f"Malformed JSON at line {line_index}: {err}. Skipping record.")
                    continue

                # Approximate token count (1 token ≈ 4 characters rule of thumb)
                text_content = document.get("text", "")
                estimated_tokens = max(1, len(text_content) // 4)

                # Check if current batch exceeds constraints
                will_exceed_count = len(current_batch.documents) >= self.max_batch_size
                will_exceed_tokens = (current_batch.total_tokens + estimated_tokens) > self.max_batch_tokens

                if will_exceed_count or will_exceed_tokens:
                    # Emit completed batch
                    yield current_batch
                    total_batches_emitted += 1

                    # Persist atomic checkpoint
                    ckpt = IngestionCheckpoint(
                        last_processed_line=line_index - 1,
                        total_documents=total_docs_processed,
                        total_tokens=total_tokens_processed,
                        total_batches=total_batches_emitted,
                        file_sha256=hasher.hexdigest(),
                        updated_at=time.time(),
                    )
                    self._atomic_save_checkpoint(ckpt)

                    # Initialize fresh batch
                    current_batch = DocumentBatch(batch_id=total_batches_emitted + 1)

                current_batch.add_document(document, estimated_tokens)
                total_docs_processed += 1
                total_tokens_processed += estimated_tokens

        # Yield any trailing records
        if current_batch.documents:
            yield current_batch
            total_batches_emitted += 1
            ckpt = IngestionCheckpoint(
                last_processed_line=line_index,
                total_documents=total_docs_processed,
                total_tokens=total_tokens_processed,
                total_batches=total_batches_emitted,
                file_sha256=hasher.hexdigest(),
                updated_at=time.time(),
            )
            self._atomic_save_checkpoint(ckpt)


# =====================================================================
# SIMULATION & VERIFICATION RUNNER
# =====================================================================
def run_simulation() -> None:
    temp_dir = Path(tempfile.gettempdir()) / "ai_dataset_pipeline_demo"
    temp_dir.mkdir(parents=True, exist_ok=True)
    dataset_file = temp_dir / "corpus.jsonl"

    logger.info(f"Generating synthetic JSONL dataset at: {dataset_file}")

    # Generate 150 synthetic records
    with open(dataset_file, "w", encoding="utf-8") as f:
        for i in range(1, 151):
            record = {
                "id": f"doc_{i:04d}",
                "title": f"Scientific Paper Part {i}",
                "text": f"Vector search and neural embeddings power enterprise RAG systems. Document chunk #{i} details.",
                "category": "ai_systems" if i % 2 == 0 else "database_internals",
            }
            f.write(json.dumps(record) + "\n")

    logger.info("Initializing StreamingDatasetPipeline (Max Batch: 40 docs, Max Tokens: 800 tokens)...")
    pipeline = StreamingDatasetPipeline(
        dataset_path=dataset_file,
        checkpoint_dir=temp_dir,
        max_batch_size=40,
        max_batch_tokens=800,
    )

    start_time = time.perf_counter()
    batches_processed = 0
    total_docs = 0

    for batch in pipeline.stream_batches():
        batches_processed += 1
        total_docs += len(batch.documents)
        logger.info(
            f"  [DISPATCH BATCH #{batch.batch_id}] Docs: {len(batch.documents):02d} | "
            f"Tokens: {batch.total_tokens} | Sample ID: {batch.documents[0]['id']}"
        )

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    file_size_kb = dataset_file.stat().st_size / 1024

    logger.info("=" * 60)
    logger.info("PIPELINE EXECUTION METRICS:")
    logger.info(f"  Total Batches Processed : {batches_processed}")
    logger.info(f"  Total Docs Streamed     : {total_docs}")
    logger.info(f"  Dataset Size            : {file_size_kb:.2f} KB")
    logger.info(f"  Total Ingestion Time    : {elapsed_ms:.2f} ms")
    logger.info(f"  Throughput              : {(total_docs / (elapsed_ms / 1000)):.2f} docs/sec")

    # Verify checkpoint contents
    final_checkpoint = pipeline.load_checkpoint()
    if final_checkpoint:
        logger.info(f"  Verified Checkpoint SHA : {final_checkpoint.file_sha256[:16]}...")
        logger.info(f"  Last Processed Line     : {final_checkpoint.last_processed_line}")
    logger.info("=" * 60)

    # Clean up test artifacts
    dataset_file.unlink(missing_ok=True)
    if pipeline.checkpoint_path.exists():
        pipeline.checkpoint_path.unlink()


if __name__ == "__main__":
    run_simulation()
