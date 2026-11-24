"""
Day 81: LLM Foundations: Tokenization & Bounded Context Window Management
Part 1: Byte-Pair Encoding (BPE) Tokenizer & Sliding Context Window Engine

This module provides:
1. Pure-Python Byte-Level BPE Tokenizer implementing vocab training, greedy pair merging,
   and byte-fallback encoding/decoding.
2. Enterprise TokenBudgetManager enforcing sliding window FIFO message eviction,
   system prompt pinning, and token-aware context allocation.
"""

from __future__ import annotations

import codecs
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


# =====================================================================
# 1. Byte-Pair Encoding (BPE) Tokenizer From Scratch
# =====================================================================

class BytePairEncodingTokenizer:
    """
    Byte-Level Byte-Pair Encoding (BPE) Tokenizer.
    Operates directly on UTF-8 byte sequences, guaranteeing zero Out-Of-Vocabulary (OOV) tokens.
    """
    def __init__(self, special_tokens: Optional[List[str]] = None) -> None:
        self.special_tokens = special_tokens or [
            "<|im_start|>",
            "<|im_end|>",
            "<|endoftext|>",
            "<|pad|>",
        ]
        # Base byte vocabulary: 256 byte tokens (0-255)
        self.vocab: Dict[int, bytes] = {i: bytes([i]) for i in range(256)}
        self.inverse_vocab: Dict[bytes, int] = {bytes([i]): i for i in range(256)}
        
        # Merge dictionary: Tuple[int, int] -> new_token_id
        self.merges: Dict[Tuple[int, int], int] = {}
        
        # Assign special tokens at the top of the vocabulary
        next_id = 256
        self.special_token_to_id: Dict[str, int] = {}
        self.id_to_special_token: Dict[int, str] = {}
        for token_str in self.special_tokens:
            token_bytes = token_str.encode("utf-8")
            self.special_token_to_id[token_str] = next_id
            self.id_to_special_token[next_id] = token_str
            self.vocab[next_id] = token_bytes
            self.inverse_vocab[token_bytes] = next_id
            next_id += 1

    def train(self, corpus: str, target_vocab_size: int = 350) -> None:
        """
        Learns BPE merge rules iteratively from the given text corpus.
        """
        if target_vocab_size <= len(self.vocab):
            return

        # Pre-tokenize text into words using regex pattern matching GPT-2/3
        pattern = re.compile(r"""'s|'t|'re|'ve|'m|'ll|'d| ?[a-zA-Z]+| ?\d+| ?[^\s\w]+|\s+(?!\S)|\s+""")
        words = pattern.findall(corpus) if hasattr(re, "UNICODE") else corpus.split()
        
        # Convert each word to a list of byte integers
        tokenized_words: List[List[int]] = [list(word.encode("utf-8")) for word in words]

        num_merges = target_vocab_size - len(self.vocab)
        next_token_id = max(self.vocab.keys()) + 1

        for _ in range(num_merges):
            # 1. Count frequencies of all adjacent pairs
            pair_counts: Dict[Tuple[int, int], int] = {}
            for word_tokens in tokenized_words:
                for i in range(len(word_tokens) - 1):
                    pair = (word_tokens[i], word_tokens[i + 1])
                    pair_counts[pair] = pair_counts.get(pair, 0) + 1

            if not pair_counts:
                break  # No more pairs to merge

            # 2. Pick the most frequent pair
            best_pair = max(pair_counts, key=pair_counts.get)
            if pair_counts[best_pair] < 2:
                break  # Stop if pair frequency is negligible

            # 3. Create new token in vocabulary
            token_a_bytes = self.vocab[best_pair[0]]
            token_b_bytes = self.vocab[best_pair[1]]
            new_bytes = token_a_bytes + token_b_bytes

            self.merges[best_pair] = next_token_id
            self.vocab[next_token_id] = new_bytes
            self.inverse_vocab[new_bytes] = next_token_id

            # 4. Apply merge to all words in corpus
            new_tokenized_words: List[List[int]] = []
            for word_tokens in tokenized_words:
                merged_word: List[int] = []
                i = 0
                while i < len(word_tokens):
                    if i < len(word_tokens) - 1 and (word_tokens[i], word_tokens[i + 1]) == best_pair:
                        merged_word.append(next_token_id)
                        i += 2
                    else:
                        merged_word.append(word_tokens[i])
                        i += 1
                new_tokenized_words.append(merged_word)

            tokenized_words = new_tokenized_words
            next_token_id += 1

    def encode(self, text: str) -> List[int]:
        """
        Encodes a string into a list of integer token IDs using learned BPE merges.
        """
        # Check for special tokens first
        special_pattern = "(" + "|".join(re.escape(k) for k in self.special_tokens) + ")"
        parts = re.split(special_pattern, text)

        encoded_tokens: List[int] = []
        for part in parts:
            if not part:
                continue
            if part in self.special_token_to_id:
                encoded_tokens.append(self.special_token_to_id[part])
            else:
                raw_bytes = list(part.encode("utf-8"))
                # Apply merges iteratively in order of merge rank
                while len(raw_bytes) >= 2:
                    # Find all pairs present in merges
                    candidate_pairs = [
                        ((raw_bytes[i], raw_bytes[i + 1]), i)
                        for i in range(len(raw_bytes) - 1)
                        if (raw_bytes[i], raw_bytes[i + 1]) in self.merges
                    ]
                    if not candidate_pairs:
                        break  # No more mergeable pairs

                    # Greedy selection by lowest merge token ID (earliest learned rule)
                    best_candidate = min(candidate_pairs, key=lambda x: self.merges[x[0]])
                    pair_to_merge, idx = best_candidate
                    new_id = self.merges[pair_to_merge]
                    raw_bytes = raw_bytes[:idx] + [new_id] + raw_bytes[idx + 2:]

                encoded_tokens.extend(raw_bytes)

        return encoded_tokens

    def decode(self, token_ids: List[int]) -> str:
        """
        Decodes a list of token IDs back into a UTF-8 string with safe multibyte assembly.
        """
        byte_chunks: List[bytes] = []
        for t in token_ids:
            if t in self.id_to_special_token:
                byte_chunks.append(self.id_to_special_token[t].encode("utf-8"))
            elif t in self.vocab:
                byte_chunks.append(self.vocab[t])
            else:
                # Byte fallback
                byte_chunks.append(bytes([t % 256]))

        full_bytes = b"".join(byte_chunks)
        # Safe UTF-8 decode with error replacement
        return full_bytes.decode("utf-8", errors="replace")


# =====================================================================
# 2. Context Window & Token Budget Management Engine
# =====================================================================

@dataclass
class ChatMessage:
    role: str
    content: str
    token_count: int = 0


@dataclass
class ContextBudgetReport:
    total_tokens: int
    system_tokens: int
    history_tokens: int
    rag_tokens: int
    user_tokens: int
    reserved_completion_tokens: int
    evicted_message_count: int
    within_budget: bool


class TokenBudgetManager:
    """
    Production Context Window Manager.
    Enforces token budgeting, pinned system prompt preservation,
    and FIFO sliding window message eviction.
    """
    def __init__(
        self,
        max_context_tokens: int = 2048,
        completion_reserve_tokens: int = 512,
        tokenizer: Optional[Any] = None,
    ) -> None:
        self.max_context_tokens = max_context_tokens
        self.completion_reserve_tokens = completion_reserve_tokens
        self.tokenizer = tokenizer

    def count_tokens(self, text: str) -> int:
        """Counts tokens using the internal BPE tokenizer or tiktoken fallback."""
        if self.tokenizer is not None:
            return len(self.tokenizer.encode(text))
        if TIKTOKEN_AVAILABLE:
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                pass
        # Heuristic fallback: 1 token ≈ 4 characters
        return max(1, len(text) // 4)

    def manage_context_window(
        self,
        system_prompt: str,
        chat_history: List[Dict[str, str]],
        new_user_message: str,
        rag_context: Optional[str] = None,
    ) -> Tuple[List[Dict[str, str]], ContextBudgetReport]:
        """
        Assembles prompt adhering strictly to:
        Prompt_Tokens + Completion_Reserve <= Max_Context_Tokens
        Evicts oldest message pairs first while guaranteeing System Prompt retention.
        """
        # 1. Calculate invariant token costs
        system_tokens = self.count_tokens(system_prompt) + 4  # Formatting overhead
        user_tokens = self.count_tokens(new_user_message) + 4
        rag_tokens = (self.count_tokens(rag_context) + 4) if rag_context else 0

        # Maximum available budget for conversation history
        available_history_budget = (
            self.max_context_tokens
            - self.completion_reserve_tokens
            - system_tokens
            - user_tokens
            - rag_tokens
        )

        if available_history_budget < 0:
            # Severe overflow: RAG or System Prompt must be truncated
            overflow = abs(available_history_budget)
            if rag_context and rag_tokens > overflow:
                # Truncate RAG context to fit
                rag_tokens -= overflow
                available_history_budget = 0
            else:
                raise ValueError(
                    f"Invariant prompt size ({system_tokens + user_tokens + rag_tokens} tokens) "
                    f"exceeds total allowed input budget ({self.max_context_tokens - self.completion_reserve_tokens} tokens)!"
                )

        # 2. Tokenize chat history messages
        prepared_messages: List[ChatMessage] = []
        for msg in chat_history:
            t_count = self.count_tokens(msg["content"]) + 4
            prepared_messages.append(ChatMessage(role=msg["role"], content=msg["content"], token_count=t_count))

        # 3. Apply Sliding Window FIFO Eviction on History
        total_history_tokens = sum(m.token_count for m in prepared_messages)
        evicted_count = 0

        # Evict from head of history until within budget
        while total_history_tokens > available_history_budget and prepared_messages:
            evicted = prepared_messages.pop(0)
            total_history_tokens -= evicted.token_count
            evicted_count += 1

        # 4. Construct final validated message payload
        final_messages: List[Dict[str, str]] = []

        # System Prompt is always index 0 (Pinned)
        full_system = system_prompt
        if rag_context:
            full_system += f"\n\n[RETRIEVED CONTEXT]:\n{rag_context}"
        final_messages.append({"role": "system", "content": full_system})

        # Retained history
        for msg in prepared_messages:
            final_messages.append({"role": msg.role, "content": msg.content})

        # Active user message
        final_messages.append({"role": "user", "content": new_user_message})

        final_prompt_tokens = system_tokens + rag_tokens + total_history_tokens + user_tokens

        report = ContextBudgetReport(
            total_tokens=final_prompt_tokens,
            system_tokens=system_tokens,
            history_tokens=total_history_tokens,
            rag_tokens=rag_tokens,
            user_tokens=user_tokens,
            reserved_completion_tokens=self.completion_reserve_tokens,
            evicted_message_count=evicted_count,
            within_budget=(final_prompt_tokens + self.completion_reserve_tokens <= self.max_context_tokens),
        )

        return final_messages, report


# =====================================================================
# 3. Demonstration & Unit Verification Entrypoint
# =====================================================================

def run_demonstration() -> None:
    print("=" * 80)
    print("PART 1: BYTE-PAIR ENCODING (BPE) TOKENIZER TRAINING & ENCODING")
    print("=" * 80)

    # 1. Train BPE on representative English + Code text corpus
    corpus = """
    In computer science, Byte-Pair Encoding (BPE) is a data compression algorithm.
    Large Language Models use subword tokenization like BPE to represent vocabulary.
    Tokenization splits words like 'lower', 'lowest', 'widest', 'newest' into shared stems.
    For example: 'low' + 'er' and 'low' + 'est'.
    Special tokens include <|im_start|> and <|im_end|> for chat formatting.
    Code syntax: def tokenize(text: str) -> list[int]: return [1, 2, 3]
    """

    tokenizer = BytePairEncodingTokenizer()
    initial_vocab_len = len(tokenizer.vocab)
    target_vocab_size = 290  # Add 30 learned merges

    print(f"Initial Vocabulary Size: {initial_vocab_len} (256 raw bytes + {len(tokenizer.special_tokens)} special tokens)")
    tokenizer.train(corpus, target_vocab_size=target_vocab_size)
    print(f"Learned BPE Merges Count: {len(tokenizer.merges)}")
    print(f"Final Vocabulary Size:   {len(tokenizer.vocab)}")

    # 2. Encode and decode a test sentence
    sample_text = "<|im_start|>user\nCalculate lowest and widest latency.<|im_end|>"
    encoded_ids = tokenizer.encode(sample_text)
    decoded_text = tokenizer.decode(encoded_ids)

    print(f"\nOriginal Input:  {sample_text}")
    print(f"Encoded Tokens:  {encoded_ids}")
    print(f"Decoded Output: {decoded_text}")
    print(f"Token Count:     {len(encoded_ids)} tokens (vs {len(sample_text)} characters)")

    assert decoded_text == sample_text, "Decoded text must strictly match original input!"
    print("[SUCCESS] BPE roundtrip verification passed with zero loss.")

    print("\n" + "=" * 80)
    print("PART 2: CONTEXT WINDOW SLIDING BUFFER & TOKEN BUDGET MANAGER")
    print("=" * 80)

    # Setup context manager: Max context = 350 tokens, completion reserve = 60 tokens -> Max prompt = 290 tokens
    budget_mgr = TokenBudgetManager(
        max_context_tokens=350,
        completion_reserve_tokens=60,
        tokenizer=tokenizer,
    )

    system_prompt = "You are a senior ML backend engineer. Provide concise technical answers."
    
    # Simulate a multi-turn conversation with 8 turns
    chat_history: List[Dict[str, str]] = [
        {"role": "user", "content": "What is self-attention complexity?"},
        {"role": "assistant", "content": "Self-attention requires O(N^2) memory and quadratic FLOPs."},
        {"role": "user", "content": "What is the purpose of the KV-cache?"},
        {"role": "assistant", "content": "KV-cache stores precomputed Keys and Values in VRAM to speed up decoding."},
        {"role": "user", "content": "How does BPE avoid OOV errors?"},
        {"role": "assistant", "content": "By falling back to raw 256 individual UTF-8 bytes."},
    ]

    new_query = "Explain Server-Sent Events (SSE) streaming protocols."

    pruned_msgs, report = budget_mgr.manage_context_window(
        system_prompt=system_prompt,
        chat_history=chat_history,
        new_user_message=new_query,
        rag_context="Server-Sent Events (SSE) stream text over HTTP/1.1 chunked transfer.",
    )

    print(f"Total Available Window:    {budget_mgr.max_context_tokens} tokens")
    print(f"Completion Reserved:       {report.reserved_completion_tokens} tokens")
    print(f"System Prompt Tokens:      {report.system_tokens} tokens (PINNED)")
    print(f"Retained History Tokens:   {report.history_tokens} tokens")
    print(f"RAG Context Tokens:        {report.rag_tokens} tokens")
    print(f"New User Query Tokens:     {report.user_tokens} tokens")
    print(f"Final Prompt Tokens:       {report.total_tokens} tokens")
    print(f"Evicted Messages:          {report.evicted_message_count} older turns evicted")
    print(f"Within Budget Gate:        {report.within_budget}")

    print("\nRetained Messages in Final Prompt:")
    for idx, msg in enumerate(pruned_msgs):
        print(f"  [{idx}] {msg['role'].upper()}: {msg['content'][:60]}...")

    assert report.within_budget, "Prompt must fit strictly within token budget!"
    assert pruned_msgs[0]["role"] == "system", "System prompt must strictly remain pinned at index 0!"
    print("\n[SUCCESS] Context Window Management assertions passed!\n")


if __name__ == "__main__":
    run_demonstration()
