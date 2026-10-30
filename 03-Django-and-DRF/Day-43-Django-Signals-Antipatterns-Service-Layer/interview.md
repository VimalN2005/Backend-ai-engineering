# Day 43: Senior Backend Engineer Interview Q&A — Django Signals vs Service Layer

This guide provides deep technical responses to senior and staff backend engineering interview questions regarding Django signal internals, transaction boundary failures, the Service Layer pattern, and reliable event-driven architectures.

---

### Question 1: Why do Django signals fail during transaction rollbacks, and how does `transaction.on_commit()` resolve this failure mode?

#### Answer:
In Django, model signals such as `post_save` and `post_delete` execute immediately after the SQL `INSERT`, `UPDATE`, or `DELETE` statement has been submitted across the database connection. Crucially, this execution happens **inside the active uncommitted transaction block**, not when the transaction commits.

#### The Rollback Vulnerability:
If a signal handler performs non-transactional side-effects—such as invoking a third-party payment gateway (Stripe), sending an email (SendGrid), or pushing a task to Celery—and a subsequent statement within that same `transaction.atomic()` block fails or raises an integrity error:
1. The database issues an SQL `ROLLBACK`, discarding all table changes.
2. The external side effect has already completed and **cannot be rolled back**.
3. The system is left in a corrupted state: a customer was billed or an email was dispatched for a database row that was never committed.

#### The `transaction.on_commit()` Mechanism:
`transaction.on_commit(callback)` registers a callback function with Django's transaction manager:
- Django tracks the nesting depth of `atomic()` blocks.
- The callbacks are held in memory on the active database connection.
- If the outermost transaction block commits successfully, Django executes all registered callbacks sequentially in the order they were registered.
- If the transaction rolls back at any point, Django **completely purges and discards** all registered callbacks.
- Additionally, `transaction.on_commit()` prevents race conditions where an asynchronous Celery worker attempts to query a database row before the HTTP thread's transaction has committed (`Model.DoesNotExist` errors).

---

### Question 2: Why do `bulk_create()` and `QuerySet.update()` bypass Django model signals, and what are the architectural consequences?

#### Answer:
Django compiles set-based bulk operations (`bulk_create()`, `bulk_update()`, `QuerySet.update()`, and `QuerySet.delete()`) directly into single set-based SQL queries (`INSERT INTO ... VALUES (...)`, `UPDATE ... WHERE ...`).

#### Why Django Bypasses Signals:
To fire `post_save` or `pre_save` signals on bulk operations, Django would be forced to:
1. Execute a `SELECT` query to instantiate Python model instances for every matching row.
2. Loop through every instance in Python memory, executing individual `pre_save` signal handlers.
3. Issue individual SQL `UPDATE` queries row by row.
4. Execute `post_save` signal handlers in Python.

This would convert an $O(1)$ set-based SQL operation into an $O(N)$ memory and network round-trip bottleneck. Django deliberately avoids this to preserve database performance.

#### The Architectural Consequences:
1. **Broken Audit Logging**: If an engineering team relies on `post_save` signals to record audit logs or change histories, any developer or automated task utilizing `bulk_create()` or `update()` will bypass the audit trail completely.
2. **Stale Caching & Search Indexes**: Signals that invalidate Redis keys or re-index documents in Elasticsearch fail silently during bulk updates, leaving caches permanently stale.
3. **Core Invariant**: **Business invariants and audit requirements must never reside in Django signals.** They belong in explicit methods within the Service Layer or inside database-level triggers and constraints.

---

### Question 3: When should an engineering team strictly choose a Service Layer over "Fat Models" and Django signals?

#### Answer:
The "Fat Model, Thin Controller" paradigm is effective for simple CRUD applications where business operations map 1:1 with single database tables. However, as an application scales to enterprise complexity, Fat Models and Signals break down under three conditions:

#### 1. Multi-Model Aggregates & Cross-Domain Orchestration:
When a business operation touches multiple domain models (e.g., placing an order requires verifying warehouse inventory, creating an order, updating customer loyalty points, and generating an invoice), placing this logic in `Order.save()` or cascading `post_save` signals violates the Single Responsibility Principle and creates hidden, tightly coupled dependencies.

#### 2. Non-Database Side Effects & External Services:
Models should represent domain state and persistence constraints. They should never know about HTTP clients, payment gateways, message brokers (Kafka/RabbitMQ), or email providers. The Service Layer encapsulates these third-party dependencies cleanly.

#### 3. Different Execution Contexts:
A business operation (e.g., `cancel_subscription`) may be triggered by:
- A user clicking a button in a React UI (DRF ViewSet).
- A webhook from Stripe (Webhook View).
- A nightly cron job (Celery task).
- An admin CLI script (Management Command).

Instead of duplicating transaction boundaries, validations, and notifications across these 4 presentation layers or hiding them in signals, all 4 controllers call a single service function: `SubscriptionService.cancel_subscription(subscription_id, reason, actor)`.

---

### Question 4: Explain how the Transactional Outbox Pattern prevents dual-write failures when publishing events from Django to Kafka or RabbitMQ.

#### Answer:
In a microservices or event-driven architecture, updating a database and publishing an event to a message broker constitutes a **Dual-Write Problem**:
- If you write to the database first and publish to Kafka in a signal or view, Kafka may be unavailable, losing the event.
- If you publish to Kafka inside `post_save` and the database transaction rolls back, Kafka receives a phantom event.
- Distributed two-phase commit (2PC / XA transactions) across SQL and Kafka is impractical due to high latency, lack of broker support, and coordinator locking.

#### The Transactional Outbox Pattern:
1. An `Outbox` table is created in the relational database:
   ```sql
   CREATE TABLE outbox_event (
       id UUID PRIMARY KEY,
       aggregate_type VARCHAR(64),
       aggregate_id BIGINT,
       event_type VARCHAR(64),
       payload JSONB,
       status VARCHAR(20) DEFAULT 'PENDING',
       created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   );
   ```
2. When the Service Layer performs a business mutation, it inserts the domain entity AND writes the event to the `outbox_event` table **within the exact same database transaction (`transaction.atomic()`)**:
   ```python
   with transaction.atomic():
       order = Order.objects.create(...)
       OutboxEvent.objects.create(
           aggregate_type="ORDER",
           aggregate_id=order.id,
           event_type="ORDER_CREATED",
           payload={"order_id": order.id, "total": str(order.total)}
       )
   ```
3. Because both writes share a single ACID transaction, it is physically impossible for the order to exist without the outbox event.
4. An asynchronous background process (Change Data Capture via Debezium reading the PostgreSQL WAL, or a high-frequency polling worker) reads `PENDING` outbox records, publishes them to Kafka, and marks them `PUBLISHED`.
5. This architecture provides **guaranteed at-least-once event delivery** with zero dual-write vulnerabilities.

---

### Question 5: What is the internal execution model of Django signals, and what are the performance and reliability implications under high traffic?

#### Answer:
Django signals (`django.dispatch.Signal`) operate under a **synchronous, in-process, single-threaded execution model**.

#### Internal Mechanics:
1. When `Signal.send(sender, **kwargs)` is called, Django retrieves a list of receiver callables connected to that signal.
2. It evaluates weak references, purges dead receivers, filters by `sender` type, and executes each receiver function sequentially in the same thread.
3. Django signals have **no internal queuing, no concurrency, and no asynchronous background processing**.

#### Performance Implications Under High Traffic:
1. **Thread Blocking & Latency Inflation**: Any blocking operation in a receiver (HTTP call, external DNS lookup, image processing, complex SQL query) blocks the Gunicorn worker thread. If a receiver takes 500 ms, the API response latency increases by 500 ms, and concurrent worker capacity collapses.
2. **Database Connection Starvation**: While a synchronous signal receiver waits for an external network response, the database transaction remains open. This locks rows in PostgreSQL and exhausts the database connection pool (`max_connections`), leading to cascading timeouts across the cluster.
3. **Cascading Failure Blast Radius**: In `Signal.send()`, if receiver 2 of 5 raises an unhandled exception, receivers 3, 4, and 5 are never executed. The entire HTTP request terminates with an uncaught 500 error.
4. **Conclusion**: Signals must be strictly restricted to lightweight, non-blocking, in-process notifications for third-party pluggable apps. Core business operations must use an explicit Service Layer combined with asynchronous task queues.
