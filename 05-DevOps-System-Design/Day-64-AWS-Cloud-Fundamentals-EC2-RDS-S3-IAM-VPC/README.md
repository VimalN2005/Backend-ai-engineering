# Day 64: AWS Cloud Fundamentals for Backend Engineers: EC2, RDS, S3, IAM & VPC Security

A comprehensive architectural blueprint covering the core Amazon Web Services (AWS) infrastructure stack for enterprise Python backends: Virtual Private Cloud (VPC) design, IAM least privilege, EC2 virtual compute, RDS PostgreSQL multi-AZ high availability, S3 object storage with pre-signed URLs, and network security boundaries.

---

## 1. Cloud Infrastructure & The AWS Shared Responsibility Model

Moving from bare-metal servers or local Docker Compose to cloud infrastructure requires understanding the **AWS Shared Responsibility Model**:

```
+─────────────────────────────────────────────────────────────────────────────+
|                     AWS SHARED RESPONSIBILITY MODEL                         |
+─────────────────────────────────────────────────────────────────────────────+
|  CUSTOMER RESPONSIBILITY: SECURITY "IN" THE CLOUD                           |
|  - Customer Data & Database Encryption (KMS)                                |
|  - IAM Policies, Credentials & User Access Controls                         |
|  - Operating System Patching (EC2), Application Code & Container Images     |
|  - Network Configuration (VPC Subnets, Route Tables, Security Groups)       |
+─────────────────────────────────────────────────────────────────────────────+
|  AWS RESPONSIBILITY: SECURITY "OF" THE CLOUD                                |
|  - Physical Data Centers, Power, Biometric Security, HVAC                   |
|  - Hardware Compute, Hypervisors (AWS Nitro System), Physical Storage       |
|  - Global Fiber Network Cables, Availability Zone Redundancy                |
|  - Managed Service Software (RDS engine patches, S3 replication)            |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 2. AWS Global Infrastructure: Regions & Availability Zones (AZs)

AWS organizes its global footprint hierarchically:

```
GLOBAL FOOTPRINT:
  AWS Region: us-east-1 (N. Virginia)
  ├── Availability Zone A (us-east-1a) ──► Isolated Data Center (Dedicated Power/Network)
  ├── Availability Zone B (us-east-1b) ──► Isolated Data Center (<1ms low-latency fiber link)
  └── Availability Zone C (us-east-1c) ──► Isolated Data Center (Flood/fault isolated)
```

### Key Infrastructure Concepts
- **AWS Region**: A separate geographic area (e.g., `us-east-1`, `eu-west-1`, `ap-south-1`). Each region is completely autonomous and isolated to satisfy data sovereignty laws.
- **Availability Zone (AZ)**: One or more discrete physical data centers within a region with redundant power, networking, and connectivity. AZs are separated by meaningful physical distances (typically tens of miles) to mitigate localized floods or outages, while connected by ultra-low-latency private fiber (<1ms round-trip).
- **High-Availability Rule**: An enterprise backend must distribute compute and database replicas across **at least two AZs** within a region to guarantee fault tolerance against full data center failure.

---

## 3. Virtual Private Cloud (VPC) Architecture & Subnetting

A **Virtual Private Cloud (VPC)** is an isolated virtual network dedicated to your AWS account. It mirrors a traditional data center network with complete control over IP address ranges, subnets, route tables, and network gateways.

```
                              VPC: 10.0.0.0/16
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  PUBLIC SUBNETS (Direct Route to Internet Gateway):                         |
|  ┌───────────────────────────────┐     ┌───────────────────────────────┐    |
|  │ Public Subnet AZ-A            │     │ Public Subnet AZ-B            │    |
|  │ 10.0.1.0/24 (251 usable IPs)  │     │ 10.0.2.0/24 (251 usable IPs)  │    |
|  │ - Application Load Balancer   │     │ - Application Load Balancer   │    |
|  │ - NAT Gateway AZ-A            │     │ - NAT Gateway AZ-B            │    |
|  └───────────────────────────────┘     └───────────────────────────────┘    |
|                                                                             |
|  PRIVATE APPLICATION SUBNETS (Routes internet egress through NAT Gateway):  |
|  ┌───────────────────────────────┐     ┌───────────────────────────────┐    |
|  │ Private App Subnet AZ-A       │     │ Private App Subnet AZ-B       │    |
|  │ 10.0.11.0/24                  │     │ 10.0.12.0/24                  │    |
|  │ - FastAPI EC2 / ECS Tasks     │     │ - FastAPI EC2 / ECS Tasks     │    |
|  │ - Celery Workers              │     │ - Celery Workers              │    |
|  └───────────────────────────────┘     └───────────────────────────────┘    |
|                                                                             |
|  ISOLATED DATABASE SUBNETS (Zero Internet Ingress or Egress):               |
|  ┌───────────────────────────────┐     ┌───────────────────────────────┐    |
|  │ Private DB Subnet AZ-A        │     │ Private DB Subnet AZ-B        │    |
|  │ 10.0.21.0/24                  │     │ 10.0.22.0/24                  │    |
|  │ - RDS PostgreSQL Primary      │ ──► │ - RDS PostgreSQL Standby      │    |
|  └───────────────────────────────┘ (Sync)└───────────────────────────────┘    |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### CIDR Notation & AWS Reserved IPs
- A standard backend VPC uses a `/16` CIDR block: `10.0.0.0/16` ($65,536\text{ IP addresses}$).
- Subnets use `/24` CIDR blocks: `10.0.1.0/24` ($256\text{ IP addresses}$).
- **AWS Reserved IPs**: In every subnet, AWS reserves the **first 4 IPs and the last 1 IP** (5 total):
  - `.0`: Network address.
  - `.1`: Reserved by AWS for VPC default router.
  - `.2`: Reserved by AWS for Amazon Provided DNS resolver (`10.0.0.2`).
  - `.3`: Reserved by AWS for future use.
  - `.255`: Network broadcast address.
  - *Usable IPs per `/24` subnet* = $256 - 5 = \mathbf{251\text{ usable hosts}}$.

---

## 4. Internet Gateways, NAT Gateways & Routing Tables

```
+─────────────────────────────────────────────────────────────────────────────+
|                               ROUTING TOPOLOGY                              |
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  Internet (0.0.0.0/0) ◄──────► Internet Gateway (IGW)                      |
|                                        ▲                                    |
|                                        │ Public Route: 0.0.0.0/0 -> igw     |
|                              ┌─────────┴─────────┐                          |
|                              │ Public Subnet     │                          |
|                              │ - NAT Gateway     │                          |
|                              └─────────▲─────────┘                          |
|                                        │ Private Route: 0.0.0.0/0 -> nat-gw |
|                              ┌─────────┴─────────┐                          |
|                              │ Private App Subnet│                          |
|                              │ (EC2 / FastAPI)   │                          |
|                              └───────────────────┘                          |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### Core Gateways
1. **Internet Gateway (IGW)**: A horizontally scaled, redundant VPC component that enables two-way communication between instances in public subnets and the internet (performs 1:1 NAT for public IPs).
2. **NAT Gateway (Network Address Translation)**: Resides in a **public subnet** with an Elastic IP (EIP). Allows instances in **private subnets** to initiate outbound traffic to the internet (e.g. downloading pip wheels, OS security updates, calling Stripe/OpenAI APIs) while **strictly blocking external inbound connections** from reaching the private instances.
3. **Route Tables**: Sets of rules determining where network traffic from subnets is directed:
   - *Public Route Table*: `0.0.0.0/0` $\rightarrow$ `igw-xxxx`
   - *Private Route Table*: `0.0.0.0/0` $\rightarrow$ `nat-xxxx`
   - *Local Route*: `10.0.0.0/16` $\rightarrow$ `local` (Routes inter-subnet traffic within VPC automatically).

---

## 5. Security Groups (Stateful) vs. Network ACLs (Stateless)

AWS implements two layers of virtual firewalls to enforce defense-in-depth:

```
+─────────────────────────────────────────────────────────────────────────────+
|                   SECURITY GROUPS VS. NETWORK ACLS                          |
+─────────────────────────────────────────────────────────────────────────────+
|  Dimension          | Security Groups (SGs)       | Network ACLs (NACLs)    |
├─────────────────────┼─────────────────────────────┼─────────────────────────┤
|  Scope              | Attached to ENI (Instance)  | Attached to Subnet      |
|  Statefulness       | STATEFUL                    | STATELESS               |
|  Rule Types         | ALLOW rules only (Default   | ALLOW and DENY rules    |
|                     | deny all incoming traffic)  | (Numbered rule order)   |
|  Return Traffic     | Automatically allowed       | Must explicitly allow   |
|                     | regardless of inbound rules | ephemeral ports (1024+) |
|  Evaluation Order   | All rules evaluated         | Evaluated by rule number|
|                     | collectively                | in ascending order      |
+─────────────────────────────────────────────────────────────────────────────+
```

### Security Group Chaining (Production Standard)
Never open ports to IP addresses if both services reside in the same VPC. **Reference the source Security Group directly**:

```
[Application Load Balancer SG] ──(Port 8000 allowed)──► [FastAPI EC2 App SG]
                                                               │
                                                       (Port 5432 allowed)
                                                               ▼
                                                    [RDS PostgreSQL DB SG]
```
- The RDS Security Group allows inbound port `5432` **only if the source is `sg-app` (FastAPI)**. Even if an attacker enters the VPC, they cannot probe the database unless they compromise an instance carrying that exact security group.

---

## 6. Identity and Access Management (IAM) & Least Privilege

IAM controls authentication (`AuthN`) and authorization (`AuthZ`) across all AWS resources.

```
+─────────────────────────────────────────────────────────────────────────────+
|                               IAM ENTITIES                                  |
+─────────────────────────────────────────────────────────────────────────────+
|  IAM User   : Persistent human identity with username/password or access key|
|  IAM Group  : Collection of users sharing policy permissions                |
|  IAM Role   : Identity assumable by trusted entities (EC2, Lambda, GitHub)  |
|  IAM Policy : JSON document explicitly defining Allowed / Denied actions    |
+─────────────────────────────────────────────────────────────────────────────+
```

### Anatomy of an IAM Policy JSON Document
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowS3UploadForReceiptsOnly",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::company-production-media/receipts/*",
      "Condition": {
        "StringEquals": {
          "s3:x-amz-server-side-encryption": "aws:kms"
        }
      }
    }
  ]
}
```

### The Principle of Least Privilege
1. **Never use the AWS Root Account** for daily engineering tasks or applications.
2. Grant permissions exclusively to specific ARNs (e.g. `arn:aws:s3:::my-bucket/*`), never `Resource: "*"`.
3. Avoid wildcard actions: specify `"s3:GetObject"` rather than `"s3:*"`.

---

## 7. Temporary Credentials & EC2 IAM Instance Profiles

**Golden Security Rule: Never embed AWS Access Keys into EC2 instances, Dockerfiles, or application code.**

Instead, attach an **IAM Role** to your EC2 instance via an **Instance Profile**:
1. When your Python backend creates a `boto3.client("s3")`, boto3 automatically queries the **Instance Metadata Service (IMDSv2)** at `http://169.254.169.254/latest/meta-data/iam/security-credentials/`.
2. AWS automatically generates temporary session tokens and rotates them every few hours.
3. If an instance is decommissioned or compromised, zero permanent keys exist to be stolen.

---

## 8. Amazon EC2: Virtual Compute Architecture

**Amazon Elastic Compute Cloud (EC2)** provides resizable virtual machine capacity powered by the **AWS Nitro System** (dedicated hardware hypervisor cards offloading networking and storage).

### EC2 Instance Naming Convention
```
             t4g . 2xlarge
              ││     │
              ││     └─► Instance Size: 8 vCPUs, 32 GB RAM
              │└───────► Processor Architecture: 'g' = Graviton (ARM64)
              └────────► Instance Family: 't' = Burstable, 'c' = Compute, 'm' = General, 'r' = Memory
```

### Storage: EBS (Elastic Block Store)
- **gp3 (General Purpose SSD)**: Default production standard. Baseline 3,000 IOPS and 125 MB/s throughput independent of drive size.
- **io2 (Provisioned IOPS SSD)**: Designed for mission-critical relational databases requiring up to 256,000 IOPS with 99.999% durability.

---

## 9. Amazon RDS PostgreSQL: High Availability & Replication

**Amazon Relational Database Service (RDS)** manages provisioning, OS patching, automated daily snapshots, and multi-AZ replication.

```
                           RDS MULTI-AZ ARCHITECTURE
+─────────────────────────────────────────────────────────────────────────────+
|                                                                             |
|  AVAILABILITY ZONE A (Active)            AVAILABILITY ZONE B (Standby)      |
|  ┌───────────────────────────────┐       ┌───────────────────────────────┐  |
|  │ Primary DB Instance           │ ────► │ Standby Replica               │  |
|  │ (Handles Reads & Writes)      │ Sync  │ (Hot standby; zero reads)     │  |
|  └───────────────┬───────────────┘       └───────────────┬───────────────┘  |
|                  │ EBS Volume                            │ EBS Volume       |
|                  ▼                                       ▼                  |
|          Primary Storage                         Synchronous Mirror         |
|                                                                             |
|  FAILOVER EVENT (Primary hardware fails or AZ outage):                      |
|  1. RDS detects primary heartbeat failure within 30 seconds.                |
|  2. RDS flips DNS CNAME record (db.xxx.rds.amazonaws.com) to Standby IP.    |
|  3. Standby promotes to Primary. Total failover recovery: 60-120 seconds!   |
|                                                                             |
+─────────────────────────────────────────────────────────────────────────────+
```

### Read Replicas (Asynchronous Scaling)
- Separate read-only instances provisioned across AZs or regions.
- Replicated **asynchronously** using PostgreSQL streaming replication.
- Direct read-heavy analytics or GET endpoints to the Read Replica endpoint, preserving Primary IOPS for transactional writes.

---

## 10. Amazon S3: Object Storage & Consistency Model

**Amazon S3 (Simple Storage Service)** is an exabyte-scale object store designed for $99.999999999\%$ (11 9's) of data durability.

### S3 Architectural Properties
- **Data Model**: Flat key-value store. "Folders" are merely prefixes within the object key (e.g. `uploads/2026/receipt.pdf`).
- **Consistency**: S3 delivers **Strong Read-After-Write Consistency** for `PUT` and `DELETE` requests of objects across all AWS regions. Once a write succeeds (HTTP 200 OK), any subsequent `GET` immediately reflects the updated object.

---

## 11. Secure Ingestion: S3 Pre-Signed URLs vs. Server Streaming

A common backend antipattern is streaming large user uploads (e.g. 50 MB PDFs or videos) directly through the FastAPI backend server:

```
ANTIPATTERN (Server Bottleneck):
User Browser ────(50 MB Upload)────► FastAPI EC2 Pod ────(50 MB Upload)────► S3 Bucket
* Problem: Saturates EC2 network bandwidth, exhausts worker threads, high memory spike.

PRODUCTION STANDARD (S3 Pre-Signed URLs):
1. User Browser ────► FastAPI: "I need to upload invoice.pdf (2 MB)"
2. FastAPI verifies JWT, generates S3 Pre-Signed PUT URL (signed with IAM role, 15m TTL).
3. FastAPI returns pre-signed URL to Browser.
4. User Browser ────(Direct PUT)───────────────────────────────────────────► S3 Bucket
* Zero backend server memory consumed! S3 absorbs 100% of the upload bandwidth.
```

---

## 12. S3 Bucket Security & KMS Encryption

Production buckets must enforce the following security controls:
1. **Block Public Access (BPA)**: Enable all 4 S3 BPA checkboxes on the bucket to prevent accidental public data leaks.
2. **Bucket Policies**: Enforce TLS encryption in transit:
   ```json
   {
     "Sid": "EnforceTLSRequestsOnly",
     "Effect": "Deny",
     "Principal": "*",
     "Action": "s3:*",
     "Resource": ["arn:aws:s3:::my-bucket", "arn:aws:s3:::my-bucket/*"],
     "Condition": {
       "Bool": { "aws:SecureTransport": "false" }
     }
   }
   ```
3. **Server-Side Encryption (SSE-KMS)**: Encrypts objects at rest using an AWS Key Management Service (KMS) customer managed key with audit trails.

---

## 13. Secrets Management: Secrets Manager vs. Parameter Store

| Dimension | AWS Secrets Manager | SSM Parameter Store |
| :--- | :--- | :--- |
| **Primary Use Case** | Database passwords, API keys requiring rotation | App config, feature flags, license strings |
| **Automatic Rotation** | Native integration with RDS, Lambda rotation | None (Manual script required) |
| **Cost** | $\$0.40$ per secret per month | **Free** (Standard tier up to 10,000 params) |
| **Max Payload Size** | $64\text{ KB}$ | $4\text{ KB}$ (Standard), $8\text{ KB}$ (Advanced) |
| **Recommendation** | **Use for RDS passwords & Stripe secrets** | **Use for general non-secret application config**|

---

## 14. Observability: CloudWatch Metrics & Alarms

Amazon CloudWatch collects metrics and logs from every AWS resource:
- **EC2**: `CPUUtilization`, `StatusCheckFailed_System`, `NetworkIn`, `NetworkOut`.
- **RDS**: `CPUUtilization`, `FreeableMemory`, `DiskQueueDepth`, `ReadIOPS`, `WriteIOPS`.
- **Alarms**: Configure automated alerts triggering Amazon SNS notifications to Slack or PagerDuty when RDS `FreeStorageSpace < 10GB` or EC2 `CPUUtilization > 85%` for 3 consecutive 1-minute periods.

---

## 15. Production AWS Backend Architecture Blueprint

| Layer | Component | AWS Implementation | Production Security & HA Configuration |
| :--- | :--- | :--- | :--- |
| **Ingress** | DNS & CDN | Amazon Route 53 + CloudFront | DNSSEC enabled; HTTPS termination with ACM certificate |
| **Gateway** | Load Balancing | Application Load Balancer (ALB) | Dual-AZ public subnets; HTTP to HTTPS redirect; WAF attached |
| **Compute** | Web & Workers | EC2 Autoscaling Group / ECS Fargate | Multi-AZ private application subnets; IAM Instance Profile |
| **Database** | Relational DB | Amazon RDS PostgreSQL 16 | Multi-AZ standby replica; private DB subnets; KMS encrypted |
| **Cache** | In-Memory Store| Amazon ElastiCache Redis 7 | Multi-AZ replication group with automatic failover |
| **Storage** | Object Store | Amazon S3 | Block Public Access ON; Pre-Signed URLs; SSE-KMS |
| **Secrets** | Credential Store| AWS Secrets Manager | Auto-rotation enabled; VPC Endpoint access |
