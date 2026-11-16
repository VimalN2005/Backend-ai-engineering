# Day 67: Database Scaling: Replication, Read Replicas, Partitioning & Sharding

An exhaustive architectural blueprint for Principal, Staff, and Senior Distributed Systems Engineers: Primary-Replica topologies, synchronous vs. asynchronous replication trade-offs, replication lag anomalies, Read-Your-Own-Writes consistency via LSN tracking, split-brain fencing, vertical vs. horizontal partitioning, and enterprise database sharding using Consistent Hashing with virtual nodes.

---

## 1. The Database Scaling Journey: The 4 Evolutionary Phases

Every high-growth platform hits the relational database performance wall. A single PostgreSQL or MySQL server initially bound by disk I/O, lock contention, and WAL serialization must evolve through 4 architectural phases:

```
                          DATABASE SCALING EVOLUTION
+─────────────────────────────────────────────────────────────────────────────+
| Phase 1: Optimization & Caching                                             |
| - Query optimization, EXPLAIN ANALYZE, B-Tree & GIN Indexes                 |
| - Distributed Caching (Redis/Memcached) for hot read keys                   |
| - Connection Pooling (PgBouncer, ProxySQL) to curb connection RAM bloat     |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ Throughput limit reached (~10k QPS)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
| Phase 2: Read Replicas (Scale Reads Horizontally)                           |
| - 1 Primary (Read/Write) + N Read Replicas (Read-Only)                     |
| - Asynchronous WAL/binlog replication over private network                  |
| - Solves read-heavy workloads (90:10 read:write ratio)                      |
| - Introduces Replication Lag & Stale Read anomalies                         |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ Write capacity saturated (>5k TPS)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
| Phase 3: Vertical Partitioning (Functional Separation)                      |
| - Split monolithic database by bounded contexts (Microservices)             |
| - Examples: Auth DB, Catalog DB, Order DB, Billing DB                       |
| - Eliminates cross-table JOINs; requires application-level stitching        |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ Single domain table outgrows 5TB
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
| Phase 4: Horizontal Partitioning & Sharding                                 |
| - Partition rows of a single table across N physical database instances    |
| - Consistent Hashing on Shard Key (e.g. `user_id`, `tenant_id`)             |
| - Virtually limitless write & storage scaling                               |
| - Cross-shard JOINs, distributed transactions (2PC/Sagas), complex rebalance|
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 2. Replication Topologies

Replication copies data from one database instance to another to provide **high availability (HA)**, **fault tolerance**, and **read scalability**.

```
                           REPLICATION TOPOLOGIES
  1. PRIMARY-REPLICA            2. MULTI-MASTER              3. MASTERLESS (QUORUM)
┌───────────────────────┐   ┌───────────────────────┐   ┌───────────────────────┐
│     Primary (R/W)     │   │ Master 1  ◄─► Master 2│   │    Node A    Node B   │
└──────────┬────────────┘   └───────┬───────┬───────┘   │        \     /        │
     WAL   │ Replication            │       │           │         Node C        │
     ┌─────┴─────┐                  ▼       ▼           │         /    \        │
     ▼           ▼             Replica 1 Replica 2      │    Node D    Node E   │
┌─────────┐ ┌─────────┐     (Complex conflict           │ (Dynamo / Cassandra   │
│Replica 1│ │Replica 2│      resolution: CRDTs/LWW)     │  W + R > N Quorum)    │
└─────────┘ └─────────┘                                 └───────────────────────┘
```

### 1. Primary-Replica (Active-Passive)
- **Architecture**: A single Primary node handles all write operations (`INSERT`, `UPDATE`, `DELETE`) and appends changes to a Write-Ahead Log (WAL). One or more Replica nodes stream the WAL and replay transactions locally.
- **Use Case**: Industry standard for 95% of web platforms (PostgreSQL, MySQL, Amazon RDS Aurora).
- **Trade-off**: Writes are still constrained to a single physical master.

### 2. Multi-Master (Active-Active)
- **Architecture**: Multiple nodes act as write primaries simultaneously, asynchronously replicating mutations to one another.
- **Use Case**: Multi-region deployments with localized writes.
- **Trade-off**: Write collisions on identical rows generate conflicts that require resolution via Last-Write-Wins (LWW) or Conflict-Free Replicated Data Types (CRDTs). High operational complexity.

### 3. Masterless (Dynamo-Style)
- **Architecture**: Every node can accept reads and writes. Consistency is maintained mathematically via configurable quorum:
  $$W + R > N$$
  Where $N$ is the replication factor, $W$ is the write quorum, and $R$ is the read quorum.
- **Use Case**: Apache Cassandra, Amazon DynamoDB, ScyllaDB.

---

## 3. Synchronous vs. Asynchronous vs. Semi-Synchronous Replication

```
                     REPLICATION SYNCHRONICITY COMPARISON
+─────────────────────────────────────────────────────────────────────────────+
| 1. SYNCHRONOUS REPLICATION                                                  |
|    Client ──► Primary ──► (Flush WAL) ──► Replica ──► (Flush WAL)           |
|      ▲                                                     │                |
|      └────────────── (ACK Client Commit) ◄─────────────────┘                |
|    • RPO = 0 (Zero data loss)                                               |
|    • High Latency: Client waits for cross-datacenter round-trip + 2x fsync |
|    • Degraded Availability: If ANY replica hangs, writes halt!              |
+─────────────────────────────────────────────────────────────────────────────+
| 2. ASYNCHRONOUS REPLICATION                                                 |
|    Client ──► Primary ──► (Flush WAL) ──► ACK Client Commit (Immediate!)   |
|                              │                                              |
|                              └──► Asynchronous Background WAL Stream ──► Rep|
|    • Ultra-Low Latency: Commits at local NVMe wire speed                    |
|    • High Availability: Primary continues even if all replicas die          |
|    • Risk of Data Loss: If Primary crashes before replica streams, RPO > 0  |
+─────────────────────────────────────────────────────────────────────────────+
| 3. SEMI-SYNCHRONOUS REPLICATION (PostgreSQL / MySQL Enterprise Standard)    |
|    Client ──► Primary ──► (Flush WAL) ──► Replica ──► (Memory ACK)          |
|      ▲                                                     │                |
|      └────────────── (ACK Client Commit) ◄─────────────────┘                |
|    • Waits for at least ONE replica to receive bytes in RAM (relay log)     |
|    • Replica does NOT need to fsync to disk before ACK                      |
|    • Balances zero data loss with manageable latency overhead               |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 4. Replication Lag & Stale Read Anomalies

In asynchronous and semi-synchronous replication, replicas trail the primary by a dynamic time window known as **Replication Lag**:

$$\text{Replication Lag} = T_{\text{current\_time}} - T_{\text{replica\_last\_applied\_commit}}$$

Replication lag expands during:
1. High-volume write bursts (e.g. bulk database updates or batch ETL ingestion).
2. Long-running analytical queries running on read replicas that block WAL replay locks.
3. Network congestion between primary and replica availability zones.

```
                      THE "READ YOUR OWN WRITES" ANOMALY
+─────────────────────────────────────────────────────────────────────────────+
| 1. User updates display name: "Alice" ──► "Alice Smith"                     |
|    Client ──POST /profile──► API Gateway ──► PRIMARY (Writes "Alice Smith") |
|                                                                             |
| 2. Client browser immediately reloads profile page:                         |
|    Client ──GET /profile───► API Gateway ──► REPLICA (Lag = 450ms)          |
|                                                  │                          |
| 3. Replica has not yet applied WAL commit:       │                          |
|    Replica returns old name: "Alice"! ◄──────────┘                          |
|                                                                             |
| Result: User thinks update failed, clicks submit 5 times (Duplicate updates)|
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 5. Enterprise Defenses: Read-Your-Own-Writes Consistency

To prevent serving stale data to a user who just modified it, enterprise backend architectures use three primary routing techniques:

### 1. Time-Window Master Pinning
When a user executes a write mutation, the application sets a short-lived cookie or server-side Redis flag (`user:session:write_timestamp = now()`). For the next $N$ seconds (e.g. $5\text{ seconds}$, exceeding maximum expected replication lag), all read requests from that user session are pinned directly to the **Primary**. Reads from other users continue routing to read replicas.

### 2. Log Sequence Number (LSN) / GTID Tracking
Modern engines (PostgreSQL LSN, MySQL GTID) provide exact monotonic byte positions in the Write-Ahead Log:
1. Primary commits write and returns current LSN: `0/16B3748`.
2. Application attaches this LSN token to the client response.
3. On subsequent reads, the client sends `X-Required-LSN: 0/16B3748`.
4. The database proxy checks the replica's `pg_last_wal_replay_lsn()`. If the replica has replayed past that LSN, it serves the read; otherwise, it waits or routes to Primary!

### 3. Monotonic Reads Guard
Guarantees that if a user reads value $v_1$ at time $t_1$, they will never subsequently observe an older value $v_0$ on a slower replica. Achieved by pinning a user session to a specific replica instance via consistent IP or session hashing.

---

## 6. Automated Failover & Split-Brain Mitigation

When a Primary node experiences a hardware crash or network partition, the cluster must promote a standby replica to become the new Primary.

```
                          SPLIT-BRAIN DISASTER SCENARIO
+─────────────────────────────────────────────────────────────────────────────+
|                   Datacenter 1                    Datacenter 2              |
|              ┌─────────────────────┐         ┌─────────────────────┐        |
|              │   OLD PRIMARY (P1)  │         │   REPLICA (Now P2)  │        |
|              │  (Isolated by Cut)  │         │ (Promoted by local) │        |
|              └──────────┬──────────┘         └──────────┬──────────┘        |
|                         │                               │                   |
|                         └── X X X FIBER CUT X X X ──────┘                   |
|                                                                             |
| Clients in Region 1 write to P1!            Clients in Region 2 write to P2!|
| Result: Divergent WAL timelines. Data corruption is IRRECONCILABLE!         |
+─────────────────────────────────────────────────────────────────────────────+
```

### Split-Brain Defense: Consensus & STONITH
1. **Consensus-Based Leader Election**: Standby promotion requires a majority quorum agreement ($\lfloor N/2 \rfloor + 1$) via Raft or Paxos (e.g. **Patroni** backed by **etcd** or Consul). An isolated primary in a minority partition cannot maintain its lease and automatically demotes itself to read-only.
2. **STONITH ("Shoot The Other Node In The Head") / Fencing Agents**: The cluster uses hardware power management (IPMI / iLO) or cloud IAM detachment to forcefully reboot or isolate the old primary before promoting the new primary.

---

## 7. Vertical Partitioning vs. Horizontal Partitioning

```
                     VERTICAL VS. HORIZONTAL PARTITIONING
  ORIGINAL TABLE (100M Rows, 25 Columns)
  ┌────────────────────────────────────────────────────────┐
  │ id | user_id | order_date | total | billing_addr | ... │
  └────────────────────────────────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
  VERTICAL PARTITIONING     HORIZONTAL PARTITIONING (SHARDING)
  (Split by Columns)        (Split by Rows)
  Table A: Core Orders      Shard 1: Rows 1 to 25,000,000
  ┌───────────────────────┐ ┌──────────────────────────────────────┐
  │ id | user_id | total  │ │ id | user_id | order_date | total |..│
  └───────────────────────┘ └──────────────────────────────────────┘
  Table B: Heavy Blobs      Shard 2: Rows 25,000,001 to 50,000,000
  ┌───────────────────────┐ ┌──────────────────────────────────────┐
  │ id | billing_address  │ │ id | user_id | order_date | total |..│
  └───────────────────────┘ └──────────────────────────────────────┘
```

- **Vertical Partitioning**: Moving rarely-accessed, heavy columns (e.g., text descriptions, JSON blobs, KYC PDFs) into a secondary table linked by foreign key. Keeps primary table rows compact in the PostgreSQL OS buffer cache.
- **Horizontal Partitioning (Table Partitioning)**: Dividing a single table within a database engine into multiple smaller physical tables using range (by date), list (by country), or hash partitioning.

---

## 8. Horizontal Sharding: Splitting Data Across Database Nodes

When a single table exceeds several terabytes and write throughput exceeds the maximum IOPS of high-end storage, **Sharding** is required. Sharding horizontally partitions rows across **independent database instances**, each with its own CPU, memory, and disks.

```
                           SHARDED ARCHITECTURE
                               [Client App]
                                    │
                                    ▼
                      [Sharding Router / Proxy]
                      (Hashes Shard Key: user_id)
                       /            │            \
                      /             │             \
                     ▼              ▼              ▼
              ┌─────────────┐┌─────────────┐┌─────────────┐
              │   SHARD 1   ││   SHARD 2   ││   SHARD 3   │
              │ (Users 1-3M)││ (Users 4-6M)││ (Users 7-9M)│
              └─────────────┘└─────────────┘└─────────────┘
```

### The Shard Key Dilemma
The **Shard Key** is the single most critical architectural choice in database design. An optimal shard key satisfies three properties:
1. **High Cardinality**: Sufficient unique values to distribute evenly across thousands of nodes (e.g. `user_id` or `uuid` vs `country_code`).
2. **Uniform Distribution**: Prevents **hotspots** (e.g., sharding by `celebrity_id` or `created_at` timestamp channels all write traffic into the single current shard).
3. **Query Colocation**: Ensures the most critical, high-frequency queries touch only a **single shard** rather than scattering across all shards.

---

## 9. Consistent Hashing with Virtual Nodes

### The Catastrophe of Modulo Sharding
Naive sharding maps keys to shards using modulo arithmetic:

$$\text{Shard ID} = \text{hash}(\text{Key}) \pmod N$$

If you have $N = 4$ shards and add a $5^{\text{th}}$ shard ($N = 5$):
- Almost every existing key now hashes to a different index: $\frac{N-1}{N} = \frac{4}{5} = \mathbf{80\%}$ **of all keys must be physically copied over the network!**
- Under high traffic, this massive rebalancing triggers network saturation and database outages.

### Consistent Hashing Ring ($0$ to $2^{32}-1$)
Consistent Hashing maps both **nodes** and **data keys** onto a circular 32-bit integer space (hash ring):

```
                       CONSISTENT HASHING RING (2^32 - 1)
                                      0
                               ┌─────────────┐
                         .─────               ─────.
                     .─'                             '─.
                 .─'                                     '─.
             Node A (Token 100)                     Node B (Token 500)
             /                                                 \
            │                                                   │
        Key X (Token 250) ──► Routed clockwise to Node B!        │
            │                                                   │
             \                                                 /
             Node D (Token 850)                     Node C (Token 700)
                 '─.                                     .─'
                     '─.                             .─'
                         '─────.             .─────'
                                └─────────────┘
```

1. **Routing Rule**: A key is hashed to a token on the ring. The key is assigned to the **first node encountered moving clockwise**.
2. **Adding a Node**: When Node E is inserted between A and B, **only the keys between A and E are migrated**. The rest of the cluster remains completely untouched!
   $$\text{Keys Moved} \approx \frac{K}{N}$$

### Virtual Nodes (vnodes): Solving Hotspots
In a basic hash ring with a small number of physical nodes, keys cluster non-uniformly.
- By assigning **100 to 256 Virtual Nodes (vnodes)** per physical machine (e.g., `NodeA-vnode-1`, `NodeA-vnode-2`, ...), physical nodes are evenly interleaved across the entire ring.
- Guarantees variance $< 5\%$ across all shards and enables proportional capacity weighting (a machine with 64GB RAM gets 200 vnodes; a 32GB machine gets 100 vnodes).

---

## 10. Cross-Shard Querying & Distributed Joins

When a query cannot specify the shard key, the database cannot route to a single shard.

```
                           SCATTER-GATHER PATTERN
                               [Client Query]
                       "SELECT * FROM orders WHERE status='PENDING'"
                                    │
                                    ▼
                      [Sharding Aggregator Proxy]
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼ (Parallel Fanout)     ▼ (Parallel Fanout)     ▼ (Parallel Fanout)
       ┌─────────┐             ┌─────────┐             ┌─────────┐
       │ Shard 1 │             │ Shard 2 │             │ Shard 3 │
       └────┬────┘             └────┬────┘             └────┬────┘
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    ▼
                         [Merge & Sort in Memory]
                                    ▼
                              [Client Response]
```

### The Pitfalls of Scatter-Gather:
1. **Latency Multiplication**: Query latency equals the slowest responding shard ($O(\text{Max Shard Latency})$).
2. **Distributed Pagination Nightmare**: Executing `OFFSET 10000 LIMIT 20` requires fetching 10,020 rows from *every single shard*, merging them in application memory, and discarding 99.9% of the returned data.
3. **Cross-Shard Distributed Transactions**: Updating accounts on two different shards requires the **Two-Phase Commit (2PC)** protocol:
   - Prepare phase + Commit phase.
   - Blocks database locks across network boundaries; vulnerable to coordinator crashes.
   - **Production Standard**: Eliminate cross-shard 2PC by using **Asynchronous Sagas** with Outbox patterns.

---

## 11. Architectural Decision Matrix: Database Scaling Strategies

| Engineering Challenge | Recommended Architectural Pattern | Primary Trade-Off | Canonical Technology |
| :--- | :--- | :--- | :--- |
| **High Read Load (90:10 Ratio)** | Asynchronous Read Replicas | Replication lag & stale reads | AWS Aurora Read Replicas, Pgpool-II |
| **Zero Tolerable RPO (Fintech)** | Semi-Synchronous Replication | Higher commit latency | PostgreSQL `synchronous_commit = remote_write` |
| **Split-Brain Prevention** | Quorum-based Leader Consensus | Requires minimum 3 nodes | Patroni + etcd, Raft |
| **Read-Your-Own-Writes** | Session LSN / Time-Window Pinning | Extra read load on Primary | Custom routing middleware, ProxySQL |
| **Monolithic Table > 10 TB** | Horizontal Sharding via Consistent Hashing | Cross-shard joins eliminated | Vitess (MySQL), Citus (Postgres), CockroachDB |
| **Uneven Shard Distribution** | Virtual Nodes (vnodes) on Hash Ring | Memory overhead of routing table | Apache Cassandra, DynamoDB |

---

## 12. Summary: Core Database Scaling Invariants

1. **Replication scales reads, not writes**: Adding 10 read replicas does not increase your primary write throughput by a single transaction.
2. **Replication lag is inevitable**: Never assume read replicas have up-to-the-millisecond data. Explicitly design for Read-Your-Own-Writes consistency.
3. **Modulo sharding is a fatal trap**: Always use Consistent Hashing with Virtual Nodes to avoid migrating 100% of your database during shard expansion.
4. **Choose your shard key for your queries**: The optimal shard key minimizes cross-shard scatter-gather operations.
5. **Avoid distributed 2PC transactions**: Keep transactions scoped to a single shard, and coordinate cross-shard workflows via asynchronous Sagas.
