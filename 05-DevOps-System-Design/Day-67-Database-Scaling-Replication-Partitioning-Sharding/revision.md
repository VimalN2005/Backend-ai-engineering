# Day 67: Database Scaling — 5-Minute Revision & Cheat Sheet

A high-density reference sheet covering database scaling strategies, replication modes, replication lag remedies, consistent hashing mathematics, and split-brain defenses.

---

## 1. Core Architectural Formulas & Tables

### Rebalancing Key Migration Formulas
- **Naive Modulo Sharding**:
  $$\text{Fraction of Keys Moved} = \frac{N}{N + 1} \quad (\text{e.g. } 4 \to 5 \text{ shards} = 80\% \text{ moved})$$
- **Consistent Hashing**:
  $$\text{Fraction of Keys Moved} \approx \frac{1}{N + 1} \quad (\text{e.g. } 4 \to 5 \text{ shards} = 20\% \text{ moved})$$

### Masterless Quorum Consistency
$$W + R > N$$
- $N$ = Replication Factor (e.g. 3)
- $W$ = Write Quorum (e.g. 2)
- $R$ = Read Quorum (e.g. 2)
- Overlapping quorum guarantees reading at least one replica with the latest write.

---

## 2. Replication Modes Comparison Matrix

| Replication Mode | Primary Write ACK Condition | Commit Latency | RPO (Data Loss Risk) | Cluster Availability |
| :--- | :--- | :--- | :--- | :--- |
| **Asynchronous** | Primary disk fsync only | **Lowest** ($\approx 1\text{ms}$) | $\text{RPO} > 0$ (Lag risk) | **Highest** (Replicas can die) |
| **Semi-Synchronous** | Primary fsync + 1 replica RAM | **Medium** ($\approx 4\text{ms}$) | $\mathbf{\text{RPO} = 0}$ (Memory safe) | **High** (Fallback on timeout) |
| **Synchronous** | Primary fsync + all replica fsync | **Highest** ($\approx 15\text{ms}$)| **Strictly $\text{RPO} = 0$** | **Lowest** (1 slow replica halts writes) |

---

## 3. Four Core Solutions to Replication Lag

1. **Time-Window Pinning**: If a user session executed a write in the last $N$ seconds (e.g. 5s), route all subsequent reads from that session to the **Primary**.
2. **LSN / GTID Guarding**: Client sends `X-Required-LSN`. Replicas serve query only if `replica_lsn >= client_lsn`; otherwise routes to Primary.
3. **Monotonic Reads (Session Stickiness)**: Pin a user's reads to a specific replica to prevent observing time travelling backwards.
4. **Local Entity Colocation**: Keep related data on the same shard to avoid cross-shard lag and distributed joins.

---

## 4. Database Sharding Cheat Sheet

```text
┌──────────────────────┬────────────────────────────────────────────────────────┐
│ Shard Key Property   │ Architectural Requirement                              │
├──────────────────────┼────────────────────────────────────────────────────────┤
│ High Cardinality     │ Thousands of unique values (UUID, user_id, tenant_id)  │
│ Uniform Distribution │ Prevents hotspots (NEVER use timestamp or auto-inc ID) │
│ Query Alignment      │ Most critical SELECT queries filter by shard key       │
│ Virtual Nodes (vnode)│ Assign 100-256 vnodes per physical node on hash ring   │
└──────────────────────┴────────────────────────────────────────────────────────┘
```

---

## 5. Rapid-Fire Flashcards (10 Q&A)

1. **Do read replicas scale write throughput?**  
   *Answer*: No. All writes must execute on the Primary; replicas only scale read throughput.

2. **What is Replication Lag?**  
   *Answer*: The time or transaction gap (LSN difference) between the Primary committing a write and a replica applying it locally.

3. **What is the "Read-Your-Own-Writes" anomaly?**  
   *Answer*: When a user updates data and immediately reads from a lagging replica, observing their pre-updated stale data.

4. **Why is naive modulo sharding ($hash(k) \pmod N$) disastrous during scaling?**  
   *Answer*: Expanding from $N$ to $N+1$ shards forces $\frac{N}{N+1}$ of all keys (e.g. 80%) to move across the network.

5. **How does Consistent Hashing minimize data migration?**  
   *Answer*: Keys are routed clockwise on a circular ring; adding a node only claims keys from its adjacent neighbor ($\approx 1/N$).

6. **What problem do Virtual Nodes (vnodes) solve on a hash ring?**  
   *Answer*: They eliminate non-uniform key clustering, ensuring an even distribution of keys (variance $< 5\%$) across physical servers.

7. **Why should you never use `created_at` timestamp as a primary shard key?**  
   *Answer*: It directs 100% of sequential write traffic into the single current shard, causing severe hotspotting.

8. **What is Split-Brain in a database cluster?**  
   *Answer*: When a network partition causes two nodes to both believe they are Primary, accepting conflicting writes and corrupting the WAL.

9. **What is STONITH?**  
   *Answer*: "Shoot The Other Node In The Head" — forcefully cutting power or network access to an old primary before promoting a standby.

10. **Why is Two-Phase Commit (2PC) avoided in high-scale microservices?**  
    *Answer*: It holds row locks across slow network round-trips; if the coordinator crashes, locks block the database indefinitely. Sagas are preferred.
