# Day 75: Capstone Project 3: Enterprise Event-Driven Microservices Backend

A complete production-grade distributed system blueprint synthesizing the entire **Phase 5: DevOps, Infrastructure & System Design** curriculum into an enterprise **Event-Driven E-Commerce Microservices Platform**.

---

## 1. Executive Summary & System Architecture

This capstone project implements an end-to-end, highly resilient, horizontally scalable **Event-Driven Microservices Backend** designed to process millions of transactions per day with zero data loss.

```
+-----------------------------------------------------------------------------------------------------------------+
|                                            ENTERPRISE INGRESS TIER                                              |
|                                                                                                                 |
|   [ Mobile Clients ]               [ Single Page Web Apps ]                 [ Third-Party Webhook Partners ]    |
|            |                                  |                                            |                    |
|            +----------------------------------+--------------------------------------------+                    |
|                                               |                                                                 |
|                                               v (HTTPS / TLS 1.3 Termination)                                   |
|                        +---------------------------------------------+                                          |
|                        |             KONG / ENVOY API GATEWAY        |                                          |
|                        |  - JWT Bearer Token Validation & Claims     |                                          |
|                        |  - Token Bucket Distributed Rate Limiting   |                                          |
|                        |  - Circuit Breaker Fast-Fail (Resilience4j) |                                          |
|                        |  - Dynamic Path Routing & Header Injection  |                                          |
|                        +----------------------+----------------------+                                          |
+-----------------------------------------------|-----------------------------------------------------------------+
                                                |
                                                v (Internal VPC Network / mTLS)
+-----------------------------------------------------------------------------------------------------------------+
|                                       CORE DOMAIN MICROSERVICES (DDD)                                           |
|                                                                                                                 |
|    +----------------------+       +----------------------+       +----------------------+       +--------------+|
|    |    ORDER SERVICE     |       |   PAYMENT SERVICE    |       |  INVENTORY SERVICE   |       | NOTIFICATION ||
|    |                      |       |                      |       |                      |       |   SERVICE    ||
|    |  +----------------+  |       |  +----------------+  |       |  +----------------+  |       |  +---------+ ||
|    |  | Orders DB      |  |       |  | Payments DB    |  |       |  | Inventory DB   |  |       |  | Audit DB| ||
|    |  | (PostgreSQL)   |  |       |  | (PostgreSQL)   |  |       |  | (PostgreSQL)   |  |       |  | (Mongo) | ||
|    |  +----------------+  |       |  +----------------+  |       |  +----------------+  |       |  +---------+ ||
|    |  | Outbox Events  |  |       |  | Outbox Events  |  |       |  | Outbox Events  |  |       +-------+------+|
|    |  +----------------+  |       |  +----------------+  |       |  +----------------+  |               ^       |
|    +-----------+----------+       +-----------+----------+       +-----------+----------+               |       |
|                |                              |                              |                          |       |
+----------------|------------------------------|------------------------------|--------------------------|-------+
                 v                              v                              v                          |
+---------------------------------------------------------------------------------------------------------|-------+
|                                    EVENT-DRIVEN BACKBONE (APACHE KAFKA)                                 |       |
|                                                                                                         |       |
|  +---------------------------------------------------------------------------------------------------+  |       |
|  |                     DEBEZIUM CHANGE DATA CAPTURE (CDC) REPLICATION ENGINE                         |  |       |
|  +---------------------------------------------------------------------------------------------------+  |       |
|                |                              |                              |                          |       |
|                v                              v                              v                          |       |
|  [ Topic: orders.events ]       [ Topic: payments.events ]     [ Topic: inventory.events ]              |       |
|    - 6 Partitions (Key: order_id) - 6 Partitions (Key: tx_id)    - 6 Partitions (Key: sku)              |       |
|    - Retention: 7 Days Compacted   - Retention: 30 Days Delete    - Retention: Compacted                |       |
|                |                              |                              |                          |       |
|                +------------------------------+------------------------------+--------------------------+       |
|                                               |                                                                 |
|                                               v                                                                 |
|                             +-----------------------------------+                                               |
|                             |      SAGA ORCHESTRATION ENGINE    |                                               |
|                             |  - State Machine Coordinator      |                                               |
|                             |  - Compensating Action Trigger    |                                               |
|                             |  - Dead-Letter Topic (DLT) Router |                                               |
|                             +-----------------------------------+                                               |
+-----------------------------------------------------------------------------------------------------------------+
```

---

## 2. Domain-Driven Design (DDD) & Service Boundaries

The platform strictly respects Domain-Driven Design (DDD) principles. Each service is built around an explicit **Bounded Context** with zero shared database tables:

### 1. Order Bounded Context
- **Aggregate Root**: `OrderAggregate` (guards line items, status state transitions, and price totals).
- **Ubiquitous Language**: `DraftOrder`, `PendingPaymentOrder`, `ConfirmedOrder`, `CancelledOrder`.
- **Database**: Dedicated PostgreSQL database (`orders_db`).

### 2. Payment Bounded Context
- **Aggregate Root**: `PaymentTransaction` (manages payment gateway tokens, authorizations, captures, and refunds).
- **Ubiquitous Language**: `AuthorizeFunds`, `CaptureCharge`, `IssueRefund`.
- **Database**: Dedicated PostgreSQL database (`payments_db`).

### 3. Inventory Bounded Context
- **Aggregate Root**: `InventoryItem` (manages available stock, active reservation leases, and warehouse allocations).
- **Ubiquitous Language**: `ReserveStock`, `ReleaseStockReservation`, `DeductPermanentStock`.
- **Database**: Dedicated PostgreSQL database (`inventory_db`).

### 4. Notification Bounded Context
- **Domain Event Consumer**: Read-only projection consumer listening to domain event topics, formatting transactional emails, SMS notifications, and audit logging.
- **Database**: Dedicated MongoDB / DynamoDB store (`notifications_db`).

---

## 3. Database-per-Service & The Strangler Fig Architecture

```
MIGRATION TOPOLOGY: Strangler Fig Pattern
+-----------------------------------------------------------------------------+
| 1. Ingress Proxy intercepts incoming customer traffic                       |
| 2. Routes new orders directly to Greenfield Order Microservice              |
| 3. Debezium CDC captures order events and synchronizes legacy ERP database  |
| 4. Old monolith continues reading synchronized views without modification   |
| 5. Monolith tables dropped once all domain services are fully decomposed    |
+-----------------------------------------------------------------------------+
```
