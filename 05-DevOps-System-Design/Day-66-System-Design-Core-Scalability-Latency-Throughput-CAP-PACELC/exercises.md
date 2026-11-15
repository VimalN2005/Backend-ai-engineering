# Day 66: System Design Core — Enterprise Practical Exercises

Three enterprise-grade practical challenges covering Conflict-Free Replicated Data Types (CRDTs), high-scale capacity planning for video platforms, and multi-region high-availability SLA design.

---

## Exercise 1: Distributed PN-Counter CRDT under PACELC Constraints

### Scenario & Objective
You are designing a globally distributed metrics counter (e.g. video view counter or live-stream like counter) deployed across 3 global regions: `us-east`, `eu-west`, and `ap-southeast`.
Under the PACELC theorem, this service must adhere to **PA/EL**:
- **Availability during partition (PA)**: Even if the transatlantic cable is severed, users in Europe must be able to like/view videos without blocking.
- **Low latency during normal state (EL)**: Writes must be acknowledged locally in $<1\text{ ms}$ without waiting for cross-oceanic synchronous consensus.
- **Eventual Convergence**: When replicas synchronize via background gossip, the counter value across all regions must mathematically converge to the exact same total with zero lost increments or decrements.

Implement a state-based **PN-Counter (Positive-Negative Counter)** CRDT in Python.

### Requirements
1. Support `increment(region, delta)` and `decrement(region, delta)`.
2. Support `value()` which returns the total global count.
3. Support `merge(other_pncounters)` which performs an idempotent, commutative, and associative join-semilattice merge.
4. Verify that concurrent operations across partitioned regions converge deterministically when merged in any order.

### Verified Production Solution

```python
"""Distributed PN-Counter (Positive-Negative Counter) CRDT Implementation."""

from __future__ import annotations
import copy
from typing import Dict, List


class PNCounter:
    """State-based Conflict-Free Replicated Data Type (PN-Counter).

    Maintains two monotonically increasing vectors:
    - P: vector of increments per region/node
    - N: vector of decrements per region/node
    Value = sum(P) - sum(N)
    """

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        # node_id -> positive increments
        self.P: Dict[str, int] = {node_id: 0}
        # node_id -> negative decrements
        self.N: Dict[str, int] = {node_id: 0}

    def increment(self, delta: int = 1) -> None:
        """Local wire-speed increment (PA/EL: Zero cross-region network latency)."""
        if delta < 0:
            raise ValueError("Delta must be positive. Use decrement() instead.")
        self.P[self.node_id] = self.P.get(self.node_id, 0) + delta

    def decrement(self, delta: int = 1) -> None:
        """Local wire-speed decrement."""
        if delta < 0:
            raise ValueError("Delta must be positive.")
        self.N[self.node_id] = self.N.get(self.node_id, 0) + delta

    def value(self) -> int:
        """Computes current local view of the counter."""
        return sum(self.P.values()) - sum(self.N.values())

    def merge(self, other: PNCounter) -> None:
        """Join-semilattice merge operation: L_new = max(L1, L2).

        Properties:
        - Commutative: merge(A, B) == merge(B, A)
        - Associative: merge(merge(A, B), C) == merge(A, merge(B, C))
        - Idempotent:  merge(A, A) == A
        """
        all_p_nodes = set(self.P.keys()).union(other.P.keys())
        for nid in all_p_nodes:
            self.P[nid] = max(self.P.get(nid, 0), other.P.get(nid, 0))

        all_n_nodes = set(self.N.keys()).union(other.N.keys())
        for nid in all_n_nodes:
            self.N[nid] = max(self.N.get(nid, 0), other.N.get(nid, 0))

    def clone(self) -> PNCounter:
        c = PNCounter(self.node_id)
        c.P = dict(self.P)
        c.N = dict(self.N)
        return c


# Verification Script
if __name__ == "__main__":
    # Initialize 3 regional counters
    us = PNCounter("us-east")
    eu = PNCounter("eu-west")
    ap = PNCounter("ap-southeast")

    # Phase 1: Local writes in parallel
    us.increment(10)   # US: +10
    eu.increment(25)   # EU: +25
    eu.decrement(5)    # EU: -5
    ap.increment(40)   # AP: +40
    ap.decrement(10)   # AP: -10

    # Total expected: 10 + (25 - 5) + (40 - 10) = 60
    assert us.value() == 10
    assert eu.value() == 20
    assert ap.value() == 30

    # Phase 2: Asymmetric gossip (Network Partition Simulation)
    # US synchronizes with EU (AP is isolated)
    us.merge(eu)
    eu.merge(us)
    assert us.value() == 30
    assert eu.value() == 30
    assert ap.value() == 30

    # Phase 3: Partition heals; all nodes merge
    us.merge(ap)
    eu.merge(us)
    ap.merge(eu)

    # All nodes must deterministically converge to 60
    assert us.value() == 60
    assert eu.value() == 60
    assert ap.value() == 60
    print("[SUCCESS] Distributed PN-Counter CRDT verified: 100% convergence across all regions!")
```

---

## Exercise 2: End-to-End Capacity Planning for Global Video Platform

### Scenario & Objective
You are the Lead Systems Architect designing the cloud and storage architecture for a global short-form video streaming platform (e.g. TikTok / YouTube Shorts).

Given Workload Parameters:
- **Daily Active Users (DAU)**: $150,000,000$ ($150\text{M}$).
- **Video Watches per User/Day**: $40$ videos.
- **Video Uploads per User/Day**: $0.05$ videos (1 in 20 users upload 1 video/day).
- **Average Video Payload**: $15\text{ Megabytes}$ (encoded at $1080\text{p}$).
- **Average Video Metadata**: $2\text{ Kilobytes}$ (JSON title, tags, uploader ID).
- **Storage Policy**: 3x replication across availability zones; videos stored for 5 years.
- **CDN Edge Cache Policy**: 80-20 Pareto distribution (top 20% of daily watched videos cached at the edge).
- **Peak Traffic Multiplier**: $2.5\times$ over average.

Calculate:
1. Daily and Peak Write QPS (Uploads) & Read QPS (Watches).
2. Required Network Ingress & Egress Bandwidth (in Gbps).
3. Daily Storage Ingestion and 5-Year Storage Capacity (in Petabytes).
4. Required CDN Edge Cache Storage Capacity (in Terabytes).

### Verified Production Solution

```python
"""High-Scale Capacity Planning Sizing Calculator."""

def calculate_video_platform_capacity():
    dau = 150_000_000
    watches_per_user = 40
    uploads_per_user = 0.05
    video_size_mb = 15.0
    video_size_bytes = video_size_mb * 1024 * 1024
    meta_size_bytes = 2048
    rep_factor = 3
    peak_multiplier = 2.5
    seconds_in_day = 86_400

    # 1. QPS Sizing
    total_daily_uploads = dau * uploads_per_user  # 7.5 million uploads
    total_daily_watches = dau * watches_per_user  # 6.0 billion watches

    avg_upload_qps = total_daily_uploads / seconds_in_day
    peak_upload_qps = avg_upload_qps * peak_multiplier

    avg_watch_qps = total_daily_watches / seconds_in_day
    peak_watch_qps = avg_watch_qps * peak_multiplier

    # 2. Network Bandwidth Sizing
    ingress_bytes_per_sec = avg_upload_qps * (video_size_bytes + meta_size_bytes)
    ingress_gbps = (ingress_bytes_per_sec * 8) / 1e9
    peak_ingress_gbps = ingress_gbps * peak_multiplier

    egress_bytes_per_sec = avg_watch_qps * video_size_bytes
    egress_tbps = (egress_bytes_per_sec * 8) / 1e12  # Terabits per sec!
    peak_egress_tbps = egress_tbps * peak_multiplier

    # 3. Storage Sizing
    daily_raw_storage_tb = (total_daily_uploads * video_size_bytes) / 1e12
    daily_replicated_storage_tb = daily_raw_storage_tb * rep_factor
    five_year_storage_pb = (daily_replicated_storage_tb * 365.25 * 5) / 1e3

    # 4. CDN Edge Working Set (Top 20% of daily watched video volume)
    # Distinct daily watched videos = 7.5M new uploads + active catalog ~ 20M videos
    catalog_distinct_daily_videos = 20_000_000
    cdn_cache_tb = (catalog_distinct_daily_videos * 0.20 * video_size_bytes) / 1e12

    return {
        "Daily Uploads": f"{total_daily_uploads:,.0f}",
        "Daily Watches": f"{total_daily_watches:,.0f}",
        "Average Upload QPS": f"{avg_upload_qps:,.1f}",
        "Peak Upload QPS": f"{peak_upload_qps:,.1f}",
        "Average Watch QPS": f"{avg_watch_qps:,.1f}",
        "Peak Watch QPS": f"{peak_watch_qps:,.1f}",
        "Ingress Bandwidth (Peak)": f"{peak_ingress_gbps:.2f} Gbps",
        "Egress Bandwidth (Average)": f"{egress_tbps:.2f} Tbps",
        "Egress Bandwidth (Peak)": f"{peak_egress_tbps:.2f} Tbps",
        "Daily Replicated Storage": f"{daily_replicated_storage_tb:,.1f} TB/day",
        "5-Year Storage Capacity": f"{five_year_storage_pb:,.1f} Petabytes (PB)",
        "CDN Edge Cache Working Set": f"{cdn_cache_tb:,.1f} Terabytes (TB)"
    }

if __name__ == "__main__":
    report = calculate_video_platform_capacity()
    print("=" * 65)
    print(" 150M DAU VIDEO PLATFORM CAPACITY SIZING REPORT")
    print("=" * 65)
    for k, v in report.items():
        print(f"  • {k:28s}: {v}")
```

#### Output Summary
- **Average Upload QPS**: $86.8\text{ writes/sec}$ (Peak: $217.0\text{ writes/sec}$)
- **Average Watch QPS**: $69,444.4\text{ reads/sec}$ (Peak: $173,611.1\text{ reads/sec}$)
- **Ingress Bandwidth (Peak)**: $27.3\text{ Gbps}$
- **Egress Bandwidth (Average)**: $8.74\text{ Terabits/sec (Tbps)}$ (Peak: $21.85\text{ Tbps}$)
- **Daily Replicated Storage**: $353.9\text{ TB/day}$
- **5-Year Replicated Storage**: $646.3\text{ Petabytes (PB)}$
- **CDN Edge Cache Working Set**: $62.9\text{ Terabytes (TB)}$

---

## Exercise 3: Designing a Resilient "Four Nines" (99.99%) Multi-Region Architecture

### Scenario & Objective
An enterprise fintech payment platform requires an SLA of **99.99% ("Four Nines") availability** (maximum allowable downtime: $\le 52.56\text{ minutes/year}$).
The platform consists of four architectural tiers:
1. **Global DNS / Traffic Router (Route53)**
2. **Ingress Load Balancers (AWS ALB)**
3. **Stateless API Gateway / Application Servers**
4. **Distributed Database Tier**

Compute the composite availability of single-region vs. multi-region configurations and formulate the architectural requirements to hit Four Nines.

### Mathematical Formulation & Solution

#### 1. Single Region Architecture (All Tiers in Series)
Suppose each tier within a single AWS region has standard individual availability:
- $A_{\text{DNS}} = 99.99\%$ ($0.9999$)
- $A_{\text{ALB}} = 99.99\%$ ($0.9999$)
- $A_{\text{API}} = 99.9\%$ ($0.999$)
- $A_{\text{DB}} = 99.95\%$ ($0.9995$) (Single RDS instance with Multi-AZ standby)

Composite Single-Region Availability:

$$A_{\text{single\_region}} = 0.9999 \times 0.9999 \times 0.9990 \times 0.9995 = 0.9983 = \mathbf{99.83\%}$$

- Maximum Downtime per Year: $(1 - 0.9983) \times 365.25 \times 86400 \approx \mathbf{14.9\text{ hours/year}}$!
- **Verdict**: A single region setup **fails the 99.99% SLA by a factor of 17!**

---

#### 2. Multi-Region Active-Active Architecture (Parallel Redundancy)
To hit 99.99%, deploy two identical full-stack regional deployments (`us-east-1` and `us-west-2`) running in parallel behind Route 53 latency-based routing with automated health check failover:

```
                            GLOBAL MULTI-REGION ARCHITECTURE
                                    [Route 53 DNS]
                               (SLA: 100% / 99.9999%)
                                    │             │
                    ┌───────────────┘             └───────────────┐
                    ▼                                             ▼
          REGION 1 (us-east-1)                          REGION 2 (us-west-2)
          ┌────────────────────────┐                    ┌────────────────────────┐
          │ ALB (99.99%)           │                    │ ALB (99.99%)           │
          │ Stateless ECS (99.9%)  │                    │ Stateless ECS (99.9%)  │
          │ CockroachDB (99.99%)   │◄──Raft Consensus──►│ CockroachDB (99.99%)   │
          └────────────────────────┘                    └────────────────────────┘
```

1. **Regional Stack Availability ($A_{\text{stack}}$)**:
   $$A_{\text{stack}} = A_{\text{ALB}} \times A_{\text{API}} \times A_{\text{DB}} = 0.9999 \times 0.999 \times 0.9999 = 0.9988$$
2. **Dual-Region Parallel Availability ($A_{\text{dual\_region}}$)**:
   $$A_{\text{dual\_region}} = 1 - (1 - A_{\text{stack}})^2 = 1 - (1 - 0.9988)^2 = 1 - (0.0012)^2 = 1 - 0.00000144 = \mathbf{0.999998} \quad (\approx 99.9998\%)$$
3. **Composite System Availability (with DNS $99.9999\%$)**:
   $$A_{\text{total}} = A_{\text{DNS}} \times A_{\text{dual\_region}} = 0.999999 \times 0.999998 = \mathbf{99.9997\%} \quad (\text{"Five Nines"!})$$

#### Key Architectural Requirements for Active-Active Multi-Region:
- **Stateless Web Tier**: Sessions stored in distributed Redis clusters or self-contained stateless JWTs.
- **CP Globally Distributed Database**: CockroachDB or Google Spanner utilizing cross-region Raft/Paxos consensus to ensure zero RPO (zero lost committed transactions during an entire AWS region blackout).
- **Sub-30s Route53 Health Check Failover**: Fast 10-second health check intervals with 3 failure counts, triggering automatic DNS failover within $\le 30\text{ seconds}$.
