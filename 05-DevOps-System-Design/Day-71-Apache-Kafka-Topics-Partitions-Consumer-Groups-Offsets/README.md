# Day 71: Apache Kafka: Topics, Partitions, Consumer Groups & Offsets

A comprehensive architectural blueprint covering Apache Kafka's distributed commit-log internals, segmented disk storage, sparse indexing, partition assignment strategies, consumer group coordination, offset management, rebalance protocols, and production durability tuning.

---

## 1. Apache Kafka Architectural Overview

Apache Kafka is an **append-only, horizontally scalable distributed commit log** designed for high-throughput event streaming, Change Data Capture (CDC), and real-time data pipelines.

```
+-----------------------------------------------------------------------------------+
|                               APACHE KAFKA CLUSTER                                |
|                                                                                   |
|  +-----------------------------+        +-----------------------------+           |
|  |       BROKER 1 (Leader)     |        |      BROKER 2 (Follower)    |           |
|  |  +-----------------------+  |        |  +-----------------------+  |           |
|  |  | Topic: orders         |  |  Sync  |  | Topic: orders         |  |           |
|  |  | Partition 0 (Leader)  |==|=======>|  | Partition 0 (Follower)|  |           |
|  |  +-----------------------+  |        |  +-----------------------+  |           |
|  +-----------------------------+        +-----------------------------+           |
|                 ^                                      ^                          |
|                 |                                      |                          |
|  +-----------------------------------------------------------------------------+  |
|  |             METADATA QUORUM: KRaft (Kafka Raft Metadata Mode)               |  |
|  |    Active Controller Broker elected via Raft Consensus (replaces ZooKeeper)  |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

### KRaft (Kafka Raft) vs Legacy ZooKeeper
Modern Kafka (2.8+ / 3.0+ production-ready) operates in **KRaft mode**, eliminating the external Apache ZooKeeper dependency:
1. **Unified Consensus**: The control plane uses an internal event-driven Raft quorum stored in the `@metadata` topic.
2. **Instant Failover**: Controller failover latency dropped from minutes (reloading hundreds of thousands of znodes from ZooKeeper) to **milliseconds**, as standby controllers already maintain an in-memory replica of the metadata log.
3. **Partition Scalability**: Supports millions of partitions per cluster, bypassing ZooKeeper's watch and memory limits.

---

## 2. Topics, Partitions, and the Append-Only Commit Log

A **Topic** is a logical stream of records. In Kafka, topics are horizontally partitioned across multiple physical brokers:

```
TOPIC: "payments.v1" (3 Partitions)
Partition 0: [0] -> [1] -> [2] -> [3] -> [4] -> [5] (Next: Offset 6)
Partition 1: [0] -> [1] -> [2] -> [3]               (Next: Offset 4)
Partition 2: [0] -> [1] -> [2] -> [3] -> [4]        (Next: Offset 5)
```

### Partitioning Guarantees
- **Strict FIFO Ordering per Partition**: Records inside a single partition have monotonically increasing 64-bit integer identifiers called **Offsets**. Kafka guarantees strict chronological order **only within a single partition**.
- **Key Determinism**: If a record has a non-null key, Kafka computes:
  $$\text{Partition} = \text{MurmurHash2}(\text{key}) \pmod{\text{TotalPartitions}}$$
  All records sharing the same key land in the same partition, guaranteeing chronological sequence for that specific entity (e.g., `user_id`, `order_id`).
- **Null Keys**: When key is null, Kafka's **Sticky Partitioner** batches records into the current partition until buffer saturation or `linger.ms` elapses, maximizing compression ratios and network utilization.

---

## 3. Storage Engine Internals: Segments and Sparse Indexing

Partitions are not stored as single massive files on disk. Instead, each partition directory on the broker's filesystem is split into **Segments**:

```
/var/lib/kafka/data/orders-0/
  ├── 00000000000000000000.log         # Raw Record Batches
  ├── 00000000000000000000.index       # Sparse Offset-to-Byte-Position Index
  ├── 00000000000000000000.timeindex   # Timestamp-to-Offset Index
  ├── 00000000000000045000.log         # Active Segment (Open for Appends)
  ├── 00000000000000045000.index
  └── leader-epoch-checkpoint
```

### Segment Anatomy
1. **`.log` (Commit Log File)**: Contains sequential `RecordBatch` entries. Records are compressed together (Snappy, LZ4, zstd, GZIP) to maximize disk bandwidth.
2. **`.index` (Sparse Offset Index)**: Maps logical record offsets to physical byte positions within the `.log` file.
   - **Why Sparse?** Kafka writes an index entry only every $N$ bytes (configured via `index.interval.bytes`, default `4096` bytes / 4 KB). Storing every offset would exhaust RAM.
   - **Lookup Mechanics**: To locate offset `45,023`:
     1. Binary search the sparse `.index` in memory to find the nearest base offset $\le 45,023$ (e.g., offset `45,000` at physical position byte `12,288`).
     2. Perform a small sequential disk read from byte `12,288` forward until exact offset `45,023` is reached.
3. **`.timeindex`**: Maps epoch timestamps to logical offsets, powering `offsetsForTimes()` lookups (e.g., "replay events from 2 hours ago").

### Linux Page Cache & Zero-Copy Architecture
Kafka does not cache messages in the Java JVM heap. Instead, it relies on the **Linux OS Page Cache**:
- **Disk Sequential I/O**: Sequential writes to rotational HDDs or NVMe SSDs achieve 600+ MB/s, bypassing random I/O penalties.
- **Zero-Copy `sendfile(2)`**: When a consumer reads data, Kafka invokes the `sendfile()` system call. The OS transfers data **directly from the Page Cache to the Network Socket buffer** without copying bytes into JVM user-space memory, completely eliminating CPU context switches and garbage collection pauses.

---

## 4. Log Retention: Delete vs. Compact

Kafka provides two orthogonal retention mechanisms:

```
Log Cleanup Policy: "delete" vs "compact"

1. DELETE (Time / Size based):
   [Old Segments (> 7 days)] ===(Purged / Unlinked)===> Deleted
   [Active Segment] <=== New writes appended

2. COMPACT (Key-based state retention):
   Raw Log:    [K1: V1] -> [K2: V1] -> [K1: V2] -> [K3: V1] -> [K2: V2]
   Compacted:  [K1: V2] -> [K3: V1] -> [K2: V2]  (Keeps latest value per key)
```

- **Delete Retention (`cleanup.policy=delete`)**: Segments older than `log.retention.hours` (default 168 hours / 7 days) or partitions exceeding `log.retention.bytes` are deleted from disk.
- **Log Compaction (`cleanup.policy=compact`)**: Preserves the **latest value for each key**. Ideal for database changelogs (CDC) and cache hydration.
  - **Tombstones**: To delete a key, the producer writes `(key, value=None)`. The compactor retains the tombstone for `delete.retention.ms` before physically removing the key.

---

## 5. Consumer Groups & Partition Assignment Strategies

A **Consumer Group** represents a coordinated logical subscriber pool. Partitions are partitioned exclusively across active consumers in the group:

```
Topic: 4 Partitions [P0, P1, P2, P3]

Scenario A (2 Consumers):
  Consumer 1: [P0, P1]
  Consumer 2: [P2, P3]

Scenario B (4 Consumers):
  Consumer 1: [P0] | Consumer 2: [P1] | Consumer 3: [P2] | Consumer 4: [P3]

Scenario C (6 Consumers):
  Consumers 1-4: [P0], [P1], [P2], [P3]
  Consumers 5-6: IDLE (Cannot be assigned partitions)
```

$$\text{Active Consumers} \le N_{\text{partitions}}$$

### Partition Assignment Strategies

| Strategy | Behavior | Pros / Cons |
| :--- | :--- | :--- |
| **RangeAssignor** | Divides partitions into contiguous ranges per topic | Can cause partition skew if multiple topics are consumed |
| **RoundRobinAssignor** | Distributes all partitions across all consumers round-robin | Uniform distribution across all topics |
| **StickyAssignor** | Maximizes preserved assignments during rebalance while keeping balance | Reduces partition movement during consumer changes |
| **CooperativeSticky** | **Incremental cooperative rebalancing** (Kafka 2.4+) | **Eliminates stop-the-world pauses** during consumer group rebalance |

---

## 6. Rebalance Protocol: Eager vs. Cooperative Sticky

### Legacy Eager Rebalance (Stop-the-World)
1. Consumer group coordinator triggers rebalance (e.g., node added or crashed).
2. **Every consumer gives up all assigned partitions immediately**.
3. All processing across the entire group freezes.
4. Consumers re-join the group (`JoinGroup`), coordinator assigns partitions (`SyncGroup`), and consumers resume.
5. **Drawback**: In a 50-node cluster, a single flaky consumer causes catastrophic P99 latency spikes and processing freezes.

### Cooperative Sticky Rebalance (Incremental)
1. When a rebalance occurs, consumers **retain their existing assigned partitions**.
2. Only partitions that need migration are revoked.
3. The group continues processing uninterrupted while the migrating partitions are reassigned in a lightweight second phase.

---

## 7. Offset Management Internals (`__consumer_offsets`)

Kafka stores consumer group offsets inside an internal compacted topic named `__consumer_offsets` (50 partitions by default).

```
Key:   [Group: "order-processors", Topic: "orders", Partition: 0]
Value: [Offset: 10423, LeaderEpoch: 2, Metadata: "", Timestamp: 1773820000]
```

### Commit Strategies
1. **Auto-Commit (`enable.auto.commit=true`)**:
   - Offsets are committed periodically every `auto.commit.interval.ms` (default 5000ms) on each call to `poll()`.
   - **Risk**: High risk of duplicate processing or data loss if the consumer crashes before business logic finishes.
2. **Synchronous Manual Commit (`commitSync()`)**:
   - Blocks until the broker confirms the offset write.
   - **Risk**: Adds latency to the event loop.
3. **Asynchronous Manual Commit (`commitAsync()`)**:
   - Non-blocking commit. Does not retry automatically to prevent out-of-order offset regressions (e.g., commit 2000 failing after commit 2050 succeeded).
4. **Synchronous Commit on Shutdown**:
   - Standard pattern: use `commitAsync()` during the high-speed poll loop and a final `commitSync()` inside the `finally` shutdown block.

---

## 8. End-to-End Durability: ISR, ACKs & Idempotence

```
Producer ---> Leader Broker (P0)
                  |
         +--------+--------+
         |                 | (Replication)
         v                 v
   Follower 1 (ISR)  Follower 2 (ISR)
```

### Durability Configurations
- **`acks=0`**: Producer fires and forgets. Lowest latency, zero durability.
- **`acks=1`**: Leader writes locally to disk/cache and confirms. If leader crashes before followers replicate, data is lost.
- **`acks=all` (or `-1`)**: Leader confirms only after **all In-Sync Replicas (ISR)** have written the record batch.
- **`min.insync.replicas=2`**: When `acks=all`, the broker requires at least 2 replicas (e.g., leader + 1 follower) to be in sync. If only 1 replica survives, broker rejects new writes with `NotEnoughReplicasException`, preventing split-brain data divergence.

### Idempotent Producer (`enable.idempotence=true`)
Solves duplicate messages caused by producer network retries:
1. Broker assigns each producer a unique **Producer ID (PID)**.
2. Each record batch includes a monotonically increasing **Sequence Number**.
3. If the broker receives a duplicate sequence number, it confirms receipt without appending it a second time.

---

## 9. Producer & Consumer Latency vs. Throughput Tuning

### Producer Tuning Matrix
| Parameter | Default | Production High-Throughput Setting | Description |
| :--- | :--- | :--- | :--- |
| `compression.type` | `none` | `lz4` or `zstd` | Compresses batches; dramatically reduces network I/O |
| `linger.ms` | `0` | `10` – `50` | Waits up to $N$ ms to accumulate larger batches |
| `batch.size` | `16384` (16 KB) | `65536` – `131072` (64–128 KB) | Maximum batch size per partition |
| `max.in.flight.requests.per.connection` | `5` | `5` (with idempotence) | Parallel requests per broker socket |

### Consumer Tuning Matrix
| Parameter | Default | Purpose |
| :--- | :--- | :--- |
| `max.poll.records` | `500` | Capped batch size returned per `poll()` invocation |
| `max.poll.interval.ms` | `300000` (5 min) | Max time consumer can spend processing before being evicted |
| `session.timeout.ms` | `45000` (45 sec) | Heartbeat timeout detecting crashed consumer processes |
| `heartbeat.interval.ms`| `3000` (3 sec) | Heartbeat thread pulse interval (must be $\le 1/3$ session timeout) |

---

## 10. Architectural Comparison Matrix: Kafka Storage vs. Traditional RDBMS/Queues

| Metric | Traditional Message Queue (RabbitMQ) | Apache Kafka Commit Log | Relational Database (PostgreSQL) |
| :--- | :--- | :--- | :--- |
| **Data Structure** | Dynamic Linked List / B-Tree in RAM | Append-Only Segmented Flat Files | B+ Tree Pages on Disk |
| **Write Complexity** | $O(1)$ enqueue | $O(1)$ sequential append | $O(\log N)$ B-Tree write + WAL |
| **Read Complexity** | $O(1)$ dequeue (destructive) | $O(1)$ sequential read from Page Cache | $O(\log N)$ B-Tree index scan |
| **Consumer Tracking** | Broker manages state per message | Consumer commits single monotonic offset | Client tracks query cursor / position |
| **Horizontal Scaling**| Cluster mirroring / Queues | Sharded Partitions across Brokers | Read replicas / Partitioned tables |
| **Throughput** | 20k–80k msg/s | **1,000,000+ msg/s** (Zero-copy) | 10k–40k write transactions/s |
