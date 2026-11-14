# Day 64: AWS Cloud Fundamentals — Enterprise Practical Exercises

Three production-level enterprise cloud challenges with architectural requirements, verification criteria, and complete, production-ready solutions.

---

## Exercise 1: Production Multi-Tier Dual-AZ VPC Infrastructure

### Problem Statement
A high-scale fintech platform must deploy a secure, highly available network topology across two Availability Zones (`us-east-1a` and `us-east-1b`):
- A top-level `/16` VPC CIDR block (`10.0.0.0/16`).
- **2 Public Subnets** (`10.0.1.0/24`, `10.0.2.0/24`) for Application Load Balancers and NAT Gateways.
- **2 Private Application Subnets** (`10.0.11.0/24`, `10.0.12.0/24`) for backend FastAPI compute.
- **2 Isolated Database Subnets** (`10.0.21.0/24`, `10.0.22.0/24`) for RDS PostgreSQL Multi-AZ.
- High availability outbound routing: each Private App Subnet must route through a dedicated NAT Gateway in its respective AZ (dual NAT Gateways for AZ fault tolerance).
- Zero internet ingress or egress for Database Subnets.

### Requirements
1. Author the declarative CloudFormation / Terraform infrastructure-as-code specification.
2. Define explicit Route Tables and associations for each tier.
3. Create Security Groups with mutual chaining: ALB SG $\rightarrow$ App SG $\rightarrow$ DB SG.

### Solution (Terraform Specification `vpc_topology.tf`)

```hcl
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# 1. Main VPC
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "production-fintech-vpc"
    Environment = "production"
  }
}

# 2. Internet Gateway for Public Subnets
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
}

# 3. Public Subnets (ALB & NAT Gateways)
resource "aws_subnet" "public_1a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "us-east-1a"
  map_public_ip_on_launch = true
}

resource "aws_subnet" "public_1b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "us-east-1b"
  map_public_ip_on_launch = true
}

# 4. Elastic IPs & Dual NAT Gateways (One per AZ for HA)
resource "aws_eip" "nat_1a" {
  domain = "vpc"
}

resource "aws_nat_gateway" "nat_1a" {
  allocation_id = aws_eip.nat_1a.id
  subnet_id     = aws_subnet.public_1a.id
}

resource "aws_eip" "nat_1b" {
  domain = "vpc"
}

resource "aws_nat_gateway" "nat_1b" {
  allocation_id = aws_eip.nat_1b.id
  subnet_id     = aws_subnet.public_1b.id
}

# 5. Private Application Subnets
resource "aws_subnet" "private_app_1a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.11.0/24"
  availability_zone = "us-east-1a"
}

resource "aws_subnet" "private_app_1b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.12.0/24"
  availability_zone = "us-east-1b"
}

# 6. Isolated Database Subnets (Zero Internet Egress)
resource "aws_subnet" "isolated_db_1a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.21.0/24"
  availability_zone = "us-east-1a"
}

resource "aws_subnet" "isolated_db_1b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.22.0/24"
  availability_zone = "us-east-1b"
}

# 7. Route Tables
# Public Route Table (0.0.0.0/0 -> IGW)
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "pub_1a" {
  subnet_id      = aws_subnet.public_1a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "pub_1b" {
  subnet_id      = aws_subnet.public_1b.id
  route_table_id = aws_route_table.public.id
}

# Private Route Table 1A (0.0.0.0/0 -> NAT 1A)
resource "aws_route_table" "private_1a" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat_1a.id
  }
}

resource "aws_route_table_association" "priv_1a" {
  subnet_id      = aws_subnet.private_app_1a.id
  route_table_id = aws_route_table.private_1a.id
}

# Private Route Table 1B (0.0.0.0/0 -> NAT 1B)
resource "aws_route_table" "private_1b" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat_1b.id
  }
}

resource "aws_route_table_association" "priv_1b" {
  subnet_id      = aws_subnet.private_app_1b.id
  route_table_id = aws_route_table.private_1b.id
}

# Isolated DB Route Table (Local only)
resource "aws_route_table" "db_isolated" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table_association" "db_1a" {
  subnet_id      = aws_subnet.isolated_db_1a.id
  route_table_id = aws_route_table.db_isolated.id
}

resource "aws_route_table_association" "db_1b" {
  subnet_id      = aws_subnet.isolated_db_1b.id
  route_table_id = aws_route_table.db_isolated.id
}

# 8. Security Group Chaining
resource "aws_security_group" "alb" {
  name        = "production-alb-sg"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name        = "production-app-sg"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]  # Chained from ALB
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "production-rds-sg"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]  # Chained from App SG only!
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

---

## Exercise 2: S3 Direct Pre-Signed Upload Pipeline in FastAPI with KMS Encryption

### Problem Statement
A medical imaging application allows verified doctors to upload large scans (100 MB–500 MB).
- Uploading through the FastAPI server causes high memory pressure and timeout errors.
- The upload must go directly from the client to an encrypted Amazon S3 bucket.
- The pre-signed URL must enforce **AWS KMS Server-Side Encryption (`aws:kms`)** and expire in exactly 15 minutes.
- After uploading directly to S3, the client calls an `/api/v1/scans/confirm` endpoint with the S3 object key to register the scan in PostgreSQL.

### Requirements
1. Implement a FastAPI router with `POST /api/v1/scans/presign-upload`.
2. Generate S3 Pre-Signed `PUT` URL using `boto3`.
3. Require and enforce client-side headers for KMS key ID: `x-amz-server-side-encryption: aws:kms`.
4. Provide unit tests validating pre-signed URL parameter generation and expiration.

### Solution (`s3_upload_router.py`)

```python
import os
import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/scans", tags=["Medical Scans"])

# Boto3 client initialized using EC2 IAM Role (Zero hardcoded keys!)
s3_client = boto3.client("s3", region_name="us-east-1")
BUCKET_NAME = os.environ.get("S3_SCANS_BUCKET", "medical-scans-production-secure")
KMS_KEY_ID = os.environ.get("KMS_KEY_ID", "arn:aws:kms:us-east-1:123456789012:key/medical-key")

class PresignRequest(BaseModel):
    file_name: str = Field(..., example="mri_scan_patient_101.dcm")
    content_type: str = Field(..., example="application/dicom")
    file_size_bytes: int = Field(..., gt=0, le=524_288_000) # Max 500 MB

class PresignResponse(BaseModel):
    upload_url: str
    object_key: str
    expires_in_seconds: int
    required_headers: dict

@router.post("/presign-upload", response_model=PresignResponse, status_code=status.HTTP_201_CREATED)
async def generate_presigned_upload(payload: PresignRequest):
    """
    Generates a secure S3 Pre-Signed PUT URL enforcing KMS encryption.
    """
    object_key = f"scans/{payload.file_name}"
    expires_in = 900  # 15 minutes

    try:
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": object_key,
                "ContentType": payload.content_type,
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": KMS_KEY_ID,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
    except ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate secure S3 pre-signed URL: {str(e)}"
        )

    return PresignResponse(
        upload_url=presigned_url,
        object_key=object_key,
        expires_in_seconds=expires_in,
        required_headers={
            "Content-Type": payload.content_type,
            "x-amz-server-side-encryption": "aws:kms",
            "x-amz-server-side-encryption-aws-kms-key-id": KMS_KEY_ID,
        }
    )
```

---

## Exercise 3: Zero-Downtime RDS PostgreSQL Multi-AZ Failover Watchdog

### Problem Statement
In production, when Amazon RDS triggers a Multi-AZ failover, the DNS CNAME record changes from the old Primary AZ IP to the Standby AZ IP. 
- Because standard Python database connection pools (`SQLAlchemy` / `asyncpg`) cache DNS lookups by default, applications can continue attempting connection to the failed primary IP, causing connection timeouts for several minutes.
- You must implement a connection pool watchdog with proper DNS TTL settings and an automated healthcheck retry loop that handles failover gracefully within 60 seconds without process restarts.

### Solution (`rds_failover_resilience.py`)

```python
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from botocore.exceptions import DBInstanceNotFoundFault

logger = logging.getLogger("rds_watchdog")

DATABASE_URL = "postgresql+asyncpg://postgres_user:secure_pwd@db.cr123456.us-east-1.rds.amazonaws.com:5432/app_db"

# Configure connection pool with short recycle and pre-ping to detect failovers instantly
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_recycle=30,      # Recycle connections every 30s to force fresh DNS resolution
    pool_pre_ping=True,    # Issues 'SELECT 1' before checkout; discards stale dead connections!
    connect_args={
        "command_timeout": 5,
        "server_settings": {"application_name": "fastapi_resilient_pool"},
    }
)

AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def execute_query_with_failover_retry(query: str, max_retries: int = 5):
    """
    Executes database operations with automatic exponential backoff during RDS failover.
    """
    for attempt in range(1, max_retries + 1):
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(text(query))
                await session.commit()
                return result
        except Exception as e:
            delay = min(2 ** attempt, 15)
            logger.warning(
                f"[RDS FAILOVER DETECTED] Attempt {attempt}/{max_retries} failed: {e}. "
                f"Retrying in {delay}s (Waiting for DNS CNAME propagation to Standby replica)..."
            )
            if attempt == max_retries:
                logger.error("[RDS CRITICAL] Multi-AZ Failover exceeded retry threshold!")
                raise
            await asyncio.sleep(delay)
```
