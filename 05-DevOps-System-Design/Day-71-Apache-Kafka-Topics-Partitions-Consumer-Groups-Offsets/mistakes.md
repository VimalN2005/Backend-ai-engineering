# Day 71: Common Mistakes & Antipatterns in Apache Kafka

Apache Kafka is an extraordinarily fast and resilient distributed streaming platform, but its architectural model differs radically from traditional relational databases and message queues. Misunderstandings around offsets, rebalances, replication, and consumer thread models cause severe production outages.

Below are **7 critical Apache Kafka antipatterns**, complete with code examples, root cause analyses, and senior production remediations.

---

## 1. Relying on `enable.auto.commit=true` in Asynchronous Consumer Pipelines

### The Mistake
Leaving `enable.auto.commit=true` (the default in many client SDKs) while processing messages asynchronously or passing records to a background worker pool:

```python
# BROKEN ANTIPATTERN: Auto-commit with async processing
consumer = KafkaConsumer(
    'orders',
    enable_auto_commit=True,             # DANGER: Periodic background commits
    auto_commit_interval_ms=5000,
    group_id='order-processor'
)

for message in consumer:
    # Offloading to worker thread or async task
    async_thread_pool.submit(process_order, message.value)
    # The next call to poll() commits the offset BEFORE the thread finishes!
```

### Why It Fails
With auto-commit, Kafka automatically commits the highest offset returned by `poll()` after `auto_commit_interval_ms` elapses.
1. **Silent Data Loss**: If `async_thread_pool` fails or the consumer node crashes while processing, Kafka has already committed the offset. Upon restarting, the consumer picks up *after* that offset, permanently dropping the unwritten records.
2. **Duplicate Bursts**: If a long-running batch takes 6 seconds and the consumer crashes before the next auto-commit, the entire previous batch is redelivered.

### Senior Engineering Fix: Explicit Manual Commits Synchronized with Work Completion
Disable auto-commit and commit offsets manually only after the business logic has successfully persisted:

```python
# PRODUCTION FIX: Manual Synchronous / Asynchronous Commit
consumer = KafkaConsumer(
    'orders',
    enable_auto_commit=False,            # Disable auto-commit
    group_id='order-processor'
)

try:
    while True:
        records_batch = consumer.poll(timeout_ms=1000, max_records=100)
        for tp, records in records_batch.items():
            for record in records:
                process_order_synchronously(record.value)
            
            # Commit exact offset after batch completes successfully
            last_offset = records[-1].offset + 1
            consumer.commit({tp: OffsetAndMetadata(last_offset, None)})
except Exception as exc:
    logger.error("Consumer fatal failure: %s", exc)
finally:
    consumer.close()
```

---

## 2. Blocking Inside `poll()` Exceeding `max.poll.interval.ms` (The "Rebalance Storm")

### The Mistake
Executing heavy CPU-bound processing, database bulk inserts, or external third-party API calls inside the main consumer event loop without tuning `max.poll.interval.ms`:

```python
# BROKEN ANTIPATTERN: Heavy blocking call inside poll loop
for message in consumer:
    # Calling an external payment gateway that occasionally takes 10–20 seconds per item
    # When processing 100 items, total loop time = 1500 seconds (25 minutes!)
    call_slow_external_api(message.value)
```

### Why It Fails
In modern Kafka, heartbeats are sent by a background thread, but Kafka monitors the application health via the time between successive `poll()` calls.
- If the time between `poll()` calls exceeds `max.poll.interval.ms` (default 300,000 ms / 5 minutes), the coordinator assumes the consumer has hung or died.
- The coordinator revokes the consumer's partitions and triggers a **Group Rebalance**.
- Meanwhile, the consumer finishes its batch and attempts to commit, only to be rejected with `CommitFailedException` (it is no longer the owner of the partition).
- Another consumer receives the same partitions, takes too long on the same backlog, gets evicted, and triggers another rebalance. The entire cluster enters a permanent **Rebalance Storm**, with zero progress made.

### Senior Engineering Fix: Cap `max.poll.records` or Tune `max.poll.interval.ms`
1. Set `max.poll.records` small enough that processing is guaranteed to complete in a fraction of `max.poll.interval.ms`.
2. Decouple polling from processing using an internal bounded work queue:

```python
# PRODUCTION FIX: Bounded batch size & timeout tuning
consumer = KafkaConsumer(
    'orders',
    max_poll_records=50,                 # Fetch only 50 records at a time
    max_poll_interval_ms=600000,         # Allow up to 10 minutes for slow downstream batches
    session_timeout_ms=45000,
    heartbeat_interval_ms=15000,
    enable_auto_commit=False
)
```

---

## 3. Using `acks=1` Instead of `acks=all` Combined with `min.insync.replicas=2`

### The Mistake
Configuring Kafka producers with `acks=1` under the assumption that "leader acknowledgment is safe enough":

```python
# BROKEN ANTIPATTERN: False sense of durability with acks=1
producer = KafkaProducer(
    bootstrap_servers='kafka:9092',
    acks=1                               # DANGER: Only waits for partition leader
)
```

### Why It Fails
With `acks=1`, the partition leader writes the message to its local OS page cache and immediately returns an acknowledgment to the producer.
- If the broker host experiences a sudden power loss, kernel panic, or hardware failure *before* the follower replicas pull the record, **the message is permanently lost**.
- When KRaft/Controller elects a follower as the new partition leader, the new leader has never seen that record. The producer will never re-send it because it received an acknowledgment.

### Senior Engineering Fix: The Durability Holy Trinity
For zero data loss in financial, ordering, or critical transaction pipelines:
1. Producer: `acks='all'` (or `-1`).
2. Topic: `min.insync.replicas=2`.
3. Topic: `replication.factor=3`.

```python
# PRODUCTION FIX: Zero-Data-Loss Producer Configuration
producer = KafkaProducer(
    bootstrap_servers='kafka:9092',
    acks='all',                          # Waits for leader AND all min.insync.replicas
    retries=2147483647,                  # Retry indefinitely until confirmed
    enable_idempotence=True,             # Prevents duplicates on producer retries
    max_in_flight_requests_per_connection=5
)
```

---

## 4. Committing Offsets Out-of-Order in Multithreaded Worker Pools

### The Mistake
Distributing records from a single partition across a thread pool and having each worker thread commit its own offset when finished:

```python
# BROKEN ANTIPATTERN: Parallel thread completion committing out-of-order
def worker_task(record):
    process(record)
    # Thread finishes processing record at offset 105
    consumer.commit_offset(record.partition, record.offset + 1)

# Thread A receives Offset 100 (takes 5 seconds)
# Thread B receives Offset 105 (takes 100 milliseconds)
# Thread B finishes first and commits Offset 106!
```

### Why It Fails
Kafka offsets are **monotonic markers**, not individual message acknowledgments! Committing offset `106` tells the broker: *"All records up to offset 105 are permanently and successfully processed"*.
- If the consumer node crashes while Thread A is still working on offset `100`, the restarted consumer reads starting from offset `106`.
- **Records 100 through 104 are skipped and permanently lost!**

### Senior Engineering Fix: Sliding Window Offset Tracker
If multithreading a single partition's workload, maintain a concurrent sliding window (or min-heap of in-flight offsets). Only commit the **continuous contiguous low watermark**:

```python
class ContiguousOffsetTracker:
    def __init__(self, start_offset: int):
        self.committed_watermark = start_offset
        self.completed_offsets = set()

    def mark_completed(self, offset: int) -> int:
        self.completed_offsets.add(offset)
        # Advance watermark continuously
        while (self.committed_watermark + 1) in self.completed_offsets:
            self.committed_watermark += 1
            self.completed_offsets.remove(self.committed_watermark)
        return self.committed_watermark  # Safe offset to commit
```

---

## 5. Partition Proliferation: Creating Thousands of Partitions per Broker

### The Mistake
Creating 500 partitions for every small topic, or dynamically creating a new partition for every customer or tenant:

```bash
# BROKEN ANTIPATTERN: Massive partition count for low-traffic topics
kafka-topics.sh --create --topic tenant-tenant123 --partitions 100 --replication-factor 3
```

### Why It Fails
Each partition on a broker is represented by open file descriptors (`.log`, `.index`, `.timeindex`), OS directory handles, in-memory buffers, and replication state machine threads.
- 5,000 partitions on a single broker leads to **OS file descriptor exhaustion**.
- When a broker restarts, electing leaders for 5,000 partitions causes **Controller failover storms and multi-minute availability outages**.
- End-to-end commit latency degrades because the replication coordinator must track thousands of active ISR loops.

### Senior Engineering Fix: Sizing Partitions by Throughput
Rule of thumb: **1 partition handles roughly 10 MB/sec of write throughput and 20–50 MB/sec of read throughput**.
- Start with 3 to 12 partitions per topic for typical services.
- Keep total partitions per broker under **2,000 to 4,000** for healthy cluster operations.

---

## 6. Misconfiguring Log Retention & Compaction (NVMe Exhaustion)

### The Mistake
Enabling `cleanup.policy=compact` on a topic without deleting obsolete records or failing to send tombstones:

```python
# BROKEN ANTIPATTERN: Attempting to delete state by sending empty string
producer.send('user-states', key='user_42', value='')  # Not a tombstone!
```

### Why It Fails
In Kafka, an empty string `""` is still considered a valid value. The log compactor will **never** delete that key. To physically remove a key from a compacted topic, the producer must write a **Tombstone** (a record where `value=None` / `null`).
Additionally, if `delete.retention.ms` is set too low (e.g., 10 ms), slow consumers may miss the tombstone before it is purged, resurrecting zombie records in their local caches!

### Senior Engineering Fix: Explicit Tombstones & Sane Compaction Ratios
```python
# PRODUCTION FIX: Proper Tombstone Generation
producer.send('user-states', key='user_42', value=None)  # Native tombstone
```
On the broker topic configuration:
```properties
cleanup.policy=compact
delete.retention.ms=86400000         # Retain tombstones for 24 hours so all consumers see the deletion
min.compaction.lag.ms=3600000        # Prevent compacting records newer than 1 hour
```

---

## 7. Scaling Consumer Instances Beyond Partition Count

### The Mistake
Attempting to scale a slow consumer group from 4 workers to 12 workers when the subscribed topic only has 4 partitions:

```yaml
# BROKEN ANTIPATTERN: Kubernetes Deployment with 12 replicas on a 4-partition topic
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-consumer
spec:
  replicas: 12   # DANGER: Topic only has 4 partitions!
```

### Why It Fails
In Apache Kafka, a single partition can only be assigned to **at most one consumer instance** within the same consumer group at any given time.
- Workers 1 through 4 receive 1 partition each.
- **Workers 5 through 12 sit completely idle**, consuming CPU and RAM without processing a single record.

### Senior Engineering Fix: Partition Sizing & In-Worker Parallelism
1. Scale topic partitions upfront: `kafka-topics.sh --alter --topic payments --partitions 12`.
2. Or use an internal thread-pool dispatcher inside each consumer to process records across multiple threads while keeping consumer group size $\le N_{\text{partitions}}$.
