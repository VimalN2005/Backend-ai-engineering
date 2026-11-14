# Day 64: AWS Cloud Fundamentals — 7 Critical Production Antipatterns

A senior cloud architect analysis of the 7 most dangerous antipatterns in AWS backend engineering, detailing failure mechanisms, bad vs. good configurations, and production remediations.

---

## 1. Antipattern 1: Placing Production RDS Databases in Public Subnets

### The Failure Mechanism
Developers frequently provision Amazon RDS instances in public subnets with `PubliclyAccessible: true` so they can connect with local SQL clients (DBeaver, TablePlus) during development.

In production:
- The database receives a public IPv4 address that is announced via BGP to the global internet.
- Port `5432` is bombarded by automated Shodan/Censys port scanners, credential stuffing bots, and brute-force dictionary attacks.
- Any vulnerability in the PostgreSQL authentication protocol or an accidental weak password results in immediate database exfiltration and ransomware compromise.

### Bad Configuration (Antipattern)
```json
// ANTI-PATTERN: Database in public subnet, open to the public internet
{
  "DBSubnetGroup": "public-subnet-group",
  "PubliclyAccessible": true,
  "SecurityGroup": {
    "Ingress": [
      { "Port": 5432, "CidrIp": "0.0.0.0/0" }
    ]
  }
}
```

### Good Configuration (Production Standard)
```json
// PRODUCTION STANDARD: Database in isolated private subnet with Security Group chaining
{
  "DBSubnetGroup": "isolated-db-subnet-group",
  "PubliclyAccessible": false,
  "SecurityGroup": {
    "Ingress": [
      {
        "Port": 5432,
        "SourceSecurityGroupId": "sg-fastapi-app-tier",
        "Description": "Inbound from application EC2 instances only"
      }
    ]
  }
}
```
*Remediation for local developer debugging*: Connect to internal databases securely via **AWS Systems Manager (SSM) Session Manager Port Forwarding** or an encrypted Bastion Host in a public subnet, never by making the database public.

---

## 2. Antipattern 2: Storing Permanent AWS Access Keys (`AKIA...`) in Instances or Code

### The Failure Mechanism
Hardcoding `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` into application configuration files, Dockerfiles, or EC2 environment files:
- Exposes permanent credentials in Git history and container layers.
- If a server is compromised via SSRF (Server-Side Request Forgery) or local file read, the attacker extracts permanent credentials that survive server deletion.

### Bad Code (Antipattern)
```python
# ANTI-PATTERN: Hardcoded permanent access keys in Python backend
import boto3

s3_client = boto3.client(
    "s3",
    aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
    aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    region_name="us-east-1"
)
```

### Good Code (Production Standard)
```python
# PRODUCTION STANDARD: IAM Instance Profile / ECS Task Role (Automatic credential rotation)
import boto3

# Boto3 automatically retrieves temporary 1-hour credentials from AWS IMDSv2!
s3_client = boto3.client("s3", region_name="us-east-1")
```

---

## 3. Antipattern 3: Using the AWS Root Account for Application Workloads

### The Failure Mechanism
The AWS Root User possesses unconditional administrative privileges across all billing, infrastructure, and user identities. It cannot be restricted by IAM policies or SCPs (Service Control Policies).

If root API keys are generated and leaked:
- Attackers can delete all AWS accounts, terminate all RDS databases, delete backups, and demand Bitcoin ransom.
- AWS cannot recover databases or snapshots deleted with root credentials if KMS keys are destroyed.

### Remediation
1. **Lock away Root Account credentials**: Enable multi-factor authentication (FIDO2 hardware key) on the root account and delete all root access keys immediately.
2. Create fine-grained IAM Roles and Users with least-privilege permissions for daily engineering tasks.

---

## 4. Antipattern 4: Opening Security Group Ingress to `0.0.0.0/0` Instead of Chaining

### The Failure Mechanism
Configuring `0.0.0.0/0` on Security Groups allows any machine on the internet to attempt TCP handshakes. 

Even if you intend to allow access only to your office IP address, public IP addresses change. Hardcoding IP CIDR blocks (`192.0.2.1/32`) creates maintenance drift. If services reside in the same VPC, referencing CIDR blocks breaks if instances scale dynamically.

### Bad Configuration (Antipattern)
```json
// Ingress rule allowing traffic from specific host IP that changes
{
  "IpProtocol": "tcp",
  "FromPort": 8000,
  "ToPort": 8000,
  "CidrIp": "0.0.0.0/0"
}
```

### Good Configuration (Production Standard)
```json
// Security Group Chaining: Ingress allowed strictly from upstream ALB Security Group
{
  "IpProtocol": "tcp",
  "FromPort": 8000,
  "ToPort": 8000,
  "UserIdGroupPairs": [
    { "GroupId": "sg-alb-public-gateway" }
  ]
}
```

---

## 5. Antipattern 5: S3 Public Bucket Data Leaks (Disabling Block Public Access)

### The Failure Mechanism
Amazon S3 buckets are private by default. However, developers often disable **S3 Block Public Access (BPA)** or add wildcard bucket policies (`"Principal": "*"`, `"Action": "s3:GetObject"`) to fix browser image upload 403 errors.

Once BPA is disabled:
- Automated scanners detect the open bucket within minutes.
- Confidential customer documents, invoices, and database backups are downloaded and publicly exposed.

### Remediation
- **Keep all 4 S3 Block Public Access settings permanently ENABLED**.
- To allow users to view private documents, generate temporary **S3 Pre-Signed GET URLs** with a 15-minute TTL, or distribute assets through **Amazon CloudFront with Origin Access Control (OAC)**.

---

## 6. Antipattern 6: Disabling Multi-AZ on Production RDS Databases

### The Failure Mechanism
Running Amazon RDS in Single-AZ mode saves 50% on database compute and storage costs. 

However, in Single-AZ mode:
- If the underlying physical server encounters a memory parity error, disk controller fault, or availability zone network severance, the database crashes.
- Recovery requires AWS to provision new hardware and restore from storage volume, resulting in **30 to 90 minutes of total application downtime**.
- Automated backups in Single-AZ mode create brief I/O freezes (several seconds) on standard instances.

### Remediation
- **Enable Multi-AZ for all production RDS databases**.
- Multi-AZ synchronously mirrors transactions to a standby replica in a secondary AZ.
- If the primary fails, RDS automatically performs a DNS failover in 60–120 seconds with **zero data loss (RPO = 0)**.

---

## 7. Antipattern 7: Uploading Large Files Through the Backend Application Server

### The Failure Mechanism
Streaming large file uploads (e.g., 50 MB PDFs, audio, or video files) through a FastAPI or Django backend server:
- Ties up an asynchronous worker coroutine or thread pool for the entire duration of the upload.
- Consumes double the network bandwidth (Client $\rightarrow$ Backend Server, then Backend Server $\rightarrow$ S3).
- Causes extreme memory pressure under concurrent uploads, triggering the Linux OOM-Killer.

### Remediation: Direct Ingestion with S3 Pre-Signed URLs
```
User Browser ────► FastAPI: Request Pre-Signed PUT URL (Tiny JSON request, ~10ms)
FastAPI      ────► User Browser: Returns Presigned URL (15m expiration)
User Browser ────(Direct PUT)────► Amazon S3 Bucket (Absorbs all I/O and bandwidth!)
```
