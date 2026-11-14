# Day 64: AWS Cloud Fundamentals — 5-Minute Rapid Revision

A rapid revision reference, cheat sheet, and flashcard collection for AWS cloud fundamentals, VPC architecture, IAM least privilege, RDS Multi-AZ, and S3 pre-signed URLs.

---

## 1. VPC Subnetting & CIDR Reference

| Subnet Tier | Purpose | Routing Configuration | Security Rule |
| :--- | :--- | :--- | :--- |
| **Public Subnet** | Application Load Balancers, NAT Gateways, Bastion Hosts | `0.0.0.0/0` $\rightarrow$ Internet Gateway (`igw-xxxx`) | Port 443/80 open to `0.0.0.0/0` on ALB |
| **Private App Subnet** | FastAPI EC2 instances, ECS Fargate, Celery Workers | `0.0.0.0/0` $\rightarrow$ NAT Gateway (`nat-xxxx`) | Ingress allowed **strictly from ALB SG** |
| **Isolated DB Subnet** | Amazon RDS PostgreSQL, ElastiCache Redis | Local VPC route only (`10.0.0.0/16` $\rightarrow$ `local`) | Ingress allowed **strictly from App SG** |

---

## 2. Production S3 Pre-Signed URL Pattern

```python
import boto3

# Boto3 client uses IAM Role attached to EC2 via Instance Profile
s3 = boto3.client("s3", region_name="us-east-1")

# 1. Generate Direct Upload PUT URL (Client -> S3 directly)
upload_url = s3.generate_presigned_url(
    ClientMethod="put_object",
    Params={"Bucket": "my-bucket", "Key": "uploads/doc.pdf", "ServerSideEncryption": "aws:kms"},
    ExpiresIn=900,  # 15 minutes
    HttpMethod="PUT",
)

# 2. Generate Private Download GET URL (Time-bounded download)
download_url = s3.generate_presigned_url(
    ClientMethod="get_object",
    Params={"Bucket": "my-bucket", "Key": "uploads/doc.pdf"},
    ExpiresIn=300,  # 5 minutes
    HttpMethod="GET",
)
```

---

## 3. AWS CLI Command Arsenal

```bash
# 1. Inspect IAM caller identity (verifies active credentials and assumed role)
aws sts get-caller-identity

# 2. List S3 buckets with server-side encryption status
aws s3 ls

# 3. Copy file to S3 with KMS encryption enforced
aws s3 cp document.pdf s3://my-bucket/docs/ --sse aws:kms

# 4. Describe RDS DB instances and check Multi-AZ status
aws rds describe-db-instances --query "DBInstances[*].[DBInstanceIdentifier,MultiAZ,DBInstanceStatus]" --output table

# 5. Describe EC2 instances and Security Groups in VPC
aws ec2 describe-instances --filters "Name=vpc-id,Values=vpc-123456" --query "Reservations[*].Instances[*].[InstanceId,PrivateIpAddress,State.Name]" --output table

# 6. Connect to private EC2 instance securely without SSH keys via SSM Session Manager
aws ssm start-session --target i-0123456789abcdef0
```

---

## 4. 10 Rapid Recall Flashcards

| # | Question | Answer |
| :-- | :--- | :--- |
| **1** | How many IP addresses does AWS reserve in every subnet? | **5 IP addresses** (`.0` network, `.1` router, `.2` DNS, `.3` future, `.255` broadcast). |
| **2** | What is the fundamental difference between Security Groups and NACLs? | Security Groups are **stateful** and apply at the instance ENI level; NACLs are **stateless** and apply at the subnet boundary. |
| **3** | Why must you never store static AWS Access Keys on EC2 instances? | Static keys can be permanently stolen; use **IAM Instance Profiles** which deliver automatically rotated 1-hour temporary tokens via IMDSv2. |
| **4** | What is the failover mechanism in Amazon RDS Multi-AZ? | RDS detects primary failure and **automatically updates the database DNS CNAME** to point to the Standby replica IP in 60–120 seconds. |
| **5** | What is the RPO (Recovery Point Objective) for RDS Multi-AZ? | **RPO = 0 (Zero data loss)** because writes are synchronously mirrored at the block/EBS storage level. |
| **6** | Why should databases be placed in an "Isolated" subnet rather than a "Private" subnet? | Isolated subnets have **zero route to NAT Gateways or IGWs**, guaranteeing complete isolation from external internet threats. |
| **7** | What is the main advantage of using S3 Pre-Signed URLs for file uploads? | Bypasses the application backend, eliminating server memory buffering and **saving 100% of the server's network bandwidth**. |
| **8** | In AWS IAM evaluation logic, what happens when an Explicit Deny statement matches? | The request is **immediately DENIED**; Explicit Deny unconditionally overrides all Allow statements. |
| **9** | What S3 setting must remain permanently enabled to prevent accidental data leaks? | **S3 Block Public Access (BPA)** (all 4 checkboxes enabled). |
| **10** | What is the difference between AWS Secrets Manager and SSM Parameter Store? | Secrets Manager provides **native automated database credential rotation** ($0.40/secret); SSM Parameter Store standard tier is **free** for general configuration strings. |
