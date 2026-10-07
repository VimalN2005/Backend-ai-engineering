# Day 13: Common Asyncio Antipatterns

Avoid these 7 production bugs when implementing asynchronous services, FastAPI endpoints, and streaming AI gateways.

---

### 1. Calling Synchronous Blocking Calls Inside Coroutines
- **Root Cause**: Using `time.sleep()`, synchronous HTTP clients (`requests`), or blocking database drivers (`psycopg2`) inside an `async def` function.
- **Consequence**: The event loop runs on a **single OS thread**. When that thread blocks on a synchronous system call, the entire event loop freezes. All other 10,000 concurrent user requests stall completely.

```python
# ❌ FATAL DISASTER: Freezes entire backend server!
import time
import requests

async def get_user_profile(user_id: str):
    time.sleep(1.0)                 # Blocks entire event loop!
    return requests.get(f"/users/{user_id}").json()

# ✅ PRODUCTION PATTERN: Use non-blocking async primitives or asyncio.to_thread
import asyncio
import httpx

async def get_user_profile(user_id: str):
    await asyncio.sleep(1.0)        # Non-blocking: yields to other tasks
    async with httpx.AsyncClient() as client:
        res = await client.get(f"/users/{user_id}")
        return res.json()
```

---

### 2. The "Disappearing Task" Garbage Collection Bug
- **Root Cause**: Firing and forgetting a background task with `asyncio.create_task(background_job())` without holding a variable reference.
- **Consequence**: The event loop maintains only **weak references** to active tasks. If CPython runs garbage collection while the task is suspended at an `await` statement, the task object is destroyed mid-execution without warning or error!

```python
# ❌ SILENT BUG: Background audit task may vanish mid-execution
def log_audit_event(event_data):
    asyncio.create_task(persist_to_elasticsearch(event_data))  # Weak ref destroyed by GC!

# ✅ PRODUCTION PATTERN: Maintain a strong reference in a set
ACTIVE_BACKGROUND_TASKS = set()

def log_audit_event(event_data):
    task = asyncio.create_task(persist_to_elasticsearch(event_data))
    ACTIVE_BACKGROUND_TASKS.add(task)
    task.add_done_callback(ACTIVE_BACKGROUND_TASKS.discard)
```

---

### 3. Swallowing `asyncio.CancelledError`
- **Root Cause**: Using a blanket `except Exception:` block that catches `asyncio.CancelledError` (or catching `BaseException`) without re-raising.
- **Consequence**: The task ignores cancellation signals during client disconnects, timeouts, or graceful application shutdown (`SIGTERM`), leaking sockets and preventing the server from stopping.

```python
# ❌ WRONG: Swallows cancellation, preventing clean shutdown
async def worker():
    try:
        while True:
            await asyncio.sleep(1)
    except BaseException:
        pass  # Never exits on cancellation!

# ✅ PRODUCTION PATTERN: Always re-raise CancelledError
async def worker():
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        # Perform cleanup
        logger.info("Cleaning up resources...")
        raise  # Must propagate CancelledError!
```

---

### 4. Forgetting to Await a Coroutine
- **Root Cause**: Calling `async_func()` without prepending `await`.
- **Consequence**: Calling an async function only instantiates an idle coroutine object; the code inside the function never runs. CPython emits `RuntimeWarning: coroutine 'async_func' was never awaited`.

```python
# ❌ CODE NEVER EXECUTES:
async def save_order(order_id):
    db.insert(order_id)

async def checkout():
    save_order(101)  # Never awaited! Warning logged, order lost.

# ✅ PRODUCTION PATTERN: Always await coroutines
async def checkout():
    await save_order(101)
```

---

### 5. Race Conditions Across `await` Points (Lack of `asyncio.Lock`)
- **Root Cause**: Assuming that because Asyncio is single-threaded, shared mutable data cannot suffer from race conditions.
- **Consequence**: Whenever an `await` expression is reached, control yields back to the event loop. Other tasks can mutate shared in-memory dictionaries or counters before the original task resumes, causing data inconsistencies.

```python
# ❌ RACE CONDITION: State can change between await points
async def withdraw(account_id, amount):
    balance = await get_balance(account_id)  # Yields control!
    if balance >= amount:
        await asyncio.sleep(0.01)            # Another task runs and withdraws here!
        await set_balance(account_id, balance - amount)

# ✅ PRODUCTION PATTERN: Synchronize with asyncio.Lock
account_lock = asyncio.Lock()

async def withdraw(account_id, amount):
    async with account_lock:
        balance = await get_balance(account_id)
        if balance >= amount:
            await set_balance(account_id, balance - amount)
```

---

### 6. Using Fragile `asyncio.gather` Instead of `asyncio.TaskGroup`
- **Root Cause**: Relying on `await asyncio.gather(t1, t2)` without handling partial failures.
- **Consequence**: If `t1` raises an unhandled exception, `gather` raises immediately, but `t2` remains running in the background as an unmanaged orphaned zombie task, leaking network connections.

```python
# ❌ LEAKS ORPHANED TASKS ON PARTIAL FAILURE
results = await asyncio.gather(fetch_a(), fetch_b())  # If a fails, b leaks!

# ✅ PRODUCTION PATTERN: Structured Concurrency with TaskGroup (Python 3.11+)
async with asyncio.TaskGroup() as tg:
    t1 = tg.create_task(fetch_a())
    t2 = tg.create_task(fetch_b())
# If t1 fails, t2 is automatically cancelled. No orphaned tasks!
```

---

### 7. Creating New Event Loops Inside Threads Without Managing Lifecycle
- **Root Cause**: Calling `asyncio.new_event_loop()` inside a thread worker without setting it as current or closing it.
- **Consequence**: Causes event loop leakage, unclosed transports, and `RuntimeError: There is no current event loop in thread`.

```python
# ❌ MANUAL LOOP MESS:
def thread_worker():
    loop = asyncio.new_event_loop()
    loop.run_until_complete(my_coro())
    # Forgot loop.close()!

# ✅ PRODUCTION PATTERN: Use asyncio.run() which creates, runs, and closes cleanly
def thread_worker():
    asyncio.run(my_coro())
```
