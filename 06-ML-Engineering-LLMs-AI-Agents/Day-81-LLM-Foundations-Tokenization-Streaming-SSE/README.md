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


---

## 5. Bounded Context Window Management Strategies

Because LLM context windows are strictly finite, backend systems running multi-turn chat applications or retrieval-augmented generation (RAG) must dynamically budget and prune tokens before submitting requests to the model.

### 5.1 The Token Budget Allocation Framework
A robust production LLM prompt adheres to a partitioned token budget:

$$L_{\text{budget}} = T_{\text{system}} + T_{\text{tools}} + T_{\text{rag\_context}} + T_{\text{chat\_history}} + T_{\text{user\_query}} + T_{\text{completion\_reserve}}$$

```
+-------------------------------------------------------------------------------------------------+
|                                    TOTAL CONTEXT WINDOW BUDGET                                  |
+------------------------------------+--------------------------+--------------------+------------+
| System Prompt & Tool Schemas       | RAG Retrieved Documents  | Chat Conversation  | Reserved   |
| (Pinned & Immutable)               | (Dynamic Injection)      | History Buffer     | Completion |
| ~1,000 tokens                      | ~4,000 tokens            | ~2,000 tokens      | 1,000 toks |
+------------------------------------+--------------------------+--------------------+------------+
```

### 5.2 Context Pruning & Retention Algorithms
When conversation history grows beyond the allocated budget, three strategies are deployed:

1. **Sliding Window Buffer with System Prompt Pinning**:
   - The System Prompt (`role: system`) is **strictly pinned** at the head of the sequence to ensure instructions, guardrails, and JSON schemas remain active.
   - Older messages from the conversation history are evicted on a First-In, First-Out (FIFO) basis while preserving message turn pairs (`user` + `assistant`).
2. **Context Summarization & Compaction**:
   - When the conversation history exceeds a watermark (e.g. 75% of context window), a lightweight background LLM (or summarizer agent) condenses older turns into an executive summary bullet list, replacing 20 older turns with a single concise summary message.
3. **Selective RAG Truncation**:
   - Rather than dropping user conversational turns, lower-ranking retrieved document chunks are discarded based on similarity score thresholds.

---

## 6. Real-Time Streaming Delivery & Server-Sent Events (SSE)

In non-streaming requests, the user or downstream service waits until the entire completion is generated before receiving any bytes. For a 500-token completion generating at 30 tokens/second, this incurs a frustrating **16.6-second delay** ($T_{\text{wait}} = \text{TTFT} + 500/30$).

Streaming APIs transform this experience: tokens are transmitted to the client the millisecond they are generated. The user sees text immediately (sub-300ms **Time-To-First-Token (TTFT)**), radically boosting perceived responsiveness and conversational fluidity.

### 6.1 Why Server-Sent Events (SSE) Over WebSockets?
For unidirectional LLM completion streams (where the server pushes text chunks to the client over an established request), **Server-Sent Events (W3C standard)** offers significant architectural advantages over WebSockets:

| Architectural Metric | Server-Sent Events (SSE) | WebSockets (WS) |
| :--- | :--- | :--- |
| **Protocol** | Standard HTTP/1.1 or HTTP/2 | Custom WS/WSS TCP handshake |
| **Directionality** | Unidirectional (Server $\to$ Client) | Full Duplex (Bidirectional) |
| **Proxy / Firewall Traversal** | Seamless (Standard port 80/443, standard headers) | Often blocked or terminated by enterprise middleboxes |
| **Auto-Reconnection** | Built-in browser reconnection & `Last-Event-ID` | Requires manual application-level reconnection |
| **Load Balancer Support** | Native HTTP load balancing & sticky sessions | Requires persistent stateful socket connection tracking |
| **Complexity & Overhead** | Low (Plain text event framing) | Medium (Frame masking, ping/pong heartbeats) |

### 6.2 The W3C Server-Sent Events Protocol Specification
SSE operates over an open HTTP connection using `Transfer-Encoding: chunked`. The response must specify the following HTTP headers:

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
Connection: keep-alive
X-Accel-Buffering: no
```

> [!IMPORTANT]
> The header `X-Accel-Buffering: no` is mandatory when proxying through Nginx or AWS Application Load Balancers (ALB). By default, reverse proxies buffer small HTTP responses until a 4KB/16KB threshold is reached. Disabling proxy buffering ensures individual token frames are forwarded immediately without latency.

### 6.3 SSE Frame Grammar
An SSE stream consists of UTF-8 encoded text separated by double newlines (`\n\n`). Each event block can contain:

```text
event: <event_name>\n
id: <message_id>\n
retry: <milliseconds>\n
data: <payload_string>\n\n
```

Keep-alive heartbeats are sent as comment lines starting with a colon (`: ping\n\n`) to prevent intermediate firewall timeouts during silent generation periods.

---

## 7. Multibyte UTF-8 Boundary Reassembly

In Byte-Level BPE, a token does not necessarily align with complete UTF-8 character boundaries. A complex Unicode glyph (such as an emoji 🚀 or Hindi character 'क') consists of 3 to 4 raw bytes.

If the LLM emits a token that ends on an incomplete byte sequence (e.g. 2 bytes of a 3-byte character) and the backend attempts to decode it immediately via `.decode("utf-8")`, a `UnicodeDecodeError` is raised!

```
Character: '🚀' (UTF-8 Bytes: 0xF0, 0x9F, 0x99, 0x80)

Token 1 emits:  [0xF0, 0x9F]        <-- INCOMPLETE (Raises UnicodeDecodeError if decoded alone)
Token 2 emits:  [0x99, 0x80]        <-- Completes the character!
```

### Production UTF-8 Stream Decoder Pattern
Production backend streaming servers maintain an incremental byte buffer:
```python
# PRODUCTION PATTERN: Incremental UTF-8 Decoding
import codecs

decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
# Feeds raw chunk bytes; emits only fully formed characters, buffering remaining bytes:
complete_text = decoder.decode(chunk_bytes, final=False)
```

---

## 8. OpenAI-Compatible SSE Streaming Payload Format

Modern AI engineering platforms (vLLM, Ollama, LangChain, LlamaIndex, OpenAI SDK) adhere to the standard OpenAI-compatible streaming response schema.

### Wire Format of Streaming Chunks

```http
data: {"id":"chatcmpl-9A1B2C","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-9A1B2C","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hello"},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-9A1B2C","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"!"},"logprobs":null,"finish_reason":null}]}

data: {"id":"chatcmpl-9A1B2C","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}]}

data: [DONE]

```

1. **Initial Chunk**: Emits `delta: {"role": "assistant"}` to announce the respondent role.
2. **Intermediate Chunks**: Emit `delta: {"content": "<token_text>"}` with `finish_reason: null`.
3. **Final Completion Chunk**: Emits empty delta `{}` with `finish_reason: "stop"` (or `"length"` if truncated).
4. **Sentinel Termination Frame**: Emits `data: [DONE]\n\n` instructing the client event listener to close the stream.
