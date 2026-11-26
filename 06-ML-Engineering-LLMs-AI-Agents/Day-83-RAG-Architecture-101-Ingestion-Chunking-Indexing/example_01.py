"""
Day 83: RAG Architecture 101: Ingestion, Chunking & Indexing
Part 1: Multi-Strategy Document Chunking Engine

This module provides:
1. FixedSizeChunker with configurable token-aware chunk size and sliding overlap.
2. RecursiveCharacterChunker implementing multi-tier separator fallback hierarchy.
3. MarkdownHeaderChunker preserving hierarchical heading breadcrumbs as chunk metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class DocumentChunk:
    chunk_id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    word_count: int = 0
    char_count: int = 0


# =====================================================================
# 1. Fixed-Size Sliding Overlap Chunker
# =====================================================================

class FixedSizeChunker:
    """
    Slices text into uniform blocks with sliding window overlap.
    Guarantees facts spanning boundaries are captured in adjacent chunks.
    """
    def __init__(self, chunk_size_words: int = 50, overlap_words: int = 10) -> None:
        if overlap_words >= chunk_size_words:
            raise ValueError("overlap_words must be strictly smaller than chunk_size_words")
        self.chunk_size_words = chunk_size_words
        self.overlap_words = overlap_words

    def chunk(self, text: str, doc_id: str = "doc") -> List[DocumentChunk]:
        words = text.split()
        if not words:
            return []

        chunks: List[DocumentChunk] = []
        step = self.chunk_size_words - self.overlap_words
        idx = 0
        chunk_idx = 0

        while idx < len(words):
            chunk_words = words[idx : idx + self.chunk_size_words]
            chunk_text = " ".join(chunk_words)
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{doc_id}_chunk_{chunk_idx:03d}",
                    text=chunk_text,
                    metadata={"strategy": "fixed_size", "start_word": idx, "end_word": idx + len(chunk_words)},
                    word_count=len(chunk_words),
                    char_count=len(chunk_text),
                )
            )
            chunk_idx += 1
            idx += step

        return chunks


# =====================================================================
# 2. Recursive Character Chunker
# =====================================================================

class RecursiveCharacterChunker:
    """
    Splits text recursively using an ordered list of natural separators:
    ["\\n\\n", "\\n", " ", ""], preserving paragraph and sentence integrity.
    """
    def __init__(
        self,
        chunk_size_chars: int = 250,
        chunk_overlap_chars: int = 40,
        separators: Optional[List[str]] = None,
    ) -> None:
        self.chunk_size_chars = chunk_size_chars
        self.chunk_overlap_chars = chunk_overlap_chars
        self.separators = separators or ["\n\n", "\n", " ", ""]

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
        final_chunks: List[str] = []
        # Find highest-priority separator present in text
        separator = separators[-1]
        for s in separators:
            if s == "" or s in text:
                separator = s
                break

        splits = text.split(separator) if separator != "" else list(text)
        new_separators = separators[separators.index(separator) + 1 :] if separator != "" else []

        good_splits: List[str] = []
        for s in splits:
            if len(s) < self.chunk_size_chars:
                good_splits.append(s)
            else:
                if good_splits:
                    merged = self._merge_splits(good_splits, separator)
                    final_chunks.extend(merged)
                    good_splits = []
                if not new_separators:
                    final_chunks.append(s)
                else:
                    recursive_subchunks = self._split_text(s, new_separators)
                    final_chunks.extend(recursive_subchunks)

        if good_splits:
            merged = self._merge_splits(good_splits, separator)
            final_chunks.extend(merged)

        return final_chunks

    def _merge_splits(self, splits: List[str], separator: str) -> List[str]:
        docs: List[str] = []
        current_doc: List[str] = []
        total = 0

        for s in splits:
            s_len = len(s)
            if total + s_len + len(separator) > self.chunk_size_chars and current_doc:
                docs.append(separator.join(current_doc))
                # Keep overlap if possible
                while total > self.chunk_overlap_chars and len(current_doc) > 1:
                    popped = current_doc.pop(0)
                    total -= len(popped) + len(separator)
            current_doc.append(s)
            total += s_len + len(separator)

        if current_doc:
            docs.append(separator.join(current_doc))
        return docs

    def chunk(self, text: str, doc_id: str = "doc") -> List[DocumentChunk]:
        raw_chunks = self._split_text(text, self.separators)
        results: List[DocumentChunk] = []
        for idx, c in enumerate(raw_chunks):
            c_clean = c.strip()
            if c_clean:
                results.append(
                    DocumentChunk(
                        chunk_id=f"{doc_id}_rec_{idx:03d}",
                        text=c_clean,
                        metadata={"strategy": "recursive_character"},
                        word_count=len(c_clean.split()),
                        char_count=len(c_clean),
                    )
                )
        return results


# =====================================================================
# 3. Markdown Header Hierarchy Chunker
# =====================================================================

class MarkdownHeaderChunker:
    """
    Parses Markdown structures (# H1, ## H2, ### H3), injecting hierarchical
    section breadcrumbs into chunk metadata to preserve structural context.
    """
    def __init__(self) -> None:
        self.header_pattern = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

    def chunk(self, markdown_text: str, doc_id: str = "md_doc") -> List[DocumentChunk]:
        lines = markdown_text.splitlines()
        chunks: List[DocumentChunk] = []

        current_headers: Dict[int, str] = {}
        current_content_lines: List[str] = []
        chunk_idx = 0

        def flush_chunk():
            nonlocal chunk_idx, current_content_lines
            content = "\n".join(current_content_lines).strip()
            if content:
                # Build breadcrumb
                breadcrumb_parts = [current_headers[lvl] for lvl in sorted(current_headers.keys())]
                breadcrumb = " > ".join(breadcrumb_parts) if breadcrumb_parts else "Introduction"
                
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{doc_id}_hdr_{chunk_idx:03d}",
                        text=content,
                        metadata={
                            "strategy": "markdown_header",
                            "breadcrumb": breadcrumb,
                            "section": breadcrumb_parts[-1] if breadcrumb_parts else "Root",
                        },
                        word_count=len(content.split()),
                        char_count=len(content),
                    )
                )
                chunk_idx += 1
            current_content_lines = []

        for line in lines:
            match = self.header_pattern.match(line)
            if match:
                flush_chunk()
                level = len(match.group(1))
                title = match.group(2).strip()

                # Clear sub-headers of deeper levels
                current_headers = {lvl: h for lvl, h in current_headers.items() if lvl < level}
                current_headers[level] = title
            else:
                current_content_lines.append(line)

        flush_chunk()
        return chunks


# =====================================================================
# 4. Demonstration & Verification Suite
# =====================================================================

def run_chunking_demonstration() -> None:
    print("=" * 80)
    print("DAY 83: PRODUCTION MULTI-STRATEGY CHUNKING ENGINE")
    print("=" * 80)

    sample_markdown = """# Enterprise AI Architecture

## 1. Vector Database Tier
Vector databases like pgvector and Qdrant provide high-performance similarity search.
They allow storing high-dimensional embeddings and metadata payloads in unified storage.

### 1.1 HNSW Graph Indexing
Hierarchical Navigable Small World (HNSW) graphs organize vectors into multi-layer skip lists.
This delivers logarithmic search complexity and high recall rates.

## 2. Context Window Management
Context windows are strictly bounded by hardware GPU VRAM constraints.
LLM prompts must budget tokens across system instructions, RAG context, and conversational history.
"""

    # 1. Test Fixed-Size Chunker
    print("\n--- 1. Testing Fixed-Size Sliding Overlap Chunker ---")
    fixed_chunker = FixedSizeChunker(chunk_size_words=20, overlap_words=5)
    fixed_chunks = fixed_chunker.chunk(sample_markdown, doc_id="policy")
    print(f"Generated {len(fixed_chunks)} fixed chunks.")
    for c in fixed_chunks[:2]:
        print(f"  [{c.chunk_id}] ({c.word_count} words): \"{c.text[:50]}...\"")

    # 2. Test Recursive Character Chunker
    print("\n--- 2. Testing Recursive Character Chunker ---")
    rec_chunker = RecursiveCharacterChunker(chunk_size_chars=180, chunk_overlap_chars=30)
    rec_chunks = rec_chunker.chunk(sample_markdown, doc_id="rec_doc")
    print(f"Generated {len(rec_chunks)} recursive chunks.")
    for c in rec_chunks[:2]:
        print(f"  [{c.chunk_id}] ({c.char_count} chars): \"{c.text[:50]}...\"")

    # 3. Test Markdown Header Chunker
    print("\n--- 3. Testing Markdown Header Chunker (Breadcrumb Extraction) ---")
    md_chunker = MarkdownHeaderChunker()
    md_chunks = md_chunker.chunk(sample_markdown, doc_id="guide")
    print(f"Generated {len(md_chunks)} markdown sections.")
    for c in md_chunks:
        print(f"  [{c.chunk_id}] Breadcrumb: '{c.metadata['breadcrumb']}'")
        print(f"    Preview: \"{c.text[:60]}...\"\n")

    # Validations
    assert len(fixed_chunks) >= 3, "Expected at least 3 fixed chunks"
    assert len(rec_chunks) >= 2, "Expected at least 2 recursive chunks"
    assert len(md_chunks) == 3, f"Expected 3 markdown sections, got {len(md_chunks)}"
    assert md_chunks[1].metadata["breadcrumb"] == "Enterprise AI Architecture > 1. Vector Database Tier > 1.1 HNSW Graph Indexing"

    print("[SUCCESS] All Multi-Strategy Chunking Engine Assertions Passed!\n")


if __name__ == "__main__":
    run_chunking_demonstration()
