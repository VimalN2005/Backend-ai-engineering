# Day 79: Principal ML Systems Interview Questions & Answers — FastAPI Model Serving & Dynamic Batching

---

### Question 1: Why does calling a synchronous CPU-bound model inference function inside an `async def` FastAPI route cause event loop starvation? Compare `def` (Starlette threadpool) vs `run_in_executor` vs dedicated queue-based dynamic batching.

#### Candidate Answer
FastAPI is built on top of Starlette and `asyncio`. In Python, the asyncio event loop runs on a **single thread**.

1. **The Failure Mode of `async def` with CPU-Bound Inference**:
   - When a route handler is declared as `async def`, FastAPI executes it directly on the main event loop thread.
   - If that function performs CPU-bound computation (such as running Scikit-Learn `predict_proba()`, PyTorch tensor math, or feature scaling), it holds the CPU and does not yield control (`await`).
   - Consequently, the event loop is starved: it cannot process incoming TCP handshakes, TLS negotiations, HTTP pipelining, or Kubernetes health pings. P99 latency escalates exponentially, and the service becomes completely unresponsive.

2. **Standard `def` (Starlette Threadpool)**:
   - When declared as standard synchronous `def`, FastAPI automatically offloads execution to an external worker thread via `anyio.to_thread.run_sync()`.
   - **Trade-off**: The event loop remains unblocked, but under high concurrency (e.g. 500 simultaneous requests), Python spins up hundreds of OS threads, saturating the CPU with context-switching overhead and memory consumption without vectorizing computation.

3. **Queue-Based Dynamic Batching (Principal Solution)**:
   - The route handler is a lightweight `async def` function that enqueues the request payload and an `asyncio.Future` into an in-memory queue.
   - A dedicated background worker pulls items, merges $N$ entities into a single 2D NumPy/Tensor matrix, and evaluates the batch in a single SIMD-vectorized call offloaded to a thread.
   - **Result**: Zero event loop starvation, maximum CPU vector register saturation, and up to **$4\times\text{ to }8\times$ higher throughput** than individual threadpool execution.

---

### Question 2: Explain the mechanics and mathematical invariants of Dynamic Batching in high-throughput inference engines. How do you tune Maximum Batch Size ($B_{\max}$) and Wait Timeout ($\Delta t_{\text{wait}}$) to optimize the throughput-latency trade-off?

#### Candidate Answer
Dynamic batching is an architectural pattern that aggregates independent incoming client requests arriving across time into micro-batches to exploit hardware vectorization (AVX-512, GPU Tensor Cores).

#### The Dispatch Invariants
A micro-batch is dispatched to the underlying model when:
$$\text{Trigger Condition} = (\text{Current Batch Size} \ge B_{\max}) \lor (\text{Elapsed Wait Time} \ge \Delta t_{\text{wait}})$$
Where:
- $B_{\max}$ is the Maximum Batch Capacity.
- $\Delta t_{\text{wait}}$ is the Maximum Coalescence Timeout.

#### Parameter Tuning Methodology
1. **Setting $\Delta t_{\text{wait}}$**:
   - $\Delta t_{\text{wait}}$ represents the maximum artificial delay injected into a single client request.
   - If client SLA requires $\text{P99 Latency} \le 20\text{ ms}$ and single-batch model execution takes $t_{\text{inf}} = 8\text{ ms}$, then:
     $$\Delta t_{\text{wait}} \le \text{SLA} - t_{\text{inf}} - t_{\text{network}} \implies 20\text{ ms} - 8\text{ ms} - 2\text{ ms} = 10\text{ ms}$$
   - In production, $\Delta t_{\text{wait}}$ is typically configured between $2.0\text{ ms}$ and $5.0\text{ ms}$.
2. **Setting $B_{\max}$**:
   - $B_{\max}$ is chosen by identifying the saturation point on the model's **Roofline throughput curve**.
   - Profile batch sizes $B \in \{1, 2, 4, 8, 16, 32, 64\}$. As $B$ increases, throughput (samples/sec) increases up to a hardware saturation point (typically $B = 16$ or $32$ on modern CPUs). Beyond this point, L2/L3 cache misses increase and inference latency grows super-linearly without throughput gain.

---

### Question 3: How do you design an in-memory canonical feature caching layer for a real-time ML prediction service? What are the normalization, hashing, and eviction challenges?

#### Candidate Answer
In high-throughput prediction services (fraud scoring, product pricing, ad click prediction), clients frequently submit identical feature payloads. Evaluating the ML pipeline for every duplicate request wastes CPU cycles.

#### Architectural Design
1. **Canonical Key Normalization**:
   - Python dictionaries are order-independent, and floating-point representations vary (`0.5000000001` vs `0.5`).
   - Normalization steps:
     1. Round all float features to a fixed decimal precision (e.g. 3 or 4 decimal places).
     2. Sort keys alphabetically.
     3. Serialize to canonical JSON or byte tuples:
        $$\text{Key} = \text{SHA256}(\text{CanonicalJSON}(x))$$
2. **Two-Tier Cache Topology**:
   - **L1 (In-Memory Local LRU)**: Thread-safe, capacity-bounded `OrderedDict` inside the Python container heap ($10,000$ items, $TTL = 300\text{ s}$). Sub-millisecond latency (<150 us).
   - **L2 (Distributed Redis Cluster)**: Shared across all container replicas in the Kubernetes deployment.
3. **Eviction & Safety**:
   - To prevent memory exhaustion (`OOMKilled`), the local cache must strictly enforce a maximum item count. When capacity is exceeded, the Least Recently Used (LRU) entry is evicted.

---

### Question 4: How should health check probes (`/healthz` vs `/ready`) be architected in Kubernetes when deploying large ML model inference microservices with slow startup times?

#### Candidate Answer
Large machine learning services often require several seconds to download model weights from S3, deserialize arrays, and compile computation graphs. Conflating liveness and readiness probes causes service outages.

#### Decoupled Probe Architecture
1. **Liveness Probe (`GET /healthz`)**:
   - Answers: *"Is the HTTP server process alive and responding to requests?"*
   - Returns HTTP 200 immediately if the Uvicorn/ASGI event loop is operational.
   - **Failure Action**: If this fails (e.g. infinite loop, process deadlock), Kubernetes restarts the container (`SIGKILL`).
2. **Readiness Probe (`GET /ready`)**:
   - Answers: *"Are the model weights fully deserialized, warmed up, and ready to accept live customer traffic?"*
   - Checks:
     - Model object is present in application state (`app.state.is_ready == True`).
     - Dynamic batching background worker is running.
     - Warm-up inference pass has completed successfully.
   - Returns HTTP 503 Service Unavailable during startup.
   - **Action**: Kubernetes routes traffic to the pod *only* after `/ready` returns HTTP 200, completely eliminating cold-start 500 errors during rolling releases.
3. **Startup Probe (`startupProbe`)**:
   - Configured with a generous failure threshold (e.g., 30 failures $\times$ 10s = 300s) to give heavy models ample time to load before the liveness probe begins evaluating.

---

### Question 5: Compare Triton Inference Server / TorchServe with custom FastAPI model serving. When is custom FastAPI serving preferred, and when must a team migrate to a dedicated C++ inference server?

#### Candidate Answer

| Capability | Custom FastAPI Serving | Dedicated Inference Server (Triton / TorchServe) |
| :--- | :--- | :--- |
| **Development Velocity** | **Very High** (pure Python, simple debugging) | Moderate (requires model configuration protobufs) |
| **Custom Business Logic** | **Trivial** (arbitrary Python preprocessing, DB queries) | Complex (requires custom Python backends or C++ plugins) |
| **Dynamic Batching** | Application-level (asyncio queue, Python overhead) | **Native C++ Kernel Batching** (zero GIL, ultra-low latency) |
| **Multi-Model Concurrency** | Limited by Python GIL / multi-processing | **Dynamic Model Pipelining & GPU Scheduling** |
| **Hardware Acceleration** | Python bindings (ONNX Runtime, PyTorch CPU) | Direct TensorRT, CUDA IPC, multi-GPU orchestration |
| **Protocol Support** | REST HTTP/1.1 (JSON), WebSockets | gRPC (HTTP/2), binary protocols, shared memory IPC |

#### Migration Decision Rule
- **Use FastAPI When**: Serving tabular/Scikit-Learn models, lightweight XGBoost, business-heavy preprocessing pipelines, or internal APIs handling $< 2,000\text{ RPS}$ where team agility and Python ecosystem integration are paramount.
- **Migrate to Triton When**: Serving deep learning LLMs, computer vision, or high-throughput GPU workloads ($> 10,000\text{ RPS}$) requiring multi-model GPU concurrency, C++ zero-copy shared memory IPC, and sub-millisecond tail latency SLAs.
