# Day 66: System Design Core: Scalability, Latency vs Throughput, CAP & PACELC

An exhaustive architectural foundation for Principal, Staff, and Senior Distributed Systems Engineers: vertical vs. horizontal scaling, latency vs. throughput trade-offs, Little's Law, Universal Scalability Law (USL), the Tail-at-Scale problem, High Availability SLAs, Brewer's CAP Theorem, Abadi's PACELC Theorem, distributed consistency models, and back-of-the-envelope capacity planning.

---

## 1. Scalability Dimensions: Vertical (Scale-Up) vs. Horizontal (Scale-Out)

**Scalability** is the measurable capability of a system to handle linearly growing workloads by proportionally provisioning additional computing resources without degrading quality of service (QoS).

```
                      VERTICAL VS. HORIZONTAL SCALING
+─────────────────────────────────────────+─────────────────────────────────────────+
|      VERTICAL SCALING (SCALE-UP)        |      HORIZONTAL SCALING (SCALE-OUT)     |
+─────────────────────────────────────────+─────────────────────────────────────────+
|                                         |                                         |
|      ┌───────────────────────────┐      |      ┌─────┐   ┌─────┐   ┌─────┐        |
|      │  GIANT SINGLE MACHINE     │      |      │Node1│   │Node2│   │Node3│        |
|      │  - 128 vCPUs              │      |      └─────┘   └─────┘   └─────┘        |
|      │  - 4,096 GB RAM           │      |         ▲         ▲         ▲           |
|      │  - 100 Gbps NIC           │      |         └─────────┼─────────┘           |
|      │  - 8x NVMe RAID           │      |                   │ Load Balancer       |
|      └───────────────────────────┘      |                   ▼                     |
|                                         |              ┌─────────┐                |
|                                         |              │ Clients │                |
|                                         |              └─────────┘                |
+─────────────────────────────────────────+─────────────────────────────────────────+
| • Hard physical limits (socket/DIMM)    | • Virtually limitless linear scaling   |
| • Exponential cost curve ($O(N^2)$)     | • Linear commodity cost curve ($O(N)$) |
| • Single Point of Failure (SPOF)        | • High Fault Tolerance & Redundancy    |
| • Requires maintenance downtime         | • Rolling zero-downtime upgrades       |
| • Simple: Zero distributed state bugs   | • Complex: Network partitions, latency |
+─────────────────────────────────────────+─────────────────────────────────────────+
```

### The Cost Asymmetry of Vertical Scaling
While replacing an `m5.large` (2 vCPUs, 8 GB RAM) with an `m5.4xlarge` (16 vCPUs, 64 GB RAM) is trivial, hardware costs scale exponentially beyond commodity tiers. A server with 448 vCPUs and 24 TB RAM (such as AWS `u-24tb1.112xlarge`) costs upwards of \$200,000 per year, yet remains a single point of failure (SPOF).

### The Golden Rule of Modern System Design
> **"Scale the stateless application tier horizontally behind load balancers; partition and replicate the stateful data tier horizontally using sharding, read replicas, and distributed consensus."**

---

## 2. Latency vs. Throughput: The Core Trade-off

System performance is defined by two fundamental, interdependent metrics:

1. **Latency ($L$ or $W$)**: The time required to process a single unit of work from submission to completion. Expressed in milliseconds ($\text{ms}$) or microseconds ($\mu\text{s}$).
2. **Throughput ($X$ or $\lambda$)**: The rate at which the system delivers completed units of work per unit of time. Expressed in Requests Per Second (RPS), Queries Per Second (QPS), or Transactions Per Second (TPS).

```
                            LATENCY VS. THROUGHPUT TRADEOFF
Latency
  ▲
  │                                                      / Saturated Knee Point
  │                                                     /  (Queueing Delay Explosions)
  │                                                    /
  │                                                   /
  │                                                  /
  │                                           ______/
  │                                     _____/
  │  Optimal Linear Range         _____/
  │  ────────────────────────────/
  └─────────────────────────────────────────────────────────────► Throughput (QPS)
```

### The Latency Breakdown Equation
End-to-end request latency is an additive sum:

$$L_{\text{total}} = L_{\text{propagation}} + L_{\text{transmission}} + L_{\text{queueing}} + L_{\text{processing}}$$

- **Propagation Delay**: Finite speed of light in optical fiber ($\approx 5\,\mu\text{s}/\text{km}$). A round trip from New York to London cannot physically take less than $\approx 60\,\text{ms}$.
- **Transmission Delay**: Time to push packet bits onto the wire ($D_{\text{bytes}} / \text{Bandwidth}_{\text{bps}}$).
- **Queueing Delay**: Time requests spend waiting in kernel socket buffers, worker task queues, and database connection pools when arrival rate exceeds service capacity.
- **Processing Delay**: Actual CPU computation, memory cache accesses, disk I/O, and database execution.

---

## 3. Little's Law & The Universal Scalability Law (USL)

### 3.1 Little's Law
In queueing theory, Little's Law states that under steady-state conditions:

$$L = \lambda \times W$$

Where:
- $L$ = Average number of concurrent requests in the system (Concurrency / Queue Depth).
- $\lambda$ = Arrival rate / Throughput (Requests per Second).
- $W$ = Average response time / Latency per request (Seconds).

#### Practical Capacity Sizing Example:
If an API gateway receives $\lambda = 5,000\text{ QPS}$ and the downstream database query latency is $W = 40\text{ ms} = 0.040\text{ s}$:

$$L = 5,000 \times 0.040 = 200 \text{ concurrent in-flight requests}$$

Therefore, your backend connection pool must maintain at least **200 active connections** to prevent queueing delay explosions!

---

### 3.2 Amdahl's Law vs. The Universal Scalability Law (USL)

#### Amdahl's Law
Amdahl's Law models speedup when adding $N$ processors, constrained by the fraction of work that is strictly sequential ($\sigma$):

$$S(N) = \frac{N}{1 + \sigma(N - 1)}$$

Amdahl's law predicts that as $N \to \infty$, throughput asymptotically approaches $1 / \sigma$.

#### Neil Gunther's Universal Scalability Law (USL)
In distributed systems, adding nodes does not just encounter serial bottlenecks—nodes must also communicate to synchronize state (cache invalidation, 2PC commits, Raft heartbeats, gossip rounds). USL introduces a **coherence penalty ($\kappa$)**:

$$X(N) = \frac{\gamma N}{1 + \sigma(N - 1) + \kappa N(N - 1)}$$

Where:
- $\sigma$ = **Contention penalty** (serialization, mutex locks, single-writer queues).
- $\kappa$ = **Coherence penalty** (cross-node crosstalk, distributed cache ping-pong, 2-phase commits).
- $\gamma$ = Processing capacity of a single isolated node.

```
                         UNIVERSAL SCALABILITY LAW (USL)
Speedup
  ▲
  │                   Amdahl's Law (Linear then Asymptotic Limit 1/σ)
  │                  . - - - - - - - - - - - - - - - - - - - - - - - - -
  │                .
  │               /  USL Retrograde Effect (Coherence Cost κ dominates)
  │              / \
  │             /   \  Peak Throughput N*
  │            /     \
  │           /       \  Adding nodes SLOWS DOWN the system!
  │          /         \
  └─────────/───────────\─────────────────────────────────────────────► Nodes (N)
```

The optimal cluster size $N^*$ before retrospective throughput degradation is:

$$N^* = \sqrt{\frac{1 - \sigma}{\kappa}}$$

---

## 4. Percentile Latency & The "Tail at Scale" Amplification

Averages ($p50$ or arithmetic mean) hide catastrophic production failures. A system where 95% of users experience $10\text{ ms}$ latency while 5% suffer $10,000\text{ ms}$ timeouts has a misleading mean of $509\text{ ms}$.

Production SLAs are measured in **Percentiles**:
- **p50 (Median)**: 50% of requests are faster than this value.
- **p90 / p95**: Standard user experience under mild load.
- **p99 (99th percentile)**: Worst 1 in 100 requests (indicates GC pauses, lock contention, database query plan degradation).
- **p99.9**: The experience of your highest-value power users who generate the most data and queries.

### The Fanout Tail Latency Multiplier
In a microservice or distributed search architecture where an API gateway fans out a single client request to $M$ backend microservices in parallel, the total request latency is determined by the **slowest service**:

$$P(\text{Slow Request}) = 1 - (1 - p)^M$$

Where $p$ is the probability of a single service experiencing tail latency.

```
             FANOUT TAIL LATENCY MULTIPLICATION (p99 = 1% SLOW)
+──────────────────────+──────────────────────────────────────────────────+
| Fanout Services (M)  | Probability User Experiences Slow p99 Response   |
+──────────────────────+──────────────────────────────────────────────────+
| 1 backend call       | 1.0%                                             |
| 10 backend calls     | 1 - (0.99)^10  =  9.6%                          |
| 50 backend calls     | 1 - (0.99)^50  = 39.5%                          |
| 100 backend calls    | 1 - (0.99)^100 = 63.4%                          |
| 200 backend calls    | 1 - (0.99)^200 = 86.6%                          |
+──────────────────────+──────────────────────────────────────────────────+
```

> **Takeaway**: When a page load touches 100 microservices, even if every microservice has a 99% success/fast rate, **nearly two out of three users (63.4%) experience the worst-case tail latency!**

---

## 5. Availability & Reliability: The Math of "Nines"

### 5.1 Formal Definitions
- **Reliability ($R(t)$)**: Probability that a system functions without failure throughout a continuous time interval $t$.
- **Availability ($A$)**: Percentage of time a system remains operational and capable of fulfilling client requests over a given period.

$$A = \frac{\text{MTBF}}{\text{MTBF} + \text{MTTR}} = \frac{\text{Uptime}}{\text{Uptime} + \text{Downtime}}$$

Where:
- **MTBF**: Mean Time Between Failures.
- **MTTR**: Mean Time To Repair (detection + failover + restoration).

### 5.2 The Downtime Table

| Availability | SLA Name | Max Downtime / Year | Max Downtime / Month | Max Downtime / Day |
| :--- | :--- | :--- | :--- | :--- |
| **99%** | "Two Nines" | 3.65 days | 7.30 hours | 14.4 minutes |
| **99.9%** | "Three Nines" | 8.76 hours | 43.8 minutes | 1.44 minutes |
| **99.99%** | "Four Nines" | 52.56 minutes | 4.38 minutes | 8.64 seconds |
| **99.999%** | "Five Nines" | **5.26 minutes** | **25.9 seconds** | **0.86 seconds** |
| **99.9999%** | "Six Nines" | 31.54 seconds | 2.59 seconds | 0.086 seconds |

### 5.3 Composite System Availability

#### 1. Components in Series (Failure of ANY component breaks the system):
$$A_{\text{series}} = A_1 \times A_2 \times A_3 \times \dots \times A_n$$

Example: If API Gateway ($99.9\%$), Auth Service ($99.9\%$), and Database ($99.9\%$) are in series:

$$A_{\text{total}} = 0.999 \times 0.999 \times 0.999 = 0.997 = 99.7\% \quad (\approx 26.3 \text{ hours downtime/year!})$$

#### 2. Components in Parallel (Redundant failover / Active-Active):
$$A_{\text{parallel}} = 1 - (1 - A_1) \times (1 - A_2) \times \dots \times (1 - A_n)$$

Example: Two redundant database replicas with independent $99.0\%$ availability:

$$A_{\text{total}} = 1 - (1 - 0.99) \times (1 - 0.99) = 1 - (0.01)^2 = 0.9999 = 99.99\% \quad (\text{Four Nines!})$$

---

## 6. Brewer's CAP Theorem: The Trilemma Explained

Formulated by Eric Brewer (2000) and formally proven by Seth Gilbert and Nancy Lynch (2002), the **CAP Theorem** dictates the fundamental trade-offs in distributed data storage.

```
                           THE CAP THEOREM TRILEMMA
                                     ▲
                                    / \
                                   /   \
                                  /  C  \
                                 /       \
                                /         \
                   CP SYSTEMS  /           \  CA SYSTEMS
                  (Spanner,   /             \ (Single RDBMS,
                  HBase, ZK) /               \ Non-partitioned)
                            /                 \
                           /                   \
                          /                     \
                         /                       \
                        /                         \
                       /       NETWORK             \
                      /       PARTITION             \
                     /        TOLERANCE              \
                    /            (P)                  \
                   /                                   \
                  ▼─────────────────────────────────────▼
                 C                                       A
            Consistency                             Availability
                                AP SYSTEMS
                            (Cassandra, DynamoDB,
                                 Couchbase)
```

### 6.1 The Three Properties
1. **Consistency (C)**: Specifically **Linearizability**. Every read operation must return the value of the most recent write or return an explicit error. All nodes see identical data at the exact same physical moment.
2. **Availability (A)**: Every non-failing node must return a non-error response for every received request (no timeouts or HTTP 500s allowed). Note: It does **not** guarantee the returned data is fresh.
3. **Partition Tolerance (P)**: The system continues to operate despite arbitrary dropped, duplicated, or delayed network packets between nodes.

### 6.2 Why "Pick Any Two" is a Fallacy
In real-world networks, **network partitions are an inescapable physical reality**. Fiber lines get cut, switches crash, network cards experience packet drops, and garbage collection (GC) pauses freeze processes for seconds.

Therefore, you **cannot choose "CA"** in a distributed system:
- If a network partition occurs ($P$), you are forced to make a binary decision:
  - **Choose CP**: Reject writes or delay reads on the isolated partition to guarantee linearizable consistency. (sacrifices Availability).
  - **Choose AP**: Allow isolated nodes to process reads and writes to remain 100% available. When the partition heals, data will be diverged/inconsistent. (sacrifices Consistency).

---

## 7. Abadi's PACELC Theorem: Beyond Network Partitions

While the CAP theorem describes system behavior *during a rare network partition*, it says **nothing about normal operational behavior** (which represents 99.9% of a system's lifespan).

In 2012, Daniel Abadi formulated the **PACELC Theorem**:

$$\textbf{If P} \implies [\textbf{A} \lor \textbf{C}], \quad \textbf{Else} \implies [\textbf{L} \lor \textbf{C}]$$

```
                               PACELC THEOREM
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|                           Is there a PARTITION (P)?                         |
|                                     │                                       |
|                     ┌───────────────┴───────────────┐                       |
|                     │ YES                           │ NO (Normal State)     |
|                     ▼                               ▼                       |
|         Choose AVAILABILITY (A)             Choose LATENCY (L)              |
|                   OR                                OR                      |
|         Choose CONSISTENCY (C)              Choose CONSISTENCY (C)          |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### 7.1 The Four PACELC Archetypes

| Archetype | Partition State | Normal State | Canonical Databases | Production Philosophy |
| :--- | :--- | :--- | :--- | :--- |
| **PC/EC** | **C**onsistency | **C**onsistency | **Google Spanner, CockroachDB, etcd, ZooKeeper** | Never compromise data correctness. Uses Paxos/Raft synchronous replication. Accepts higher write latency. |
| **PA/EL** | **A**vailability | **L**atency | **Amazon DynamoDB, Apache Cassandra, Couchbase** | Prioritize wire-speed low latency and 100% availability. Uses asynchronous replication and eventual consistency. |
| **PC/EL** | **C**onsistency | **L**atency | **MongoDB (with Primary writes & Secondary reads)** | During partitions, writes to minority partition fail (CP). Under normal state, reads from secondaries are low-latency but potentially stale. |
| **PA/EC** | **A**vailability | **C**onsistency | **VoltDB / H-Store, Megastore** | Extremely rare: guarantees linearizable consistency under normal state, but switches to AP under partition. |

---

## 8. Distributed Consistency Models Spectrum

Consistency is not binary; it spans a continuum from strict mathematical synchronization to eventual convergence:

```
                      DISTRICTED CONSISTENCY SPECTRUM
Strongest                                                               Weakest
Linearizability ──► Sequential ──► Causal ──► Read-Your-Writes ──► Eventual
(External Clock)    (Logical Clock) (Cause-Effect) (Session Sticky)   (Gossip/CRDT)
```

1. **Linearizability (Strict Consistency)**:
   - Global real-time ordering. If client A writes value $v_1$ at physical time $t_1$, any client reading at physical time $t_2 > t_1$ MUST see $v_1$.
   - Requires synchronized clocks (Google TrueTime with atomic clocks & GPS) or Raft/Paxos consensus.
2. **Sequential Consistency (Lamport, 1979)**:
   - Operations take effect in some sequential order that is identical across all nodes, and operations of each individual process appear in program order.
3. **Causal Consistency**:
   - Operations that are causally related must be seen by every node in the same order. Concurrent operations that are not causally related may be seen in different orders.
4. **Read-After-Write (Monotonic Read) Consistency**:
   - A user who updates their profile immediately reads their own updated data, even if other users across the globe see stale data for several seconds.
5. **Eventual Consistency**:
   - If no further mutations occur, all replicas will eventually converge to the same value. Convergence is achieved via **Vector Clocks**, **Conflict-Free Replicated Data Types (CRDTs)**, or **Last-Write-Wins (LWW)**.

---

## 9. Back-of-the-Envelope Capacity Planning

Capacity estimation is the primary quantitative tool used by Principal Architects to size systems before writing a single line of code.

### 9.1 Powers of Two Reference

| Power of 2 | Exact Bytes | Approximation | Term |
| :--- | :--- | :--- | :--- |
| $2^{10}$ | 1,024 | $\approx 1,000$ ($10^3$) | 1 Kilobyte (KB) |
| $2^{20}$ | 1,048,576 | $\approx 1,000,000$ ($10^6$) | 1 Megabyte (MB) |
| $2^{30}$ | 1,073,741,824 | $\approx 1,000,000,000$ ($10^9$) | 1 Gigabyte (GB) |
| $2^{40}$ | 1,099,511,627,776 | $\approx 1,000,000,000,000$ ($10^{12}$) | 1 Terabyte (TB) |
| $2^{50}$ | 1,125,899,906,842,624 | $\approx 10^{15}$ | 1 Petabyte (PB) |

### 9.2 Latency Numbers Every Systems Engineer Must Know

```
L1 Cache Reference:                   0.5 ns
Branch Mispredict:                    5.0 ns
L2 Cache Reference:                   7.0 ns
Mutex Lock / Unlock:                 25.0 ns
Main Memory (RAM) Access:           100.0 ns  (0.1 µs)
Compress 1KB with Zstandard:      2,000.0 ns  (2.0 µs)
Read 1MB sequentially from NVMe: 10,000.0 ns  (10.0 µs)
Read 1MB sequentially from SSD: 250,000.0 ns  (250.0 µs)
Datacenter LAN Round Trip:      500,000.0 ns  (0.5 ms)
Read 1MB sequentially from HDD: 2,000,000.0 ns (2.0 ms)
Cross-Continent RTT (NYC to SF): 40,000,000.0 ns (40.0 ms)
Transatlantic Cable (NYC to LON): 60,000,000.0 ns (60.0 ms)
```

---

## 10. Complete Capacity Planning Framework (Example: Global Social Feed)

Assume we are designing the backend infrastructure for a global social platform:
- **Daily Active Users (DAU)**: $100,000,000$ ($100\text{M}$).
- **Posts per user/day**: $2$ posts.
- **Feed views per user/day**: $20$ read views.
- **Average post payload**: $500\text{ bytes}$ text metadata + $200\text{ KB}$ image (assume 20% of posts have images).

### Step 1: Throughput (QPS) Estimation
Seconds in a day $\approx 86,400 \approx 100,000\text{ seconds}$ (conservative rule of thumb for engineering approximations).

$$\text{Write QPS} = \frac{100\text{M} \times 2}{86,400} \approx \frac{200,000,000}{86,400} \approx 2,315 \text{ writes/sec}$$

$$\text{Peak Write QPS (factor of 2)} = 2,315 \times 2 \approx 4,630 \text{ writes/sec}$$

$$\text{Read QPS} = \frac{100\text{M} \times 20}{86,400} \approx \frac{2,000,000,000}{86,400} \approx 23,150 \text{ reads/sec}$$

$$\text{Peak Read QPS (factor of 2)} = 23,150 \times 2 \approx 46,300 \text{ reads/sec}$$

### Step 2: Storage Sizing (Per Day & Over 5 Years)
- **Text Storage / Day**: $200\text{M posts} \times 500\text{ bytes} = 100\text{ GB/day}$.
- **Media Storage / Day**: $200\text{M posts} \times 20\% \times 200\text{ KB} = 40\text{M} \times 200\text{ KB} = 8,000\text{ GB} = 8\text{ TB/day}$.
- **Total Storage / Day** $\approx 8.1\text{ TB/day}$.

$$\text{5-Year Storage} = 8.1\text{ TB/day} \times 365 \times 5 \approx 14,780\text{ TB} \approx 14.8\text{ Petabytes (PB)}$$

### Step 3: Network Bandwidth Estimation
- **Ingress Bandwidth**:
  $$\frac{8.1\text{ TB/day} \times 8\text{ bits}}{86,400\text{ s}} \approx \frac{64.8\text{ Terabits}}{86,400\text{ s}} \approx 750\text{ Mbps}$$
- **Egress Bandwidth (serving feeds)**:
  Assume feed page returns 10 posts ($10 \times (500\text{B} + 0.2 \times 200\text{KB}) = 405\text{ KB}$):
  $$\text{Egress Bandwidth} = 23,150\text{ reads/sec} \times 405\text{ KB} \times 8\text{ bits} \approx 75\text{ Gbps!}$$

---

## 11. Architectural Decision Matrix: CAP/PACELC System Selection

| Use Case | Required Invariant | Selected Trade-off | Recommended Architecture |
| :--- | :--- | :--- | :--- |
| **Financial Ledger / Banking** | Zero double-spend, linearizable account balances | **CP / PC/EC** | CockroachDB, Google Spanner, PostgreSQL (Primary) |
| **Global E-Commerce Shopping Cart**| Cart must never drop items; accept writes anywhere | **AP / PA/EL** | Amazon DynamoDB, Cassandra with CRDTs |
| **Real-Time IoT Sensor Telemetry**| Millions of writes/sec; losing 1 sample is tolerable | **AP / PA/EL** | InfluxDB, Apache Cassandra, Kafka |
| **Distributed Leader Election & Config**| Exactly one primary master allowed at all times | **CP / PC/EC** | etcd, Apache ZooKeeper, Consul (Raft consensus) |
| **Content Discovery / Social Feed** | Millisecond response times; slight staleness fine | **AP / PA/EL** | Redis Cache + Cassandra/Scannable NoSQL |

---

## 12. Summary: Core System Design Tenets

1. **Hardware is cheap, but outages are expensive**: Scale horizontally on commodity infrastructure rather than relying on hyper-scale single bare-metal machines.
2. **Beware the tail latency trap**: In parallel microservice fanouts, the $99^{\text{th}}$ percentile of your backends becomes the median experience of your users.
3. **Little's Law is invariant**: You cannot cheat queue depth. If throughput increases or downstream queries slow down, concurrency MUST expand or requests will drop.
4. **Network partitions are non-negotiable**: You can never choose "CA" in distributed architectures. You must explicitly design for CP (Consistency) or AP (Availability).
5. **PACELC governs daily life**: Even when the network is healthy, you face an unrelenting choice between lower latency ($L$) and strict consistency ($C$).
