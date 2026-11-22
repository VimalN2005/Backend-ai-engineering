# Day 79: Rapid 5-Minute Revision & Flashcards — FastAPI Model Serving

---

## 1. High-Yield Architecture Summary (5-Minute Read)

1. **The ASGI Event Loop Rule**:
   - `async def` routes run directly on the single-threaded event loop. Never run CPU-bound inference directly in `async def`—it starves the loop and blows up P99 tail latency.
   - Use `loop.run_in_executor(None, fn)` to offload to a threadpool, or use a dedicated queue-based dynamic batcher.
2. **Dynamic Batching Mechanics**:
   - Trades a negligible microsecond coalescence wait ($\Delta t_{\text{wait}} \le 5\text{ ms}$) to evaluate requests in SIMD-vectorized batches ($B_{\max} \le 32$).
   - Enqueue `(request, future)` into `asyncio.Queue`, batch worker evaluates `model.predict_proba(matrix)`, resolves individual futures.
   - Boosts request throughput by **$3\times\text{ to }8\times$** on multi-core CPUs.
3. **In-Memory Canonical Feature Caching**:
   - Hashing sorted, rounded feature tuples (`SHA-256`) into an `OrderedDict` LRU cache.
   - Returns predictions in **$< 150 \text{ microseconds}$** on cache hits, bypassing model execution.
4. **FastAPI Lifespan Management**:
   - Use `@asynccontextmanager` lifespan to load models and run warmup passes *once* at startup.
   - Never load or unpickle models inside request handlers.
5. **Kubernetes Health Probes**:
   - `/healthz` (liveness): Process alive.
   - `/ready` (readiness): Model loaded, warmed up, and ready for user traffic.

---

## 2. Key Mathematical Formulas & Operational Metrics

- **Dynamic Batch Dispatch Trigger**:
  $$\text{Dispatch} = (\text{Queue Size} \ge B_{\max}) \lor (\text{Elapsed Time} \ge \Delta t_{\text{wait}})$$
- **Cache Hit Ratio**:
  $$\text{Hit Ratio \%} = \left(\frac{\text{Hits}}{\text{Hits} + \text{Misses}}\right) \times 100$$
- **Latency Bounded Constraint**:
  $$\text{Client Latency} \le \Delta t_{\text{wait}} + t_{\text{inference}} + t_{\text{network}}$$

---

## 3. Top 10 Active Recall Flashcards

### Card 1: What happens if you execute a CPU-bound `model.predict()` directly inside an `async def` FastAPI route?
**Answer**: It blocks the single-threaded asyncio event loop. All concurrent client requests, health checks, and incoming connections are completely halted until inference finishes, causing P99 latency explosions.

### Card 2: How does dynamic batching improve ML inference throughput on CPUs?
**Answer**: By coalescing independent client requests into a single 2D matrix, it allows CPU SIMD vector units (AVX-512) and BLAS libraries to perform parallel matrix operations, avoiding single-entity instruction overhead.

### Card 3: What two conditions trigger the dispatch of a dynamic micro-batch?
**Answer**: Either the collected batch size reaches $B_{\max}$ (Maximum Batch Size) OR the elapsed wait time reaches $\Delta t_{\text{wait}}$ (Maximum Coalescence Timeout).

### Card 4: How does a dynamic batcher associate individual predictions with client requests?
**Answer**: Each enqueued request is paired with an `asyncio.Future`. When the batch prediction completes, the worker iterates through the result array and resolves each future via `future.set_result(prediction[i])`.

### Card 5: Why must floating-point feature values be rounded before generating canonical cache keys?
**Answer**: Tiny floating-point rounding variations (e.g. `0.2000000001` vs `0.2`) produce different SHA-256 hashes, causing cache misses on mathematically identical feature vectors.

### Card 6: What is the risk of using an unconstrained Python dictionary as an in-memory cache?
**Answer**: It grows monotonically with unique requests, creating a memory leak that eventually breaches the container's memory limit, triggering Kubernetes `OOMKilled` (Exit Code 137). An explicit LRU capacity must be enforced.

### Card 7: What is the purpose of running a dummy inference warm-up pass during application startup?
**Answer**: It pre-faults virtual memory pages into RAM, primes CPU L1/L2 caches, and initializes underlying BLAS/OpenMP worker threads before the pod receives live traffic, eliminating cold-start latency spikes.

### Card 8: Why should `/healthz` and `/ready` be decoupled into two separate endpoints?
**Answer**: `/healthz` confirms the process is alive, while `/ready` indicates the model weights are loaded and warm. Decoupling prevents Kubernetes from routing customer traffic to containers that are still initializing.

### Card 9: What is the difference between declaring a route as `def` vs `async def` in FastAPI?
**Answer**: `def` routes are automatically offloaded by FastAPI to a background threadpool, keeping the event loop free. `async def` routes run directly on the event loop and must never perform blocking synchronous computation.

### Card 10: When should an enterprise migrate from FastAPI model serving to Triton Inference Server?
**Answer**: When serving high-throughput GPU workloads, deep learning models, multi-model pipelines ($>10,000\text{ RPS}$), or when C++ zero-copy shared memory IPC and dynamic hardware kernel scheduling are required.
