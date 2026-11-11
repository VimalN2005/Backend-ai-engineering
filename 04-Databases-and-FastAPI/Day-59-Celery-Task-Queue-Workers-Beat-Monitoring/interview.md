# Staff & Senior Backend Engineer Interview Guide: Celery & Distributed Task Queues

Five rigorous architectural interview questions and production-grade answers focusing on Celery internals, broker mechanics, consensus, and system design.

---

### Question 1: Explain the mechanical difference between `acks_early` and `acks_late`. What catastrophic edge cases arise if `acks_late` is enabled on non-idempotent tasks?

#### Candidate Answer
By default, Celery operates under **early acknowledgment** (`task_acks_late = False`). When a worker picks up a task message from the broker (RabbitMQ or Redis), it sends an immediate `Basic.Ack` to the broker *before* executing the Python task body. 
- **The Early ACK Trade-off**: If the worker child process gets killed midway through execution—by the Linux Out-Of-Memory (OOM) killer, an unhandled C-extension segmentation fault, or a host power failure—the task message is already gone from the broker. The task vanishes permanently, creating silent data loss.
- **The Late ACK Mechanism (`task_acks_late = True`)**: The worker delays sending `Basic.Ack` until the task function cleanly returns or explicitly fails. If the worker process drops dead or loses connection, the broker detects the closed TCP socket (or RabbitMQ consumer channel drop) and automatically redelivers the unacknowledged message to another healthy worker node (`At-Least-Once Delivery`).

#### The Catastrophic Failure Mode on Non-Idempotent Tasks
If a task performs external side effects (e.g., charging a customer via Stripe, sending an SMS, or debiting an account) and is configured with `task_acks_late = True`:
1. Worker 1 receives `charge_credit_card(user_id=42, amount=100)`.
2. Worker 1 calls Stripe API; Stripe successfully charges \$100.
3. Before Worker 1 can transmit the ACK back to RabbitMQ, an OOM spike or network blip kills Worker 1.
4. RabbitMQ notices the channel dropped and redelivers the unacknowledged task to Worker 2.
5. Worker 2 executes `charge_credit_card(user_id=42, amount=100)` again, double-charging the customer!

#### Architectural Rule
`task_acks_late = True` and `task_reject_on_worker_lost = True` should always be paired with **distributed idempotency keys** (e.g., using Redis `SET lock_key token NX EX` or database unique transaction constraints) so that redelivered tasks detect prior execution and no-op safely.

---

### Question 2: Compare RabbitMQ vs. Redis as a Celery Message Broker under high throughput and network partitions. When would you strictly choose one over the other?

#### Candidate Answer

```
+───────────────────────────+────────────────────────────────────+────────────────────────────────────+
| Dimension                 | RabbitMQ                           | Redis                              |
+───────────────────────────+────────────────────────────────────+────────────────────────────────────+
| Protocol & Routing        | Native AMQP 0-9-1 with Direct,     | Emulated queues via LPUSH / BRPOP  |
|                           | Topic, Fanout, and Headers exchanges| or Redis Streams.                  |
| Reliability & Persistence | Erlang Mnesia + durable disk queues| In-memory key-value store; RDB     |
|                           | guaranteed even under RAM pressure.| snapshots + AOF log.               |
| Consumer Acknowledgment   | Protocol-level native ACK/NACK     | Visibility timeout simulation via  |
|                           | per channel; instant redelivery.   | unacknowledged queue scanning.     |
| Memory Exhaustion Profile | Pages unconsumed messages to disk; | Reaches maxmemory: either crashes, |
|                           | backpressures producers safely.    | drops keys (eviction), or throws.  |
| Priority Queues           | Native `x-max-priority` byte sort  | Emulated using multiple lists      |
+───────────────────────────+────────────────────────────────────+────────────────────────────────────+
```

#### The Redis `visibility_timeout` Trap
Redis lacks AMQP channel-level acknowledgments. Celery emulates ACK semantics by moving messages into an unacknowledged queue with a timestamp. If a task executes longer than `visibility_timeout` (default: 3600 seconds / 1 hour), Celery's client library assumes the worker died and re-queues the message, leading to **concurrent duplicate execution** of an actively running task!

#### Production Decision Matrix
- **Choose RabbitMQ** for enterprise core banking, e-commerce checkout, mission-critical workflows with strict delivery SLAs, complex topic routing, and multi-tenant task distribution.
- **Choose Redis** for high-velocity, lightweight operations (e.g., email notifications, thumbnail resizing, analytics caching) where operational simplicity and ultra-low dispatch latency (< 200 µs) outweigh strict protocol durability guarantees.

---

### Question 3: What is the "Worker Prefork Pool Deadlock" problem caused by synchronous `.get()` calls, and how do you architect canvas workflows to eliminate it?

#### Candidate Answer
In Celery's default prefork execution pool, the worker process spawns $N$ child worker processes (where $N = \text{concurrency}$). 

If a task function dispatches a sub-task and immediately calls `.get()` to wait for its result:
```python
@app.task
def master_task(record_id):
    # BLOCKS the worker child process!
    res = sub_task.delay(record_id).get()
    return res * 2
```

If $N$ requests trigger `master_task` simultaneously:
1. All $N$ child worker processes in the pool are assigned a `master_task`.
2. Each of the $N$ workers dispatches a `sub_task` into the broker and blocks waiting on `.get()`.
3. The $N$ `sub_task` messages sit in the broker queue waiting for an available worker process.
4. **Deadlock**: None of the `sub_task` messages can ever be picked up because 100% of the worker slots are blocked waiting for those exact `sub_task` messages to finish! The entire worker cluster freezes permanently.

#### Canvas Remediation
Never block inside a task. Instead of calling `.get()`, compose asynchronous Direct Acyclic Graphs (DAGs) using **Celery Canvas**:
- **Sequential Pipeline**: Use `chain(sub_task.s(record_id), finalize.s())`.
- **Parallel Fan-Out / Fan-In**: Use `chord(group(worker.s(i) for i in items))(reducer.s())`.
The master process exits immediately after enqueuing the workflow; the broker and result backend coordinate execution without tying up worker processes.

---

### Question 4: How do you prevent the classic "Post-Commit Race Condition" where a Celery task executes before the PostgreSQL database transaction commits?

#### Candidate Answer
This is one of the most frequent and elusive concurrency bugs in web architectures.

#### The Race Mechanism
1. A FastAPI or Django web endpoint receives `POST /orders`.
2. The endpoint begins a database transaction and executes `INSERT INTO orders (id, amount) VALUES (501, 100)`.
3. The endpoint calls `process_order.delay(order_id=501)`.
4. The message arrives in the Redis/RabbitMQ broker in **1 to 2 milliseconds**.
5. An idle Celery worker instantly dequeues the message and executes `SELECT * FROM orders WHERE id=501`.
6. **The Crash**: Because the web server transaction has not yet reached `COMMIT`, PostgreSQL's default `READ COMMITTED` transaction isolation hides the uncommitted row from the worker's separate database connection! The query returns `None`, throwing an `ObjectDoesNotExist` or `RecordNotFound` exception.
7. A few milliseconds later, the web server's transaction finally commits, leaving the order stuck in an orphaned, unprocessed state.

#### Production Solutions
1. **Framework-Level Commit Hooks (Django)**:
   ```python
   from django.db import transaction
   transaction.on_commit(lambda: process_order.delay(order_id=501))
   ```
2. **SQLAlchemy / FastAPI Pattern**:
   Always execute `await session.commit()` before calling `apply_async`:
   ```python
   session.add(new_order)
   await session.commit()  # Guaranteed written to database log
   process_order.delay(order_id=new_order.id)
   ```
3. **Worker-Side Retry Defense**:
   Configure tasks to automatically retry on missing records with short exponential backoff:
   ```python
   @app.task(bind=True, max_retries=3, default_retry_delay=1)
   def process_order(self, order_id: int):
       order = db.query(Order).get(order_id)
       if not order:
           raise self.retry(exc=RecordNotFound(f"Order {order_id} not visible yet."))
   ```

---

### Question 5: How does Celery's `chord` primitive coordinate parallel worker tasks into a single callback, and what role does the Result Backend play in chord unlocking?

#### Candidate Answer
A **Chord** consists of two components:
1. A **Header**: A `group` of tasks executed in parallel.
2. A **Body**: A callback task that receives a list of all header results once every header task completes.

$$\text{chord}\Big(\text{group}\big(T_1, T_2, \dots, T_k\big)\Big) \longrightarrow \text{Callback}\big([R_1, R_2, \dots, R_k]\big)$$

#### The Role of the Result Backend
Unlike simple tasks, a `chord` **strictly requires a shared Result Backend** (Redis, Memcached, or Database). 

When using Redis as the backend:
1. When the chord is initialized, Celery writes a metadata key in Redis containing a counter set to $k$ (the number of header tasks) and an ID reference to the callback task:
   `Key: celery-task-meta-chord-<chord_id>`
2. As each worker finishes an individual header task ($T_i$), it writes its result to `celery-task-meta-<task_id>` and atomically executes a Redis Lua script or `DECR` command against the chord counter key.
3. The worker that executes the decrement that drops the counter to **exactly 0** is designated as the **unlocker**.
4. That finishing worker gathers all $k$ return values from Redis and dispatches the callback task into the broker queue:
   `callback.apply_async(args=[[R_1, R_2, ..., R_k]])`

#### Failure Dynamics & Chord Stalls
If any single header task crashes silently (e.g. worker OOM killed with `acks_early`), the counter never reaches 0. Without proper monitoring, the chord callback remains stalled forever in Redis. To safeguard chords in production:
- Ensure `task_acks_late = True`.
- Configure `chord_unlocker` fallback polling.
- Attach errbacks via `link_error=handle_chord_failure.s()` so exceptions trigger alerting and cleanup.
