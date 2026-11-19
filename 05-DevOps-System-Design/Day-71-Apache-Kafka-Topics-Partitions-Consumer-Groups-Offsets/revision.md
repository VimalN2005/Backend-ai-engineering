# Day 71: Rapid Revision & Flashcards: Apache Kafka

A 5-minute reference sheet, mathematical formulas, critical configuration tables, and 10 self-testing flashcards covering Apache Kafka topics, partitions, consumer groups, and offset mechanics.

---

## 1. High-Density Cheat Sheet

### Core Concepts Summary
- **KRaft Mode**: Replaces ZooKeeper with an internal Raft metadata quorum stored in the `@metadata` topic. Enables sub-second controller failovers and millions of partitions per cluster.
- **Segment Files**: Partitions are stored on disk as rolled segments (`.log`, `.index`, `.timeindex`). Segments roll when reaching `segment.bytes` (default 1 GB) or `segment.ms`.
- **Sparse Index**: Maps logical relative offsets to physical byte positions every $N$ bytes (default 4 KB). Enables $O(\log S)$ binary search lookup followed by small sequential scan.
- **Zero-Copy**: The Linux kernel `sendfile(2)` system call transfers data directly from the OS Page Cache to the network socket, bypassing the JVM heap.
- **Retention**: Time-based deletion (`cleanup.policy=delete`) vs. key-based deduplication (`cleanup.policy=compact`). Deletion in compacted topics requires a **Tombstone** (`value=None`).
- **Consumer Group Rule**: Within a consumer group, each partition is consumed by **at most one** consumer. Extra consumers beyond the partition count sit idle.
- **Rebalance Protocol**: Cooperative Sticky Rebalancing (KIP-429) replaces legacy Eager Rebalancing, eliminating stop-the-world pauses by migrating only affected partitions incrementally.

---

## 2. Mathematical Reference & Formulas

### 1. Partition Sizing Formula
To determine the number of partitions $P$ needed for a topic:
$$P = \max\left(\left\lceil \frac{T_p}{S_p} \right\rceil, \left\lceil \frac{T_c}{S_c} \right\rceil\right)$$
Where:
- $T_p$: Target aggregate write throughput (MB/sec).
- $S_p$: Single producer throughput per partition ($\approx 10$ MB/sec).
- $T_c$: Target aggregate read throughput (MB/sec).
- $S_c$: Single consumer throughput per partition ($\approx 25$ MB/sec).

### 2. Consumer Lag Formula
For partition $p$:
$$\text{Lag}_p = \text{High Watermark}_p - \text{Committed Offset}_p$$
Total Consumer Group Lag:
$$\text{Total Lag} = \sum_{p=0}^{N-1} (\text{High Watermark}_p - \text{Committed Offset}_p)$$

### 3. Maximum Consumer Concurrency
$$\text{Active Consumers} \le N_{\text{partitions}}$$

---

## 3. Critical Production Configurations Reference

| Parameter | Recommended Value | Scope | Description |
| :--- | :--- | :--- | :--- |
| `acks` | `'all'` (or `-1`) | Producer | Waits for all In-Sync Replicas to commit before confirming write |
| `enable.idempotence` | `true` | Producer | Guarantees exactly-once write per partition using PID + Sequence Number |
| `min.insync.replicas` | `2` | Broker/Topic | Minimum ISR required to accept writes when `acks=all` |
| `replication.factor` | `3` | Topic | Number of physical copies across independent brokers |
| `unclean.leader.election.enable` | `false` | Broker/Topic | Prevents out-of-sync followers from becoming leader (zero data loss) |
| `partition.assignment.strategy` | `CooperativeStickyAssignor` | Consumer | Incremental rebalancing avoiding stop-the-world freezes |
| `enable.auto.commit` | `false` | Consumer | Ensures manual offset control, preventing silent data drops |
| `max.poll.interval.ms` | `300000` (5 min) | Consumer | Maximum duration consumer can spend processing before rebalance eviction |

---

## 4. 10 Rapid Revision Flashcards

### Flashcard 1
**Q: How does modern Kafka manage metadata without Apache ZooKeeper?**  
**A:** Via **KRaft (Kafka Raft Metadata Mode)**. Brokers elect an active metadata controller using Raft consensus, storing all metadata in an internal `@metadata` topic, achieving sub-second failover.

---

### Flashcard 2
**Q: Why doesn't Kafka maintain an index entry for every single message in a partition?**  
**A:** Storing every offset would cause massive memory bloat. Kafka uses a **Sparse Index** (`.index`), recording an entry only every 4 KB (`index.interval.bytes`). To find an offset, it binary-searches the sparse index and scans sequentially for a few bytes.

---

### Flashcard 3
**Q: How does Kafka achieve extreme I/O throughput without caching messages in the Java JVM heap?**  
**A:** Kafka bypasses the JVM heap and relies on the **Linux OS Page Cache** for memory caching combined with the zero-copy Linux `sendfile(2)` system call, streaming data directly from page cache to network socket buffers.

---

### Flashcard 4
**Q: What is a "Tombstone" in a compacted Kafka topic, and what happens if a producer sends an empty string instead?**  
**A:** A Tombstone is a record with a non-null key and `value=None` (`null`). It marks the key for deletion. If a producer sends an empty string `""`, Kafka treats it as valid payload and **never deletes the key**.

---

### Flashcard 5
**Q: What is the primary difference between Eager Rebalance and Cooperative Sticky Rebalance?**  
**A:** In Eager Rebalancing, **all consumers revoke all partitions**, freezing processing across the entire group (stop-the-world). Cooperative Sticky revokes **only the migrating partitions**, allowing all other consumers to continue processing without interruption.

---

### Flashcard 6
**Q: What causes a "Rebalance Storm" in a consumer group?**  
**A:** When consumers take longer to process a batch than `max.poll.interval.ms`, the coordinator assumes they are dead, revokes their partitions, and triggers a rebalance. The next consumer also times out on the same backlog, causing an infinite loop of rebalances.

---

### Flashcard 7
**Q: Why is `acks=1` considered dangerous for financial or critical data pipelines?**  
**A:** `acks=1` only waits for the partition leader to write to its local page cache. If the leader crashes before followers replicate the record, the record is permanently lost upon failover.

---

### Flashcard 8
**Q: If a topic has 6 partitions and a consumer group has 10 consumers, how many consumers will actively process data?**  
**A:** Exactly **6 consumers** will process data (1 partition per consumer). The remaining **4 consumers will sit completely idle** as standby replicas.

---

### Flashcard 9
**Q: Why is it dangerous to commit offsets out-of-order across a thread pool processing a single partition?**  
**A:** Kafka offsets are monotonic pointers. Committing offset 105 tells Kafka that *all* records up to 104 are complete. If offset 100 is still processing on a slow thread and the node crashes, records 100–104 are skipped permanently.

---

### Flashcard 10
**Q: What does `unclean.leader.election.enable = false` guarantee?**  
**A:** It guarantees that only replicas currently in the **In-Sync Replicas (ISR)** set can ever become the partition leader. If all ISR nodes crash, the partition remains offline until one recovers, ensuring **zero data loss and zero offset truncation**.
