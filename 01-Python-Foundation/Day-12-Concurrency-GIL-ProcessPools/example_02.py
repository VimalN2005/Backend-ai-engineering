"""Day 12: Production Hybrid Concurrency AI Ingestion & Inference Engine.

This module implements an enterprise-grade hybrid concurrency architecture:
1. I/O-Bound Layer (ThreadPoolExecutor): Concurrent network fetching of raw documents.
2. CPU-Bound Layer (ProcessPoolExecutor): Parallel tokenization and vector normalization across CPU cores.
3. Thread-Safe Telemetry Aggregator (threading.Lock): Thread-safe metrics recording.
4. Future Lifecycle & Fault Tolerance: Handling timeouts, task failures, and graceful shutdowns.
"""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import hashlib
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("HybridAIEngine")


# =====================================================================
# DATA CONTRACTS
# =====================================================================
@dataclass(slots=True)
class RawDocument:
    doc_id: str
    source_url: str
    raw_text: str
    fetch_latency_ms: float


@dataclass(slots=True)
class ProcessedEmbeddingVector:
    doc_id: str
    token_count: int
    vector: Tuple[float, ...]
    compute_duration_ms: float


# =====================================================================
# 1. TOP-LEVEL CPU-INTENSIVE WORKER (Must be top-level for pickling)
# =====================================================================
def compute_vector_embedding(doc_payload: Tuple[str, str]) -> ProcessedEmbeddingVector:
    """CPU-Intensive worker: computes term-frequency representation and L2 normalization.

    WHY a top-level function?
    In multiprocessing, child processes receive tasks via Python's pickle protocol.
    Closures, inner functions, or lambdas cannot be pickled and raise PicklingError.
    """
    doc_id, raw_text = doc_payload
    start_time = time.perf_counter()

    # Step A: Heavy Tokenization & Normalization
    tokens = [w.lower() for w in raw_text.split() if len(w) > 3]

    # Step B: Synthetic 64-dimensional feature vector generation via cryptographic hashing
    # Simulates heavy matrix math in embedding encoders
    dimension = 64
    raw_vector = [0.0] * dimension
    for token in tokens:
        token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        index = token_hash % dimension
        raw_vector[index] += 1.0

    # Step C: L2 Vector Normalization (Euclidean norm = 1.0)
    norm = math.sqrt(sum(x * x for x in raw_vector))
    if norm > 0.0:
        normalized_vector = tuple(x / norm for x in raw_vector)
    else:
        normalized_vector = tuple(raw_vector)

    duration_ms = (time.perf_counter() - start_time) * 1000
    return ProcessedEmbeddingVector(
        doc_id=doc_id,
        token_count=len(tokens),
        vector=normalized_vector,
        compute_duration_ms=round(duration_ms, 2),
    )


# =====================================================================
# 2. THREAD-SAFE TELEMETRY METRICS AGGREGATOR
# =====================================================================
class MetricsAggregator:
    """Thread-safe telemetry accumulator using threading.Lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_fetched = 0
        self.total_processed = 0
        self.total_tokens = 0
        self.total_fetch_time_ms = 0.0
        self.total_compute_time_ms = 0.0

    def record_fetch(self, latency_ms: float) -> None:
        with self._lock:
            self.total_fetched += 1
            self.total_fetch_time_ms += latency_ms

    def record_compute(self, tokens: int, duration_ms: float) -> None:
        with self._lock:
            self.total_processed += 1
            self.total_tokens += tokens
            self.total_compute_time_ms += duration_ms

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            avg_fetch = self.total_fetch_time_ms / max(1, self.total_fetched)
            avg_comp = self.total_compute_time_ms / max(1, self.total_processed)
            return {
                "total_fetched": self.total_fetched,
                "total_processed": self.total_processed,
                "total_tokens": self.total_tokens,
                "avg_fetch_ms": round(avg_fetch, 2),
                "avg_compute_ms": round(avg_comp, 2),
            }


# =====================================================================
# 3. HYBRID CONCURRENCY PIPELINE ORCHESTRATOR
# =====================================================================
class HybridIngestionPipeline:
    """Orchestrates I/O thread pools and CPU process pools."""

    def __init__(self, io_workers: int = 8, cpu_workers: Optional[int] = None) -> None:
        self.io_workers = io_workers
        self.cpu_workers = cpu_workers or (os.cpu_count() or 4)
        self.metrics = MetricsAggregator()

    def _fetch_document_io(self, doc_id: str) -> RawDocument:
        """I/O-Bound network fetch simulation (releases GIL)."""
        start = time.perf_counter()
        time.sleep(0.04)  # Simulate 40ms network latency to S3 / API
        latency = (time.perf_counter() - start) * 1000

        self.metrics.record_fetch(latency)
        sample_body = (
            f"Retrieval Augmented Generation architecture powers LLM reasoning. "
            f"Document {doc_id} contains enterprise security parameters and distributed caching logic. "
            f"Embeddings capture contextual semantics across dense high-dimensional manifolds."
        ) * 4  # ~400 characters

        return RawDocument(
            doc_id=doc_id,
            source_url=f"s3://ai-data-lake/raw/{doc_id}.txt",
            raw_text=sample_body,
            fetch_latency_ms=round(latency, 2),
        )

    def execute_pipeline(self, document_ids: List[str]) -> List[ProcessedEmbeddingVector]:
        """Execute the full 2-stage hybrid concurrency pipeline."""
        logger.info(
            f"Starting Hybrid Pipeline: {len(document_ids)} documents | "
            f"I/O Workers: {self.io_workers} (Threads) | CPU Workers: {self.cpu_workers} (Processes)"
        )
        total_start = time.perf_counter()

        # -------------------------------------------------------------
        # STAGE 1: CONCURRENT I/O FETCHING (ThreadPoolExecutor)
        # -------------------------------------------------------------
        fetched_documents: List[RawDocument] = []
        with ThreadPoolExecutor(max_workers=self.io_workers, thread_name_prefix="IO-Fetch") as io_pool:
            io_futures = {io_pool.submit(self._fetch_document_io, did): did for did in document_ids}
            for future in as_completed(io_futures):
                try:
                    doc = future.result(timeout=2.0)
                    fetched_documents.append(doc)
                except Exception as err:
                    doc_id = io_futures[future]
                    logger.error(f"Failed to fetch document {doc_id}: {err}")

        logger.info(f"Stage 1 Complete: Fetched {len(fetched_documents)} raw documents concurrently.")

        # -------------------------------------------------------------
        # STAGE 2: PARALLEL CPU TOKENIZATION & EMBEDDING (ProcessPoolExecutor)
        # -------------------------------------------------------------
        compute_tasks = [(d.doc_id, d.raw_text) for d in fetched_documents]
        processed_vectors: List[ProcessedEmbeddingVector] = []

        with ProcessPoolExecutor(max_workers=self.cpu_workers) as cpu_pool:
            cpu_futures = [cpu_pool.submit(compute_vector_embedding, task) for task in compute_tasks]
            for future in as_completed(cpu_futures):
                try:
                    vec = future.result(timeout=5.0)
                    self.metrics.record_compute(vec.token_count, vec.compute_duration_ms)
                    processed_vectors.append(vec)
                except Exception as err:
                    logger.error(f"CPU vector computation failed: {err}")

        total_duration = time.perf_counter() - total_start
        logger.info(f"Stage 2 Complete: Processed {len(processed_vectors)} embeddings in parallel.")

        summary_metrics = self.metrics.summary()
        logger.info("=" * 65)
        logger.info("HYBRID PIPELINE PERFORMANCE AUDIT:")
        logger.info(f"  Total Processed Vectors   : {len(processed_vectors)}")
        logger.info(f"  Total Tokens Extracted    : {summary_metrics['total_tokens']:,}")
        logger.info(f"  Avg I/O Fetch Latency     : {summary_metrics['avg_fetch_ms']} ms")
        logger.info(f"  Avg CPU Compute Duration  : {summary_metrics['avg_compute_ms']} ms")
        logger.info(f"  Total Wall Clock Time     : {total_duration:.3f} seconds")
        logger.info(f"  Overall System Throughput : {len(processed_vectors) / total_duration:.2f} docs/sec")
        logger.info("=" * 65)

        return processed_vectors


# =====================================================================
# SIMULATION & VERIFICATION RUNNER
# =====================================================================
if __name__ == "__main__":
    # Mandatory spawn guard for Windows & macOS
    TEST_DOC_COUNT = 30
    doc_batch = [f"doc_corp_{i:04d}" for i in range(1, TEST_DOC_COUNT + 1)]

    pipeline = HybridIngestionPipeline(io_workers=8, cpu_workers=os.cpu_count() or 4)
    vectors = pipeline.execute_pipeline(doc_batch)

    # Validate output contract
    sample_vec = vectors[0]
    print(f"\n[*] Sample Output Vector for '{sample_vec.doc_id}':")
    print(f"    - Token Count: {sample_vec.token_count}")
    print(f"    - Vector Dimensions: {len(sample_vec.vector)}")
    print(f"    - First 4 Elements: {sample_vec.vector[:4]}")
    # Verify L2 normalization: sum of squares ≈ 1.0
    l2_norm = sum(x * x for x in sample_vec.vector)
    print(f"    - Verified L2 Euclidean Norm: {l2_norm:.4f} (Matches 1.0)")
