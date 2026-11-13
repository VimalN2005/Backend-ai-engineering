"""
Day 62: Docker Compose Multi-Container Orchestration, Networking & Volumes
=============================================================================
Core Concept / Working Code: Docker Compose DAG Validator & Network Topology

This module provides a production-grade Python engine that parses, validates,
and analyzes multi-container Docker Compose specifications:
1. Service Dependency Graph (DAG) construction using Kahn's Topological Sort.
2. Circular Dependency Detection.
3. 10 Enterprise Compose Invariant Rules Validator (Healthchecks, Isolation, Volumes).
4. Network Reachability Matrix Simulator (Inter-container DNS & firewall rules).
5. Automated Test Suite & Performance Benchmark.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. COMPOSE DATA STRUCTURES
# ==============================================================================

@dataclasses.dataclass
class ServiceNode:
    """Represents a container service declared in docker-compose.yml."""
    name: str
    image: Optional[str] = None
    build_context: Optional[str] = None
    networks: List[str] = dataclasses.field(default_factory=list)
    ports: List[str] = dataclasses.field(default_factory=list)
    expose: List[str] = dataclasses.field(default_factory=list)
    volumes: List[str] = dataclasses.field(default_factory=list)
    environment: Dict[str, str] = dataclasses.field(default_factory=dict)
    depends_on: Dict[str, Dict[str, str]] = dataclasses.field(default_factory=dict)
    healthcheck: Optional[Dict[str, Any]] = None
    restart_policy: Optional[str] = None
    logging_config: Optional[Dict[str, Any]] = None


@dataclasses.dataclass
class NetworkNode:
    """Represents a virtual bridge network declared in docker-compose.yml."""
    name: str
    driver: str = "bridge"
    internal: bool = False
    subnet: Optional[str] = None


@dataclasses.dataclass
class ValidationFinding:
    """Represents an architectural finding or defect identified in the Compose topology."""
    rule_id: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    service: str
    message: str
    remediation: str


# ==============================================================================
# 2. DAG COMPILER & TOPOLOGICAL SORT (KAHN'S ALGORITHM)
# ==============================================================================

class CircularDependencyError(Exception):
    """Raised when services in docker-compose.yml contain a cyclic dependency."""
    pass


class ComposeDAGResolver:
    """
    Constructs a Directed Acyclic Graph (DAG) of service startup dependencies
    and computes the deterministic container boot sequence using Kahn's Algorithm.
    """

    def resolve_startup_sequence(self, services: Dict[str, ServiceNode]) -> List[str]:
        """
        Computes linearized boot order where dependencies start before dependents.
        """
        in_degree: Dict[str, int] = {s: 0 for s in services}
        adjacency: Dict[str, List[str]] = {s: [] for s in services}

        # Build dependency edges: if A depends_on B, edge is B -> A (B must start before A)
        for s_name, s_node in services.items():
            for dep_name in s_node.depends_on.keys():
                if dep_name not in services:
                    raise ValueError(f"Service '{s_name}' depends on undeclared service '{dep_name}'")
                adjacency[dep_name].append(s_name)
                in_degree[s_name] += 1

        # Queue services with 0 dependencies
        queue = collections.deque([s for s, deg in in_degree.items() if deg == 0])
        startup_order: List[str] = []

        while queue:
            current = queue.popleft()
            startup_order.append(current)

            for dependent in adjacency[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(startup_order) != len(services):
            cyclic = [s for s, deg in in_degree.items() if deg > 0]
            raise CircularDependencyError(
                f"Cyclic dependency detected in Docker Compose graph involving services: {cyclic}"
            )

        return startup_order


# ==============================================================================
# 3. ENTERPRISE COMPOSE ARCHITECTURAL LINTER (10 INVARIANTS)
# ==============================================================================

class DockerComposeLinter:
    """
    Validates Docker Compose topology against 10 production enterprise standards.
    """

    def lint(
        self,
        services: Dict[str, ServiceNode],
        networks: Dict[str, NetworkNode],
        volumes: List[str],
    ) -> List[ValidationFinding]:
        findings: List[ValidationFinding] = []

        # 1. Check DAG Circular Dependencies
        resolver = ComposeDAGResolver()
        try:
            resolver.resolve_startup_sequence(services)
        except CircularDependencyError as e:
            findings.append(
                ValidationFinding(
                    rule_id="RULE-CMP-001",
                    severity="CRITICAL",
                    service="GLOBAL",
                    message=str(e),
                    remediation="Break cyclic dependency by redesigning service communication or using message brokers.",
                )
            )

        # Iterate individual services
        for name, svc in services.items():
            # Rule 2: Healthcheck Condition in depends_on
            for dep_name, condition_dict in svc.depends_on.items():
                cond = condition_dict.get("condition", "service_started")
                dep_svc = services.get(dep_name)
                # If dependency is a database/cache and condition is NOT service_healthy
                if dep_svc and any(kw in dep_name.lower() for kw in ("postgres", "redis", "mysql", "mongo", "db")):
                    if cond != "service_healthy":
                        findings.append(
                            ValidationFinding(
                                rule_id="RULE-CMP-002",
                                severity="HIGH",
                                service=name,
                                message=f"Service depends on database '{dep_name}' using '{cond}' instead of 'service_healthy'. Risk of TCP startup race condition!",
                                remediation=f"Change to: depends_on.{dep_name}.condition: service_healthy.",
                            )
                        )

            # Rule 3: Missing Healthcheck on Stateful Services
            if any(kw in name.lower() for kw in ("postgres", "redis", "db", "web", "api")):
                if not svc.healthcheck:
                    findings.append(
                        ValidationFinding(
                            rule_id="RULE-CMP-003",
                            severity="HIGH",
                            service=name,
                            message=f"Stateful or web service '{name}' has no healthcheck declared.",
                            remediation="Declare explicit healthcheck: test, interval, timeout, retries.",
                        )
                    )

            # Rule 4: Host Port Leak on Internal Databases
            if any(kw in name.lower() for kw in ("postgres", "redis", "mysql", "mongo")):
                for port_mapping in svc.ports:
                    # e.g. "5432:5432" or "0.0.0.0:5432:5432"
                    if ":" in port_mapping and not port_mapping.startswith("127.0.0.1:"):
                        findings.append(
                            ValidationFinding(
                                rule_id="RULE-CMP-004",
                                severity="CRITICAL",
                                service=name,
                                message=f"Database port '{port_mapping}' is exposed to public host interface! Violates network isolation.",
                                remediation="Remove 'ports:' from database service and use internal Compose networks or 'expose:' only.",
                            )
                        )

            # Rule 5: Named Volume Persistence for Databases
            if any(kw in name.lower() for kw in ("postgres", "mysql", "mongo")):
                has_named_vol = False
                for v in svc.volumes:
                    # e.g. "postgres_data:/var/lib/postgresql/data"
                    source = v.split(":")[0]
                    if source in volumes:
                        has_named_vol = True
                if not has_named_vol:
                    findings.append(
                        ValidationFinding(
                            rule_id="RULE-CMP-005",
                            severity="CRITICAL",
                            service=name,
                            message=f"Database '{name}' data directory does not use a persistent named volume! Data lost on container teardown.",
                            remediation="Mount a named volume defined under root 'volumes:' (e.g. postgres_data:/var/lib/postgresql/data).",
                        )
                    )

            # Rule 6: Network Tier Segmentation (Database should not be on frontend network)
            if any(kw in name.lower() for kw in ("postgres", "mysql", "redis", "worker")):
                if any("front" in net.lower() or "public" in net.lower() for net in svc.networks):
                    findings.append(
                        ValidationFinding(
                            rule_id="RULE-CMP-006",
                            severity="HIGH",
                            service=name,
                            message=f"Backend/Database service '{name}' is attached to frontend public network! Violates tier segmentation.",
                            remediation="Attach service exclusively to isolated 'backend_tier' network.",
                        )
                    )

            # Rule 7: Restart Policy
            if not svc.restart_policy or svc.restart_policy == "no":
                findings.append(
                    ValidationFinding(
                        rule_id="RULE-CMP-007",
                        severity="MEDIUM",
                        service=name,
                        message=f"Service '{name}' has no auto-restart policy. Will remain dead after a crash.",
                        remediation="Set 'restart: unless-stopped' or 'restart: on-failure'.",
                    )
                )

            # Rule 8: Log Rotation Limits
            if not svc.logging_config or "options" not in svc.logging_config:
                findings.append(
                    ValidationFinding(
                        rule_id="RULE-CMP-008",
                        severity="MEDIUM",
                        service=name,
                        message=f"Service '{name}' lacks logging constraints. Can cause host root disk exhaustion.",
                        remediation="Add logging: driver: json-file, options: { max-size: '10m', max-file: '5' }.",
                    )
                )

            # Rule 9: Plaintext Secret Leakage in Environment
            sensitive_kw = ["PASSWORD", "SECRET", "PRIVATE_KEY", "API_KEY"]
            for k, val in svc.environment.items():
                if any(sk in k.upper() for sk in sensitive_kw):
                    # Check if hardcoded plaintext rather than variable interpolation
                    if not val.startswith("${") and len(val) > 0 and val != "placeholder":
                        findings.append(
                            ValidationFinding(
                                rule_id="RULE-CMP-009",
                                severity="HIGH",
                                service=name,
                                message=f"Plaintext secret hardcoded in environment: '{k}={val}'.",
                                remediation=f"Use variable interpolation '${{{k}}}' or Docker secrets.",
                            )
                        )

        # Rule 10: Backend Network Internal Flag
        backend_nets = [n for name, n in networks.items() if "back" in name.lower() or "data" in name.lower()]
        for bn in backend_nets:
            if not bn.internal:
                findings.append(
                    ValidationFinding(
                        rule_id="RULE-CMP-010",
                        severity="MEDIUM",
                        service="NETWORKS",
                        message=f"Backend network '{bn.name}' is not marked 'internal: true'. Allows accidental external internet access.",
                        remediation=f"Set 'networks.{bn.name}.internal: true'.",
                    )
                )

        return findings


# ==============================================================================
# 4. NETWORK REACHABILITY MATRIX SIMULATOR
# ==============================================================================

class ComposeNetworkSimulator:
    """
    Simulates inter-container packet routing and DNS reachability based on
    Docker bridge network memberships.
    """

    def compute_reachability(self, services: Dict[str, ServiceNode]) -> Dict[str, Set[str]]:
        """
        Determines which services can communicate with which other services.
        RULE: In Docker, two containers can communicate IF AND ONLY IF they share
        at least one common user-defined bridge network!
        """
        matrix: Dict[str, Set[str]] = {s: set() for s in services}

        for s1_name, s1_node in services.items():
            for s2_name, s2_node in services.items():
                if s1_name == s2_name:
                    continue
                # Find network intersection
                shared_networks = set(s1_node.networks).intersection(set(s2_node.networks))
                if shared_networks:
                    matrix[s1_name].add(s2_name)

        return matrix


# ==============================================================================
# 5. INTEGRATION VERIFICATION & BENCHMARK SUITE
# ==============================================================================

def run_compose_dag_and_topology_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 62 COMPOSE DIAGNOSTICS: DAG SCHEDULER & TOPOLOGY VALIDATOR")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # SETUP MOCK CONFIGURATIONS: NAIVE VS PRODUCTION
    # --------------------------------------------------------------------------
    # 1. Anti-Pattern Naive Setup
    naive_services = {
        "web": ServiceNode(
            name="web",
            networks=["default_flat"],
            ports=["8000:8000"],
            depends_on={"postgres": {"condition": "service_started"}},  # Race condition!
            restart_policy=None,
        ),
        "postgres": ServiceNode(
            name="postgres",
            networks=["default_flat"],
            ports=["5432:5432"],  # Port leak!
            volumes=["./local_pg_data:/var/lib/postgresql/data"],  # Host bind mount!
            environment={"POSTGRES_PASSWORD": "supersecretpassword123"},  # Hardcoded!
            healthcheck=None,
            restart_policy=None,
        ),
    }
    naive_networks = {"default_flat": NetworkNode(name="default_flat", internal=False)}
    naive_volumes = []

    # 2. Hardened Production Enterprise Topology
    prod_services = {
        "proxy": ServiceNode(
            name="proxy",
            networks=["frontend_tier"],
            ports=["80:80", "443:443"],
            depends_on={"web": {"condition": "service_healthy"}},
            restart_policy="unless-stopped",
            logging_config={"driver": "json-file", "options": {"max-size": "10m", "max-file": "5"}},
        ),
        "web": ServiceNode(
            name="web",
            networks=["frontend_tier", "backend_tier"],
            expose=["8000"],
            depends_on={
                "postgres": {"condition": "service_healthy"},
                "redis": {"condition": "service_healthy"},
            },
            healthcheck={"test": ["CMD", "curl", "-f", "http://localhost:8000/health"]},
            restart_policy="unless-stopped",
            environment={"DATABASE_URL": "${DATABASE_URL}", "REDIS_URL": "${REDIS_URL}"},
            logging_config={"driver": "json-file", "options": {"max-size": "10m", "max-file": "5"}},
        ),
        "worker": ServiceNode(
            name="worker",
            networks=["backend_tier"],
            depends_on={
                "postgres": {"condition": "service_healthy"},
                "redis": {"condition": "service_healthy"},
            },
            restart_policy="unless-stopped",
            logging_config={"driver": "json-file", "options": {"max-size": "10m", "max-file": "5"}},
        ),
        "postgres": ServiceNode(
            name="postgres",
            networks=["backend_tier"],
            volumes=["postgres_data:/var/lib/postgresql/data"],
            healthcheck={"test": ["CMD-SHELL", "pg_isready -U app_user"]},
            restart_policy="unless-stopped",
            environment={"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"},
            logging_config={"driver": "json-file", "options": {"max-size": "10m", "max-file": "5"}},
        ),
        "redis": ServiceNode(
            name="redis",
            networks=["backend_tier"],
            volumes=["redis_data:/data"],
            healthcheck={"test": ["CMD", "redis-cli", "ping"]},
            restart_policy="unless-stopped",
            logging_config={"driver": "json-file", "options": {"max-size": "10m", "max-file": "5"}},
        ),
    }
    prod_networks = {
        "frontend_tier": NetworkNode(name="frontend_tier", internal=False),
        "backend_tier": NetworkNode(name="backend_tier", internal=True),
    }
    prod_volumes = ["postgres_data", "redis_data"]

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 1: LINT NAIVE ANTI-PATTERN TOPOLOGY
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 1] Evaluating Anti-Pattern Naive Compose Topology...")
    linter = DockerComposeLinter()
    naive_findings = linter.lint(naive_services, naive_networks, naive_volumes)

    print(f"Total Defects Intercepted: {len(naive_findings)}")
    for f in naive_findings:
        print(f"  -> [{f.severity}] {f.rule_id} ({f.service}): {f.message}")

    assert any(f.rule_id == "RULE-CMP-002" for f in naive_findings), "Expected unconditioned dependency warning"
    assert any(f.rule_id == "RULE-CMP-004" for f in naive_findings), "Expected host port leak warning"
    assert any(f.rule_id == "RULE-CMP-005" for f in naive_findings), "Expected missing named volume warning"
    assert any(f.rule_id == "RULE-CMP-009" for f in naive_findings), "Expected plaintext secret warning"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 2: LINT PRODUCTION ENTERPRISE TOPOLOGY
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 2] Evaluating Hardened Production Topology...")
    prod_findings = linter.lint(prod_services, prod_networks, prod_volumes)

    print(f"Total Defects on Production Spec: {len(prod_findings)}")
    if prod_findings:
        for f in prod_findings:
            print(f"  -> [{f.severity}] {f.rule_id} ({f.service}): {f.message}")
    else:
        print("  ✓ ZERO DEFECTS: Production Compose Topology satisfies 100% of architectural standards!")

    assert len(prod_findings) == 0, f"Expected 0 findings on production spec, got {len(prod_findings)}"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 3: SERVICE STARTUP DAG RESOLUTION (KAHN'S ALGORITHM)
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 3] Computing Deterministic Service Startup Sequence...")
    resolver = ComposeDAGResolver()
    boot_sequence = resolver.resolve_startup_sequence(prod_services)

    print("Computed Startup Order:")
    for idx, svc in enumerate(boot_sequence, start=1):
        print(f"  Step {idx}: Launch container '{svc}'")

    # Invariants: postgres and redis must boot before web and worker; web must boot before proxy
    assert boot_sequence.index("postgres") < boot_sequence.index("web")
    assert boot_sequence.index("redis") < boot_sequence.index("web")
    assert boot_sequence.index("web") < boot_sequence.index("proxy")
    print("  ✓ Startup Sequence Verified: Dependencies precede dependents with zero race hazards!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 4: NETWORK REACHABILITY & TIER SEGREGATION
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 4] Verifying Inter-Container Network Reachability Matrix...")
    net_sim = ComposeNetworkSimulator()
    matrix = net_sim.compute_reachability(prod_services)

    for src, reachable in sorted(matrix.items()):
        targets = ", ".join(sorted(reachable)) if reachable else "NONE (Isolated)"
        print(f"  Container '{src:<7}' can communicate with: [{targets}]")

    # Invariant: Public Proxy can talk to web, but CANNOT talk to database or redis!
    assert "web" in matrix["proxy"]
    assert "postgres" not in matrix["proxy"], "CRITICAL: Reverse proxy must NOT reach database directly!"
    assert "redis" not in matrix["proxy"], "CRITICAL: Reverse proxy must NOT reach redis directly!"

    # Invariant: Web can talk to both proxy (frontend) and postgres/redis (backend)
    assert "proxy" in matrix["web"]
    assert "postgres" in matrix["web"]
    assert "redis" in matrix["web"]
    print("  ✓ Tier Isolation Verified: Proxy cannot probe backend databases; DMZ enforced!")

    # --------------------------------------------------------------------------
    # BENCHMARK: TOPOLOGY RESOLUTION & LINT THROUGHPUT
    # --------------------------------------------------------------------------
    print("\n[BENCHMARK] Measuring DAG Resolution & Lint Execution Performance...")
    iterations = 2500
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = resolver.resolve_startup_sequence(prod_services)
        _ = linter.lint(prod_services, prod_networks, prod_volumes)
    elapsed = time.perf_counter() - t0
    ops_sec = iterations / elapsed

    print(f"  Evaluated {iterations} complete Compose topologies in {elapsed:.3f}s ({ops_sec:,.0f} ops/sec)")
    print(f"  Mean latency per validation: {(elapsed / iterations) * 1000.0:.3f} ms")

    print("\n" + "=" * 80)
    print(" ALL DAY 62 COMPOSE DAG & TOPOLOGY DIAGNOSTICS PASSED CLEANLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_compose_dag_and_topology_diagnostics()
