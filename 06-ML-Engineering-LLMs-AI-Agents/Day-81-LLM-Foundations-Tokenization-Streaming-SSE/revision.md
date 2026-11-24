# Day 81: Rapid Revision & Active Recall Cheat Sheet

## 1. High-Yield Architectural Summary (5-Minute Scan)

- **Tokenization Foundation**:
  - LLMs process discrete token IDs, not words or characters.
  - **Byte-Pair Encoding (BPE)**: Operates on UTF-8 bytes ($256$ base tokens). Greedily merges the most frequent adjacent pairs until target vocabulary size $V$ is reached. Zero Out-Of-Vocabulary (OOV) errors.
  - **Space & Case Sensitivity**: `" Hello"` $\ne$ `"Hello"`. Tokens preserve exact whitespace boundaries.
- **Context Windows & Attention Complexity**:
  - Self-Attention scales quadratically: $\mathcal{O}(N^2)$ in compute and memory.
  - Total prompt sequence constraint: $L_{\text{prompt}} + L_{\text{completion}} \le L_{\text{context}}$.
  - **KV-Cache**: Precomputes and stores Key and Value activations in GPU VRAM across decoding steps to avoid $\mathcal{O}(N^2)$ recalculation per token.
- **KV-Cache Memory Footprint Formula**:
  $$\text{Memory}_{\text{KV}} = 2 \times n_{\text{layers}} \times n_{\text{kv\_heads}} \times d_{\text{head}} \times \text{seq\_len} \times \text{batch\_size} \times \text{precision\_bytes}$$
  *(Grouped-Query Attention (GQA) reduces $n_{\text{kv\_heads}}$, slashing memory by $4\times - 8\times$ vs Multi-Head Attention).*
- **Bounded Context Management**:
  - **Budget Allocation**: Partition tokens across System, Tools, RAG Context, Chat History, and Reserved Completion.
  - **Pinning Rule**: Never evict the System Prompt (`role: system`). Evict older chat turns via sliding window FIFO.
- **Server-Sent Events (SSE) Protocol**:
  - Unidirectional HTTP streaming via `Transfer-Encoding: chunked`.
  - Required Headers:
    - `Content-Type: text/event-stream; charset=utf-8`
    - `Cache-Control: no-cache, no-transform`
    - `Connection: keep-alive`
    - `X-Accel-Buffering: no` *(prevents Nginx/ALB from buffering chunks)*
  - Frame structure: `data: <json>\n\n`, termination sentinel: `data: [DONE]\n\n`.
  - Multibyte UTF-8 characters (emojis 🚀, non-ASCII) split across chunks must be reassembled using `codecs.getincrementaldecoder("utf-8")`.

---

## 2. Core Mathematical Formulations

| Concept | Mathematical Equation / Complexity | Operational Significance |
| :--- | :--- | :--- |
| **Self-Attention FLOPs** | $\mathcal{O}(4N^2d + 2Nd^2)$ | Doubling context length quadruples attention computation. |
| **KV-Cache Size** | $2 \cdot L \cdot H_{\text{kv}} \cdot D \cdot S \cdot B \cdot P$ | Directly determines maximum concurrent serving batch size on GPU. |
| **BPE Pair Frequency** | $\arg\max_{(A, B)} \sum_{c \in C} \mathbb{I}(c[i]=A \land c[i+1]=B)$ | Greedy rule selecting which token pair to merge next. |
| **Total Request Latency** | $T_{\text{total}} = \text{TTFT} + \frac{N_{\text{completion}}}{\text{Tokens Per Second}}$ | TTFT is compute-bound (prefill); Tokens/sec is memory-bandwidth bound (decode). |

---

## 3. 10 Active Recall Flashcards

### Card 1
- **Q**: Why do modern LLMs use Byte-Level BPE instead of Word-level or Character-level tokenization?
- **A**: Word-level produces massive vocabularies and frequent `<UNK>` errors; character-level quadruples sequence length ($\mathcal{O}(N^2)$ attention explosion). Byte-level BPE provides optimal sequence compression while guaranteeing zero OOV errors via 256 byte fallbacks.

### Card 2
- **Q**: What causes `" Hello"` and `"Hello"` to map to different token IDs in BPE?
- **A**: BPE encodes whitespace as a distinct character/byte prefix within subwords. Missing or extra whitespace fundamentally alters the token ID and resulting vector embedding.

### Card 3
- **Q**: Why does self-attention memory complexity scale quadratically $\mathcal{O}(N^2)$?
- **A**: Every token in the sequence must compute dot-product attention scores against every other token in the sequence, producing an $N \times N$ attention weight matrix per head.

### Card 4
- **Q**: What is the purpose of the Key-Value (KV) Cache during inference?
- **A**: In autoregressive decoding, past tokens do not change. Caching their Key and Value activation vectors in GPU VRAM prevents redundant recalculations, reducing decoding step complexity from $\mathcal{O}(N^2)$ to $\mathcal{O}(N)$.

### Card 5
- **Q**: How does Grouped-Query Attention (GQA) reduce KV-Cache memory consumption?
- **A**: Instead of allocating one KV head per Query head (MHA), GQA groups multiple Query heads (e.g. 8 Query heads) to share a single Key and Value head, reducing KV-cache size by $8\times$.

### Card 6
- **Q**: What critical HTTP header must be included when streaming SSE through Nginx or AWS ALB?
- **A**: `X-Accel-Buffering: no` (and `Cache-Control: no-transform`), which tells reverse proxies not to buffer small chunk responses in 4KB/16KB buffers, ensuring immediate token delivery.

### Card 7
- **Q**: What happens if you run `.decode("utf-8")` directly on a raw byte chunk containing the first 2 bytes of a 4-byte emoji?
- **A**: It throws `UnicodeDecodeError` because the byte sequence is incomplete. Use `codecs.getincrementaldecoder("utf-8")` to hold incomplete trailing bytes until subsequent chunks arrive.

### Card 8
- **Q**: When implementing a sliding context window buffer, why must the System Prompt never be evicted?
- **A**: Evicting the system prompt destroys model persona, tool definitions, output JSON schema contracts, and safety guardrails, exposing the model to prompt injection.

### Card 9
- **Q**: What is the difference between Time-To-First-Token (TTFT) and Inter-Token Latency (ITL)?
- **A**: TTFT measures the prefill phase (prompt ingestion, compute-bound). ITL measures the autoregressive decode phase (generating each token, memory-bandwidth bound).

### Card 10
- **Q**: What is the W3C SSE sentinel frame that signals the client to terminate the stream?
- **A**: `data: [DONE]\n\n`.
