# Day 59 Rapid Revision: Celery & Distributed Task Queues

A concise 5-minute reference cheat sheet covering Celery 5.x architecture, configuration, canvas patterns, and production CLI commands.

---

## 1. Architectural Mental Model

```
[ Web Client / API ] ──( apply_async / delay )──▶ [ Message Broker: RabbitMQ / Redis ]
                                                             │
                                                      BRPOP / Basic.Consume
                                                             │
                                                             ▼
                                                    [ Celery Worker Pool ]
                                                     ├── Prefork Process 1
                                                     ├── Prefork Process 2
                                                     └── Prefork Process N
                                                             │
                                                    Write Status & Return
                                                             │
                                                             ▼
                                                    [ Result Backend: Redis ]
                                                    celery-task-meta-<id>
```

---

## 2. Production Settings Boilerplate (The Essential 8)

```python
from celery import Celery

app = Celery("app_name", broker="redis://localhost:6379/0", backend="redis://localhost:6379/1")

app.conf.update(
    # 1. Security: Disallow arbitrary code execution via pickle
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # 2. Durability: Acknowledge only AFTER clean execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # 3. Fair Dispatch: Prevent queue starvation across mixed task durations
    worker_prefetch_multiplier=1,

    # 4. State Observability: Emit STARTED event to Flower / Prometheus
    task_track_started=True,

    # 5. Resource Protection: Kill hung workers and runaway queries
    task_soft_time_limit=300,  # 5 minutes -> SoftTimeLimitExceeded
    task_time_limit=360,       # 6 minutes -> Hard SIGKILL

    # 6. Result Garbage Collection: Prevent Redis RAM exhaustion
    result_expires=3600,       # 1 hour TTL on task results

    # 7. Memory Leak Prevention: Recycle child worker processes
    worker_max_tasks_per_child=1000,

    # 8. Timezone Standard
    timezone="UTC",
    enable_utc=True,
)
```

---

## 3. Celery Canvas Cheat Sheet

| Primitive | Mathematical Model | Syntax Pattern | Use Case |
|---|---|---|---|
| **Chain** | $f(g(h(x)))$ | `chain(step1.s(x), step2.s(), step3.s())` or `step1.s(x) \| step2.s() \| step3.s()` | Sequential multi-stage data pipelines (Extract $\rightarrow$ Transform $\rightarrow$ Load). |
| **Group** | $[f(x_1), f(x_2), \dots]$ | `group(task.s(i) for i in items)` | Parallel fan-out execution across independent tasks. |
| **Chord** | $g([f(x_1), f(x_2), \dots])$ | `chord(group(worker.s(i) for i in items))(reducer.s())` | Parallel map-reduce workflows (distributed auditing, multi-resolution transcoding). |
| **Signature** | Curried Task | `task.s(arg1, arg2)` | Creates a callable signature that accepts piped arguments from upstream tasks. |
| **Immutable** | Discard Input | `task.si(arg1)` | Ignores the return value of upstream tasks in a chain. |

---

## 4. Key Production CLI Commands

```bash
# 1. Launch Prefork Worker (4 CPU processes, explicit queues)
celery -A app worker -l INFO -c 4 -P prefork -Q default,high_priority -n worker1@%h

# 2. Launch I/O Worker using Gevent (High-concurrency webhook/scraping)
celery -A app worker -l INFO -c 500 -P gevent -Q webhooks -n gevent_worker@%h

# 3. Launch Celery Beat Periodic Scheduler
celery -A app beat -l INFO --schedule=/tmp/celerybeat-schedule

# 4. Launch Flower Monitoring UI on port 5555
celery -A app flower --port=5555 --basic_auth=admin:ProductionSecret2026

# 5. Production Diagnostics & Cluster Inspection
celery -A app inspect active          # List tasks currently executing
celery -A app inspect reserved        # List prefetched uncompleted tasks
celery -A app inspect stats           # Show pool processes, uptime, and system info
celery -A app inspect ping            # Verify worker cluster heartbeat

# 6. Remote Worker Control & Throttling
celery -A app control rate_limit tasks.send_email 20/m   # Throttle dynamically to 20/min
celery -A app control revoke <task_id> --terminate       # Terminate hung task with SIGKILL
celery -A app control pool_grow 2                        # Add 2 child processes dynamically
celery -A app purge                                      # Clear all queued tasks from broker
```

---

## 5. Rapid Recall Flashcards

### Flashcard 1
- **Q**: What causes the Celery "Post-Commit Race Condition" in web applications?
- **A**: Dispatched tasks arrive at the worker and query the database before the web server transaction commits, returning `None` due to `READ COMMITTED` isolation. Prevent using `transaction.on_commit()` or committing prior to `apply_async`.

### Flashcard 2
- **Q**: Why is `worker_prefetch_multiplier = 1` crucial for queues with long tasks?
- **A**: The default multiplier (4) causes workers to hoard multiple tasks in their local memory buffer. If one task takes an hour, the remaining tasks are held hostage while other worker nodes sit idle.

### Flashcard 3
- **Q**: What happens if a task calls `.get()` inside a prefork worker pool?
- **A**: It blocks a worker process slot waiting for the subtask. If concurrent requests equal the pool size, all processes block waiting for subtasks that can never be scheduled, deadlocking the cluster.

### Flashcard 4
- **Q**: Why must Celery tasks be idempotent when `task_acks_late = True` is enabled?
- **A**: If a worker crashes or drops network connection after completing work but before sending the ACK, the broker redelivers the task to another worker. Idempotency prevents duplicate side-effects (e.g. double-billing).

### Flashcard 5
- **Q**: What is the difference between `task_time_limit` and `task_soft_time_limit`?
- **A**: `task_soft_time_limit` raises a catchable `SoftTimeLimitExceeded` exception in Python for graceful cleanup. `task_time_limit` sends an uncatchable `SIGKILL` to force-kill the process.
