"""
Day 64: AWS Cloud Fundamentals for Backend Engineers
=============================================================================
Production-Grade Pattern: End-to-End AWS Cloud Architecture Simulator

This module simulates the multi-tier production infrastructure of a Python backend
running across AWS Global Infrastructure:
1. Multi-AZ VPC Packet Router (IGW, NAT Gateways, Public/Private/DB Subnets).
2. Amazon RDS PostgreSQL Multi-AZ Synchronous Mirroring & Automatic Failover.
3. S3 Direct Upload Pipeline (Pre-Signed PUT ingestion bypassing API memory).
4. Amazon CloudWatch Metric Alarms & SNS Notification Watchdog.
5. End-to-End Diagnostic Suite & Throughput Benchmark.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. MULTI-AZ VPC PACKET ROUTER & SUBNET TOPOLOGY
# ==============================================================================

class SubnetTier(enum.Enum):
    PUBLIC = "PUBLIC"          # Direct route to Internet Gateway (IGW)
    PRIVATE_APP = "PRIVATE_APP"  # Egress to internet via NAT Gateway only
    ISOLATED_DB = "ISOLATED_DB"  # Zero internet route (In-VPC local routing only)


@dataclasses.dataclass
class Subnet:
    """Represents an AWS VPC Subnet."""
    subnet_id: str
    cidr_block: str
    availability_zone: str
    tier: SubnetTier
    route_table: Dict[str, str] = dataclasses.field(default_factory=dict)


class VPCNetworkRouter:
    """
    Simulates AWS VPC routing tables, Internet Gateways (IGW), and NAT Gateways.
    Enforces packet routing rules and security isolation between tiers.
    """

    def __init__(self, vpc_cidr: str = "10.0.0.0/16"):
        self.vpc_cidr = vpc_cidr
        self.subnets: Dict[str, Subnet] = {}
        self.igw_attached = True
        self.nat_gateways: Dict[str, str] = {}  # az -> nat_gateway_id

    def add_subnet(self, subnet_id: str, cidr: str, az: str, tier: SubnetTier) -> Subnet:
        routes = {"10.0.0.0/16": "local"}
        if tier == SubnetTier.PUBLIC:
            routes["0.0.0.0/0"] = "igw-01"
        elif tier == SubnetTier.PRIVATE_APP:
            routes["0.0.0.0/0"] = f"nat-gw-{az}"
        # ISOLATED_DB has only local route

        subnet = Subnet(
            subnet_id=subnet_id,
            cidr_block=cidr,
            availability_zone=az,
            tier=tier,
            route_table=routes,
        )
        self.subnets[subnet_id] = subnet
        return subnet

    def route_packet(self, source_subnet_id: str, destination_ip: str, is_inbound: bool = False) -> Tuple[bool, str]:
        """
        Simulates VPC routing table evaluation for outbound or inbound packet flows.
        """
        if source_subnet_id not in self.subnets:
            return False, f"Unknown subnet {source_subnet_id}"

        subnet = self.subnets[source_subnet_id]

        # 1. Inbound check
        if is_inbound:
            if subnet.tier != SubnetTier.PUBLIC:
                return False, f"DROPPED: Subnet '{subnet.subnet_id}' is {subnet.tier.value}; inbound internet traffic is blocked!"
            return True, f"ROUTED: Inbound traffic delivered to public subnet via IGW."

        # 2. Outbound check
        if destination_ip.startswith("10.0."):
            return True, "ROUTED: Local VPC inter-subnet communication allowed via 'local' route."

        # External destination (e.g. 8.8.8.8 or api.stripe.com)
        target_gw = subnet.route_table.get("0.0.0.0/0")
        if not target_gw:
            return False, f"DROPPED: Subnet '{subnet.subnet_id}' ({subnet.tier.value}) has NO default route to internet."

        if target_gw == "igw-01":
            return True, "ROUTED: Direct egress to internet via Internet Gateway (IGW)."
        elif target_gw.startswith("nat-gw-"):
            return True, f"ROUTED: Egress to internet translated through NAT Gateway '{target_gw}'."

        return False, "DROPPED: Unreachable destination."


# ==============================================================================
# 2. AMAZON RDS POSTGRESQL MULTI-AZ FAILOVER SIMULATOR
# ==============================================================================

class RdsNodeRole(enum.Enum):
    PRIMARY = "PRIMARY (Read-Write)"
    STANDBY = "STANDBY (Sync Mirror - Zero Reads)"


@dataclasses.dataclass
class RdsNode:
    instance_id: str
    availability_zone: str
    role: RdsNodeRole
    is_healthy: bool = True
    committed_wal_lsn: int = 1000  # Log Sequence Number


class AmazonRDSMultiAZCluster:
    """
    Simulates Amazon RDS PostgreSQL Multi-AZ synchronous replication, heartbeat
    monitoring, and automatic failover with DNS CNAME swapping.
    """

    def __init__(self, cluster_id: str = "prod-pg-cluster"):
        self.cluster_id = cluster_id
        self.dns_cname = "prod-pg.cr123456.us-east-1.rds.amazonaws.com"
        self.primary_node = RdsNode(
            instance_id="prod-pg-1a",
            availability_zone="us-east-1a",
            role=RdsNodeRole.PRIMARY,
        )
        self.standby_node = RdsNode(
            instance_id="prod-pg-1b",
            availability_zone="us-east-1b",
            role=RdsNodeRole.STANDBY,
        )
        self.active_dns_target = self.primary_node.instance_id
        self.failover_count = 0

    def write_transaction(self, query: str) -> Tuple[bool, str]:
        """
        Simulates synchronous commit across Primary and Standby EBS storage.
        """
        if not self.primary_node.is_healthy:
            return False, "WRITE REJECTED: Primary database node is down!"

        # Synchronous commit to Primary
        self.primary_node.committed_wal_lsn += 10

        # Synchronous replication to Standby (EBS level mirror)
        if self.standby_node.is_healthy:
            self.standby_node.committed_wal_lsn = self.primary_node.committed_wal_lsn

        return True, f"Transaction committed synchronously. WAL LSN: {self.primary_node.committed_wal_lsn}"

    def trigger_az_failure(self, failed_az: str) -> Dict[str, Any]:
        """
        Simulates catastrophic loss of AZ (e.g. power grid failure in us-east-1a).
        Triggers RDS automated failover watchdog.
        """
        report = {
            "failed_az": failed_az,
            "failover_triggered": False,
            "data_loss_wal_lag": 0,
            "old_primary": self.primary_node.instance_id,
            "new_primary": None,
            "failover_duration_sec": 42.5,
        }

        if self.primary_node.availability_zone == failed_az:
            self.primary_node.is_healthy = False
            report["failover_triggered"] = True

            # Calculate replication lag
            lag = self.primary_node.committed_wal_lsn - self.standby_node.committed_wal_lsn
            report["data_loss_wal_lag"] = lag  # In Multi-AZ, lag is strictly 0!

            # Promote Standby to Primary
            self.standby_node.role = RdsNodeRole.PRIMARY
            report["new_primary"] = self.standby_node.instance_id
            self.active_dns_target = self.standby_node.instance_id
            self.failover_count += 1

            # Swap pointers
            temp = self.primary_node
            self.primary_node = self.standby_node
            self.standby_node = temp
            self.standby_node.role = RdsNodeRole.STANDBY

        return report


# ==============================================================================
# 3. S3 DIRECT PRE-SIGNED UPLOAD PIPELINE
# ==============================================================================

class S3DirectUploadPipeline:
    """
    Demonstrates the performance difference between Server-Proxied uploads vs
    S3 Pre-Signed Direct Uploads.
    """

    def simulate_server_proxied_upload(self, file_size_mb: float) -> Dict[str, Any]:
        """Antipattern: File bytes stream through EC2 application memory."""
        # Consumes EC2 RAM and 2x network transfer (Client -> EC2 -> S3)
        ec2_ram_consumed_mb = file_size_mb
        ec2_network_in_mb = file_size_mb
        ec2_network_out_mb = file_size_mb
        return {
            "method": "Server Proxied",
            "ec2_ram_consumed_mb": ec2_ram_consumed_mb,
            "ec2_total_network_mb": ec2_network_in_mb + ec2_network_out_mb,
            "bottleneck": "Saturates EC2 bandwidth and memory pools",
        }

    def simulate_presigned_direct_upload(self, file_size_mb: float) -> Dict[str, Any]:
        """Production Standard: Browser uploads directly to S3 via pre-signed URL."""
        # EC2 only handles a tiny JSON request to mint pre-signed URL (~500 bytes)
        return {
            "method": "S3 Direct Pre-Signed URL",
            "ec2_ram_consumed_mb": 0.0005,  # Negligible (~500 bytes)
            "ec2_total_network_mb": 0.001,   # URL request only
            "bottleneck": "None! S3 absorbs 100% of upload I/O and bandwidth",
        }


# ==============================================================================
# 4. CLOUDWATCH METRIC ALARMS & WATCHDOG
# ==============================================================================

@dataclasses.dataclass
class CloudWatchAlarm:
    alarm_name: str
    metric_name: str
    threshold: float
    comparison_operator: str  # "GreaterThanThreshold" or "LessThanThreshold"
    evaluation_periods: int
    is_in_alarm: bool = False


class CloudWatchWatchdog:
    """
    Simulates Amazon CloudWatch metric alarms and SNS alert dispatching.
    """

    def __init__(self):
        self.alarms: Dict[str, CloudWatchAlarm] = {}
        self.dispatched_alerts: List[str] = []

    def create_alarm(self, name: str, metric: str, threshold: float, op: str, periods: int = 1) -> CloudWatchAlarm:
        alarm = CloudWatchAlarm(name, metric, threshold, op, periods)
        self.alarms[name] = alarm
        return alarm

    def ingest_metric(self, metric_name: str, value: float) -> None:
        for alarm in self.alarms.values():
            if alarm.metric_name == metric_name:
                breached = False
                if alarm.comparison_operator == "GreaterThanThreshold" and value > alarm.threshold:
                    breached = True
                elif alarm.comparison_operator == "LessThanThreshold" and value < alarm.threshold:
                    breached = True

                if breached and not alarm.is_in_alarm:
                    alarm.is_in_alarm = True
                    alert_msg = f"ALARM '{alarm.alarm_name}' TRIGGERED: {metric_name} = {value} breached threshold {alarm.threshold}!"
                    self.dispatched_alerts.append(alert_msg)
                elif not breached and alarm.is_in_alarm:
                    alarm.is_in_alarm = False


# ==============================================================================
# 5. INTEGRATION DIAGNOSTICS & BENCHMARK HARNESS
# ==============================================================================

def run_aws_architecture_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 64 AWS ARCHITECTURE SIMULATOR: VPC ROUTING, RDS MULTI-AZ & S3")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 1: MULTI-AZ VPC PACKET ROUTING & ISOLATION
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 1] Verifying Multi-Tier VPC Routing & Security Isolation...")
    router = VPCNetworkRouter("10.0.0.0/16")

    # Provision Dual-AZ subnets
    pub_sub = router.add_subnet("sub-pub-1a", "10.0.1.0/24", "us-east-1a", SubnetTier.PUBLIC)
    app_sub = router.add_subnet("sub-app-1a", "10.0.11.0/24", "us-east-1a", SubnetTier.PRIVATE_APP)
    db_sub = router.add_subnet("sub-db-1a", "10.0.21.0/24", "us-east-1a", SubnetTier.ISOLATED_DB)

    # A: Public Subnet can accept inbound internet traffic (ALB)
    in_ok, in_msg = router.route_packet("sub-pub-1a", "10.0.1.50", is_inbound=True)
    print(f"  Inbound to Public Subnet (ALB): {in_ok} ({in_msg})")
    assert in_ok is True

    # B: Private Subnet BLOCKS inbound internet traffic
    priv_in_ok, priv_in_msg = router.route_packet("sub-app-1a", "10.0.11.20", is_inbound=True)
    print(f"  Inbound to Private Subnet (App): {priv_in_ok} ({priv_in_msg})")
    assert priv_in_ok is False

    # C: App Subnet can egress to internet via NAT Gateway
    egr_ok, egr_msg = router.route_packet("sub-app-1a", "198.51.100.1", is_inbound=False)
    print(f"  Outbound from App Subnet to Internet: {egr_ok} ({egr_msg})")
    assert egr_ok is True
    assert "NAT Gateway" in egr_msg

    # D: Database Subnet CANNOT reach internet (Zero route)
    db_egr_ok, db_egr_msg = router.route_packet("sub-db-1a", "198.51.100.1", is_inbound=False)
    print(f"  Outbound from DB Subnet to Internet: {db_egr_ok} ({db_egr_msg})")
    assert db_egr_ok is False
    assert "DROPPED" in db_egr_msg
    print("  ✓ VPC Security Boundaries Verified: Isolated DB has ZERO internet ingress or egress!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 2: RDS POSTGRESQL MULTI-AZ SYNCHRONOUS FAILOVER
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 2] Testing Amazon RDS Multi-AZ Synchronous Replication & Failover...")
    rds = AmazonRDSMultiAZCluster()

    # Commit transactions to Primary
    ok_tx, tx_msg = rds.write_transaction("INSERT INTO users (id, name) VALUES (1, 'alice');")
    assert ok_tx is True
    assert rds.primary_node.committed_wal_lsn == rds.standby_node.committed_wal_lsn
    print(f"  Primary in us-east-1a and Standby in us-east-1b synchronized at LSN {rds.primary_node.committed_wal_lsn}.")

    # Simulate Disaster: Datacenter failure in us-east-1a
    print("  SIMULATING DISASTER: Total power loss in Availability Zone 'us-east-1a'...")
    failover_report = rds.trigger_az_failure("us-east-1a")

    print(f"  Failover Triggered     : {failover_report['failover_triggered']}")
    print(f"  Promoted New Primary   : {failover_report['new_primary']} in us-east-1b")
    print(f"  Data Loss (WAL lag)    : {failover_report['data_loss_wal_lag']} (Zero RPO achieved!)")
    print(f"  DNS CNAME Swapped Target: {rds.active_dns_target}")

    assert failover_report["failover_triggered"] is True
    assert failover_report["data_loss_wal_lag"] == 0, "Multi-AZ must guarantee 0 data loss!"
    assert rds.active_dns_target == "prod-pg-1b"
    print("  ✓ RDS Multi-AZ Failover Verified: Automatic promotion with 0 data loss and DNS flip!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 3: S3 DIRECT PRE-SIGNED UPLOAD EFFICIENCY
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 3] Comparing Server Proxied vs S3 Pre-Signed Uploads (50 MB File)...")
    s3_pipe = S3DirectUploadPipeline()
    proxied = s3_pipe.simulate_server_proxied_upload(50.0)
    presigned = s3_pipe.simulate_presigned_direct_upload(50.0)

    print(f"  Server-Proxied Upload : Consumed {proxied['ec2_ram_consumed_mb']:.1f} MB EC2 RAM, {proxied['ec2_total_network_mb']:.1f} MB Network I/O")
    print(f"  S3 Pre-Signed Upload  : Consumed {presigned['ec2_ram_consumed_mb']:.4f} MB EC2 RAM, {presigned['ec2_total_network_mb']:.4f} MB Network I/O")

    ram_savings_pct = ((proxied["ec2_ram_consumed_mb"] - presigned["ec2_ram_consumed_mb"]) / proxied["ec2_ram_consumed_mb"]) * 100.0
    print(f"  ✓ S3 Direct Upload Optimization: {ram_savings_pct:.2f}% reduction in server memory and network load!")
    assert ram_savings_pct > 99.0

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 4: CLOUDWATCH METRIC ALARMS & SNS WATCHDOG
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 4] Testing CloudWatch Metric Alarms & Alerts...")
    cw = CloudWatchWatchdog()
    cw.create_alarm("RDS-Low-Storage", "FreeStorageSpaceGB", 10.0, "LessThanThreshold")
    cw.create_alarm("EC2-High-CPU", "CPUUtilization", 85.0, "GreaterThanThreshold")

    # Ingest normal values
    cw.ingest_metric("FreeStorageSpaceGB", 45.0)
    cw.ingest_metric("CPUUtilization", 42.0)
    assert len(cw.dispatched_alerts) == 0

    # Trigger breach on low storage
    cw.ingest_metric("FreeStorageSpaceGB", 6.2)
    assert len(cw.dispatched_alerts) == 1
    print(f"  ✓ Dispatched Alert: {cw.dispatched_alerts[0]}")
    assert "RDS-Low-Storage" in cw.dispatched_alerts[0]

    # --------------------------------------------------------------------------
    # BENCHMARK: PACKET ROUTING & FAILOVER SIMULATION THROUGHPUT
    # --------------------------------------------------------------------------
    print("\n[BENCHMARK] Measuring VPC Packet Routing & RDS Write Throughput...")
    iterations = 25000
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = router.route_packet("sub-app-1a", "198.51.100.1")
        _ = rds.write_transaction("INSERT INTO metric VALUES (1);")
    elapsed = time.perf_counter() - t0
    ops_sec = iterations / elapsed

    print(f"  Executed {iterations:,} VPC packet routings and synchronous writes in {elapsed:.3f}s ({ops_sec:,.0f} ops/sec)")
    print(f"  Mean latency: {(elapsed / iterations) * 1000.0:.3f} ms")

    print("\n" + "=" * 80)
    print(" ALL DAY 64 AWS ARCHITECTURE SIMULATOR DIAGNOSTICS COMPLETED CLEANLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_aws_architecture_diagnostics()
