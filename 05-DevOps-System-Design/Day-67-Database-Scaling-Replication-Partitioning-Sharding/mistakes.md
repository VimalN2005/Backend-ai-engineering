# Day 67: Database Scaling — Critical Engineering Antipatterns

A comprehensive deep dive into the 7 most lethal production antipatterns in database scaling, covering replication traps, consistency anomalies, sharding key mistakes, split-brain disasters, and distributed query collapse.

---

## 1. Believing Read Replicas Scale Write Throughput

### The Antipattern
Provisioning additional read replicas (e.g. scaling from 2 to 8 PostgreSQL read replicas on Amazon RDS) when the database is crashing due to write saturation (high write IOPS, row lock contention, WAL serialization bottlenecks).

```
❌ THE REPLICA WRITE FALLACY
Incoming High Write Load (15,000 Writes/sec)
                  │
                  ▼
       ┌─────────────────────┐
       │    PRIMARY (R/W)    │ ◄── 100% CPU & NVMe IOPS Bottleneck!
       └──────────┬──────────┘
                  │ (Must broadcast WAL to 8 Replicas!)
  ┌───────┬───────┼───────┬───────┬───────┬───────┐
  ▼       ▼       ▼       ▼       ▼       ▼       ▼
[Rep 1] [Rep 2] [Rep 3] [Rep 4] [Rep 5] [Rep 6] [Rep 7] ... (Idle Reads)
```

### Failure Mechanism
- In a Primary-Replica architecture, **all write operations (`INSERT`, `UPDATE`, `DELETE`, DDL) must execute on the single Primary instance**.
- Read replicas do not absorb a single write; in fact, the Primary must spend additional CPU and network bandwidth streaming WAL records to each connected replica.
- Adding read replicas to a write-saturated database **worsens the Primary's performance** and increases replication lag.

### Production Remediation
When write capacity is exhausted:
1. **Vertical Partitioning**: Split tables across functional domain databases (e.g., move `audit_logs` or `analytics_events` out of the transactional OLTP database).
2. **Write Batching & Queuing**: Buffer high-frequency writes through Kafka or Redis and execute bulk multi-row inserts (`INSERT INTO ... VALUES (...), (...)`).
3. **Horizontal Sharding**: Partition rows of the table across independent physical database clusters using Consistent Hashing.

---

## 2. Ignoring Replication Lag for Read-After-Write Flows

### The Antipattern
Routing user read traffic to read replicas immediately after a write without tracking the user's write session or transaction Log Sequence Number (LSN).

```python
# ❌ STALE READ ANOMALY CODE
@app.post("/update-profile")
async def update_profile(user_id: int, new_name: str):
    # 1. Writes to PRIMARY
    await db_primary.execute("UPDATE users SET name = :n WHERE id = :id", {"n": new_name, "id": user_id})
    return {"status": "ok"}

@app.get("/profile")
async def get_profile(user_id: int):
    # 2. Immediately reads from REPLICA (Lag = 400ms!)
    user = await db_replica.fetch_one("SELECT * FROM users WHERE id = :id", {"id": user_id})
    return user  # RETURNS OLD NAME!
```

### Failure Mechanism
- Asynchronous replication entails physical lag (network transport delay, disk I/O scheduling, lock contention on replica).
- When a user updates their profile and refreshes the page, their browser issues a `GET` request within milliseconds.
- The read replica has not yet applied the WAL record containing the update. The user sees their old profile data, assumes the system is broken, and repeatedly clicks the save button, spawning duplicate operations and support tickets.

### Production Remediation
Implement **Read-Your-Own-Writes Consistency**:
1. **Time-Window Pinning**: If a client wrote within the last $N$ seconds (e.g. $3\text{s}$), pin all read queries from their session to the **Primary**.
2. **LSN / GTID Guarding**: Return the transaction commit LSN (`pg_current_wal_lsn()`) in an HTTP response header or cookie. On subsequent reads, check if `pg_last_wal_replay_lsn() >= required_lsn`; if not, route to Primary or wait.

---

## 3. Using Naive Modulo Hashing ($hash(key) \pmod N$) for Sharding

### The Antipattern
Mapping database records to shard instances using modulo arithmetic on the shard key:

$$\text{Shard ID} = \text{hash}(\text{user\_id}) \pmod N$$

```
❌ THE MODULO REBALANCING AVALANCHE (N = 4 -> N = 5)
Key 'user_101': hash=101 -> 101 % 4 = Shard 1  ──► 101 % 5 = Shard 1 (Stayed)
Key 'user_102': hash=102 -> 102 % 4 = Shard 2  ──► 102 % 5 = Shard 2 (Stayed)
Key 'user_103': hash=103 -> 103 % 4 = Shard 3  ──► 103 % 5 = Shard 3 (Stayed)
Key 'user_104': hash=104 -> 104 % 4 = Shard 0  ──► 104 % 5 = Shard 4 (MOVED!)
Key 'user_105': hash=105 -> 105 % 4 = Shard 1  ──► 105 % 5 = Shard 0 (MOVED!)
Key 'user_106': hash=106 -> 106 % 4 = Shard 2  ──► 106 % 5 = Shard 1 (MOVED!)
...
Result: 80% of ALL database records must be physically copied over the network!
```

### Failure Mechanism
- When scaling a cluster from $N$ to $N+1$ shards, modulo arithmetic alters the assigned shard index for $\frac{N}{N+1}$ of all keys.
- Scaling from 4 to 5 shards moves **$80\%$ of your multi-terabyte database** across the network simultaneously, triggering severe network saturation, table write locks, and prolonged downtime.

### Production Remediation
Always use **Consistent Hashing with Virtual Nodes (vnodes)** on a 32-bit ring.
- Adding a shard only moves keys belonging to the immediate counter-clockwise segment:
  $$\text{Keys Moved} \approx \frac{K}{N+1} = \frac{1}{5} = \mathbf{20\%}$$
- The remaining $80\%$ of records remain completely untouched on their existing shards.

---

## 4. Selecting a Monotonically Increasing Key as Shard Key

### The Antipattern
Sharding a high-throughput table (e.g. `orders`, `events`, `logs`) using an auto-incrementing integer ID or a timestamp (`created_at`) as the shard key.

```sql
-- ❌ CATASTROPHIC WRITE HOTSPOT SHARDING
CREATE TABLE orders (
    order_id BIGSERIAL,      -- Monotonically increasing!
    created_at TIMESTAMPTZ,  -- Monotonically increasing!
    user_id UUID,
    amount NUMERIC
) PARTITION BY RANGE (created_at);
```

### Failure Mechanism
- At any given second, all current orders have timestamps belonging to the current hour/day partition or IDs belonging to the highest range.
- **100% of incoming write traffic is funneled into a single physical shard node!**
- The other 15 shards in the cluster sit idle with 0% write utilization while the active shard suffers CPU, disk, and lock exhaustion (Hotspotting).

### Production Remediation
1. Shard on a **uniformly distributed, high-cardinality key** with strong entity affinity (e.g., `user_id` or `tenant_id` via Consistent Hashing).
2. If time-range queries are required, adopt **Compound Sharding**:
   $$\text{Shard Key} = (\text{user\_id}, \text{created\_at})$$
   Writes for different users are distributed across all shards, while local time-series queries remain efficient within a user's partition.

---

## 5. Permitting Cross-Shard Two-Phase Commit (2PC) Transactions

### The Antipattern
Designing microservices or database schemas that require atomic transactions spanning multiple physical database shards using distributed Two-Phase Commit (2PC).

```
❌ DISTRIBUTED 2PC LOCK CONGESTION
Coordinator ──Prepare──► [Shard 1 (Debit User A)]   ◄── Holds row lock!
Coordinator ──Prepare──► [Shard 2 (Credit User B)]  ◄── Holds row lock!
   │ (Network delay / packet drop / coordinator pause: 800ms)
   ▼
Rows on Shard 1 and Shard 2 are locked. Incoming queries on User A and B BLOCK!
```

### Failure Mechanism
- Two-Phase Commit requires two network round trips (`Prepare` and `Commit`).
- During the entire prepare-commit window, row-level locks are held across both physical shards.
- If the coordinator crashes or a network partition delays the second phase, locks remain held indefinitely. Database connection pools saturate in seconds, causing system-wide cascading failure.

### Production Remediation
1. **Entity Colocation**: Place related entities on the same shard using shard key affinity (e.g. place a Customer, their Orders, and their Invoices on the same shard via `customer_id`). Transactions inside a single shard execute as fast local ACID transactions.
2. **Asynchronous Sagas**: For cross-shard workflows, replace synchronous 2PC with an **Orchestrated Saga Pattern** using Outbox tables and message queues with compensating transactions.

---

## 6. Failing to Implement Quorum Fencing / STONITH during Failover

### The Antipattern
Configuring an automated failover script that promotes a standby replica to Primary whenever heartbeats from the Primary are missed for 5 seconds, without verifying quorum consensus or fencing the old primary.

```
❌ UNPROTECTED STANDBY PROMOTION (SPLIT-BRAIN)
[Old Primary (P1)] ◄── Isolated by network glitch (Still alive!)
       │
       └── X X X Heartbeat lost for 5s X X X ──► [Standby Replica (P2)]
                                                      │
                                                      ▼ Promoted to NEW Primary!
App Cluster A writes to P1!                App Cluster B writes to P2!
Both databases accept divergent writes. LSNs and WAL timelines permanently diverge!
```

### Failure Mechanism
- A transient network partition or high CPU spike can cause missed heartbeats while the old primary is still running.
- If the standby promotes itself while the old primary is still accepting writes, the cluster enters **Split-Brain**.
- When network connectivity is restored, both nodes have committed conflicting transactions with identical or divergent LSNs. Automatic reconciliation is impossible, requiring manual database restoration and permanent data loss.

### Production Remediation
1. **Consensus-Backed Leader Leases**: Standbys can only promote if a distributed consensus quorum ($\lfloor N/2 \rfloor + 1$) agrees (e.g., **Patroni** using etcd or Consul). The primary must continuously refresh an ephemeral lease; if it loses quorum, it immediately demotes itself to read-only.
2. **STONITH ("Shoot The Other Node In The Head")**: The failover mechanism must forcefully power down or revoke the network interface of the old primary (via IPMI, AWS IAM instance detachment, or routing table drop) before promoting the standby.

---

## 7. Unconstrained Scatter-Gather Queries and Deep Pagination

### The Antipattern
Allowing client API endpoints to execute unindexed or unpartitioned queries across a sharded database, accompanied by deep SQL pagination.

```sql
-- ❌ LETHAL SCATTER-GATHER DEEP PAGINATION
-- Query has no shard key -> Must fan out to ALL 64 shards!
SELECT * FROM audit_logs 
WHERE action = 'LOGIN_FAILED' 
ORDER BY created_at DESC 
LIMIT 20 OFFSET 100000;
```

### Failure Mechanism
- Because the query lacks a shard key, the proxy must dispatch the query in parallel to **every single shard** (64 shards).
- To fulfill `OFFSET 100000 LIMIT 20`, **every individual shard must scan and return 100,020 rows** to the aggregator proxy ($64 \times 100,020 = 6,401,280\text{ rows}$ transferred over the network!).
- The aggregator proxy consumes gigabytes of RAM attempting to merge-sort 6.4 million rows in memory, triggering CPU throttling and out-of-memory (OOM) crashes.

### Production Remediation
1. **Mandate Shard Keys in Filter Predicates**: Enforce that queries against sharded tables include the partition key in the `WHERE` clause.
2. **Keyset / Cursor-Based Pagination**: Replace `OFFSET` with keyset cursor pagination (`WHERE created_at < :last_seen_timestamp ORDER BY created_at DESC LIMIT 20`).
3. **Dedicated Search Indexing**: Route unstructured or cross-shard search queries away from the relational database and into Elasticsearch, OpenSearch, or ClickHouse.

---

## Summary Matrix: Antipatterns vs. Production Standard

| # | Database Scaling Antipattern | Primary Root Cause | Enterprise Production Standard |
|---|---|---|---|
| **1** | Adding read replicas for write spikes | Replicas only scale reads | Vertical partitioning, write batching, and horizontal sharding |
| **2** | Ignoring replication lag on writes | Asynchronous WAL replay delay | Time-window pinning or LSN-guarded read routing |
| **3** | Modulo hashing ($hash(k) \pmod N$) | Rebalance moves $(N-1)/N$ of all keys | Consistent Hashing Ring with Virtual Nodes (moves only $K/N$) |
| **4** | Monotonic / timestamp shard key | Sequential writes hit a single node | High-cardinality uniform key (`user_id`) or compound sharding |
| **5** | Cross-shard Two-Phase Commit (2PC) | Holding row locks across network round-trips | Entity colocation on parent shard key or asynchronous Sagas |
| **6** | Failover without quorum fencing | Standby promotes while old primary runs | Distributed consensus leases (Patroni/etcd) and STONITH fencing |
| **7** | Scatter-gather with deep offset | Full cluster fanout transferring millions of rows | Keyset pagination, shard key enforcement, or OpenSearch OLAP |
