# Day 69: Load Balancing & Autoscaling — 5-Minute Revision & Cheat Sheet

A high-density reference sheet covering L4/L7 load balancing, balancing algorithms, Kubernetes HPA mathematics, flapping defense, and KEDA event-driven autoscaling.

---

## 1. Core Mathematical Formulas Cheat Sheet

### Kubernetes HPA Scaling Formula
$$\text{desiredReplicas} = \left\lceil \text{currentReplicas} \times \left( \frac{\text{currentMetricValue}}{\text{desiredMetricValue}} \right) \right\rceil$$
- **10% Tolerance Band (No-Op Guard)**:
  $$\text{If } \left| 1.0 - \frac{\text{currentMetricValue}}{\text{desiredMetricValue}} \right| \le 0.10 \implies \text{No Scaling Action Taken}$$

### KEDA Event-Driven Queue Sizing
$$\text{desiredReplicas} = \left\lceil \frac{\text{queueDepth}}{\text{targetMessagesPerPod}} \right\rceil$$

### Power of Two Random Choices (P2C)
$$\text{Max Server Load} = \frac{\ln \ln N}{\ln 2} + O(1) \quad (\text{Exponential drop from } O(\ln N / \ln \ln N))$$

---

## 2. Layer 4 vs. Layer 7 Comparison Matrix

| Dimension | Layer 4 (Transport / NLB) | Layer 7 (Application / ALB / Nginx) |
| :--- | :--- | :--- |
| **OSI Layer** | Layer 4 (TCP / UDP) | Layer 7 (HTTP, HTTPS, gRPC, WebSocket) |
| **Inspection Scope** | IP:Port 4-tuple only | Headers, Cookies, URL Paths, JSON Body |
| **TLS Termination** | Pass-through (no decryption) | Full TLS 1.3 termination & hardware offload |
| **Direct Server Return (DSR)**| Supported (Outbound bypasses LB) | Not Supported (All traffic traverses proxy) |
| **Throughput & Latency** | $10\text{M+}$ PPS (Microseconds) | $50\text{k} - 200\text{k}$ RPS (Milliseconds) |
| **Resource Overhead** | Negligible (stateless packet rewrite)| High (RAM connection buffers, TLS state) |

---

## 3. Load Balancing Algorithms Summary

```text
┌───────────────────────┬────────────────────────────────────────────────────────┐
│ Algorithm             │ Best Production Use Case                               │
├───────────────────────┼────────────────────────────────────────────────────────┤
│ Round-Robin           │ Symmetric hardware with identical request durations    │
│ Weighted Round-Robin  │ Heterogeneous hardware capacities (CPU cores / RAM)    │
│ Least Connections     │ Long-lived sessions, WebSockets, variable-duration SQL │
│ Power of 2 (P2C)      │ Large clusters (100+ nodes); avoids thundering herd    │
│ IP Hash               │ Stateful sessions (Beware of corporate NAT hotspots!)  │
└───────────────────────┴────────────────────────────────────────────────────────┘
```

---

## 4. Rapid-Fire Flashcards (10 Q&A)

1. **What is Direct Server Return (DSR) in Layer 4 load balancing?**  
   *Answer*: Inbound requests pass through the L4 load balancer, but large outbound responses stream directly from the backend server to the client, bypassing the LB bandwidth bottleneck.

2. **Why does Power of Two Choices (P2C) outperform Least Connections at scale?**  
   *Answer*: P2C takes $O(1)$ time to sample 2 random nodes and avoids the "thundering herd" where multiple proxies concurrently target the single server with the lowest connection count.

3. **What is the official Kubernetes HPA scaling equation?**  
   *Answer*: $\text{desiredReplicas} = \lceil \text{currentReplicas} \times (\text{currentMetric} / \text{targetMetric}) \rceil$.

4. **What is the purpose of the 10% HPA tolerance band?**  
   *Answer*: It prevents small metric fluctuations ($|1 - \text{ratio}| \le 0.10$) from triggering continuous, unnecessary pod scaling actions.

5. **What is Autoscaler Flapping (Thrashing), and how do you eliminate it?**  
   *Answer*: Rapid oscillation between scaling up and scaling down; eliminated by enforcing a 300-second scale-down stabilization window (`stabilizationWindowSeconds: 300`).

6. **Why is scaling background queue consumers on CPU utilization an antipattern?**  
   *Answer*: Workers waiting on I/O or DB remain at low CPU (15%) while millions of messages pile up in the queue; KEDA scales directly on queue depth.

7. **Why should you never set HPA target CPU to 95%?**  
   *Answer*: Kubernetes takes 45–60s to pull images and start new pods; pods hitting 95% crash from OOM before replacement pods become ready. Target 60–70% instead.

8. **What is Connection Draining (Deregistration Delay)?**  
   *Answer*: Stopping new traffic to a terminating backend while allowing in-flight active HTTP requests to complete gracefully before firing SIGKILL.

9. **What does a container `preStop` sleep hook prevent?**  
   *Answer*: It delays container shutdown by 15s to allow ingress controllers and ALBs to remove the pod IP from routing tables before the app stops listening.

10. **What is KEDA Scale-to-Zero?**  
    *Answer*: Scaling a worker Deployment down to 0 replicas when the message queue is empty, eliminating idle cloud compute costs.
