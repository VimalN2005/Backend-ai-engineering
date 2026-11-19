# Day 72: Common Mistakes & Antipatterns in Microservices & Saga Architectures

Transitioning from a monolith to distributed microservices introduces complex failure modes. Teams frequently recreate monolithic coupling over network sockets (the "Distributed Monolith") or fail to account for the absence of isolation in distributed Sagas.

Below are **7 critical microservices and Saga antipatterns**, complete with code examples, root cause analyses, and senior production remediations.

---

## 1. The Distributed Monolith: Sharing a Single Database Across Services

### The Mistake
Splitting application business logic into separate deployable containers while pointing them all at the same shared PostgreSQL/MySQL database:

```
+---------------+     +---------------+     +---------------+
| Order Service |     |Billing Service|     | Auth Service  |
+-------+-------+     +-------+-------+     +-------+-------+
        |                     |                     |
        +---------------------+---------------------+
                              |
                              v
                   [(Shared Production DB)]
```

### Why It Fails
This is the single most common microservices mistake. You incur all the operational overhead of microservices (Kubernetes, network latency, distributed tracing) while retaining all the worst flaws of the monolith:
1. **Schema Coupling**: Modifying the `users` or `orders` table breaks 4 other services without warning.
2. **Lock Contention**: A long-running report query in Billing locks table rows needed by the high-throughput Order Service.
3. **Hidden Dependencies**: Bypassing API boundaries by querying another service's private tables directly destroys team autonomy.

### Senior Engineering Fix: Strict Database-per-Service
Enforce that each microservice owns its datastore exclusively. Cross-service data access **must** occur via well-defined REST/gRPC APIs, Kafka event streams, or asynchronous CDC read-replicas.

---

## 2. Neglecting Lack of Isolation in Sagas: The Dirty Read & Lost Update Anomaly

### The Mistake
Assuming a Saga provides full ACID transactional guarantees:

```python
# BROKEN ANTIPATTERN: Assuming isolation during multi-step Saga
# Step 1: Saga A reserves flight seat 12A (sets status to 'RESERVED')
flight_svc.reserve_seat(flight_id="FL-99", seat="12A")

# Meanwhile, Customer B searches for seats...
# Flight service reports seat 12A is unavailable. Customer B books another airline!

# Step 2: Saga A's payment fails. Saga A compensates and releases seat 12A.
flight_svc.release_seat_compensation(flight_id="FL-99", seat="12A")
# Result: Seat 12A flew empty! Airline lost revenue due to a Dirty Read.
```

### Why It Fails
Sagas guarantee **Atomicity**, **Consistency**, and **Durability**, but **lack Isolation (I)**. Intermediate states are immediately visible to concurrent users because local transactions commit at each step.
- **Dirty Read**: Service B reads intermediate uncommitted state that is later rolled back.
- **Lost Update**: Saga 1 updates entity state; Saga 2 concurrently overwrites it without accounting for Saga 1.

### Senior Engineering Fix: Semantic Locking & Pending States
Always model pending states explicitly in your domain:
1. Mark the resource with a semantic lock: `status = 'PENDING_PAYMENT'`.
2. Allow concurrent users to view the item as "Temporarily On Hold", or implement **Commutative Operations** (where operation order does not affect final balance).
3. Set short TTL leases (e.g., 5-minute checkout hold) that automatically expire if compensation fails.

---

## 3. Synchronous REST Cascading Chains (Temporal Coupling)

### The Mistake
Building synchronous deep call graphs across microservices:

```
Client ---> API Gateway ---> Order Service (HTTP)
                                └──> Payment Service (HTTP)
                                       └──> Fraud Service (HTTP)
                                              └──> User Service (HTTP)
```

### Why It Fails
1. **Latency Multiplication**: Total latency is the cumulative sum of all synchronous network hops:
   $$\text{Latency}_{\text{total}} = L_{\text{order}} + L_{\text{payment}} + L_{\text{fraud}} + L_{\text{user}}$$
2. **Availability Collapse**: If each service has 99.5% availability, a chain of 4 synchronous services has:
   $$\text{Availability} = 0.995^4 \approx 98.01\% \quad (\approx 7.2 \text{ hours downtime/month!})$$
3. **Thread Starvation**: A slow response in the Fraud Service causes worker thread pools in Payment and Order services to back up, triggering cascading outages across the entire cluster.

### Senior Engineering Fix: Asynchronous Event-Driven Choreography
Convert non-blocking steps into asynchronous message events via Kafka or RabbitMQ. Use synchronous HTTP/gRPC **only for real-time reads or the initial command ingestion**, and process downstream steps asynchronously.

---

## 4. Non-Idempotent Compensating Transactions (Double Refund Catastrophe)

### The Mistake
Writing compensating transactions without strict idempotency barriers:

```python
# BROKEN ANTIPATTERN: Non-idempotent compensation
def refund_payment(order_id, amount):
    # Network blip occurs after bank deducts money but before response returns to SEC.
    # SEC retries compensation!
    bank_gateway.transfer_funds(to=customer_account, amount=amount)
    # DANGER: Customer gets refunded twice!
```

### Why It Fails
In distributed systems, the network is unreliable. The Saga Orchestrator will **always retry** a compensating transaction if it experiences a timeout or 504.
If `refund_payment` or `release_stock` is not idempotent, retrying the compensation executes duplicate financial refunds or creates phantom inventory.

### Senior Engineering Fix: Idempotency Keys & Database Deduplication
Use a unique idempotency key (e.g., `refund_{order_id}`) stored in a unique constraint table:

```python
# PRODUCTION FIX: Idempotent Compensation
def refund_payment(order_id, amount):
    idempotency_key = f"refund_{order_id}"
    
    # Atomic check-and-insert in database
    if not db.insert_idempotency_token(idempotency_key):
        logger.info("Refund for %s already processed. Skipping duplicate.", order_id)
        return True

    # Call external provider with idempotency key
    return bank_gateway.transfer_funds(
        to=customer_account,
        amount=amount,
        idempotency_key=idempotency_key
    )
```

---

## 5. The "Smart Gateway / God Proxy" Antipattern

### The Mistake
Adding domain business logic, data joins, or pricing calculations directly into the API Gateway:

```python
# BROKEN ANTIPATTERN: Business logic leaking into Gateway
@app.route("/api/checkout", methods=["POST"])
def checkout():
    # Gateway computes VIP discounts and tax rules
    user = call_user_svc()
    discount = 0.20 if user.is_vip else 0.05
    tax = compute_state_tax(user.state)
    # Gateway transforms order lines...
```

### Why It Fails
1. **Violates Separation of Concerns**: The API Gateway team becomes a bottleneck for all product changes.
2. **Coupled Deployments**: Changing a discount calculation requires redeploying the core edge proxy.
3. **Gateway Instability**: Heavy CPU processing or memory allocations inside the gateway degrade ingress routing performance for all tenants.

### Senior Engineering Fix: Thin Gateway with Dedicated BFF
Keep the API Gateway **thin**: limit it strictly to cross-cutting concerns (TLS termination, authentication token validation, rate limiting, and request routing).
If client-specific data transformation or aggregation is needed, deploy a dedicated **Backend-for-Frontend (BFF)** microservice owned by the client product team.

---

## 6. Premature Microservices Decomposition (Splitting before Understanding)

### The Mistake
Breaking a greenfield startup product into 25 microservices on Day 1 because "Netflix and Uber use microservices":

### Why It Fails
At early stages, product requirements and domain models shift rapidly.
- When domain boundaries change in a monolith, refactoring is an in-IDE renaming of packages and method calls ($10$ minutes).
- When domain boundaries change across 25 microservices, you must modify 25 database schemas, 25 REST APIs, 25 CI/CD pipelines, and manage distributed network data migrations ($3$ months).

### Senior Engineering Fix: The Modular Monolith
Start with a **Modular Monolith**:
- Enforce strict package/module boundaries and dependency rules within a single repository and deployable artifact.
- Extract microservices **only** when a specific module requires independent horizontal scaling, distinct security isolation, or is maintained by a dedicated autonomous engineering team.

---

## 7. Triggering Irreversible External Side Effects Before the Pivot Transaction

### The Mistake
Sending non-compensable notifications or triggering irreversible physical actions before the transaction commits:

```python
# BROKEN ANTIPATTERN: Non-compensable action before pivot step
def execute_order():
    # Step 1: Send SMS confirmation
    sms_gateway.send("Your order has been confirmed!")
    
    # Step 2: Attempt Payment
    payment = charge_credit_card() # FAILS! Card expired!
    # Problem: You cannot "un-send" the SMS! Customer is confused and calls support.
```

### Why It Fails
A Saga consists of:
1. **Compensable Steps**: Can be rolled back (e.g., reserve stock, hold credits).
2. **Pivot Step**: The point of no return (e.g., card is charged).
3. **Retriable Steps**: Follow the pivot step and must run forward until completion (e.g., send SMS, print shipping label).

### Senior Engineering Fix: Order Steps by Compensability
Always position non-compensable actions (SMS, emails, external webhooks, physical robot warehouse commands) **strictly AFTER the Pivot Transaction**:

```python
# PRODUCTION FIX: Safe Step Sequencing
# Compensable:
hold_credits()
reserve_stock()

# PIVOT STEP:
charge_card()  # If this fails, release credits and stock cleanly.

# RETRIABLE STEPS (Guaranteed forward progression):
send_confirmation_sms()
queue_warehouse_packing()
```
