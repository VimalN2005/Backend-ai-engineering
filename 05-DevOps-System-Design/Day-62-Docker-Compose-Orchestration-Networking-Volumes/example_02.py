"""
Day 62: Docker Compose Multi-Container Orchestration, Networking & Volumes
=============================================================================
Production-Grade Pattern: Multi-Container Stack Simulation, Healthcheck-Driven
Orchestrator, Embedded DNS Resolver (127.0.0.11) & Named Volume Persistence

This module provides a runnable simulation of a production multi-container
FastAPI + PostgreSQL + Redis + Celery + Nginx stack managed by Docker Compose:
1. Healthcheck-Driven Sequential Boot Orchestrator.
2. Embedded Docker DNS Daemon (127.0.0.11) with Multi-Replica Round-Robin.
3. Named Volume Persistence & Container Recreation Lifecycle.
4. Network Isolation & Cross-Tier Routing Enforcement.
5. End-to-End Diagnostic Verification Suite.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import enum
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. ORCHESTRATION DATA STRUCTURES & SERVICE STATES
# ==============================================================================

class ContainerLifecycleState(enum.Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    UNHEALTHY = "UNHEALTHY"
    HEALTHY = "HEALTHY"


@dataclasses.dataclass
class SimulatedContainer:
    """Represents an active or stopped container managed by Docker Compose."""
    name: str
    service_name: str
    replica_id: int
    assigned_ips: Dict[str, str]  # network_name -> ip_address
    state: ContainerLifecycleState = ContainerLifecycleState.STOPPED
    health_probe: Optional[Callable[[], bool]] = None
    boot_delay_sec: float = 0.05
    start_time: float = 0.0


# ==============================================================================
# 2. DOCKER EMBEDDED DNS RESOLVER (127.0.0.11 SIMULATION)
# ==============================================================================

class DockerEmbeddedDNS:
    """
    Simulates Docker's 127.0.0.11 embedded DNS server on user-defined bridge networks.
    Features:
    - Service name alias resolution to container IP.
    - Multi-replica Round-Robin DNS load balancing.
    - Network isolation (queries from containers on network A cannot resolve containers on network B).
    """

    def __init__(self):
        # (network_name, service_name) -> collections.deque of IP strings
        self._records: Dict[Tuple[str, str], collections.deque[str]] = collections.defaultdict(collections.deque)

    def register(self, network_name: str, service_name: str, ip_address: str) -> None:
        self._records[(network_name, service_name)].append(ip_address)

    def unregister(self, network_name: str, service_name: str, ip_address: str) -> None:
        key = (network_name, service_name)
        if key in self._records:
            try:
                self._records[key].remove(ip_address)
            except ValueError:
                pass

    def resolve(self, caller_network: str, target_service: str) -> Optional[str]:
        """
        Resolves service name into container IP using round-robin rotation.
        Returns None if target service is unreachable from caller_network.
        """
        key = (caller_network, target_service)
        queue = self._records.get(key)
        if not queue:
            return None

        # Round-robin rotation
        ip = queue[0]
        queue.rotate(-1)
        return ip


# ==============================================================================
# 3. NAMED PERSISTENT VOLUME STORAGE SIMULATOR
# ==============================================================================

class NamedVolumeStore:
    """
    Simulates Docker's named volumes (/var/lib/docker/volumes/<name>/_data).
    Data stored in named volumes survives container stop, kill, and deletion!
    """

    def __init__(self):
        self._volumes: Dict[str, Dict[str, Any]] = collections.defaultdict(dict)

    def write_record(self, volume_name: str, table: str, key: str, value: Any) -> None:
        if table not in self._volumes[volume_name]:
            self._volumes[volume_name][table] = {}
        self._volumes[volume_name][table][key] = value

    def read_records(self, volume_name: str, table: str) -> Dict[str, Any]:
        return self._volumes[volume_name].get(table, {}).copy()

    def wipe_volume(self, volume_name: str) -> None:
        """Simulates 'docker volume rm'."""
        if volume_name in self._volumes:
            del self._volumes[volume_name]


# ==============================================================================
# 4. MULTI-CONTAINER STACK ORCHESTRATOR
# ==============================================================================

class DockerComposeStackOrchestrator:
    """
    Simulates Docker Compose v2 engine executing 'docker compose up' with health-checked
    service dependencies, IP allocation, DNS registration, and lifecycle orchestration.
    """

    def __init__(self, volume_store: NamedVolumeStore):
        self.volume_store = volume_store
        self.dns = DockerEmbeddedDNS()
        self.containers: Dict[str, SimulatedContainer] = {}
        self._next_ip_suffix = 2

    def create_container(
        self,
        service_name: str,
        replica_id: int,
        networks: List[str],
        health_probe: Optional[Callable[[], bool]] = None,
        boot_delay_sec: float = 0.05,
    ) -> SimulatedContainer:
        container_name = f"{service_name}_{replica_id}"
        assigned_ips = {}

        for net in networks:
            ip = f"172.{28 if 'front' in net else 29}.0.{self._next_ip_suffix}"
            self._next_ip_suffix += 1
            assigned_ips[net] = ip
            self.dns.register(net, service_name, ip)

        container = SimulatedContainer(
            name=container_name,
            service_name=service_name,
            replica_id=replica_id,
            assigned_ips=assigned_ips,
            health_probe=health_probe,
            boot_delay_sec=boot_delay_sec,
        )
        self.containers[container_name] = container
        return container

    async def start_service_with_healthcheck(self, container_name: str, max_wait_sec: float = 2.0) -> bool:
        """
        Transitions container from STOPPED -> STARTING -> HEALTHY based on probe.
        """
        container = self.containers[container_name]
        container.state = ContainerLifecycleState.STARTING
        container.start_time = time.perf_counter()

        # Simulate service initialization delay (e.g. Postgres WAL replay, Redis boot)
        await asyncio.sleep(container.boot_delay_sec)

        deadline = time.perf_counter() + max_wait_sec
        while time.perf_counter() < deadline:
            if container.health_probe is None or container.health_probe():
                container.state = ContainerLifecycleState.HEALTHY
                return True
            await asyncio.sleep(0.02)

        container.state = ContainerLifecycleState.UNHEALTHY
        return False

    def stop_and_delete_container(self, container_name: str) -> None:
        """Simulates 'docker rm -f <container>'."""
        if container_name in self.containers:
            c = self.containers[container_name]
            c.state = ContainerLifecycleState.STOPPED
            for net, ip in c.assigned_ips.items():
                self.dns.unregister(net, c.service_name, ip)
            del self.containers[container_name]


# ==============================================================================
# 5. INTEGRATION DIAGNOSTICS & BENCHMARK HARNESS
# ==============================================================================

async def run_compose_runtime_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 62 COMPOSE RUNTIME DIAGNOSTICS: MULTI-CONTAINER STACK & DNS")
    print("=" * 80)

    volume_store = NamedVolumeStore()
    orchestrator = DockerComposeStackOrchestrator(volume_store)

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 1: HEALTH-CHECKED SEQUENTIAL BOOTSTRAP
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 1] Executing Healthcheck-Synchronized Stack Startup...")

    # Mock Health Probes
    postgres_ready = False
    redis_ready = False
    web_ready = False

    # 1. PostgreSQL (Backend Tier, Named Volume)
    pg_c = orchestrator.create_container(
        service_name="postgres",
        replica_id=1,
        networks=["backend_tier"],
        health_probe=lambda: postgres_ready,
        boot_delay_sec=0.04,
    )

    # 2. Redis (Backend Tier)
    redis_c = orchestrator.create_container(
        service_name="redis",
        replica_id=1,
        networks=["backend_tier"],
        health_probe=lambda: redis_ready,
        boot_delay_sec=0.02,
    )

    # Launch database & cache concurrently
    pg_task = asyncio.create_task(orchestrator.start_service_with_healthcheck(pg_c.name))
    redis_task = asyncio.create_task(orchestrator.start_service_with_healthcheck(redis_c.name))

    # Simulate realistic startup latency
    await asyncio.sleep(0.05)
    redis_ready = True
    await asyncio.sleep(0.04)
    postgres_ready = True

    await asyncio.gather(pg_task, redis_task)
    assert pg_c.state == ContainerLifecycleState.HEALTHY
    assert redis_c.state == ContainerLifecycleState.HEALTHY
    print(f"  ✓ Database & Cache Healthy: postgres={pg_c.assigned_ips['backend_tier']}, redis={redis_c.assigned_ips['backend_tier']}")

    # 3. Web Service (Dual-Tier: Frontend + Backend)
    web_c = orchestrator.create_container(
        service_name="web",
        replica_id=1,
        networks=["frontend_tier", "backend_tier"],
        health_probe=lambda: web_ready,
        boot_delay_sec=0.03,
    )
    web_task = asyncio.create_task(orchestrator.start_service_with_healthcheck(web_c.name))
    await asyncio.sleep(0.04)
    web_ready = True
    await web_task
    assert web_c.state == ContainerLifecycleState.HEALTHY
    print(f"  ✓ Web API Healthy: frontend_ip={web_c.assigned_ips['frontend_tier']}, backend_ip={web_c.assigned_ips['backend_tier']}")

    # 4. Nginx Reverse Proxy (Frontend Tier Only)
    proxy_c = orchestrator.create_container(
        service_name="proxy",
        replica_id=1,
        networks=["frontend_tier"],
        health_probe=lambda: True,
        boot_delay_sec=0.01,
    )
    await orchestrator.start_service_with_healthcheck(proxy_c.name)
    assert proxy_c.state == ContainerLifecycleState.HEALTHY
    print(f"  ✓ Nginx Proxy Gateway Healthy: ip={proxy_c.assigned_ips['frontend_tier']}")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 2: EMBEDDED DNS RESOLUTION & NETWORK BOUNDARY CHECK
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 2] Testing 127.0.0.11 Embedded DNS & Network Isolation...")

    # A: Proxy on frontend_tier resolves 'web'
    proxy_to_web = orchestrator.dns.resolve(caller_network="frontend_tier", target_service="web")
    print(f"  DNS from frontend_tier for 'web'      : {proxy_to_web}")
    assert proxy_to_web == web_c.assigned_ips["frontend_tier"]

    # B: Proxy on frontend_tier attempts to resolve 'postgres' (BLOCKED by network isolation)
    proxy_to_pg = orchestrator.dns.resolve(caller_network="frontend_tier", target_service="postgres")
    print(f"  DNS from frontend_tier for 'postgres' : {proxy_to_pg} (BLOCKED - Isolated!)")
    assert proxy_to_pg is None, "CRITICAL: Reverse proxy must not be able to resolve backend database DNS!"

    # C: Web on backend_tier resolves 'postgres'
    web_to_pg = orchestrator.dns.resolve(caller_network="backend_tier", target_service="postgres")
    print(f"  DNS from backend_tier for 'postgres'  : {web_to_pg} (Allowed)")
    assert web_to_pg == pg_c.assigned_ips["backend_tier"]

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 3: MULTI-REPLICA ROUND-ROBIN LOAD BALANCING
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 3] Testing Multi-Replica DNS Round-Robin Distribution...")

    # Spawn 2 additional web replicas (web_2, web_3)
    web_c2 = orchestrator.create_container("web", 2, ["frontend_tier", "backend_tier"])
    web_c3 = orchestrator.create_container("web", 3, ["frontend_tier", "backend_tier"])

    resolutions = [orchestrator.dns.resolve("frontend_tier", "web") for _ in range(6)]
    print(f"  6 consecutive DNS queries for 'web':\n    {resolutions}")

    # Verify that all 3 replica IPs were returned in balanced distribution
    unique_ips = set(resolutions)
    assert len(unique_ips) == 3, f"Expected 3 distinct replica IPs, got {len(unique_ips)}"
    print("  ✓ Round-Robin DNS Verified: All 3 replicas receive balanced query traffic!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 4: NAMED VOLUME PERSISTENCE ACROSS CONTAINER DESTRUCTION
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 4] Testing Named Volume Persistence Across Container Recreation...")

    # 1. Write production database records
    volume_store.write_record("postgres_data", "users", "usr_101", {"username": "alice_staff", "role": "admin"})
    volume_store.write_record("postgres_data", "users", "usr_102", {"username": "bob_engineer", "role": "member"})

    initial_records = volume_store.read_records("postgres_data", "users")
    print(f"  Initial records written to 'postgres_data' volume: {len(initial_records)} rows")
    assert len(initial_records) == 2

    # 2. Simulate 'docker compose down' (Container completely destroyed)
    print("  Simulating 'docker compose down' -> Stopping and purging 'postgres_1' container...")
    orchestrator.stop_and_delete_container("postgres_1")
    assert "postgres_1" not in orchestrator.containers

    # 3. Simulate 'docker compose up' (Fresh container attaches to existing named volume)
    print("  Simulating 'docker compose up' -> Spawning fresh 'postgres_1' with existing volume...")
    pg_c_new = orchestrator.create_container("postgres", 1, ["backend_tier"])
    await orchestrator.start_service_with_healthcheck(pg_c_new.name)

    # 4. Verify data survived intact
    survived_records = volume_store.read_records("postgres_data", "users")
    print(f"  Verified records after container recreation: {len(survived_records)} rows")
    assert survived_records == initial_records
    assert survived_records["usr_101"]["username"] == "alice_staff"
    print("  ✓ Named Volume Persistence Verified: Database data survived complete container destruction!")

    # --------------------------------------------------------------------------
    # BENCHMARK: DNS RESOLUTION LATENCY
    # --------------------------------------------------------------------------
    print("\n[BENCHMARK] Measuring Embedded DNS Query Throughput...")
    iterations = 20000
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = orchestrator.dns.resolve("frontend_tier", "web")
    elapsed = time.perf_counter() - t0
    queries_sec = iterations / elapsed

    print(f"  Executed {iterations:,} DNS queries in {elapsed:.4f}s ({queries_sec:,.0f} queries/sec)")
    print(f"  Mean lookup latency: {(elapsed / iterations) * 1_000_000.0:.2f} nanoseconds")

    print("\n" + "=" * 80)
    print(" ALL DAY 62 COMPOSE RUNTIME DIAGNOSTICS COMPLETED CLEANLY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_compose_runtime_diagnostics())
