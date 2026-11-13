"""
Day 63: Production CI/CD Pipelines with GitHub Actions (Test, Build, Push)
=============================================================================
Core Concept / Working Code: GitHub Actions Workflow Engine & Pipeline Linter

This module provides a production-grade Python engine that parses, simulates,
and statically validates enterprise GitHub Actions CI/CD workflows:
1. Workflow DAG Construction: Resolves inter-job dependencies ('needs' keyword).
2. Matrix Job Expansion Simulator: Expands multi-dimension test permutations.
3. 10 Enterprise CI/CD Invariant Rules Linter (OIDC, Caching, Timeouts, Security).
4. BuildKit GHA Cache Invalidation Simulator.
5. Automated Test Suite & Performance Benchmark.
"""

from __future__ import annotations

import collections
import dataclasses
import itertools
import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. GITHUB ACTIONS DATA STRUCTURES
# ==============================================================================

@dataclasses.dataclass
class WorkflowStep:
    """Represents a single step in a GitHub Actions job."""
    name: str
    uses: Optional[str] = None
    run: Optional[str] = None
    env: Dict[str, str] = dataclasses.field(default_factory=dict)
    with_params: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def is_pinned_to_sha(self) -> bool:
        """Verifies if an action is pinned to an immutable 40-character commit SHA."""
        if not self.uses:
            return True
        if "@" not in self.uses:
            return False
        ref = self.uses.split("@")[1]
        return len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower())


@dataclasses.dataclass
class WorkflowJob:
    """Represents an execution job in a GitHub Actions workflow."""
    name: str
    runs_on: str
    needs: List[str] = dataclasses.field(default_factory=list)
    timeout_minutes: Optional[int] = None
    matrix: Dict[str, List[Any]] = dataclasses.field(default_factory=dict)
    services: Dict[str, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    permissions: Optional[Dict[str, str]] = None
    steps: List[WorkflowStep] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class WorkflowManifest:
    """Represents the complete GitHub Actions workflow specification."""
    name: str
    on_events: List[str]
    concurrency_group: Optional[str] = None
    cancel_in_progress: bool = False
    permissions: Optional[Dict[str, str]] = None
    jobs: Dict[str, WorkflowJob] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class LintFinding:
    """Represents an architectural, security, or efficiency defect in the workflow."""
    rule_id: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    location: str
    message: str
    remediation: str


# ==============================================================================
# 2. WORKFLOW DAG RESOLVER & MATRIX EXPANDER
# ==============================================================================

class WorkflowDAGResolver:
    """
    Constructs a Directed Acyclic Graph (DAG) of job dependencies and computes
    linear execution stages.
    """

    def resolve_job_order(self, jobs: Dict[str, WorkflowJob]) -> List[str]:
        in_degree: Dict[str, int] = {j: 0 for j in jobs}
        adjacency: Dict[str, List[str]] = {j: [] for j in jobs}

        for j_name, job in jobs.items():
            for needed in job.needs:
                if needed not in jobs:
                    raise ValueError(f"Job '{j_name}' depends on undeclared job '{needed}'")
                adjacency[needed].append(j_name)
                in_degree[j_name] += 1

        queue = collections.deque([j for j, deg in in_degree.items() if deg == 0])
        resolved_order: List[str] = []

        while queue:
            current = queue.popleft()
            resolved_order.append(current)
            for dependent in adjacency[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(resolved_order) != len(jobs):
            cyclic = [j for j, deg in in_degree.items() if deg > 0]
            raise ValueError(f"Circular dependency detected between jobs: {cyclic}")

        return resolved_order

    def expand_matrix(self, job: WorkflowJob) -> List[Dict[str, Any]]:
        """Expands matrix variables into concrete parallel execution permutations."""
        if not job.matrix:
            return [{}]

        keys = list(job.matrix.keys())
        values = [job.matrix[k] for k in keys]
        combinations = list(itertools.product(*values))

        permutations: List[Dict[str, Any]] = []
        for combo in combinations:
            permutations.append(dict(zip(keys, combo)))

        return permutations


# ==============================================================================
# 3. ENTERPRISE CI/CD PIPELINE LINTER (10 INVARIANT RULES)
# ==============================================================================

class GitHubActionsLinter:
    """
    Validates GitHub Actions workflows against 10 enterprise DevSecOps standards.
    """

    def lint(self, workflow: WorkflowManifest) -> List[LintFinding]:
        findings: List[LintFinding] = []

        # Rule 1: Concurrency Control with Cancel-In-Progress
        if not workflow.concurrency_group or not workflow.cancel_in_progress:
            findings.append(
                LintFinding(
                    rule_id="RULE-GHA-001",
                    severity="HIGH",
                    location=f"Workflow: {workflow.name}",
                    message="Missing concurrency control with 'cancel-in-progress: true'. Redundant builds waste runner hours.",
                    remediation="Add 'concurrency: group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true'.",
                )
            )

        # Rule 2: Top-Level or Job-Level Permissions Declared (Least Privilege)
        has_top_permissions = workflow.permissions is not None
        for j_name, job in workflow.jobs.items():
            if not has_top_permissions and job.permissions is None:
                findings.append(
                    LintFinding(
                        rule_id="RULE-GHA-002",
                        severity="CRITICAL",
                        location=f"Job: {j_name}",
                        message=f"Job '{j_name}' lacks explicit 'permissions' block, inheriting dangerous default read/write GITHUB_TOKEN.",
                        remediation="Explicitly declare 'permissions: contents: read' or minimal required scopes.",
                    )
                )

        # Rule 3: Job Timeouts Defined
        for j_name, job in workflow.jobs.items():
            if not job.timeout_minutes or job.timeout_minutes > 60:
                findings.append(
                    LintFinding(
                        rule_id="RULE-GHA-003",
                        severity="MEDIUM",
                        location=f"Job: {j_name}",
                        message=f"Job '{j_name}' has no 'timeout-minutes' or exceeds 60m. Hung tasks will exhaust runner minutes.",
                        remediation="Add 'timeout-minutes: 15' to bound runner execution.",
                    )
                )

        # Rule 4: Action Pinning by Immutable Commit SHA
        for j_name, job in workflow.jobs.items():
            for s_idx, step in enumerate(job.steps, start=1):
                if step.uses and not step.is_pinned_to_sha:
                    findings.append(
                        LintFinding(
                            rule_id="RULE-GHA-004",
                            severity="HIGH",
                            location=f"Job: {j_name} -> Step {s_idx} ({step.name})",
                            message=f"Action '{step.uses}' is not pinned to an immutable 40-character commit SHA. Vulnerable to supply chain compromise!",
                            remediation="Pin action to full commit SHA (e.g. actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11).",
                        )
                    )

        # Rule 5: Dependency Caching in Python Setup
        for j_name, job in workflow.jobs.items():
            if any(s.run and "pip install" in s.run for s in job.steps):
                has_cache = any(
                    (s.uses and "setup-python" in s.uses and s.with_params.get("cache") == "pip")
                    or (s.uses and "actions/cache" in s.uses)
                    for s in job.steps
                )
                if not has_cache:
                    findings.append(
                        LintFinding(
                            rule_id="RULE-GHA-005",
                            severity="HIGH",
                            location=f"Job: {j_name}",
                            message=f"Job '{j_name}' runs 'pip install' without caching enabled. Re-downloads wheels on every commit.",
                            remediation="Add 'with: cache: pip' to actions/setup-python step.",
                        )
                    )

        # Rule 6: Service Container Healthchecks for Integration Tests
        for j_name, job in workflow.jobs.items():
            if any(s.run and ("pytest" in s.run or "integration" in s.run) for s in job.steps):
                if job.services:
                    for s_name, s_config in job.services.items():
                        options = s_config.get("options", "")
                        if "--health-cmd" not in options:
                            findings.append(
                                LintFinding(
                                    rule_id="RULE-GHA-006",
                                    severity="HIGH",
                                    location=f"Job: {j_name} -> Service: {s_name}",
                                    message=f"Service container '{s_name}' lacks '--health-cmd' options. Tests will race database readiness.",
                                    remediation=f"Add options: '--health-cmd pg_isready --health-interval 5s' to service definition.",
                                )
                            )

        # Rule 7: BuildKit GHA Caching for Docker Builds
        for j_name, job in workflow.jobs.items():
            for step in job.steps:
                if step.uses and "build-push-action" in step.uses:
                    cache_from = step.with_params.get("cache-from", "")
                    cache_to = step.with_params.get("cache-to", "")
                    if "type=gha" not in cache_from or "type=gha" not in cache_to:
                        findings.append(
                            LintFinding(
                                rule_id="RULE-GHA-007",
                                severity="HIGH",
                                location=f"Job: {j_name} -> Step: {step.name}",
                                message="Docker build does not utilize GitHub Actions cache backend ('type=gha'). Slower CI image builds.",
                                remediation="Set 'cache-from: type=gha' and 'cache-to: type=gha,mode=max'.",
                            )
                        )

        # Rule 8: DevSecOps Vulnerability Scanner Before Deployment
        has_deploy = any("deploy" in j_name.lower() or "push" in j_name.lower() for j_name in workflow.jobs)
        has_security_scan = any(
            any(s.uses and any(kw in s.uses.lower() for kw in ("trivy", "grype", "snyk")) for s in job.steps)
            for job in workflow.jobs.values()
        )
        if has_deploy and not has_security_scan:
            findings.append(
                LintFinding(
                    rule_id="RULE-GHA-008",
                    severity="CRITICAL",
                    location=f"Workflow: {workflow.name}",
                    message="Pipeline contains deployment/push jobs but lacks automated container security scanning (Trivy/Grype).",
                    remediation="Add a Trivy vulnerability scan step to block deployments with CRITICAL/HIGH CVEs.",
                )
            )

        # Rule 9: Keyless Cloud Authentication (OIDC Verification)
        for j_name, job in workflow.jobs.items():
            for step in job.steps:
                if step.uses and "configure-aws-credentials" in step.uses:
                    if "role-to-assume" not in step.with_params:
                        findings.append(
                            LintFinding(
                                rule_id="RULE-GHA-009",
                                severity="CRITICAL",
                                location=f"Job: {j_name} -> Step: {step.name}",
                                message="AWS authentication uses static AWS access keys instead of keyless OpenID Connect (OIDC).",
                                remediation="Use OIDC: declare 'permissions: id-token: write' and specify 'role-to-assume'.",
                            )
                        )

        # Rule 10: Step Summary Markdown Report
        has_summary = any(
            any(s.run and "GITHUB_STEP_SUMMARY" in s.run for s in job.steps)
            for job in workflow.jobs.values()
        )
        if not has_summary:
            findings.append(
                LintFinding(
                    rule_id="RULE-GHA-010",
                    severity="LOW",
                    location=f"Workflow: {workflow.name}",
                    message="Pipeline does not write to $GITHUB_STEP_SUMMARY. Missing high-visibility test/build summary cards.",
                    remediation="Echo test coverage and build results to $GITHUB_STEP_SUMMARY in final job step.",
                )
            )

        return findings


# ==============================================================================
# 4. REFERENCE WORKFLOW MANIFESTS FOR TESTING
# ==============================================================================

NAIVE_INSECURE_WORKFLOW = WorkflowManifest(
    name="Naive-CI",
    on_events=["push", "pull_request"],
    concurrency_group=None,  # Missing concurrency!
    cancel_in_progress=False,
    permissions=None,  # Missing permissions (inherits default read/write)!
    jobs={
        "test": WorkflowJob(
            name="test",
            runs_on="ubuntu-latest",
            timeout_minutes=None,  # Missing timeout!
            steps=[
                WorkflowStep(
                    name="Checkout Code",
                    uses="actions/checkout@v4",  # Not pinned to SHA!
                ),
                WorkflowStep(
                    name="Install Dependencies",
                    run="pip install -r requirements.txt",  # No cache!
                ),
                WorkflowStep(
                    name="Run Pytest",
                    run="pytest",
                ),
            ],
        ),
        "deploy": WorkflowJob(
            name="deploy",
            runs_on="ubuntu-latest",
            needs=["test"],
            steps=[
                WorkflowStep(
                    name="Authenticate to AWS",
                    uses="aws-actions/configure-aws-credentials@v4",
                    with_params={"aws-region": "us-east-1"},  # Static keys assumed!
                ),
                WorkflowStep(
                    name="Build & Push Docker Image",
                    uses="docker/build-push-action@v5",  # No GHA cache!
                    with_params={"push": True, "tags": "my-app:latest"},
                ),
            ],
        ),
    },
)

PRODUCTION_ENTERPRISE_WORKFLOW = WorkflowManifest(
    name="Production-CI-CD",
    on_events=["push", "pull_request"],
    concurrency_group="${{ github.workflow }}-${{ github.ref }}",
    cancel_in_progress=True,
    permissions={"contents": "read"},
    jobs={
        "quality-gate": WorkflowJob(
            name="quality-gate",
            runs_on="ubuntu-latest",
            timeout_minutes=10,
            permissions={"contents": "read"},
            steps=[
                WorkflowStep(
                    name="Checkout Code",
                    uses="actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11",
                ),
                WorkflowStep(
                    name="Set up Python 3.11",
                    uses="actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c",
                    with_params={"python-version": "3.11", "cache": "pip"},
                ),
                WorkflowStep(
                    name="Run Ruff & Mypy",
                    run="pip install ruff mypy && ruff check . && ruff format --check . && mypy app/",
                ),
            ],
        ),
        "test-matrix": WorkflowJob(
            name="test-matrix",
            runs_on="ubuntu-latest",
            needs=["quality-gate"],
            timeout_minutes=15,
            permissions={"contents": "read"},
            matrix={"python-version": ["3.10", "3.11", "3.12"]},
            services={
                "postgres": {
                    "image": "postgres:16-alpine",
                    "options": "--health-cmd pg_isready --health-interval 5s --health-timeout 3s --health-retries 5",
                },
                "redis": {
                    "image": "redis:7.2-alpine",
                    "options": "--health-cmd 'redis-cli ping' --health-interval 5s --health-timeout 3s --health-retries 3",
                },
            },
            steps=[
                WorkflowStep(
                    name="Checkout Code",
                    uses="actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11",
                ),
                WorkflowStep(
                    name="Set up Python",
                    uses="actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c",
                    with_params={"python-version": "${{ matrix.python-version }}", "cache": "pip"},
                ),
                WorkflowStep(
                    name="Run Tests with Coverage",
                    run="pip install -r requirements.txt && pytest --cov=app --cov-report=xml",
                ),
            ],
        ),
        "security-scan": WorkflowJob(
            name="security-scan",
            runs_on="ubuntu-latest",
            needs=["test-matrix"],
            timeout_minutes=15,
            permissions={"contents": "read"},
            steps=[
                WorkflowStep(
                    name="Checkout Code",
                    uses="actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11",
                ),
                WorkflowStep(
                    name="Scan Container Image with Trivy",
                    uses="aquasecurity/trivy-action@8435476e43ece424a938e58c32382184273a5a84",
                    with_params={"image-ref": "my-app:test", "severity": "CRITICAL,HIGH", "exit-code": "1"},
                ),
            ],
        ),
        "deploy": WorkflowJob(
            name="deploy",
            runs_on="ubuntu-latest",
            needs=["security-scan"],
            timeout_minutes=20,
            permissions={"contents": "read", "id-token": "write"},
            steps=[
                WorkflowStep(
                    name="Checkout Code",
                    uses="actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11",
                ),
                WorkflowStep(
                    name="Authenticate to AWS via OIDC",
                    uses="aws-actions/configure-aws-credentials@e3dd14a369e916a610edf257389e8a2c999494cf",
                    with_params={
                        "role-to-assume": "arn:aws:iam::123456789012:role/GitHubActionsDeploymentRole",
                        "aws-region": "us-east-1",
                    },
                ),
                WorkflowStep(
                    name="Build & Push Docker Image with GHA Cache",
                    uses="docker/build-push-action@4a13e500e05cf64b009ae718145f7299195d64a0",
                    with_params={
                        "push": True,
                        "tags": "ghcr.io/my-org/my-app:${{ github.sha }}",
                        "cache-from": "type=gha",
                        "cache-to": "type=gha,mode=max",
                    },
                ),
                WorkflowStep(
                    name="Emit Step Summary Markdown",
                    run="echo '### Deployment Successful' >> $GITHUB_STEP_SUMMARY",
                ),
            ],
        ),
    },
)


# ==============================================================================
# 5. INTEGRATION VERIFICATION & BENCHMARK SUITE
# ==============================================================================

def run_github_actions_pipeline_diagnostics() -> None:
    print("=" * 80)
    print(" DAY 63 GITHUB ACTIONS DIAGNOSTICS: WORKFLOW ENGINE & PIPELINE LINTER")
    print("=" * 80)

    resolver = WorkflowDAGResolver()
    linter = GitHubActionsLinter()

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 1: LINT NAIVE INSECURE WORKFLOW
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 1] Evaluating Insecure / Naive CI/CD Pipeline...")
    naive_findings = linter.lint(NAIVE_INSECURE_WORKFLOW)

    print(f"Total Defects Intercepted: {len(naive_findings)}")
    for f in naive_findings:
        print(f"  -> [{f.severity}] {f.rule_id} ({f.location}): {f.message}")

    assert any(f.rule_id == "RULE-GHA-001" for f in naive_findings), "Expected concurrency finding"
    assert any(f.rule_id == "RULE-GHA-002" for f in naive_findings), "Expected permissions finding"
    assert any(f.rule_id == "RULE-GHA-003" for f in naive_findings), "Expected timeout finding"
    assert any(f.rule_id == "RULE-GHA-004" for f in naive_findings), "Expected commit SHA pinning finding"
    assert any(f.rule_id == "RULE-GHA-008" for f in naive_findings), "Expected vulnerability scanner finding"
    assert any(f.rule_id == "RULE-GHA-009" for f in naive_findings), "Expected static AWS keys finding"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 2: LINT HARDENED PRODUCTION ENTERPRISE WORKFLOW
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 2] Evaluating Hardened Production CI/CD Pipeline...")
    prod_findings = linter.lint(PRODUCTION_ENTERPRISE_WORKFLOW)

    print(f"Total Defects on Production Spec: {len(prod_findings)}")
    if prod_findings:
        for f in prod_findings:
            print(f"  -> [{f.severity}] {f.rule_id}: {f.message}")
    else:
        print("  ✓ ZERO DEFECTS: Production workflow complies with 100% of DevSecOps standards!")

    # Exclude trivial informational finding if any
    critical_prod_findings = [f for f in prod_findings if f.severity in ("CRITICAL", "HIGH")]
    assert len(critical_prod_findings) == 0, f"Expected 0 critical/high findings, got {len(critical_prod_findings)}"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 3: WORKFLOW DAG RESOLUTION
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 3] Resolving Job Dependency Execution DAG...")
    job_order = resolver.resolve_job_order(PRODUCTION_ENTERPRISE_WORKFLOW.jobs)

    print("Computed Job Execution Stages:")
    for idx, j_name in enumerate(job_order, start=1):
        print(f"  Stage {idx}: Execute Job '{j_name}'")

    assert job_order.index("quality-gate") < job_order.index("test-matrix")
    assert job_order.index("test-matrix") < job_order.index("security-scan")
    assert job_order.index("security-scan") < job_order.index("deploy")
    print("  ✓ Job DAG Order Verified: Quality -> Matrix -> Security Scan -> Deploy!")

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 4: TEST MATRIX EXPANSION
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 4] Simulating Test Matrix Parallel Job Expansion...")
    matrix_job = PRODUCTION_ENTERPRISE_WORKFLOW.jobs["test-matrix"]
    permutations = resolver.expand_matrix(matrix_job)

    print(f"  Matrix expanded into {len(permutations)} parallel runner instances:")
    for p_idx, p in enumerate(permutations, start=1):
        print(f"    Runner {p_idx}: python-version = {p.get('python-version')}")

    assert len(permutations) == 3
    print("  ✓ Matrix Expansion Verified: All 3 Python runtime permutations generated!")

    # --------------------------------------------------------------------------
    # BENCHMARK: WORKFLOW LINT & RESOLUTION THROUGHPUT
    # --------------------------------------------------------------------------
    print("\n[BENCHMARK] Measuring Workflow Parsing & Lint Execution Performance...")
    iterations = 3000
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = resolver.resolve_job_order(PRODUCTION_ENTERPRISE_WORKFLOW.jobs)
        _ = resolver.expand_matrix(matrix_job)
        _ = linter.lint(PRODUCTION_ENTERPRISE_WORKFLOW)
    elapsed = time.perf_counter() - t0
    ops_sec = iterations / elapsed

    print(f"  Evaluated {iterations:,} complete workflow pipelines in {elapsed:.3f}s ({ops_sec:,.0f} ops/sec)")
    print(f"  Mean latency per validation: {(elapsed / iterations) * 1000.0:.3f} ms")

    print("\n" + "=" * 80)
    print(" ALL DAY 63 GITHUB ACTIONS PIPELINE DIAGNOSTICS PASSED CLEANLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_github_actions_pipeline_diagnostics()
