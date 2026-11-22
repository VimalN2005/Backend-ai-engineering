# Day 79: Production Mistakes & Antipatterns — FastAPI Model Serving & Dynamic Batching

In production machine learning systems, hosting models behind ASGI web APIs (FastAPI/Uvicorn) introduces complex concurrency failure modes. Misunderstanding asynchronous programming, dynamic batching timeouts, or threadpool boundaries can degrade a model serving cluster from thousands of requests per second to complete event loop paralysis and P99 latency explosions. Below are seven critical enterprise antipatterns and their production-grade remedies.

---

### Mistake 1: Blocking the ASGI Event Loop with Synchronous Model Inference

#### The Anti-Pattern
Invoking CPU-bound model inference directly inside an `async def` endpoint:
```python
# BROKEN: Freezes the entire ASGI event loop
@app.post("/v1/predict")
async def predict(req: PredictionRequest):
    # Synchronous CPU-bound matrix computation blocks all other coroutines!
    prob = model.predict_proba(req.to_matrix())[0, 1]
    return {"prob": prob}
```

#### Why It Breaks Production
1. Python's `asyncio` is cooperative single-threaded concurrency. When an `async def` function executes a synchronous CPU-bound task (like Scikit-Learn or PyTorch inference), the event loop stops processing events.
2. No other incoming HTTP connection, TLS handshake, or health check ping can proceed until inference finishes.
3. Under a concurrency of only 50 requests/sec, P99 latency spikes from 10ms to over 5,000ms, and Kubernetes liveness probes fail, triggering cascading pod restarts.

#### Production Solution
Offload CPU-bound inference to a worker threadpool using `loop.run_in_executor` or use a dedicated queue-based dynamic batcher:
```python
# PRODUCTION GRADE: Offload CPU-bound inference to threadpool
@app.post("/v1/predict")
async def predict(req: PredictionRequest):
    loop = asyncio.get_running_loop()
    prob = await loop.run_in_executor(
        None, lambda: model.predict_proba(req.to_matrix())[0, 1]
    )
    return {"prob": prob}
```

---

### Mistake 2: Unbounded Dynamic Batch Timeout Causing P99 Tail Latency Violations

#### The Anti-Pattern
Setting a dynamic batching wait condition without a strict timeout, or setting an excessively large timeout ($\Delta t > 50\text{ ms}$):
```python
# BROKEN: Hangs low-traffic requests waiting for a full batch
while len(batch) < max_batch_size:
    item = await queue.get()  # Waits indefinitely until max_batch_size is reached!
    batch.append(item)
```

#### Why It Breaks Production
1. During off-peak traffic periods (e.g. night hours or niche regional traffic), requests arrive at a low rate (e.g. 1 request every 500ms).
2. The first client request is trapped waiting for 15 subsequent requests to arrive before the batch executes.
3. Individual client response time spikes from 5ms to over 7 seconds, violating client SLA timeouts and triggering HTTP 504 Gateway Timeouts at the API gateway (Envoy/Kong).

#### Production Solution
Always enforce a strict deadline-driven timeout ($\Delta t_{\max} \le 5\text{–}10\text{ ms}$) using a hybrid time/size dispatch condition:
```python
# PRODUCTION GRADE: Deadline-bounded dynamic batching
deadline = time.perf_counter() + timeout_seconds
while len(batch) < max_batch_size:
    if not queue.empty():
        batch.append(queue.get_nowait())
    else:
        if time.perf_counter() >= deadline:
            break
        await asyncio.sleep(0.0005)
```

---

### Mistake 3: Memory Leaks via Unbounded In-Memory Feature Caches

#### The Anti-Pattern
Using a standard Python `dict` or unbounded `functools.lru_cache` to cache model predictions:
```python
# BROKEN: Infinite memory growth
prediction_cache = {}

@app.post("/predict")
async def predict(req: PredictionRequest):
    key = str(req.features)
    if key in prediction_cache:
        return prediction_cache[key]
    prob = compute_inference(req)
    prediction_cache[key] = prob  # Unbounded dictionary growth!
    return prob
```

#### Why It Breaks Production
1. In high-throughput systems ingesting millions of unique client requests, an unbounded dictionary grows monotonically over time.
2. The Python process heap steadily expands until the container breaches its Kubernetes memory limit (`cgroup memory quota`).
3. The Linux kernel immediately sends `SIGKILL` (`OOMKilled`, exit code 137), crashing the serving container.

#### Production Solution
Always use a capacity-bounded Least Recently Used (LRU) cache with a Time-To-Live (TTL) expiration policy:
```python
# PRODUCTION GRADE: Bounded LRU cache with eviction
class BoundedLRUCache:
    def __init__(self, maxsize: int = 10_000, ttl_seconds: float = 300.0):
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self._cache = OrderedDict()

    def set(self, key: str, val: float):
        if len(self._cache) >= self.maxsize:
            self._cache.popitem(last=False)  # Evict oldest
        self._cache[key] = (val, time.time())
```

---

### Mistake 4: Deserializing Model Artifacts on Every Incoming HTTP Request

#### The Anti-Pattern
Loading the model from disk or S3 inside the request handler:
```python
# BROKEN: 50ms-200ms latency penalty per request
@app.post("/predict")
async def predict(req: PredictionRequest):
    model = joblib.load("model.joblib")  # Deserializes from disk on EVERY call!
    return model.predict(req.features)
```

#### Why It Breaks Production
1. Deserializing an ensemble model on every request incurs severe disk I/O and CPU overhead (~20ms to 200ms per request).
2. Every request allocates hundreds of megabytes of temporary heap memory, triggering aggressive Garbage Collection (GC) pauses that freeze the server.

#### Production Solution
Load and warm up the model artifact exactly once during application startup using FastAPI's `@asynccontextmanager` lifespan handler:
```python
# PRODUCTION GRADE: Load once during lifespan startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.model = joblib.load("model.joblib", mmap_mode="r")
    # Warm up model
    app.state.model.predict(dummy_input)
    yield
```

---

### Mistake 5: Missing Request-Response Correlation in Dynamic Batchers

#### The Anti-Pattern
Collecting requests in a shared list without associating each request with its corresponding response promise:
```python
# BROKEN: Race condition corrupts client responses
batch_requests = []

async def submit(req):
    batch_requests.append(req)
    # How does the caller know WHICH output index belongs to this request?
```

#### Why It Breaks Production
1. When multiple concurrent clients submit requests, their arrival order in the queue must map 1:1 with the rows in the batched feature matrix.
2. If request arrays are misaligned, client A receives client B's fraud prediction! In fintech or healthcare applications, this results in catastrophic data leakage and regulatory compliance violations.

#### Production Solution
Associate each enqueued request with an individual `asyncio.Future`:
```python
# PRODUCTION GRADE: 1:1 Future correlation
@dataclass
class QueueItem:
    request: PredictionRequest
    future: asyncio.Future[float]

# In worker:
for i, item in enumerate(batch):
    item.future.set_result(probabilities[i])
```

---

### Mistake 6: Unvalidated Request Payloads Triggering Silent Schema Corruption

#### The Anti-Pattern
Accepting raw dictionaries or unvalidated JSON bodies:
```python
# BROKEN: Missing validation
@app.post("/predict")
async def predict(data: dict):
    # If client passes "income": -50000 or "debt_ratio": "high", code crashes or yields garbage!
    return model.predict([[data["income"], data["debt"]]])
```

#### Why It Breaks Production
1. Missing or malformed fields cause `KeyError`, while negative values or text in numeric columns cause downstream NumPy array conversion failures (`ValueError`).
2. If default imputers are present in the pipeline, extreme or missing values may be silently imputed without warning, leading to silent prediction failures.

#### Production Solution
Enforce strict Pydantic V2 schemas with field-level bounds:
```python
# PRODUCTION GRADE: Strict Pydantic V2 validation
class PredictionRequest(BaseModel):
    income: float = Field(..., gt=0.0)
    debt_ratio: float = Field(..., ge=0.0, le=1.0)
```

---

### Mistake 7: Inadequate Health & Readiness Probes Routing Traffic to Cold Pods

#### The Anti-Pattern
Combining liveness and readiness into a single dummy endpoint:
```python
# BROKEN: Pod reports ready before model weights are loaded into memory!
@app.get("/health")
def health(): return {"status": "ok"}
```

#### Why It Breaks Production
1. Kubernetes routes live ingress traffic to a newly created pod as soon as its readiness probe returns HTTP 200.
2. If the model takes 5 seconds to load from disk or S3, incoming user requests hit uninitialized state, failing with HTTP 500 errors.

#### Production Solution
Decouple `/healthz` (liveness) from `/ready` (readiness):
```python
# PRODUCTION GRADE: Separate liveness and readiness
@app.get("/healthz")
def liveness(): return {"status": "alive"}

@app.get("/ready")
def readiness(request: Request):
    if not getattr(request.app.state, "is_ready", False):
        raise HTTPException(status_code=503, detail="Model initializing")
    return {"status": "ready"}
```
