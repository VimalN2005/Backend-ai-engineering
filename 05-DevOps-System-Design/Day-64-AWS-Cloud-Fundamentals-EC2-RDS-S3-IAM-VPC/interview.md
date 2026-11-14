# Day 64: AWS Cloud Fundamentals — Principal/Staff Interview Q&As

Five deep, architectural interview questions with exhaustive answers expected of Senior, Staff, and Principal Cloud Architects & Backend Engineers.

---

## Question 1: Trace the exact packet path and network translation of an HTTP request from a client browser through an AWS VPC to a private backend and database.

### Answer
Understanding the end-to-end packet journey across an AWS VPC demonstrates mastery of cloud networking primitives:

```
                            END-TO-END PACKET ROUTING
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  1. DNS: Client browser resolves `api.mycompany.com` via Route 53           |
|     Returns ALB Anycast public IP addresses.                                |
|     │                                                                       |
|     ▼                                                                       |
|  2. INTERNET GATEWAY (IGW):                                                 |
|     Packet arrives at AWS VPC edge via IGW.                                 |
|     IGW maps public target IP to ALB's private ENI IP (1:1 NAT).            |
|     NACL on Public Subnet evaluates inbound rule (Allow Port 443).          |
|     │                                                                       |
|     ▼                                                                       |
|  3. APPLICATION LOAD BALANCER (ALB) IN PUBLIC SUBNET (10.0.1.50):           |
|     - Terminates TLS handshake using AWS Certificate Manager (ACM) cert.    |
|     - Evaluates ALB Security Group ingress (Port 443 open to 0.0.0.0/0).     |
|     - Inspects HTTP headers, path routing rules (`/api/v1/*`).              |
|     - Opens a NEW TCP connection to target backend in Private Subnet.       |
|     │                                                                       |
|     ▼                                                                       |
|  4. FASTAPI APP ON EC2 / ECS IN PRIVATE SUBNET (10.0.11.20):                |
|     - Route Table uses `local` route (10.0.0.0/16) for inter-subnet transit.|
|     - App Security Group ingress checks: Source == `sg-alb` on Port 8000.   |
|     - Kernel passes packet to Uvicorn worker process.                       |
|     - Application initiates SQL query to PostgreSQL database.               |
|     │                                                                       |
|     ▼                                                                       |
|  5. AMAZON RDS POSTGRESQL IN ISOLATED DATABASE SUBNET (10.0.21.30):         |
|     - Transits via `local` VPC route.                                       |
|     - DB Security Group ingress checks: Source == `sg-app` on Port 5432.    |
|     - Postgres executes query and returns data over established connection. |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## Question 2: Why are Security Groups stateful while Network ACLs are stateless? Explain the underlying connection-tracking engine.

### Answer

### Stateful Security Groups (conntrack)
Security Groups are implemented at the **Elastic Network Interface (ENI)** level using virtualized **Connection Tracking (`conntrack`) tables**:
- When an inbound packet matches an Allow rule (e.g. Inbound TCP Port 443 from `0.0.0.0/0`), the hypervisor creates an entry in its state table tracking the tuple `(Source IP, Source Port, Dest IP, Dest Port, Protocol)`.
- When the backend application generates the return response packet, the hypervisor inspects the state table, recognizes the established session, and **automatically permits the outbound packet**—even if all outbound rules are set to Deny!

### Stateless Network ACLs (NACLs)
Network ACLs operate at the **Subnet Boundary** and do not maintain state tables:
- NACLs inspect each packet in complete isolation without memory of prior requests.
- If you allow inbound traffic on Port 80, the return traffic from your web server uses a high ephemeral port chosen by the client OS (typically ports `1024-65535`).
- Therefore, in NACLs, you **must explicitly create an outbound rule permitting ephemeral ports (`1024-65535`)**; otherwise, return traffic is blocked at the subnet boundary!

---

## Question 3: Contrast RDS Multi-AZ Deployments with Read Replicas across replication mechanism, RPO/RTO metrics, and failover behavior.

### Answer

```
+────────────────────+─────────────────────────────+─────────────────────────────+
| Dimension          | RDS Multi-AZ                | RDS Read Replicas           |
+────────────────────+─────────────────────────────+─────────────────────────────+
| Primary Purpose    | **High Availability & DR**  | **Read Scalability**        |
| Replication Type   | **Synchronous** (EBS level) | **Asynchronous** (PG Stream)|
| Secondary Target   | Standby (Cannot accept reads| Active (Accepts read queries|
| AZ Placement       | Different AZ in same region | Same AZ, cross-AZ, or cross-|
| Recovery Point (RPO| **RPO = 0** (Zero data loss)| RPO > 0 (Replication lag)   |
| Recovery Time (RTO)| **RTO = 60-120 seconds**     | Manual promotion required   |
| Failover Mechanism | **Automatic DNS CNAME swap**| App must update endpoints   |
+────────────────────+─────────────────────────────+─────────────────────────────+
```

### Critical Architectural Distinction
- In **Multi-AZ**, transactions are not acknowledged to the client until written to both the Primary and Standby physical storage volumes. This guarantees **zero data loss**, but introduces a slight write latency overhead (~1–2ms).
- In **Read Replicas**, replication is asynchronous. Under heavy write spikes, replication lag can increase. Directing reads to replicas can result in **eventual consistency reading anomalies** (e.g., reading immediately after write might return stale data).

---

## Question 4: How does AWS IAM evaluate multiple overlapping policies (Identity-based, Resource-based, Permissions Boundaries, SCPs)?

### Answer
AWS evaluates authorization requests using a strict **Evaluation Logic Pipeline**:

```
                              IAM EVALUATION LOGIC
+─────────────────────────────────────────────────────────────────────────────+
|  1. Default Deny                                                            |
|     Is there an Explicit DENY statement anywhere in the policy chain?       |
|     ├── YES ──► IMMEDIATE DENY (Hard Stop; overrides everything!)           |
|     └── NO  ──► Continue Evaluation                                         |
|                                                                             |
|  2. Organizations Service Control Policies (SCPs)                           |
|     Does the parent AWS Organization allow this action?                     |
|     └── If NO ──► DENIED                                                    |
|                                                                             |
|  3. IAM Permissions Boundary                                                |
|     Does the user's maximum allowable permissions boundary allow it?        |
|     └── If NO ──► DENIED                                                    |
|                                                                             |
|  4. Identity-Based OR Resource-Based Policy Allow                           |
|     Is there at least one matching explicit ALLOW in an IAM Role/User policy|
|     OR in a Resource Policy (e.g. S3 Bucket Policy / KMS Key Policy)?       |
|     ├── YES ──► ALLOWED                                                     |
|     └── NO  ──► IMPLICIT DENY                                               |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## Question 5: Explain the mathematical mechanics of AWS Signature Version 4 (SigV4) used in S3 Pre-Signed URLs and why it conserves backend resources.

### Answer

### SigV4 Signing Key Derivation
AWS SigV4 prevents replay attacks and credential exposure by deriving a scoped HMAC-SHA256 key:
1. `kDate = HMAC-SHA256("AWS4" + SecretKey, "20261114")`
2. `kRegion = HMAC-SHA256(kDate, "us-east-1")`
3. `kService = HMAC-SHA256(kRegion, "s3")`
4. `kSigning = HMAC-SHA256(kService, "aws4_request")`

The resulting `kSigning` key is valid only for that specific date, region, and service.

### String to Sign & Verification
The pre-signed URL embeds:
- `X-Amz-Algorithm=AWS4-HMAC-SHA256`
- `X-Amz-Credential=<AccessKey>/<Date>/<Region>/s3/aws4_request`
- `X-Amz-Expires=900`
- `X-Amz-Signature=<HexHMAC>`

When the browser uploads directly to S3:
- S3 recreates the Canonical Request string from the HTTP method (`PUT`), URI (`/bucket/file.png`), query parameters, and signed headers.
- S3 derives the exact same `kSigning` key and computes the HMAC.
- If the signature matches and `current_time <= issued_time + expires`, S3 accepts the upload directly.

### Backend Resource Savings
By bypassing the backend server:
- **Zero EC2 Memory Buffering**: Uploading a 500 MB file consumes zero RAM on the application pod.
- **Zero Double-Hop Bandwidth**: Network traffic flows directly Client $\rightarrow$ S3, eliminating the Client $\rightarrow$ EC2 $\rightarrow$ S3 double transfer cost.
- **Zero Thread Starvation**: Async event loops remain completely responsive for high-frequency REST queries.
