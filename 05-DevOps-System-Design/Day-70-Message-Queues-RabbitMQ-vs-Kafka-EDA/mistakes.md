# Day 70: Common Mistakes & Antipatterns in Message Queues & Event-Driven Architecture

In distributed architectures, message brokers are the nervous system connecting decoupled services. However, conflating broker paradigms (e.g., treating a queue like a database or treating Kafka like a task queue) causes catastrophic production failures.

Below are **7 critical distributed messaging antipatterns**, complete with code examples, root cause analyses, and senior production remediations.

---

## 1. The Dual-Write Antipattern: Updating Database & Broker Independently

### The Mistake
Writing to the primary database and publishing an event to a message broker in an uncoordinated, non-atomic sequence:

```python
# BROKEN ANTIPATTERN: Dual-Write Vulnerability
def create_order(db_session, broker, order_data):
    # Step 1: Save to Postgres
    order = db_session.add(Order(**order_data))
    db_session.commit()

    # Step 2: Publish event to RabbitMQ / Kafka
    # DANGER: If network blips, broker crashes, or app restarts here,
    # the event is NEVER published. Inconsistent distributed state!
    broker.publish(exchange="orders", routing_key="created", body=order.to_dict())
```

### Why It Fails
Distributed transactions (2PC / XA) are slow and fragile across heterogeneous systems (e.g., PostgreSQL + RabbitMQ/Kafka). If the application server crashes between `db_session.commit()` and `broker.publish()`, downstream services (Payment, Shipping, Inventory) will never know the order was created. Conversely, if you publish *before* committing the DB, an uncommitted DB rollback leaves phantom messages processed by downstream consumers.

### Senior Engineering Fix: The Transactional Outbox Pattern
Write the business entity and an outbox event in the **same local ACID database transaction**. A reliable Change Data Capture (CDC) worker (or Debezium tailing the write-ahead log) asynchronously relays the event to the broker with at-least-once delivery:

```sql
-- Production Schema for Transactional Outbox
BEGIN TRANSACTION;
  INSERT INTO orders (id, customer_id, amount, status) 
  VALUES ('ord_101', 'cust_42', 250.00, 'PENDING');

  INSERT INTO outbox_events (event_id, aggregate_type, aggregate_id, payload, status)
  VALUES ('evt_999', 'Order', 'ord_101', '{"order_id": "ord_101", "amount": 250.0}', 'PENDING');
COMMIT;
```

---

## 2. Unbounded Consumer Prefetch (`prefetch_count=0`) Causing OOM & Starvation

### The Mistake
Consuming from RabbitMQ with the default unthrottled prefetch (`prefetch_count=0`):

```python
# BROKEN ANTIPATTERN: Default unbounded prefetch
channel.basic_consume(queue="heavy_jobs", on_message_callback=process_job)
# When 5,000 jobs arrive, RabbitMQ eagerly dumps all 5,000 into Worker 1's memory!
```

### Why It Fails
In AMQP 0-9-1, a prefetch of `0` means "unlimited". RabbitMQ immediately pushes every available message down the socket buffer into the consumer's client process:
1. **OOM Crashes**: If processing each message consumes memory (e.g., image rendering, PDF generation, or large JSON parsing), the consumer process exhausts RAM and gets killed by the OS OOM killer.
2. **Worker Starvation**: A single fast connection swallows thousands of tasks, leaving 10 other idle worker instances completely starved of work.

### Senior Engineering Fix: Set Explicit `basic_qos`
Always enforce a sensible prefetch window (typically `1` for heavy compute jobs, or `10–50` for lightweight I/O-bound microservices):

```python
# PRODUCTION FIX: Strict Flow-Control Prefetch
channel.basic_qos(prefetch_count=1)  # Only 1 unacknowledged message allowed inflight
channel.basic_consume(queue="heavy_jobs", on_message_callback=process_job)
```

---

## 3. Poison Pill Infinite Nack Loops (`basic_nack(requeue=True)`)

### The Mistake
Requeueing malformed or permanently unprocessable messages unconditionally on failure:

```python
# BROKEN ANTIPATTERN: Infinite Poison-Pill Retry Loop
def on_message(ch, method, properties, body):
    try:
        payload = json.loads(body)
        process_order(payload)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        # DANGER: Requeuing a permanent validation or schema failure
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
```

### Why It Fails
If `body` has corrupted JSON or a schema violation, `json.loads` or `process_order` will fail 100% of the time. Requeueing puts the message right back onto the head of the queue. The worker immediately consumes it again, fails again, and re-queues it in a tight infinite loop. CPU spikes to 100%, disk I/O saturates with log spam, and valid messages behind the poison pill are completely blocked.

### Senior Engineering Fix: Dead-Letter Exchange (DLX) + Retry Limits
Track delivery attempts using message headers or let RabbitMQ route directly to a DLQ on `requeue=False`:

```python
# PRODUCTION FIX: Drop to DLQ after Max Attempts
MAX_RETRIES = 3

def on_message(ch, method, properties, body):
    try:
        payload = json.loads(body)
        process_order(payload)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        headers = properties.headers or {}
        retry_count = headers.get("x-retry-count", 0) + 1
        
        if retry_count <= MAX_RETRIES:
            # Republish to delay/retry queue with incremented counter
            headers["x-retry-count"] = retry_count
            ch.basic_publish(
                exchange="retry_exchange",
                routing_key="orders.retry",
                body=body,
                properties=pika.BasicProperties(headers=headers)
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # Drop from primary queue into DLQ (requeue=False triggers DLX routing)
            logger.error("Max retries exceeded for message %s. Moving to DLQ.", method.delivery_tag)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
```

---

## 4. Expecting Global FIFO Ordering with Competing Consumers in RabbitMQ

### The Mistake
Assuming a RabbitMQ queue guarantees global chronological processing when multiple consumer workers are subscribed to it:

```
Producer -> [ Queue: msg1, msg2, msg3 ]
               ├──> Worker A receives msg1 (takes 500ms)
               └──> Worker B receives msg2 (takes 50ms)
Result: Worker B finishes msg2 BEFORE Worker A finishes msg1!
```

### Why It Fails
RabbitMQ preserves FIFO ordering *inside the queue data structure*. However, once messages are handed off to competing concurrent consumer threads or microservices, network jitter and variable task processing times shatter order preservation. If `msg1` is `OrderCreated` and `msg2` is `OrderCancelled`, `OrderCancelled` may execute first!

### Senior Engineering Fix: Partitioned Ordering (Kafka) or Single-Active Consumer
1. **In RabbitMQ**: Use **Single Active Consumer** (`x-single-active-consumer: true`) or partition messages across dedicated queues by customer ID.
2. **In Kafka**: Use **Partition Keying**. Records with the same key (`customer_id`) always route to the exact same partition, where a single designated consumer in the group guarantees strict serial ordering.

---

## 5. Misconfigured Kafka Keys Causing Severe Partition Skew

### The Mistake
Producing to Kafka with low-cardinality keys, timestamps, or null keys when strict key-based ordering is required:

```python
# BROKEN ANTIPATTERN: Low cardinality or static key
producer.send(topic="user_clicks", key="global_event", value=payload)
```

### Why It Fails
Kafka determines the destination partition using `murmur2(key) % num_partitions`. If the key is static (e.g., `"global_event"` or a single tenant ID that generates 90% of your traffic), **all messages land in a single partition**.
- Partition 0 reaches 100% disk and CPU capacity.
- Partitions 1, 2, and 3 sit completely idle.
- Scaling consumer group instances has zero effect because only one consumer thread can process Partition 0.

### Senior Engineering Fix: High-Entropy Natural Entity Keys
Use high-cardinality entity identifiers (`user_id`, `order_id`, `device_uuid`):

```python
# PRODUCTION FIX: High-entropy entity key
partition_key = str(payload["user_id"]).encode("utf-8")
producer.send(topic="user_clicks", key=partition_key, value=payload)
```
If a single tenant is genuinely massive ("celebrity problem"), use salted keys: `f"{tenant_id}_{random.randint(0, 3)}"`.

---

## 6. Treating Message Queues as Long-Term Permanent Storage

### The Mistake
Leaving millions of unconsumed messages in RabbitMQ for days or weeks as a "log" or "backup".

### Why It Fails
RabbitMQ stores messages in Erlang processes and RAM-backed indexes. When unacknowledged or unconsumed queue depths swell:
1. RabbitMQ pages queue pages to disk, causing catastrophic garbage collection pauses.
2. Memory alarms trigger (`vm_memory_high_watermark`), blocking all incoming publisher connections across the entire cluster.
3. Message queues are transient flow-control buffers, **not** persistent analytical databases.

### Senior Engineering Fix: Kafka or Object Storage for Long-Term Retention
- If data must be retained for days, months, or replayed indefinitely, use **Apache Kafka** (which stores data sequentially on disk using optimized segmented commit logs and zero-copy `sendfile(2)`).
- If messages in RabbitMQ must not accumulate unboundedly, set `x-max-length` or `x-message-ttl` with a Dead Letter Queue.

---

## 7. Prematurely Adopting Apache Kafka for Simple Asynchronous Task Queues

### The Mistake
Deploying a multi-node Apache Kafka cluster (with ZooKeeper/KRaft, schema registries, and partition tuning) just to send transactional emails or execute asynchronous background jobs.

### Why It Fails
Kafka is an immutable, distributed commit log designed for streaming high-throughput event series (100k+ events/sec, time-series, CDC). It does **not** support:
- Individual message routing by arbitrary wildcards (no Topic/Fanout/Headers exchanges).
- Individual message acknowledgments and selective retries (you can only commit monotonic offsets per partition; nacking message 3 while acking message 4 is impossible).
- Delayed message execution or native TTL per message.

### Senior Engineering Fix: Choose the Right Tool for the Domain
| Requirement | Recommended Tool |
| :--- | :--- |
| Asynchronous task processing, worker queues, delayed retries | **RabbitMQ / Celery / Redis Streams** |
| High-throughput event streaming, CDC, metric ingestion, replayability | **Apache Kafka / Redpanda / AWS Kinesis** |
| Complex routing topology (Fanout to 10 microservices, Topic wildcards) | **RabbitMQ (AMQP 0-9-1)** |
| Replaying 30 days of historical data through a new analytics engine | **Apache Kafka** |

---

## Summary Checklist for Messaging Excellence
1. **Never dual-write without Outbox**: Always combine DB writes and event generation inside the same ACID boundary.
2. **Always set prefetch QoS**: Never leave prefetch unbounded (`prefetch_count=0`) in worker queues.
3. **Always bound retries**: Forward unprocessable messages to a DLQ; never nack with `requeue=True` unconditionally.
4. **Partition by natural high-entropy keys**: Avoid partition skew in Kafka.
5. **Match broker to workload**: Use RabbitMQ for routing & task distribution; use Kafka for event streams & replayable logs.
