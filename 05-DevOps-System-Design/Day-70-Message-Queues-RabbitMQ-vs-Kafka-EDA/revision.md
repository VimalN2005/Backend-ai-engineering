# Day 70: Rapid Revision & Flashcards: Message Queues & Event-Driven Architecture

A rapid 5-minute reference guide, mathematical formulas, and 10 self-testing flashcards covering RabbitMQ, Apache Kafka, and Event-Driven Architecture.

---

## 1. High-Density Cheat Sheet

### RabbitMQ vs. Apache Kafka at a Glance

| Feature / Dimension | RabbitMQ (AMQP 0-9-1) | Apache Kafka (Distributed Log) |
| :--- | :--- | :--- |
| **Core Architecture** | Smart Broker / Dumb Consumer | Dumb Broker / Smart Consumer |
| **Storage Model** | Ephemeral RAM queues + swap to disk | Immutable append-only commit logs on disk |
| **Message Consumption** | Push-based (`basic.deliver`) | Pull-based (`consumer.poll()`) |
| **Message Deletion** | Destructive: Deleted upon ACK (`basic.ack`) | Non-destructive: Retained based on time/size policy |
| **Time-Travel Replay** | Not supported (once acked, message is gone) | Fully supported: Reset offset to any historical point |
| **Routing Flexibility** | Complex (Direct, Fanout, Topic, Headers) | Simple (Partition key hash to partition ID) |
| **Ordering Guarantees** | FIFO per queue (broken with competing consumers)| Strict FIFO per partition |
| **Throughput Capacity** | 20k – 80k msg/sec per node | 100k – 1M+ msg/sec per cluster (via zero-copy `sendfile`) |
| **Consumer Scaling Limit** | Practically unlimited competing consumers | Bounded by partition count ($N_{\text{consumers}} \le N_{\text{partitions}}$) |
| **Primary Use Cases** | Asynchronous job queues, RPC, complex routing | Event streaming, log aggregation, CDC, event sourcing |

---

## 2. Mathematical Reference & Formulas

### 1. Partition Sizing Formula
To determine the minimum number of partitions $P$ needed to handle target cluster throughput:
$$P = \max\left(\left\lceil \frac{T_p}{S_p} \right\rceil, \left\lceil \frac{T_c}{S_c} \right\rceil\right)$$
Where:
- $T_p$: Target aggregate production throughput (MB/sec or msg/sec).
- $S_p$: Single producer throughput per partition.
- $T_c$: Target aggregate consumption throughput.
- $S_c$: Single consumer throughput per partition.

### 2. Maximum Consumer Concurrency in Kafka
In a Kafka Consumer Group:
$$\text{Active Consumers} \le N_{\text{partitions}}$$
Any consumer beyond $N_{\text{partitions}}$ remains completely idle ($0$ assigned partitions).

### 3. AMQP Prefetch Sizing (Little's Law for Messaging)
To prevent worker starvation while capping memory utilization:
$$\text{Optimal Prefetch} \approx \text{Throughput} \times \text{Average Latency} = \lambda \times W$$
*Example*: If a worker processes 100 msg/sec with an average latency of 0.05 seconds, set $\text{prefetch} \approx 100 \times 0.05 = 5$.

---

## 3. AMQP 0-9-1 Exchange Quick Reference

1. **Direct Exchange**: Matches routing key exactly against binding key (`routing_key == binding_key`).
2. **Fanout Exchange**: Ignores routing key completely; broadcasts every message to all bound queues.
3. **Topic Exchange**: Pattern matching with dot-delimited tokens:
   - `*` (asterisk): Matches **exactly one word** (e.g., `orders.*.europe`).
   - `#` (hash): Matches **zero or more words** (e.g., `orders.#`).
4. **Headers Exchange**: Routes based on message header attributes; uses `x-match: all` (AND) or `x-match: any` (OR).

---

## 4. 10 Rapid Revision Flashcards

### Flashcard 1
**Q: What is the primary operational consequence of RabbitMQ storing unconsumed messages for long periods?**  
**A:** RabbitMQ maintains message indices in RAM. When queues swell, Erlang processes trigger paging to disk, causing garbage collection spikes, high memory alarms (`vm_memory_high_watermark`), and publisher blocking across the cluster.

---

### Flashcard 2
**Q: Why does setting `prefetch_count = 0` in RabbitMQ risk crashing workers?**  
**A:** `0` means unlimited in AMQP. RabbitMQ immediately dumps all available messages over the network socket buffer into the consumer's client process, causing Out-Of-Memory (OOM) crashes and starving other idle workers.

---

### Flashcard 3
**Q: How does Apache Kafka achieve extreme throughput compared to traditional message brokers?**  
**A:** Kafka leverages **sequential disk I/O** (which rivals random RAM access speeds), OS Page Cache caching, and the zero-copy Linux `sendfile(2)` system call, avoiding CPU copies between kernel and user space.

---

### Flashcard 4
**Q: If a Kafka topic has 4 partitions and a consumer group has 6 consumers, how many consumers will process messages?**  
**A:** Exactly **4 consumers** will process messages (one partition per consumer). The remaining **2 consumers will sit idle** as standby backups.

---

### Flashcard 5
**Q: What is a Poison Pill message, and how should it be handled?**  
**A:** A malformed payload or unrecoverable error that fails processing 100% of the time. It should be tracked with an retry counter header, rejected with `requeue=False` upon exceeding retry thresholds, and routed to a **Dead-Letter Queue (DLQ)** for manual inspection.

---

### Flashcard 6
**Q: What is the Dual-Write problem in event-driven microservices?**  
**A:** Attempting to update a local database and publish an event to a message broker in separate steps. If the app crashes between the two steps, state becomes permanently inconsistent across services.

---

### Flashcard 7
**Q: How does the Transactional Outbox Pattern resolve the Dual-Write problem?**  
**A:** The business entity and the corresponding event are written into the **same relational database transaction** (local ACID boundary). An independent CDC poller or WAL reader (e.g., Debezium) asynchronously publishes the outbox events to the broker.

---

### Flashcard 8
**Q: Can RabbitMQ preserve message ordering when multiple consumers read from the same queue?**  
**A:** No. While the queue itself maintains FIFO order, concurrent consumer threads process messages at variable speeds, resulting in out-of-order execution. Strict ordering requires a Single Active Consumer or partitioned sharding.

---

### Flashcard 9
**Q: In Kafka, how do you ensure that all events for a specific user ID are processed in strict sequential order?**  
**A:** Set the message key to `user_id`. Kafka hashes the key to a specific partition, and because each partition is consumed by only one consumer in a group, per-user events are processed in strict sequential order.

---

### Flashcard 10
**Q: What is the difference between `At-Least-Once` delivery and `Exactly-Once` processing?**  
**A:** `At-Least-Once` guarantees no messages are lost, but duplicates may arrive upon network failures. `Exactly-Once` end-to-end processing is achieved when an `At-Least-Once` pipeline feeds into an **Idempotent Consumer** that deduplicates messages using unique message identifiers.
