# Day 81: LLM Foundations: Tokenization, Context Windows, Streaming APIs & Server-Sent Events (SSE)

Large Language Models (LLMs) do not consume raw text, words, or sentences. At their foundational computational layer, LLMs operate entirely over discrete numerical token identifiers mapped into high-dimensional vector spaces. For backend AI engineers, understanding the operational mechanics of **tokenization**, **context window constraints**, **KV-cache memory consumption**, and **real-time streaming delivery via Server-Sent Events (SSE)** is essential for building low-latency, cost-effective, and production-hardened AI platforms.

---

## 1. Executive Architecture Overview

In a production LLM serving system, an incoming user request traverses five distinct architectural stages before reaching the client:

```
+--------------------------------------------------------------------------------------------------+
|                                PRODUCTION LLM INFERENCE LIFECYCLE                                |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|  [ Client / UI ]                                                                                 |
|         │                                                                                        |
|         │ 1. POST /v1/chat/completions (Stream: true, Messages: [...])                           |
|         ▼                                                                                        |
|  [ API Gateway / Reverse Proxy ] ──> Rate Limiting, Auth, Request Validation                     |
|         │                                                                                        |
|         ▼                                                                                        |
|  [ Context Window & Token Budget Engine ]                                                        |
|         │ 2. Tokenize prompt with model vocabulary (e.g., cl100k_base / o200k_base)              |
|         │ 3. Apply Sliding Window / Compression: Allocate budget (System, History, RAG, Reserve) |
|         │ 4. Enforce Token Constraints: L_prompt + L_completion <= L_context                     |
|         ▼                                                                                        |
|  [ Inference Engine / KV-Cache Coordinator ] (vLLM / TensorRT-LLM / Triton)                      |
|         │                                                                                        |
|         ├─ Phase A: Prefill (Prompt Evaluation)                                                  |
|         │   Compute Prompt Self-Attention in parallel; populate initial KV-Cache entries.       |
|         │                                                                                        |
|         └─ Phase B: Autoregressive Decoding Loop (Token-by-Token)                                |
|             Loop until <|endoftext|> or max_tokens reached:                                      |
|               Token_t ~ Softmax(Logits_t / Temperature)                                          |
|               Append Key_t, Value_t to KV-Cache                                                  |
|               Yield raw Token_t delta immediately                                                |
|         │                                                                                        |
|         ▼                                                                                        |
|  [ SSE Streaming Buffer & Serializer ]                                                           |
|         │ 5. Chunk-level UTF-8 byte assembly (preventing fragmented multibyte characters)        |
|         │ 6. Format W3C SSE frame: "data: {\"choices\": [{\"delta\": {\"content\": ...}}]}\n\n"   |
|         │ 7. Flush over HTTP/1.1 chunked connection with immediate write-through                 |
|         ▼                                                                                        |
|  [ Client Consumption Stream ] ──> Interactive real-time token rendering                         |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. Why LLMs Process Tokens, Not Characters or Words

Traditional Natural Language Processing (NLP) historically utilized either **word-level** or **character-level** representations, both of which suffer from fatal engineering bottlenecks in deep transformer architectures:

| Dimensional Metric | Word-Level Tokenization | Character-Level Tokenization | Subword / Byte-Level BPE |
| :--- | :--- | :--- | :--- |
| **Vocabulary Size ($V$)** | Massive ($100\text{k} - 2\text{M}+$ words) | Tiny ($100 - 256$ characters) | Optimal ($32\text{k} - 128\text{k}$ subwords) |
| **Out-of-Vocabulary (OOV)** | High (`<UNK>` tokens lose meaning) | Zero (Every character represented) | Zero (Byte-level fallback guarantees coverage) |
| **Sequence Length ($N$)** | Short ($1.0\times$ baseline) | Extreme ($4\times - 6\times$ baseline) | Balanced ($1.2\times - 1.4\times$ word length) |
| **Attention Cost ($\mathcal{O}(N^2)$)**| Minimal computation | Prohibitive $\mathcal{O}((5N)^2) = 25\times$ FLOPs | Efficient, scalable attention budget |
| **Morphological Learning** | Poor (e.g. `walk`, `walks`, `walking` distinct) | Good (learns sub-character patterns) | Optimal (shares roots and stems) |

### The Subword Sweet Spot
Modern LLMs (GPT-4, Claude 3.5, Llama 3, Mistral) universally standardize on **Byte-Pair Encoding (BPE)** or **WordPiece**. Subword algorithms represent common words as single tokens (e.g., `"the"` $\to$ token `1820`), while decomposing rare terms, technical jargon, foreign languages, and code syntax into granular constituent subwords (e.g., `"tokenization"` $\to$ `["token", "ization"]`).

---

## 3. Byte-Pair Encoding (BPE) Algorithmic Mechanics

Byte-Pair Encoding (BPE) was originally developed as a data compression algorithm (Gage, 1994) and adapted for neural machine translation by Sennrich et al. (2016). Modern LLMs use **Byte-Level BPE**, operating directly on raw UTF-8 bytes rather than unicode characters.

### 3.1 The BPE Training Pipeline
Given a training corpus:
1. **Byte Initialization**: Start with a base vocabulary containing all 256 possible byte values ($0\times00$ to $0\times\text{FF}$) plus special control tokens.
2. **Frequency Counting**: Segment words into individual byte sequences. Count the co-occurrence frequency of all adjacent byte/token pairs across the entire corpus:
   $$\text{freq}(t_i, t_{i+1}) = \sum_{c \in \text{Corpus}} \mathbb{I}(c[k] = t_i \land c[k+1] = t_{i+1})$$
3. **Greedy Iterative Merging**: Identify the most frequent adjacent pair $(A, B)$ and merge it into a new composite token $AB$, appending $AB$ to the vocabulary with a assigned merge rank.
4. **Stopping Criteria**: Repeat until the target vocabulary size $V$ (e.g., 50,257 for GPT-2/3, 100,277 for GPT-4 `cl100k_base`, 128,256 for Llama 3) is attained.

```
Initial Corpus:  "low", "low", "lower", "newest", "widest"
Token Splitting:
  l o w : 2
  l o w e r : 1
  n e w e s t : 1
  w i d e s t : 1

Step 1: Pair ('e', 's') occurs 2 times ("newest", "widest") -> Merge to 'es'
Step 2: Pair ('es', 't') occurs 2 times -> Merge to 'est'
Step 3: Pair ('l', 'o') occurs 3 times ("low", "lower") -> Merge to 'lo'
Step 4: Pair ('lo', 'w') occurs 3 times -> Merge to 'low'
Resulting Vocabulary: {l, o, w, e, r, n, s, t, i, d, es, est, lo, low, ...}
```

### 3.2 Critical Tokenization Traps for Backend Engineers
1. **Space Sensitivity**: `" Hello"` (with leading space) and `"Hello"` (without space) map to completely different token IDs:
   - In `cl100k_base`: `" Hello"` $\to$ `[9906]`, while `"Hello"` $\to$ `[15339]`.
   - String concatenation without whitespace awareness alters embedding lookup vectors.
2. **Numbers and Arithmetic Fragility**: BPE algorithms historically chunked multi-digit numbers inconsistently:
   - `"123456"` might tokenize as `["12", "34", "56"]` or `["123", "456"]`. This explains why early LLMs struggled with arithmetic and reversed-string puzzles. Modern tokenizers enforce explicit regex splits on individual digits.
3. **Multilingual Token Inflation**: Non-Latin scripts (Hindi, Arabic, Japanese, Cyrillic) require 3 to 4 UTF-8 bytes per character. In older tokenizers like `p50k_base`, a single Hindi word could consume 12+ tokens, drastically increasing inference costs and truncating available context windows.

---

## 4. Context Windows & KV-Cache Memory Economics

Every LLM architecture is constrained by its maximum sequence capacity ($L_{\text{context}}$), encompassing both the input prompt and output completion:

$$L_{\text{context}} \ge L_{\text{prompt}} + L_{\text{completion}}$$

### 4.1 Quadratic Self-Attention Complexity
In standard Transformer multi-head self-attention, every token must compute attention scores against every other token in the sequence:

$$\text{Attention}(Q, K, V) = \text{Softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V$$

For a sequence of length $N$:
- Matrix multiplication $QK^T$ requires $\mathcal{O}(N^2 \cdot d)$ floating-point operations (FLOPs).
- Memory footprint of the attention matrix scales quadratically as $\mathcal{O}(N^2)$.
- Doubling context length from $4\text{k}$ to $8\text{k}$ quadruples self-attention memory from $16\text{M}$ to $64\text{M}$ interaction elements per layer.

### 4.2 The Key-Value (KV) Cache
In autoregressive decoding, generating token $t+1$ depends on the representations of tokens $1 \dots t$. Without a cache, generating each new token would require recalculating attention keys ($K$) and values ($V$) for all preceding tokens, resulting in $\mathcal{O}(N^2)$ decoding cost per step ($\mathcal{O}(N^3)$ total).

The **KV-Cache** stores the precomputed Key and Value tensors for all prior tokens in GPU VRAM:

$$\text{Memory}_{\text{KV-Cache}} = 2 \times n_{\text{layers}} \times n_{\text{kv\_heads}} \times d_{\text{head}} \times \text{seq\_len} \times \text{batch\_size} \times \text{bytes\_per\_element}$$

Where:
- $2$: One tensor for Keys, one tensor for Values.
- $n_{\text{layers}}$: Depth of Transformer network (e.g., 32 layers for 8B models, 80 layers for 70B models).
- $n_{\text{kv\_heads}}$: Number of key-value attention heads (Grouped-Query Attention reduces this significantly, e.g., 8 KV heads vs 32 Query heads in Llama 3).
- $d_{\text{head}}$: Head dimension (typically 128).
- $\text{bytes\_per\_element}$: 2 bytes for FP16/BF16, 1 byte for FP8.

#### Concrete Calculation Example (Llama-3-70B FP16, Batch Size = 16, Context = 8,192):
$$\text{Memory} = 2 \times 80 \times 8 \times 128 \times 8192 \times 16 \times 2 \text{ bytes}$$
$$\text{Memory} = 42,949,672,960 \text{ bytes} \approx \mathbf{40.0\text{ GB VRAM}}$$

The KV-Cache for active requests alone consumes 40 GB of GPU RAM, competing directly with model weights for physical memory! This mathematical reality explains why LLM providers charge strictly per input and output token and enforce rigid context window limits.
