"""
Day 04: Operators, Control Flow & High-Performance Comprehensions
File: example_02.py - Production AI Document Ingestion & Token Batching Pipeline

ALIGNMENT WITH AI BACKEND ENGINEER ROLE:
Job Description Requirement:
"Integrate LLMs, RAG pipelines... develop features for document ingestion,
chat streaming, and optimize for low latency, token usage, and API cost efficiency."

WHY THIS ARCHITECTURE IS ESSENTIAL:
In RAG (Retrieval-Augmented Generation) systems, raw documents uploaded by users
frequently contain empty sections, noise, excessive whitespace, and arbitrary lengths.
Processing thousands of raw PDF pages using naive nested loops blocks API event loops
and leads to out-of-memory (OOM) crashes in worker pods.

This module implements a streaming ingestion pipeline using:
1. Guard Clauses for defensive payload validation.
2. Generator Expressions for lazy, low-memory text sanitization.
3. Dictionary Comprehensions for metadata indexing.
4. Token window chunking optimized for embedding models.
"""

import time
from typing import Dict, List, Iterator, Any, Optional


class DocumentIngestionError(Exception):
    """Raised when an incoming document payload fails ingestion preconditions."""
    pass


class AIDocumentIngestionPipeline:
    """
    High-performance pipeline for cleaning, filtering, and chunking documents
    prior to vector database embedding.
    """

    def __init__(self, max_token_limit: int = 512, min_token_threshold: int = 10) -> None:
        self.max_token_limit = max_token_limit
        self.min_token_threshold = min_token_threshold

    def validate_incoming_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        """
        WHY GUARD CLAUSES:
        By verifying preconditions and failing immediately (Fail-Fast principle),
        we eliminate nested 'if-else' pyramids and prevent malformed data from
        wasting expensive embedding API calls (cost efficiency).
        """
        if not payload:
            raise DocumentIngestionError("Payload cannot be None or empty")
        
        if "document_id" not in payload or not isinstance(payload["document_id"], str):
            raise DocumentIngestionError("Missing or invalid 'document_id'")

        if "pages" not in payload or not isinstance(payload["pages"], list):
            raise DocumentIngestionError("Payload must contain a 'pages' list")

        if len(payload["pages"]) == 0:
            raise DocumentIngestionError("Document contains zero pages")

    def sanitize_page_stream(self, raw_pages: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
        """
        WHY GENERATOR EXPRESSIONS:
        Using a generator expression streams sanitized page objects on demand.
        If a document contains 5,000 pages, this generator consumes negligible RAM (<1 KB)
        compared to allocating a 5,000-item list upfront.
        """
        return (
            {
                "page_num": page.get("page_num", idx + 1),
                "clean_text": " ".join(page["text"].split()),
                "raw_char_count": len(page["text"])
            }
            for idx, page in enumerate(raw_pages)
            # Guard conditions embedded in generator filter
            if page.get("text") and len(page["text"].strip()) > 0
        )

    def chunk_text_window(
        self,
        sanitized_pages: Iterator[Dict[str, Any]],
        chunk_size: int = 50,
        chunk_overlap: int = 10
    ) -> Iterator[Dict[str, Any]]:
        """
        Generates overlapping token chunks for vector database ingestion.
        Uses sliding window mechanics to preserve semantic context across chunk boundaries.
        """
        chunk_counter = 0

        for page in sanitized_pages:
            words = page["clean_text"].split()
            total_words = len(words)

            if total_words < self.min_token_threshold:
                # Discard noise/empty chunks to save vector storage and embedding costs
                continue

            # Sliding token window
            step = chunk_size - chunk_overlap
            for start_idx in range(0, total_words, step):
                chunk_words = words[start_idx : start_idx + chunk_size]
                
                # Discard tiny trailing fragments
                if len(chunk_words) < self.min_token_threshold and chunk_counter > 0:
                    break

                chunk_counter += 1
                yield {
                    "chunk_id": f"chunk_{chunk_counter:04d}",
                    "page_num": page["page_num"],
                    "token_count": len(chunk_words),
                    "text": " ".join(chunk_words)
                }

    def build_metadata_index(self, chunks: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        WHY DICTIONARY COMPREHENSION:
        Constructs a fast lookup index of chunk token counts in O(N) time at C-speed.
        """
        return {c["chunk_id"]: c["token_count"] for c in chunks}


def run_production_simulation() -> None:
    print("=" * 65)
    print("  PRODUCTION AI RAG DOCUMENT INGESTION & CHUNKING SIMULATION")
    print("=" * 65)

    pipeline = AIDocumentIngestionPipeline(max_token_limit=512, min_token_threshold=5)

    # Simulated PDF extract payload containing noise, empty pages, and real text
    raw_document_payload = {
        "document_id": "doc_rag_q3_report_2026",
        "metadata": {"source": "sec_filing", "department": "finance"},
        "pages": [
            {"page_num": 1, "text": "   \n\n  "},  # Noisy empty page (should be filtered out)
            {
                "page_num": 2, 
                "text": (
                    "Enterprise AI systems require scalable backend APIs built with FastAPI and Python. "
                    "Retrieval-Augmented Generation combines dense semantic search with vector embeddings "
                    "stored in databases like Qdrant or pgvector. LLM agents execute tools dynamically "
                    "to fetch live database records, generate SQL queries, and stream token responses."
                )
            },
            {
                "page_num": 3,
                "text": "Page three header. " * 15  # Repetitive structured text
            }
        ]
    }

    # 1. Validate incoming payload using Guard Clauses
    print("\n[Step 1] Validating payload preconditions with Guard Clauses...")
    pipeline.validate_incoming_payload(raw_document_payload)
    print("         Payload validated successfully!")

    # 2. Lazily sanitize pages
    print("\n[Step 2] Streaming sanitized pages via Generator Expressions...")
    sanitized_stream = pipeline.sanitize_page_stream(raw_document_payload["pages"])

    # 3. Chunk text windows with semantic overlap
    print("\n[Step 3] Chunking text into token windows for Vector DB embedding...")
    start_time = time.perf_counter()
    chunks_generated = list(pipeline.chunk_text_window(sanitized_stream, chunk_size=20, chunk_overlap=5))
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    for chunk in chunks_generated[:3]:
        print(f"         [{chunk['chunk_id']}] Page {chunk['page_num']} | {chunk['token_count']} tokens: '{chunk['text'][:55]}...'")

    # 4. Generate metadata index using Dictionary Comprehension
    metadata_index = pipeline.build_metadata_index(chunks_generated)
    print(f"\n[Step 4] Metadata Index (Dict Comprehension): {metadata_index}")
    print(f"\nExecution Latency: {elapsed_ms:.3f} ms | Total Chunks: {len(chunks_generated)}")
    print("=" * 65)


if __name__ == "__main__":
    run_production_simulation()
