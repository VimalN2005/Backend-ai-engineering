"""
Day 07: Robust Exception Hierarchies, Context Managers & Resource Safety
File: example_02.py - Production Atomic Multi-Resource Transaction Manager (RAG Ingestion)

ALIGNMENT WITH AI BACKEND ENGINEER ROLE:
Job Description Requirement:
"Work with SQL/NoSQL databases and asynchronous systems...
Develop features for document ingestion, chat streaming, and tool execution."

WHY THIS ARCHITECTURE IS ESSENTIAL:
In production RAG systems, ingesting a document is a multi-step operation:
1. Insert document metadata into PostgreSQL (`documents` table).
2. Upsert high-dimensional vector embeddings into the Vector Database (Qdrant).

If Step 1 succeeds but Step 2 crashes (e.g. network timeout or out-of-quota error),
a naive backend leaves orphaned document records in PostgreSQL pointing to non-existent
vectors, corrupting search indexes and causing silent retrieval failures!

This module implements an Atomic Ingestion Transaction Context Manager that:
1. Enforces transactional atomicity across database records.
2. Automatically triggers database rollback if vector upsert fails.
3. Translates low-level socket failures into structured domain errors.
"""

import time
import logging
from contextlib import contextmanager
from typing import Generator, Dict, Any, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# =====================================================================
# DOMAIN EXCEPTIONS
# =====================================================================

class IngestionPipelineError(Exception):
    """Base exception for document ingestion pipeline failures."""
    pass


class VectorStoreUpsertError(IngestionPipelineError):
    """Raised when writing embeddings to Qdrant/Pinecone fails."""
    pass


class DatabaseTransactionError(IngestionPipelineError):
    """Raised when PostgreSQL transaction commit fails."""
    pass


# =====================================================================
# ATOMIC TRANSACTION MANAGER
# =====================================================================

class MockPostgresSession:
    """Simulates a SQLAlchemy / psycopg database transaction session."""

    def __init__(self):
        self.is_active = True
        self.staged_records: List[str] = []

    def insert(self, record_name: str) -> None:
        logging.info(f"  [PostgreSQL] Staging insert: '{record_name}'")
        self.staged_records.append(record_name)

    def commit(self) -> None:
        logging.info(f"  [PostgreSQL] COMMIT: Successfully persisted {len(self.staged_records)} records to disk.")
        self.is_active = False

    def rollback(self) -> None:
        logging.warning(f"  [PostgreSQL] ROLLBACK: Reverting all staged changes: {self.staged_records}!")
        self.staged_records.clear()
        self.is_active = False


class MockVectorStoreClient:
    """Simulates Qdrant / Pinecone vector collection operations."""

    @staticmethod
    def upsert_vectors(doc_id: str, vectors: List[List[float]], fail_simulation: bool = False) -> None:
        logging.info(f"  [Vector DB] Upserting {len(vectors)} embedding vectors for doc_id='{doc_id}'...")
        if fail_simulation:
            raise ConnectionError("Qdrant collection timed out: socket connection dropped.")
        logging.info(f"  [Vector DB] Vectors indexed successfully.")


class AtomicRAGIngestionManager:
    """
    Coordinates atomic transactions between PostgreSQL and Vector Database.
    """

    @classmethod
    @contextmanager
    def atomic_transaction(cls) -> Generator[MockPostgresSession, None, None]:
        """
        WHY CONTEXT MANAGER:
        Guarantees that any exception in the caller's with-block automatically
        executes session.rollback() before releasing the connection.
        """
        session = MockPostgresSession()
        logging.info("[TX BEGIN] Starting atomic database transaction.")
        try:
            yield session
            # If no exception occurred in caller's with-block, commit!
            session.commit()
            logging.info("[TX SUCCESS] Transaction committed cleanly.")
        except Exception as err:
            # If ANY failure occurs, roll back PostgreSQL state immediately!
            session.rollback()
            logging.error(f"[TX ABORTED] Rolled back transaction due to error: {err}")
            raise DatabaseTransactionError("Transaction aborted due to downstream failure") from err
        finally:
            logging.info("[TX CLEANUP] Connection returned to connection pool.")


def execute_document_ingestion(document_id: str, trigger_failure: bool = False) -> None:
    """
    Demonstrates atomic dual-system ingestion.
    """
    print(f"\n--- [INGESTING DOCUMENT: {document_id}] ---")
    
    try:
        # Wrap relational changes inside the atomic transaction context manager
        with AtomicRAGIngestionManager.atomic_transaction() as db:
            # Step 1: Insert document metadata into PostgreSQL
            db.insert(f"doc_metadata_{document_id}")

            # Step 2: Generate and upsert vector embeddings to Qdrant
            # Simulated embeddings: 3 vectors of dimension 4
            dummy_vectors = [[0.1, 0.2, 0.3, 0.4]] * 3
            try:
                MockVectorStoreClient.upsert_vectors(document_id, dummy_vectors, fail_simulation=trigger_failure)
            except ConnectionError as net_err:
                # Chain vector store error to domain exception
                raise VectorStoreUpsertError(f"Vector DB upsert failed for {document_id}") from net_err

    except IngestionPipelineError as pipeline_err:
        print(f"\n[ALERT] Pipeline handled domain failure: {pipeline_err}")
        print(f"        Underlying cause: {pipeline_err.__cause__}")


def run_production_simulation() -> None:
    print("=" * 65)
    print("  PRODUCTION ATOMIC MULTI-RESOURCE TRANSACTION SIMULATION")
    print("=" * 65)

    # Scenario 1: Clean ingestion (both DB and Vector DB succeed)
    execute_document_ingestion("doc_q3_financial_report", trigger_failure=False)

    # Scenario 2: Vector DB crashes midway -> PostgreSQL must auto-rollback!
    execute_document_ingestion("doc_corrupted_payload", trigger_failure=True)

    print("\n" + "=" * 65)


if __name__ == "__main__":
    run_production_simulation()
