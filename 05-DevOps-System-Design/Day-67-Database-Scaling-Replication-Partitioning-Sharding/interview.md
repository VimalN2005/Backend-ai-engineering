# Day 67: Database Scaling — Principal / Staff Engineering Interview Questions

In-depth technical analyses for Principal, Staff, and Senior Distributed Systems Engineers covering relational replication internals, consistency anomalies, sharding mathematics, split-brain fencing, and distributed transaction alternatives.

---

## Question 1: How does physical streaming replication work under the hood in PostgreSQL/MySQL, and what are the exact trade-offs between Asynchronous, Semi-Synchronous, and Synchronous replication regarding RPO, RTO, and write commit latency?

### Candidate Answer

### 1. Underlying Replication Mechanics
In modern relational databases (PostgreSQL and MySQL InnoDB):
- When a client executes a write transaction (`COMMIT`), the database engine does **not** write dirty table pages directly to random disk sectors. Instead, it serializes a continuous binary stream of change records to the **Write-Ahead Log (WAL)** in PostgreSQL or the **Binary Log (binlog)** in MySQL.
- Each WAL record is indexed by a strictly monotonic 64-bit integer called the **Log Sequence Number (LSN)** representing the exact byte offset in the log stream (e.g., `0/16B3748`).
- In PostgreSQL, replication is driven by two OS processes:
  1. **WAL Sender (`walsender`)** on the Primary reads WAL records from memory buffers or disk and transmits them over a TCP streaming connection.
  2. **WAL Receiver (`walreceiver`)** on the Replica receives bytes over TCP, writes them to its local WAL/relay log, and notifies the **Startup Process** (`WAL Replay Engine`) to apply page-level modifications to the replica's local data files.

```
                   PHYSICAL STREAMING REPLICATION PIPELINE
┌─────────────────────────────────┐                 ┌─────────────────────────────────┐
│         PRIMARY NODE            │                 │         REPLICA NODE            │
│  Client Commit                  │                 │                                 │
│        │                        │                 │                                 │
│        ▼                        │                 │                                 │
│  [Shared Buffers]               │                 │                                 │
│        │                        │                 │                                 │
│        ▼                        │                 │                                 │
│  [WAL Buffer] ──► WAL Sender ───┼──TCP Socket────►│ WAL Receiver ──► [Relay Log]    │
│        │                        │                 │                       │         │
│     (fsync)                     │                 │                       ▼         │
│        ▼                        │                 │                 Startup Process │
│  [Primary NVMe Disk]            │                 │                 (WAL Replay)    │
└─────────────────────────────────┘                 └─────────────────────────────────┘
```

### 2. The Replication Trilemma: RPO vs. Latency vs. Availability

| Mode | ACK Condition to Client | RPO (Data Loss Risk) | Write Latency | Cluster Availability Impact |
| :--- | :--- | :--- | :--- | :--- |
| **Asynchronous** | Primary flushes WAL to local disk; returns ACK immediately. Replicas stream in background. | $\text{RPO} > 0$ (If Primary crashes before streaming, un-replicated commits are lost). | **Lowest** (Local NVMe wire speed, $\approx 0.5-1.5\text{ ms}$). | **Highest** (Primary continues accepting writes even if all replicas die). |
| **Semi-Synchronous** (`remote_write`) | Primary waits until at least ONE replica acknowledges receipt of WAL bytes into RAM (relay log). | $\mathbf{\text{RPO} = 0}$ in 99.99% of failures (data safely in replica RAM). | **Medium** (Local fsync + 1 network round-trip RTT, $\approx 3-6\text{ ms}$). | **High** (Falls back to async if replica acknowledgement times out). |
| **Synchronous** (`remote_apply`) | Primary waits until replica flushes WAL to disk and applies changes to storage pages. | **Strictly $\mathbf{\text{RPO} = 0}$** (Zero data loss under any failure). | **Highest** (2x disk fsync + full cross-AZ network round-trip, $\approx 10-25\text{ ms}$). | **Lowest** (If the synchronous replica hangs or experiences GC pause, all writes freeze!). |

---

## Question 2: In a database cluster with asynchronous read replicas, how do you architecturally solve the "Read-Your-Own-Writes" anomaly without routing all read traffic to the Primary?

### Candidate Answer
The **Read-Your-Own-Writes** (RYOW) anomaly occurs when a user updates state (e.g., changes their email or billing address), immediately navigates to view it, but reads from a lagging replica, observing their pre-updated state.

Routing all reads to the Primary defeats the horizontal scaling purpose of read replicas. In enterprise production, we solve this via **hybrid session-aware proxy routing**:

```
                 READ-YOUR-OWN-WRITES DUAL-STRATEGY ROUTER
                               [Client Request]
                                      │
                                      ▼
                        [Database Routing Middleware]
                                      │
            ┌─────────────────────────┴─────────────────────────┐
            │ Has user written in last T seconds (e.g. 5s)?     │
            │ OR is client LSN > replica applied LSN?           │
            ├─────────────────────────┬─────────────────────────┤
            ▼ YES                     ▼ NO                      ▼
    [Route to PRIMARY]        [Check Replica LSN]       [Route to REPLICA POOL]
    (Guarantee freshness)     (Replica caught up)       (Load-balanced reads)
```

### Strategy 1: Session Time-Window Pinning
1. When a user submits an HTTP mutation (`POST`, `PUT`, `DELETE`), the backend updates the database on the Primary.
2. The API gateway sets a short-lived cookie or server-side Redis key: `user:<id>:write_ts = now()`.
3. For subsequent incoming `GET` requests, the routing middleware inspects `user:<id>:write_ts`. If `now() - write_ts < 5.0s` (the maximum expected p99 replication lag), the query routes to the **Primary**.
4. Once the window expires, reads safely fall back to the read replica pool.
5. *Scale Benefit*: Only the small fraction of users who just mutated state load the Primary; 95%+ of passive reading users load the replicas.

### Strategy 2: Monotonic LSN / GTID Token Passing
1. Upon committing a write transaction on the Primary, PostgreSQL returns `pg_current_wal_lsn()` (e.g., `0/3000040`).
2. The application injects an HTTP header into the response: `X-Database-LSN: 0/3000040`.
3. The client browser / mobile app sends this header on subsequent queries: `X-Required-LSN: 0/3000040`.
4. The database connection proxy (or middleware) samples available read replicas and reads `pg_last_wal_replay_lsn()`.
   - If `replica_lsn >= client_required_lsn`: the replica serves the query with guaranteed consistency.
   - If all replicas are lagging: the proxy routes to Primary or pauses the worker for up to $20\text{ms}$ to allow the replica to catch up.

---

## Question 3: Mathematically prove why Naive Modulo Sharding ($hash(k) \pmod N$) moves $\frac{N-1}{N}$ keys during expansion, and why Consistent Hashing reduces key movement to $\frac{K}{N}$.

### Candidate Answer

### 1. Proof for Naive Modulo Sharding
Assume a cluster of $N$ shards where a key $k$ is mapped to shard index:

$$S_N(k) = \text{hash}(k) \pmod N$$

Suppose we add 1 shard, expanding the cluster to $N + 1$ shards. The new mapping is:

$$S_{N+1}(k) = \text{hash}(k) \pmod{N + 1}$$

For any arbitrary key $k$, the probability that its assigned shard remains unchanged is the probability that:

$$\text{hash}(k) \pmod N = \text{hash}(k) \pmod{N + 1}$$

By the Chinese Remainder Theorem and number-theoretic properties of modular arithmetic for relatively prime integers $N$ and $N+1$, the two modular reductions coincide with probability:

$$P(\text{Key Remains on Same Shard}) = \frac{1}{N + 1}$$

Therefore, the fraction of keys that **must be physically migrated across the network** is:

$$P(\text{Key Moves}) = 1 - \frac{1}{N + 1} = \frac{N}{N + 1}$$

- For $N = 4 \implies N = 5$: $\frac{4}{5} = \mathbf{80\%}$ **of all keys move!**
- For $N = 9 \implies N = 10$: $\frac{9}{10} = \mathbf{90\%}$ **of all keys move!**

### 2. Proof for Consistent Hashing
In a Consistent Hashing Ring (Karger et al., 1997), both nodes and keys are mapped onto a circular token space (e.g. $[0, 2^{32} - 1]$).
A key is routed clockwise to the first node whose token is greater than or equal to the key's token.

```
                      CONSISTENT HASHING RING EXPANSION
             Node A (Token 100)                      Node B (Token 500)
             /                                                 \
            │               [Node E Inserted at Token 300]      │
            │                         ▲                         │
            │                         │                         │
            │             Only keys between 100 and 300         │
            │               are migrated to Node E!             │
```

When a new node $N_{m+1}$ is inserted onto the ring:
1. It is positioned between two existing nodes (say, counter-clockwise neighbor $A$ and clockwise neighbor $B$).
2. The only keys whose clockwise path changes are those residing in the interval between $A$ and $N_{m+1}$.
3. Keys mapped between $N_{m+1}$ and $B$, or between any other pairs of nodes on the ring, are **completely unaffected**.

Given $K$ total keys distributed uniformly across $N$ existing nodes, each node owns an average fraction of $\frac{1}{N}$ of the ring. When an $(N+1)^{\text{th}}$ node joins, it claims an average share of $\frac{1}{N+1}$ of the ring from its neighbors:

$$\text{Expected Keys Migrated} = \frac{K}{N + 1}$$

- In a 4-to-5 shard expansion, Consistent Hashing moves only **$20\%$ of keys**, compared to **$80\%$ in modulo sharding** (a $4\times$ reduction in data migration and network I/O).

---

## Question 4: What is the Split-Brain problem during database failover, and how do consensus-backed orchestration tools (e.g., Patroni with etcd) implement Fencing and STONITH to guarantee single-primary invariants?

### Candidate Answer

### 1. The Anatomy of Split-Brain
**Split-Brain** occurs when a cluster of database nodes is partitioned by a network outage or process stall, and two different nodes simultaneously believe they are the legitimate **Primary (Read/Write)**.

```
                               SPLIT-BRAIN DISASTER
     Partition 1 (Minority: US-East-1a)      Partition 2 (Majority: US-East-1b, 1c)
     ┌────────────────────────────────┐      ┌────────────────────────────────┐
     │        OLD PRIMARY (pg-1)      │      │        NEW PRIMARY (pg-2)      │
     │      (Still accepts writes)    │      │       (Promoted by standby)    │
     └───────────────┬────────────────┘      └───────────────┬────────────────┘
                     │                                       │
                     └─── X X X NETWORK PARTITION X X X ─────┘
                               (Trans-AZ Packet Drop)
```

1. Clients connected to Availability Zone 1a continue writing to `pg-1`.
2. Clients connected to Availability Zone 1b write to `pg-2`.
3. When the network heals, both nodes possess **divergent WAL timelines** with identical LSN counters.
4. Because ACID relational engines cannot automatically merge two conflicting write-ahead logs, **automatic convergence is mathematically impossible**. Transactions must be manually inspected, resulting in corrupted foreign keys, duplicate primary keys, and permanent data loss.

### 2. Consensus-Backed Fencing (Patroni + etcd)
Enterprise clusters eliminate split-brain through **Distributed Lease Quorums**:

1. **Ephemeral Leader Lease**: The Primary must acquire and continuously renew an ephemeral key in a distributed consensus store (etcd, Consul, or ZooKeeper) with a strict Time-to-Live (e.g. `TTL = 10s`). The Primary issues heartbeats every 2 seconds.
2. **Voluntary Stepdown (Self-Fencing)**: If the Primary fails to refresh its etcd lease within 10 seconds (e.g. because it is isolated on a minority network partition), it **immediately fences itself**:
   - Drops all active client connection pools.
   - Demotes its local engine to `READ_ONLY` mode.
3. **Consensus Election**: The remaining standby nodes in the majority partition observe lease expiration. They run a Raft/Paxos consensus round. Only the standby that has received all committed WAL records (highest LSN) can acquire the leader key and promote itself.

### 3. STONITH ("Shoot The Other Node In The Head")
In mission-critical enterprise environments, self-fencing is augmented by hardware or cloud fencing:
- Before the standby can complete its promotion, a **fencing agent** executes an external command:
  - AWS: Modifies EC2 IAM permissions or detaches the EBS volume from the old primary.
  - Bare-metal: Sends an IPMI / iLO command to the physical power supply of the old primary server to forcefully cut power.
- **Guarantee**: Guarantees with 100% mathematical certainty that the old primary is dead before the new primary accepts a single write transaction.

---

## Question 5: Why is Two-Phase Commit (2PC) considered an antipattern for distributed cross-shard transactions, and how does the Saga Pattern replace it?

### Candidate Answer

### 1. Why 2PC Fails at High Scale
The **Two-Phase Commit (2PC)** protocol attempts to provide atomic, linearizable transactions across multiple physical database shards:
- **Phase 1 (Prepare)**: Coordinator asks all participant shards: *"Can you commit?"* Each shard executes the write, flushes undo/redo logs, and **acquires row-level write locks**.
- **Phase 2 (Commit)**: If all participants vote YES, coordinator broadcasts `COMMIT`; otherwise `ROLLBACK`.

### Fatal Flaws of 2PC:
1. **Blocking Protocol (Lock Congestion)**: Row locks are held across **multiple cross-datacenter network round trips**. In high-concurrency systems, holding locks for $50-200\text{ms}$ causes incoming transactions on the same rows to queue up, saturating database connection pools.
2. **Coordinator Single Point of Failure (SPOF)**: If the coordinator crashes after Phase 1, participant shards are left in an indeterminate "prepared" state. They cannot commit or abort autonomously, locking database resources indefinitely until manual administrative intervention.
3. **Availability Degradation**: In accordance with the CAP theorem, a 2PC transaction requiring $M$ shards has an availability equal to $A_{\text{total}} = \prod_{i=1}^M A_i$. A 5-shard 2PC transaction across 99% available nodes drops composite transaction availability to $95.1\%$.

---

### 2. The Modern Alternative: The Saga Pattern
Instead of holding blocking locks across shards, a **Saga** models a business transaction as a sequence of **local, independent ACID transactions** coordinated via asynchronous messaging:

```
                            ORCHESTRATED SAGA PATTERN
       [Order Service (Coordinator)]
           │                 ▲
   1. Local Tx (Shard 1)     │ 2. Order Created Event
           ▼                 │
   [Customer Service] ───────┼───────► [Inventory Service]
           │                 │                 │
   3. Debit Balance (Shard 2)│         4. Reserve Stock (Shard 3)
           │                 │                 │
           ▼                 │                 ▼ (OutOfStock Exception!)
   (Success: $100 debited)   │         [Compensating Transaction Triggered!]
                             │                 │
                             └─────────────────┴──► Refund $100 to Customer (Shard 2)
```

1. **No Distributed Locks**: Each shard commits its local transaction immediately, releasing row locks in microseconds.
2. **Compensating Transactions**: If any step fails (e.g. `Inventory Service` fails due to out-of-stock), the Saga coordinator issues **compensating transactions** to reverse previously committed steps (e.g., refunding the debit on the customer's account).
3. **Eventual Consistency**: The system embraces temporary intermediate states, guaranteeing that the workflow will eventually either complete successfully or cleanly roll back via compensations without locking the database cluster.
