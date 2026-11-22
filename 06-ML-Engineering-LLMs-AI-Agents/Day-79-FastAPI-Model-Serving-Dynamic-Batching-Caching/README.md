# Day 79: FastAPI Model Serving: Dynamic Batching, In-Memory Inference & Caching

## 1. Production Problem Statement: The Low-Throughput Single-Sample Dilemma

In modern AI and ML microservices, models are frequently exposed via REST or gRPC APIs using modern Python ASGI web frameworks such as **FastAPI**. While FastAPI provides asynchronous non-blocking I/O via Python's `asyncio` event loop, machine learning inference (Scikit-Learn, PyTorch, XGBoost, ONNX Runtime) is fundamentally **CPU-bound, synchronous, and non-preemptive**.

When an engineering team deploys a naive model endpoint:
```python
# BROKEN: Blocks the ASGI event loop completely
@app.post("/predict")
async def predict(req: PredictRequest):
    # Synchronous CPU-bound matrix multiplication blocks all concurrent requests!
    return model.predict(req.features)
```

Three critical production failure modes inevitably emerge:

1. **Event Loop Starvation & Latency Cascades**:
   - Running CPU-bound inference directly inside an `async def` route blocks the single-threaded asyncio event loop.
   - All other incoming HTTP connections, TLS handshakes, and health check pings are stalled. P99 latency spikes from 10ms to several seconds under modest concurrency (50 requests/sec).
2. **CPU Under-Utilization & Cache Inefficiency**:
   - Modern CPU architectures and SIMD vector units (AVX-512) achieve peak throughput when processing batches of $16\text{–}64$ vectors simultaneously.
   - Processing one single entity per API request wastes CPU vector registers, incurs continuous memory bus prefetching penalties, and keeps CPU utilization below $20\%$.
3. **Redundant Re-computation on Identical Payloads**:
   - In production fraud detection, credit underwriting, or recommendation services, identical feature vectors or repetitive client queries arrive repeatedly.
   - Without an intelligent in-memory feature cache, expensive inference pipelines recompute the exact same matrix transformations repeatedly.

The enterprise solution is the **FastAPI Dynamic Batching & Multi-Tier Caching Architecture**: offloading inference to dedicated worker threads, coalescing concurrent incoming client requests into dynamic micro-batches, and caching hot feature predictions.

---

## 2. Production Architecture Blueprint: Dynamic Batching & Feature Caching

```
+-------------------------------------------------------------------------------------------------------+
|                    FASTAPI HIGH-THROUGHPUT ML SERVING ARCHITECTURE                                    |
+-------------------------------------------------------------------------------------------------------+
|                                                                                                       |
|   Client Request 1 (FastAPI /v1/predict)        Client Request 2          Client Request N            |
|            |                                           |                         |                    |
|            v                                           v                         v                    |
|   +-----------------------------------------------------------------------------------------------+   |
|   |                      FastAPI Async Ingress Layer (Pydantic V2 Validation)                     |   |
|   +-----------------------------------------------------------------------------------------------+   |
|                                                  |                                                    |
|                                                  v                                                    |
|                           +-----------------------------------------------+                           |
|                           |      Two-Tier Canonical Feature Cache         |                           |
|                           | - SHA-256 Hashing of Normalized Features      |                           |
|                           | - Thread-Safe LRU In-Memory Buffer            |                           |
|                           +-----------------------------------------------+                           |
|                                         |                   |                                         |
|                                   [Cache Hit]         [Cache Miss]                                    |
|                                         |                   |                                         |
|                                         v                   v                                         |
|                                  Return Cached     +----------------------------------+               |
|                                  Prediction (100us)| Request Queue (asyncio.Queue)    |               |
|                                                    | - Enqueue (Payload, Future)      |               |
|                                                    +----------------------------------+               |
|                                                                     |                                 |
|                                                                     v                                 |
|                                                    +----------------------------------+               |
|                                                    | Dynamic Batching Engine Worker   |               |
|                                                    | - Collects up to MaxBatchSize=32 |               |
|                                                    |   OR wait up to MaxTimeout=5ms   |               |
|                                                    +----------------------------------+               |
|                                                                     |                                 |
|                                                                     v                                 |
|                                                    +----------------------------------+               |
|                                                    | SIMD Vectorized Batch Inference  |               |
|                                                    | - Offloaded to Dedicated Worker  |               |
|                                                    | - Single model.predict_proba(X)  |               |
|                                                    | - Vectorized NumPy Evaluation    |               |
|                                                    +----------------------------------+               |
|                                                                     |                                 |
|                                                                     v                                 |
|                                                    +----------------------------------+               |
|                                                    | Batch Demultiplexer & Future Set |               |
|                                                    | - Populate LRU Cache             |               |
|                                                    | - Set Future.set_result(res)     |               |
|                                                    +----------------------------------+               |
|                                                                     |                                 |
|                                                                     v                                 |
|   Return Client 1 Response                      Return Client 2 Response      Return Client N         |
|   {"risk_prob": 0.082, "latency_ms": 3.4}       {"risk_prob": 0.914}          {"risk_prob": 0.125}    |
|                                                                                                       |
+-------------------------------------------------------------------------------------------------------+
```

---

## 3. ASGI Event Loop vs CPU-Bound Inference: `def` vs `async def`

Understanding Python's asynchronous concurrency model is critical when hosting Scikit-Learn or ONNX models in FastAPI:

1. **`async def` with CPU-Bound Operations (ANTI-PATTERN)**:
   - When a route is defined with `async def`, FastAPI executes it directly on the main thread's asyncio event loop.
   - Any CPU-bound call (e.g. `model.predict()`, feature scaling) freezes the entire process. No other coroutine can run until inference finishes.
2. **Standard `def` (FastAPI Threadpool)**:
   - When a route is declared as standard `def` (without `async`), FastAPI automatically dispatches it to a background threadpool (`anyio.to_thread.run_sync`).
   - The event loop remains unblocked, but unconstrained thread creation causes context-switching thrashing and memory overhead under high concurrency.
3. **Queue-Based Dynamic Batching Worker (GOLD STANDARD)**:
   - Ingress endpoints are lightweight `async def` functions that merely enqueue an incoming payload and an `asyncio.Future` into an in-memory queue.
   - A dedicated background worker pulls batches from the queue, executes vectorized batch inference, and resolves the futures.
   - This achieves **maximum CPU hardware saturation**, **bounded latency**, and **zero event loop blocking**.

---

## 4. The Dynamic Batching Algorithm

Dynamic batching trades a negligible microsecond delay for massive hardware throughput gains:

### Mathematical Invariants
A batch is dispatched to the model when **either** of two conditions is satisfied:
$$\text{Dispatch Condition} = (\text{Current Batch Size} \ge B_{\max}) \lor (\text{Wait Time} \ge \Delta t_{\max})$$
Where:
- $B_{\max}$ is the Maximum Batch Size (e.g., 16 or 32 samples).
- $\Delta t_{\max}$ is the Maximum Coalescence Timeout (e.g., 5.0 milliseconds).

```
Timeline:
t = 0.0ms: Request 1 arrives -> added to batch [1] -> Timer started (timeout = 5.0ms)
t = 1.2ms: Request 2 arrives -> added to batch [1, 2]
t = 2.8ms: Request 3 arrives -> added to batch [1, 2, 3]
t = 5.0ms: Timeout reached! -> Batch [1, 2, 3] dispatched to CPU SIMD kernel.
t = 6.2ms: Single batch prediction completes -> Results dispatched to Clients 1, 2, and 3.
```
- Individual client latency is bounded: $\le \Delta t_{\max} + t_{\text{inference}}$.
- System throughput scales linearly with batch coalescence.

---

## 5. In-Memory Canonical Feature Caching

To prevent redundant computation, incoming feature payloads are normalized and hashed:
1. **Canonical Key Generation**: Numeric values are rounded to a fixed precision (e.g. 4 decimals), sorted by feature name, and hashed using SHA-256:
   $$\text{CacheKey} = \text{SHA256}(\text{CanonicalJSON}(x))$$
2. **LRU Eviction**: In-memory cache is bounded to a maximum capacity (e.g. 50,000 entries) with a Time-To-Live (TTL). When capacity is reached, the Least Recently Used item is purged.
3. **Cache Hit Performance**: Cache hits return predictions in **$< 150 \text{ microseconds}$**, completely bypassing model inference pipelines.

---

## 6. Production Engineering Checklist for FastAPI ML Services

- [ ] **Unblocked Event Loop**: Are all CPU-bound feature transforms and model inference calls executed outside the main asyncio loop?
- [ ] **Bounded Batch Timeout**: Is $\Delta t_{\max}$ strictly capped ($\le 5\text{–}10\text{ ms}$) to ensure single-request latency SLAs are never breached?
- [ ] **Lifespan Model Loading**: Is model loading and warm-up confined to `@asynccontextmanager` lifespan startup, eliminating per-request loading?
- [ ] **Defensive Schema Validation**: Are all API inputs strictly validated using Pydantic V2 models with bounds checking?
- [ ] **Memory-Bounded Cache**: Does the in-memory feature cache implement explicit max-size LRU eviction to prevent process memory leaks?
- [ ] **Health & Readiness Separation**: Does `/healthz` check liveness immediately, while `/ready` validates that model weights are loaded and warm?
- [ ] **Graceful Worker Shutdown**: Does the dynamic batching background task cancel cleanly during container SIGTERM signals?
