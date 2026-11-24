# Day 81: Production Exercises — LLM Foundations & Streaming

Below are 3 enterprise-grade production engineering exercises covering multi-tier context window compaction, multibyte UTF-8 stream reassembly, and GPU KV-Cache capacity planning.

---

## Exercise 1: Token-Aware Multi-Tier Context Compaction Engine

### Problem Statement
In production multi-turn conversational agents with Retrieval-Augmented Generation (RAG), prompts can easily overflow the model context window. Implement a `ContextCompactionEngine` that:
1. Strictly **pins** the System Prompt at the top.
2. Accepts incoming RAG context chunks, ranking them by relevance score and keeping only chunks that fit within the RAG budget ($T_{\text{rag}} \le 1200$ tokens).
3. If conversation history exceeds the history budget ($T_{\text{hist}} \le 800$ tokens), rather than dropping messages entirely, it condenses the oldest $K$ turns into a single summary block `{"role": "system", "content": "[PREVIOUS CONTEXT SUMMARY]: ..."}` and preserves the most recent 2 turns.

### Complete Solution & Verification Test

```python
"""
Exercise 1: Token-Aware Multi-Tier Context Compaction Engine
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class RAGChunk:
    chunk_id: str
    text: str
    relevance_score: float
    token_count: int


class ContextCompactionEngine:
    def __init__(
        self,
        max_prompt_budget: int = 2500,
        rag_budget: int = 1200,
        history_budget: int = 800,
    ) -> None:
        self.max_prompt_budget = max_prompt_budget
        self.rag_budget = rag_budget
        self.history_budget = history_budget

    def _count_tokens(self, text: str) -> int:
        # Standard token heuristic: 1 token ≈ 4 characters
        return max(1, len(text) // 4)

    def compact_and_assemble(
        self,
        system_prompt: str,
        rag_chunks: List[RAGChunk],
        chat_history: List[Dict[str, str]],
        current_query: str,
    ) -> List[Dict[str, str]]:
        # 1. Pin System Prompt
        assembled_messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # 2. Filter & Sort RAG Chunks by relevance score (Greedy Packing)
        sorted_chunks = sorted(rag_chunks, key=lambda c: c.relevance_score, reverse=True)
        packed_rag: List[str] = []
        current_rag_tokens = 0

        for chunk in sorted_chunks:
            if current_rag_tokens + chunk.token_count <= self.rag_budget:
                packed_rag.append(f"[{chunk.chunk_id}] {chunk.text}")
                current_rag_tokens += chunk.token_count

        if packed_rag:
            assembled_messages[0]["content"] += "\n\n[CONTEXT KNOWLEDGE]:\n" + "\n".join(packed_rag)

        # 3. Compact Chat History if exceeding budget
        history_tokens = sum(self._count_tokens(m["content"]) for m in chat_history)

        if history_tokens > self.history_budget and len(chat_history) > 2:
            # Keep most recent 2 turns, summarize older turns
            older_turns = chat_history[:-2]
            recent_turns = chat_history[-2:]

            summary_points = [f"- {m['role']}: {m['content'][:40]}..." for m in older_turns]
            summary_content = "[SUMMARY OF PREVIOUS TURNS]:\n" + "\n".join(summary_points)

            assembled_messages.append({"role": "system", "content": summary_content})
            assembled_messages.extend(recent_turns)
        else:
            assembled_messages.extend(chat_history)

        # 4. Append Current User Query
        assembled_messages.append({"role": "user", "content": current_query})

        return assembled_messages


# Verification Test
if __name__ == "__main__":
    engine = ContextCompactionEngine(max_prompt_budget=2500, rag_budget=100, history_budget=50)

    system = "You are a specialized code assistant."
    chunks = [
        RAGChunk(chunk_id="doc1", text="Python async syntax guide.", relevance_score=0.95, token_count=40),
        RAGChunk(chunk_id="doc2", text="Legacy Python 2 documentation.", relevance_score=0.30, token_count=80),
        RAGChunk(chunk_id="doc3", text="FastAPI streaming response reference.", relevance_score=0.88, token_count=50),
    ]
    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a high-level programming language."},
        {"role": "user", "content": "Tell me about GIL."},
        {"role": "assistant", "content": "The Global Interpreter Lock limits execution to one thread."},
        {"role": "user", "content": "What is asyncio?"},
        {"role": "assistant", "content": "Asyncio is a library for concurrent single-threaded code."},
    ]
    query = "How do I stream SSE in FastAPI?"

    result = engine.compact_and_assemble(system, chunks, history, query)

    assert result[0]["role"] == "system"
    assert "Python async syntax guide." in result[0]["content"]
    assert "Legacy Python 2 documentation." not in result[0]["content"], "Lower relevance chunk should be omitted"
    assert any("[SUMMARY OF PREVIOUS TURNS]" in m["content"] for m in result), "History should be summarized"
    assert result[-1]["content"] == query

    print("Exercise 1 Verification: All Assertions Passed!\n")
```

---

## Exercise 2: Multibyte UTF-8 Safe Streaming Buffer

### Problem Statement
In streaming LLM architectures, raw tokens arrive as byte arrays. Multibyte characters (such as emojis or non-English characters) can be fragmented across adjacent chunks. Build an `IncrementalUTF8StreamDecoder` that accepts byte chunks, safely holds incomplete trailing bytes, and yields only valid, fully-formed unicode characters.

### Complete Solution & Verification Test

```python
"""
Exercise 2: Multibyte UTF-8 Safe Streaming Buffer
"""
import codecs
from typing import Iterator, List


class IncrementalUTF8StreamDecoder:
    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def decode_chunks(self, byte_chunks: List[bytes]) -> Iterator[str]:
        for idx, chunk in enumerate(byte_chunks):
            is_final = (idx == len(byte_chunks) - 1)
            # Decodes fully formed characters; holds partial trailing bytes in internal state
            text = self.decoder.decode(chunk, final=is_final)
            if text:
                yield text


# Verification Test
if __name__ == "__main__":
    # Test with emoji: 🚀 (Rocket: 0xF0, 0x9F, 0x99, 0x80)
    rocket_bytes = "🚀".encode("utf-8")
    assert len(rocket_bytes) == 4

    # Fragment the rocket emoji across 3 separate byte chunks!
    chunk1 = b"Launching " + rocket_bytes[:2]    # b'Launching \xf0\x9f'
    chunk2 = rocket_bytes[2:3]                   # b'\x99'
    chunk3 = rocket_bytes[3:] + b" to space!"    # b'\x80 to space!'

    decoder = IncrementalUTF8StreamDecoder()
    emitted = list(decoder.decode_chunks([chunk1, chunk2, chunk3]))

    full_text = "".join(emitted)
    print("Emitted Chunks:", emitted)
    print("Reconstructed Text:", full_text)

    assert "Launching 🚀 to space!" == full_text
    print("Exercise 2 Verification: All Assertions Passed!\n")
```

---

## Exercise 3: KV-Cache GPU Memory Estimator and Concurrency Planner

### Problem Statement
Write a production capacity planning tool that calculates:
1. The exact KV-Cache memory footprint per sequence in Gigabytes.
2. The maximum number of concurrent streams a GPU cluster can host before exceeding available VRAM headroom.

Given formula:
$$\text{Memory}_{\text{KV}} = 2 \times n_{\text{layers}} \times n_{\text{kv\_heads}} \times d_{\text{head}} \times \text{seq\_len} \times \text{bytes\_per\_elem}$$

### Complete Solution & Verification Test

```python
"""
Exercise 3: KV-Cache GPU Memory Estimator & Concurrency Capacity Planner
"""
from dataclasses import dataclass


@dataclass
class ModelArchitecture:
    name: str
    num_layers: int
    num_kv_heads: int  # Grouped-Query Attention (GQA) heads
    head_dim: int
    bytes_per_param: int = 2  # FP16/BF16 = 2 bytes, FP8 = 1 byte


class KVCacheCapacityPlanner:
    @staticmethod
    def calculate_single_stream_kv_mb(arch: ModelArchitecture, seq_len: int) -> float:
        total_bytes = (
            2  # Keys and Values
            * arch.num_layers
            * arch.num_kv_heads
            * arch.head_dim
            * seq_len
            * arch.bytes_per_param
        )
        return total_bytes / (1024 * 1024)

    @staticmethod
    def max_concurrent_streams(
        arch: ModelArchitecture,
        seq_len: int,
        available_vram_gb: float,
        safety_headroom_pct: float = 0.15,
    ) -> int:
        usable_vram_mb = (available_vram_gb * 1024) * (1.0 - safety_headroom_pct)
        kv_per_stream_mb = KVCacheCapacityPlanner.calculate_single_stream_kv_mb(arch, seq_len)
        return int(usable_vram_mb // kv_per_stream_mb)


# Verification Test
if __name__ == "__main__":
    # Llama 3 70B architecture: 80 layers, 8 KV heads (GQA), 128 head_dim, FP16
    llama3_70b = ModelArchitecture(
        name="Llama-3-70B",
        num_layers=80,
        num_kv_heads=8,
        head_dim=128,
        bytes_per_param=2,
    )

    # 1. Calculate per-stream memory at 8k context
    kv_8k_mb = KVCacheCapacityPlanner.calculate_single_stream_kv_mb(llama3_70b, seq_len=8192)
    print(f"Llama-3-70B KV-Cache at 8k Context: {kv_8k_mb:.2f} MB (~{kv_8k_mb/1024:.2f} GB)")

    # 2. Concurrency calculation: 8x A100 (80GB each = 640GB total).
    # Model weights consume ~140GB in FP16, leaving 500GB for KV-cache.
    concurrency = KVCacheCapacityPlanner.max_concurrent_streams(
        llama3_70b,
        seq_len=8192,
        available_vram_gb=500.0,
        safety_headroom_pct=0.10,
    )

    print(f"Max Concurrent Streams supported at 8k context: {concurrency} active requests")
    assert concurrency > 150, "Expected at least 150 concurrent streams"
    print("Exercise 3 Verification: All Assertions Passed!\n")
```
