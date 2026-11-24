# Day 81: Principal AI Systems Interview Questions & Answers

## Question 1: Explain the internal algorithmic mechanics of Byte-Level Byte-Pair Encoding (BPE). Why does modern LLM tokenization operate on UTF-8 bytes rather than characters or words?

### Deep Technical Answer
Modern LLM architectures (GPT-4, Llama 3, Mistral) universally operate on **Byte-Level BPE** rather than character- or word-level vocabularies:

1. **The Out-Of-Vocabulary (OOV) Dilemma**:
   - Word-level tokenizers require massive vocabularies ($> 1\text{M}$ entries) and still fail when encountering typos, technical jargon, or multilingual inputs, mapping unknown tokens to `<UNK>`, which destroys semantic context.
   - Character-level tokenizers solve OOV, but sequence lengths explode by $4\times$ to $6\times$. Because Transformer self-attention scales quadratically ($\mathcal{O}(N^2)$), a $4\times$ sequence length increase incurs a catastrophic $16\times$ increase in FLOPs and memory.

2. **Byte-Level BPE Mechanics**:
   - **Base Vocabulary**: Initializes with exactly 256 individual UTF-8 byte tokens ($0\times00$ to $0\times\text{FF}$) plus designated special control tokens (`<|endoftext|>`).
   - **Merge Learning**: The algorithm greedily identifies the most statistically frequent adjacent byte pairs across the training corpus and merges them into composite tokens (e.g., `b't' + b'h' \to b'th'`, then `b'th' + b'e' \to b'the'`).
   - **Guaranteed Coverage**: Any string in any human language, programming language, or binary data can be represented as a sequence of UTF-8 bytes. Even if an emoji or rare unicode character was never observed during tokenizer training, it falls back gracefully to its constituent 3 or 4 raw byte tokens without throwing an error or emitting `<UNK>`.

---

## Question 2: Derive the exact mathematical formula for Key-Value (KV) Cache memory footprint during autoregressive decoding. How does Grouped-Query Attention (GQA) reduce this footprint compared to Multi-Head Attention (MHA)?

### Deep Technical Answer
In autoregressive Transformer decoding, generating each subsequent token requires computing attention over all past keys and values. To eliminate recomputing past activations, the keys ($K$) and values ($V$) are cached in GPU VRAM.

### The KV-Cache Formula
For a sequence of length $S$ at batch size $B$:

$$\text{KV-Cache Bytes} = 2 \times n_{\text{layers}} \times n_{\text{kv\_heads}} \times d_{\text{head}} \times S \times B \times P$$

Where:
- $2$: Two separate cached tensors ($K$ and $V$).
- $n_{\text{layers}}$: Total transformer layer depth.
- $n_{\text{kv\_heads}}$: Number of Key-Value attention heads.
- $d_{\text{head}}$: Dimension per head ($d_{\text{model}} / n_{\text{query\_heads}}$).
- $S$: Total sequence length (prompt tokens + generated tokens).
- $B$: Batch size (number of concurrent active streams).
- $P$: Precision in bytes ($2$ bytes for FP16/BF16, $1$ byte for FP8).

### MHA vs. MQA vs. GQA
- **Multi-Head Attention (MHA)**: Each Query head has its own dedicated Key and Value head ($n_{\text{kv\_heads}} = n_{\text{query\_heads}}$). For a 70B model with 64 heads, KV-cache requires 64 Key-Value projections per layer, severely bottlenecking max batch size.
- **Multi-Query Attention (MQA)**: All Query heads share a single Key and Value head ($n_{\text{kv\_heads}} = 1$). While this slashes KV-cache by $64\times$, it causes capacity degradation on complex reasoning tasks.
- **Grouped-Query Attention (GQA)**: Divides Query heads into groups sharing a single KV head (e.g., Llama 3 uses 64 Query heads and 8 KV heads, an $8:1$ ratio). GQA retains full MHA reasoning quality while reducing KV-cache memory consumption by **$8\times$**, enabling $8\times$ higher concurrent streaming capacity on the same GPU hardware.

---

## Question 3: Why is Server-Sent Events (SSE) overwhelmingly favored over WebSockets for LLM completion streaming in production backends? Under what specific scenario would WebSockets be technically necessary?

### Deep Technical Answer

### Why SSE Dominates LLM Inference
1. **Unidirectional Asymmetry**: LLM inference is fundamentally unidirectional: the client submits a single prompt payload via HTTP POST, and the server emits an incremental stream of tokens over time. SSE is purpose-built for server-to-client streaming.
2. **Native HTTP Infrastructure Compatibility**: SSE is standard HTTP/1.1 or HTTP/2. It works seamlessly through existing reverse proxies (Nginx, Traefik, Envoy), API gateways (Kong, AWS API Gateway), Cloudflare CDNs, and corporate firewalls without requiring protocol upgrades or custom timeout overrides.
3. **Built-in Auto-Reconnection & State Tracking**: Browsers provide native `EventSource` reconnection logic, transmitting `Last-Event-ID` upon transient network drops.
4. **Stateless Load Balancing**: WebSockets require long-lived bidirectional stateful TCP connections, complicating horizontal pod autoscaling and rolling deployments. SSE requests can be load-balanced like standard HTTP requests.

### When WebSockets Are Strictly Necessary
WebSockets become technically required only when the interaction requires **true bidirectional, low-latency, full-duplex communication**:
- **Real-Time Voice-to-Voice AI Agents**: Streaming client audio input (microphone chunks) while simultaneously streaming server audio output (TTS chunks).
- **Client Interruptions (Barge-in)**: Allowing the user to speak or interrupt mid-sentence, requiring the client to immediately signal cancellation over the active socket without tearing down the connection.

---

## Question 4: What is Time-To-First-Token (TTFT) versus Inter-Token Latency (ITL)? What architectural techniques optimize each?

### Deep Technical Answer
LLM serving latency consists of two fundamentally distinct operational regimes:

1. **Time-To-First-Token (TTFT) (The Prefill Phase)**:
   - The duration from request receipt until the very first token is emitted to the client.
   - **Workload Characteristic**: **Compute-Bound**. The entire prompt is ingested simultaneously in parallel matrix multiplications ($QK^T$).
   - **Optimization Techniques**:
     - *Chunked Prefill*: Slicing long prompts into smaller blocks so prompt evaluation does not monopolize the GPU and block active decoding passes.
     - *Prompt Caching / Prefix Caching*: Reusing precomputed KV-cache for identical system prompts or common document prefixes (e.g., vLLM automatic prefix caching).
     - *FlashAttention-2 / FlashAttention-3*: Eliminates intermediate attention matrix writes to high-bandwidth memory (HBM), operating entirely inside fast SRAM.

2. **Inter-Token Latency (ITL) (The Decode Phase)**:
   - The time elapsed between generating token $t$ and token $t+1$ (also measured as generation tokens/sec).
   - **Workload Characteristic**: **Memory-Bandwidth Bound**. In each forward pass, the GPU must stream tens of gigabytes of model weights from HBM to compute cores to generate a single token.
   - **Optimization Techniques**:
     - *Speculative Decoding*: Using a lightweight draft model (e.g., 1B model) to propose $K$ candidate tokens, verifying all $K$ tokens in a single parallel forward pass of the large target model.
     - *Weight Quantization (FP8, INT4, AWQ)*: Reduces memory-bus bandwidth pressure by reducing weight transfer sizes.
     - *Continuous Dynamic Batching*: Inserting new requests into active decoding iterations dynamically as slots free up.

---

## Question 5: How does a production backend detect and handle client socket disconnections during long-running streaming generation?

### Deep Technical Answer
When an end-user navigates away, closes the browser tab, or cancels generation in the UI, an unoptimized backend continues executing autoregressive decoding passes until `max_tokens` is reached, burning costly GPU compute.

### Multi-Tier Disconnection Architecture
1. **TCP Connection Drop (Kernel / Socket Layer)**:
   - The client browser closes the TCP connection, sending a `FIN` or `RST` packet.
2. **Reverse Proxy Layer (Nginx / Envoy / AWS ALB)**:
   - The proxy detects the client socket closure and terminates the upstream connection to the backend server.
3. **ASGI Server Layer (Uvicorn / Hypercorn)**:
   - Uvicorn monitors the ASGI receive channel: `event = await receive()`.
   - When the client socket drops, Uvicorn sends an `{"type": "http.disconnect"}` message into the channel.
4. **Application Framework Layer (FastAPI / Starlette)**:
   - The application framework raises `asyncio.CancelledError` inside the active streaming generator task, or exposes `await request.is_disconnected()`.
5. **Inference Engine Layer (vLLM / Triton / TGI)**:
   - Upon catching `CancelledError` or detecting disconnection, the application must immediately invoke the engine's cancellation API:
     ```python
     engine.abort_request(request_id)
     ```
   - This immediately evicts the active request from the GPU batch scheduler and frees its KV-cache blocks in memory, reclaiming capacity for pending queries.
