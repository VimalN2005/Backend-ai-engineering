# Senior Backend & AI Systems Interview: Interval Problems

This document details the top 5 high-impact interview questions asked by Tier-1 tech companies (Google, Meta, AWS, Stripe) regarding interval algorithms, sweep-line mechanics, and timeline scheduling systems.

---

### Question 1: How does the Sweep-Line algorithm work, and why is tie-breaking critical when intervals touch?

#### The Question
"Given a continuous timeline of events with start and end boundaries, explain the difference array / sweep-line technique. Why does the comparator tie-breaking rule matter when meeting $A$ ends at time $T$ and meeting $B$ starts at time $T$?"

#### Candidate Answer
The **Sweep-Line (Event Differential)** algorithm transforms a 2D range problem into a 1D discrete event stream. Instead of maintaining contiguous blocks across continuous time:
1. Deconstruct each interval $[s, e)$ into two discrete point events:
   - Arrival event at timestamp $s$ with weight $+1$ (or $+W$ for resources like GPU memory).
   - Departure event at timestamp $e$ with weight $-1$ (or $-W$).
2. Sort all $2N$ events chronologically.
3. Sweep a virtual line from left to right, maintaining a running accumulator (`current_capacity += delta`). The maximum value of `current_capacity` across the sweep is the global peak concurrent resource demand.

#### Tie-Breaking Mechanics
When meeting $A$ has range $[1, 2)$ and meeting $B$ has range $[2, 3)$, both have an event at $T = 2$:
- $A$'s departure: $(2, -1)$
- $B$'s arrival: $(2, +1)$

In half-open intervals $[s, e)$, the instant $T=2$ is exclusive to $A$ and inclusive to $B$. The resource allocated to $A$ is freed before or exactly as $B$ takes over.
- **Correct Ordering**: Departure $(-1)$ processed before Arrival $(+1)$. Running total: $1 - 1 = 0 \to 0 + 1 = 1$. Peak = 1.
- **Incorrect Ordering**: Arrival $(+1)$ processed before Departure $(-1)$. Running total: $1 + 1 = 2 \to 2 - 1 = 1$. Peak = 2 (**Phantom Capacity Spike**).

In Python, sorting tuples `(timestamp, delta)` where departure is $-1$ and arrival is $+1$ naturally guarantees correct tie-breaking because $-1 < +1$.

---

### Question 2: Prove why sorting by End Time is optimal for the Interval Scheduling Maximization Problem (ISMP).

#### The Question
"In the Interval Scheduling Maximization Problem (finding the maximum subset of mutually non-overlapping intervals), why does greedily choosing the interval with the earliest finish time guarantee an optimal solution? Provide a formal or semi-formal proof."

#### Candidate Answer
We prove this using the **"Greedy Stays Ahead"** induction technique.

Let $G = \{g_1, g_2, \dots, g_k\}$ be the sequence of intervals selected by the greedy algorithm (sorted by earliest end time).
Let $O = \{o_1, o_2, \dots, o_m\}$ be any optimal set of intervals (sorted by start/end time).
We must prove that $|G| = |O|$, i.e., $k = m$.

#### Lemma
For every $r \le k$, the finish time of greedy interval $g_r$ is less than or equal to the finish time of optimal interval $o_r$:
$$\text{end}(g_r) \le \text{end}(o_r)$$

#### Proof of Lemma by Induction:
1. **Base Case ($r = 1$)**: Greedy selects $g_1$ as the interval with the absolute earliest finish time among all available intervals in the input. Therefore, $\text{end}(g_1) \le \text{end}(o_1)$ by definition.
2. **Inductive Step**: Assume true for $r-1$, i.e., $\text{end}(g_{r-1}) \le \text{end}(o_{r-1})$.
   - Since $O$ is a valid set of non-overlapping intervals, interval $o_r$ must start after $o_{r-1}$ ends:
     $$\text{start}(o_r) \ge \text{end}(o_{r-1})$$
   - By our inductive hypothesis, $\text{end}(g_{r-1}) \le \text{end}(o_{r-1})$, so:
     $$\text{start}(o_r) \ge \text{end}(g_{r-1})$$
   - This means interval $o_r$ was available and compatible when greedy was selecting its $r$-th interval $g_r$.
   - Since greedy always picks the compatible interval with the earliest finish time:
     $$\text{end}(g_r) \le \text{end}(o_r)$$

#### Conclusion ($k = m$):
Suppose for contradiction that $O$ has more intervals than $G$ ($m > k$).
When greedy terminates after picking $g_k$, the optimal set still has at least one more interval $o_{k+1}$.
Because $o_{k+1}$ is compatible with $o_k$, we know $\text{start}(o_{k+1}) \ge \text{end}(o_k) \ge \text{end}(g_k)$.
Thus, $o_{k+1}$ is also compatible with $g_k$, meaning greedy could have selected at least one more interval.
This contradicts the termination condition of the greedy algorithm. Hence, $k = m$, and greedy is guaranteed optimal.

---

### Question 3: What are the trade-offs between Min-Heap and Sweep-Line for Meeting Rooms II?

#### The Question
"Meeting Rooms II (finding minimum concurrent rooms) can be solved using either a Min-Heap or a Sweep-Line / Two-Pointers algorithm. How do they compare in terms of time, memory access patterns, and real-time streaming capability?"

#### Candidate Answer

| Architectural Dimension | Min-Heap Approach | Sweep-Line / Two-Pointers |
|---|---|---|
| **Sorting Cost** | $O(N \log N)$ to sort intervals by start | $O(N \log N)$ to sort $2N$ event points |
| **Heap Operations** | $N$ pushes and up to $N$ pops: $O(N \log K)$ where $K$ is max concurrent rooms | $0$ heap operations (pure pointer increments) |
| **Space Overhead** | $O(K)$ space (heap only holds active concurrent rooms) | $O(N)$ space (must allocate arrays for $2N$ events) |
| **Memory Locality** | Tree-based heap pointer jumps; more cache misses | Contiguous array linear scan; optimal CPU cache prefetching |
| **Online / Streaming Fit** | **Superior for streaming**: Incoming requests can be processed incrementally against active heap | **Batch-only**: Requires knowing all boundaries upfront to sort global timeline |
| **Room Assignment** | Trivial: Can easily attach metadata (e.g., room IDs, pod allocations) | Harder: Only computes scalar peak count, does not assign discrete rooms |

#### Senior Architectural Recommendation:
- If the goal is **pure batch metrics** (e.g. daily offline telemetry to find peak GPU utilization), use **Sweep-Line / Two-Pointers** because it has minimal constant factor overhead and exploits CPU vectorization.
- If the goal is an **online reservation engine** or requires **discrete room/server ID allocation** (e.g. allocating Room 1 vs Room 2), use the **Dual-Heap Architecture**.

---

### Question 4: How are Interval Algorithms applied to IP CIDR Block and Firewall Rule Optimization?

#### The Question
"In cloud networking (e.g., AWS Security Groups, Kubernetes Calico CNI, or iptables), firewalls have strict hardware TCAM (Ternary Content-Addressable Memory) limits. How do backend systems merge redundant IP CIDR blocks and port ranges?"

#### Candidate Answer
Network addresses and port spans are interval ranges on the integer number line:
1. **IPv4 as 32-bit Integers**: An IPv4 address is an unsigned 32-bit integer (`0` to `4,294,967,295`).
   - A CIDR block `192.168.1.0/24` represents the integer interval $[3,232,235,776,\; 3,232,236,031]$.
   - Port ranges (e.g., `80-443`, `8080-8088`) are directly integer intervals.
2. **Rule Explosion Problem**: Security teams define hundreds of fragmented, overlapping rules across security groups. TCAM memory on network switches is extremely scarce and expensive (often limited to a few thousand rules).
3. **Interval Merge Pipeline**:
   - Parse all CIDR blocks into half-open integer intervals $[s, e)$.
   - Sort intervals by start integer.
   - Run **Merge Intervals**: If $s_{\text{curr}} \le e_{\text{prev}} + 1$ (handling contiguous adjacent subnets), merge them into a single continuous range.
   - Run **CIDR Decomposition**: Convert the merged raw integer ranges back into the minimal power-of-two aligned CIDR prefix blocks.
4. **Impact**: Compresses 50,000 fragmented firewall rules down to 1,200 canonical CIDRs, preventing packet drop and keeping latency under 10 microseconds.

---

### Question 5: When should you transition from static Interval Sorting to dynamic Segment Trees or Interval Trees?

#### The Question
"Sorting intervals takes $O(N \log N)$ time, and querying an overlap takes $O(N)$. When does this architecture break down, and what data structures replace it in production?"

#### Candidate Answer

#### The Breaking Point
Static sorting works when the set of intervals is static or processed in batches. It fails when:
1. **High-Frequency Dynamic Mutations**: New intervals are inserted and deleted continuously in real-time ($O(N)$ re-insertion cost in sorted lists).
2. **Sub-Millisecond Range Point Queries**: Given a point $T$ or a query range $[Q_s, Q_e]$, we need to return all overlapping intervals in $O(\log N + K)$ time rather than scanning all $N$ intervals.

#### The Production Alternatives

1. **Interval Tree (Augmented Red-Black BST)**:
   - Every node stores an interval $[s, e]$ and the subtree attribute $\text{max\_end} = \max(e, \text{left.max\_end}, \text{right.max\_end})$.
   - Insertion & Deletion: $O(\log N)$.
   - Find any overlap with $[q_s, q_e]$: $O(\log N)$.
   - Best for: Dynamic calendars (Google Calendar API), flight scheduling engines.

2. **Segment Tree**:
   - Discretizes the coordinate domain into elementary intervals.
   - Insertion & Range queries: $O(\log U)$ where $U$ is the timeline coordinate universe.
   - Supports range updates via lazy propagation (e.g., "Add 5 GPUs of load to all time points between $t=100$ and $t=500$").
   - Best for: Cloud capacity booking engines with continuous variable capacity subtraction.

3. **PostgreSQL GiST Indexes (`tsrange` / `tstzrange`)**:
   - In production databases, instead of implementing custom trees in memory, use PostgreSQL range types with Generalized Search Tree (GiST) indexes:
     ```sql
     CREATE TABLE reservations (
         id SERIAL PRIMARY KEY,
         room_id INT,
         during TSTZRANGE,
         EXCLUDE USING GIST (room_id WITH =, during WITH &&)
     );
     ```
   - The database guarantees zero double-bookings via kernel-level GiST interval tree indexes with zero race conditions.
