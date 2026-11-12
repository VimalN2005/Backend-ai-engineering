"""
Day 61: Docker Fundamentals, Dockerfile Best Practices & Multi-Stage Builds
=============================================================================
Core Concept / Working Code: Dockerfile AST Analyzer & Layer Cache Simulator

This module provides a production-grade Python tool that parses, analyzes, and
simulates Docker image construction, BuildKit layer caching mechanics, and
security best practices for containerized Python applications.

Key Architectural Capabilities:
1. Dockerfile AST Parser: Tokens, directives, and instruction tree generation.
2. Layer Cache Simulator: Models BuildKit's cryptographic layer hashing and
   sequential top-down cache invalidation triggers.
3. Security & Best Practice Linter: Evaluates 10 non-negotiable container rules.
4. Multi-Stage Size Model: Quantifies disk space and attack surface reduction.
5. Automated Test Suite & Benchmark Suite.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. DOCKERFILE ABSTRACT SYNTAX TREE (AST) DATA STRUCTURES
# ==============================================================================

@dataclasses.dataclass(frozen=True)
class DockerInstruction:
    """Represents a single parsed Dockerfile instruction."""
    line_number: int
    directive: str  # FROM, RUN, COPY, ADD, ENV, WORKDIR, USER, EXPOSE, CMD, ENTRYPOINT, HEALTHCHECK
    raw_args: str
    stage_name: Optional[str] = None
    is_multiline: bool = False

    @property
    def is_exec_form(self) -> bool:
        """Determines if the command is formatted as a JSON array (exec form)."""
        stripped = self.raw_args.strip()
        return stripped.startswith("[") and stripped.endswith("]")

    @property
    def parsed_args(self) -> List[str]:
        """Parses arguments into tokens, respecting exec JSON or shell words."""
        if self.is_exec_form:
            try:
                return json.loads(self.raw_args.strip())
            except json.JSONDecodeError:
                return self.raw_args.split()
        return self.raw_args.split()


@dataclasses.dataclass
class DockerBuildStage:
    """Represents a logical stage within a multi-stage Dockerfile."""
    stage_index: int
    stage_name: str
    base_image: str
    instructions: List[DockerInstruction] = dataclasses.field(default_factory=list)


# ==============================================================================
# 2. DOCKERFILE PARSER IMPLEMENTATION
# ==============================================================================

class DockerfileParser:
    """
    Parses a Dockerfile text document into distinct build stages and structured
    instruction AST nodes, handling continuation lines and stage aliases.
    """

    DIRECTIVE_PATTERN = re.compile(r"^\s*([A-Z]+)\s+(.*)$", re.IGNORECASE)
    FROM_PATTERN = re.compile(r"^FROM\s+([^\s]+)(?:\s+AS\s+([^\s]+))?", re.IGNORECASE)

    def parse(self, dockerfile_content: str) -> List[DockerBuildStage]:
        """
        Parses raw Dockerfile text into an ordered list of build stages.
        """
        raw_lines = dockerfile_content.splitlines()
        stages: List[DockerBuildStage] = []
        current_stage: Optional[DockerBuildStage] = None
        current_instruction_lines: List[Tuple[int, str]] = []
        is_continuation = False

        for line_num, line in enumerate(raw_lines, start=1):
            stripped = line.strip()

            # Ignore empty lines and comments unless inside multiline instruction
            if not stripped or stripped.startswith("#"):
                if not is_continuation:
                    continue

            # Check for line continuation character ('\')
            if stripped.endswith("\\"):
                current_instruction_lines.append((line_num, stripped[:-1].strip()))
                is_continuation = True
                continue
            else:
                current_instruction_lines.append((line_num, stripped))
                is_continuation = False

            # Combine multi-line tokens into a single cohesive instruction
            full_instruction_text = " ".join(t[1] for t in current_instruction_lines).strip()
            first_line_num = current_instruction_lines[0][0]
            is_multi = len(current_instruction_lines) > 1
            current_instruction_lines.clear()

            match = self.DIRECTIVE_PATTERN.match(full_instruction_text)
            if not match:
                continue

            directive = match.group(1).upper()
            raw_args = match.group(2).strip()

            if directive == "FROM":
                from_match = self.FROM_PATTERN.match(full_instruction_text)
                base_image = from_match.group(1) if from_match else raw_args
                alias = from_match.group(2) if (from_match and from_match.group(2)) else f"stage_{len(stages)}"

                current_stage = DockerBuildStage(
                    stage_index=len(stages),
                    stage_name=alias,
                    base_image=base_image,
                )
                stages.append(current_stage)

            instruction = DockerInstruction(
                line_number=first_line_num,
                directive=directive,
                raw_args=raw_args,
                stage_name=current_stage.stage_name if current_stage else "global",
                is_multiline=is_multi,
            )

            if current_stage:
                current_stage.instructions.append(instruction)

        return stages


# ==============================================================================
# 3. BUILDKIT LAYER CACHING SIMULATOR
# ==============================================================================

@dataclasses.dataclass
class LayerCacheEntry:
    """Represents a computed image layer and its caching metadata."""
    layer_index: int
    directive: str
    command: str
    layer_digest: str
    cache_hit: bool
    estimated_size_mb: float
    reason: str


class LayerCacheSimulator:
    """
    Simulates Docker BuildKit's cryptographic layer digest generation and sequential
    cache invalidation hierarchy.
    """

    def __init__(self, file_checksums: Dict[str, str]):
        """
        :param file_checksums: Mapping of workspace relative file paths to SHA256 checksums.
        """
        self.file_checksums = file_checksums

    def simulate_build(
        self,
        stage: DockerBuildStage,
        cached_layer_digests: Optional[List[str]] = None,
    ) -> List[LayerCacheEntry]:
        """
        Simulates the sequential build execution of a stage.

        CACHE INVARIANT:
        If a layer's hash changes (due to command modification or file checksum change),
        THAT layer and ALL subsequent layers in that stage are forcefully invalidated!
        """
        results: List[LayerCacheEntry] = []
        cache_busted = False
        running_parent_digest = "sha256:0000000000000000000000000000000000000000"

        for idx, inst in enumerate(stage.instructions):
            # 1. Compute layer inputs (Parent Digest + Directive + Command args + File Checksums)
            hasher = hashlib.sha256()
            hasher.update(running_parent_digest.encode("utf-8"))
            hasher.update(inst.directive.encode("utf-8"))
            hasher.update(inst.raw_args.encode("utf-8"))

            extra_checksum_info = ""
            if inst.directive in ("COPY", "ADD"):
                # Compute combined hash of files copied
                copied_files = [f for f in inst.parsed_args if not f.startswith("-") and f != "."]
                # If copying directory or specific files
                matched_hashes = []
                for path, chk in sorted(self.file_checksums.items()):
                    if any(path.startswith(cf) or cf in (".", "./") for cf in copied_files):
                        matched_hashes.append(f"{path}:{chk}")
                extra_checksum_info = "|".join(matched_hashes)
                hasher.update(extra_checksum_info.encode("utf-8"))

            current_layer_digest = "sha256:" + hasher.hexdigest()[:16]

            # 2. Check if cached
            is_hit = False
            reason = "Cache Hit"

            if cache_busted:
                is_hit = False
                reason = "Invalidated: Previous layer cache was busted"
            elif cached_layer_digests and idx < len(cached_layer_digests):
                if cached_layer_digests[idx] == current_layer_digest:
                    is_hit = True
                    reason = "Cache Hit: Layer digest identical"
                else:
                    is_hit = False
                    cache_busted = True
                    reason = f"Cache Miss: Digest changed from {cached_layer_digests[idx]} to {current_layer_digest}"
            else:
                is_hit = False
                cache_busted = True
                reason = "Cache Miss: No prior build cache available"

            # 3. Estimate layer size based on directive type
            layer_size = self._estimate_size(inst)

            results.append(
                LayerCacheEntry(
                    layer_index=idx,
                    directive=inst.directive,
                    command=inst.raw_args[:50] + ("..." if len(inst.raw_args) > 50 else ""),
                    layer_digest=current_layer_digest,
                    cache_hit=is_hit,
                    estimated_size_mb=layer_size,
                    reason=reason,
                )
            )

            running_parent_digest = current_layer_digest

        return results

    def _estimate_size(self, inst: DockerInstruction) -> float:
        """Heuristic estimation of layer size impact."""
        if inst.directive == "FROM":
            if "slim" in inst.raw_args:
                return 150.0
            elif "alpine" in inst.raw_args:
                return 50.0
            return 900.0  # Full ubuntu/debian python image
        elif inst.directive == "RUN":
            if "pip install" in inst.raw_args:
                return 65.0
            elif "apt-get" in inst.raw_args:
                if "build-essential" in inst.raw_args:
                    return 380.0
                return 45.0
            return 10.0
        elif inst.directive in ("COPY", "ADD"):
            if "requirements" in inst.raw_args:
                return 0.05
            return 15.0  # Application source code
        return 0.0  # Metadata directives (ENV, USER, WORKDIR, EXPOSE) add 0MB to filesystem


# ==============================================================================
# 4. SECURITY & BEST PRACTICE LINTER (10 INVARIANT RULES)
# ==============================================================================

@dataclasses.dataclass
class LintFinding:
    """Security or optimization finding reported on a Dockerfile."""
    rule_id: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    line_number: int
    message: str
    remediation: str


class DockerfileLinter:
    """
    Evaluates 10 non-negotiable enterprise container standards against a Dockerfile AST.
    """

    def lint(self, stages: List[DockerBuildStage], dockerignore_rules: List[str]) -> List[LintFinding]:
        findings: List[LintFinding] = []

        if not stages:
            findings.append(
                LintFinding(
                    rule_id="RULE-000",
                    severity="CRITICAL",
                    line_number=0,
                    message="Dockerfile is empty or contains no valid stages.",
                    remediation="Add a valid FROM instruction.",
                )
            )
            return findings

        # Rule 1: Multi-stage build check
        if len(stages) < 2:
            findings.append(
                LintFinding(
                    rule_id="RULE-001",
                    severity="HIGH",
                    line_number=stages[0].instructions[0].line_number if stages[0].instructions else 1,
                    message="Single-stage build detected. Build tools and compilers will leak into production image.",
                    remediation="Use a multi-stage build: 'FROM python:... AS builder' and 'FROM python:... AS runner'.",
                )
            )

        # Inspect final production stage (the last stage executed)
        final_stage = stages[-1]

        # Rule 2: Non-root user check
        user_declared = any(inst.directive == "USER" for inst in final_stage.instructions)
        if not user_declared:
            findings.append(
                LintFinding(
                    rule_id="RULE-002",
                    severity="CRITICAL",
                    line_number=final_stage.instructions[-1].line_number if final_stage.instructions else 1,
                    message="Container runs as root (UID 0). Violates Principle of Least Privilege.",
                    remediation="Create an unprivileged user ('useradd -u 10001 appuser') and switch to it using 'USER appuser'.",
                )
            )

        # Rule 3: Avoid 'latest' tag
        for stage in stages:
            if ":" not in stage.base_image or stage.base_image.endswith(":latest"):
                findings.append(
                    LintFinding(
                        rule_id="RULE-003",
                        severity="MEDIUM",
                        line_number=stage.instructions[0].line_number,
                        message=f"Base image '{stage.base_image}' uses implicit or explicit ':latest' tag.",
                        remediation="Pin specific immutable base image tag, e.g. 'python:3.11.8-slim-bookworm'.",
                    )
                )

        # Rule 4: Dependency layer ordering (COPY requirements before COPY .)
        copy_insts = [inst for inst in final_stage.instructions if inst.directive in ("COPY", "ADD")]
        saw_copy_all = False
        for c in copy_insts:
            args = c.raw_args.split()
            if any(a in (".", "./", "/app") for a in args):
                saw_copy_all = True
            if "requirements" in c.raw_args and saw_copy_all:
                findings.append(
                    LintFinding(
                        rule_id="RULE-004",
                        severity="HIGH",
                        line_number=c.line_number,
                        message="Dependency definition copied AFTER full source code copy. Breaks layer caching!",
                        remediation="Move 'COPY requirements.txt .' before 'COPY . .' to preserve pip install cache.",
                    )
                )

        # Rule 5: Exec form for CMD / ENTRYPOINT
        for inst in final_stage.instructions:
            if inst.directive in ("CMD", "ENTRYPOINT"):
                if not inst.is_exec_form:
                    findings.append(
                        LintFinding(
                            rule_id="RULE-005",
                            severity="HIGH",
                            line_number=inst.line_number,
                            message=f"{inst.directive} uses shell form ('{inst.raw_args}'). PID 1 signal forwarding will fail!",
                            remediation=f"Use exec JSON form: {inst.directive} [\"executable\", \"arg1\", \"arg2\"].",
                        )
                    )

        # Rule 6: Package manager cache cleanup in RUN layers
        for stage in stages:
            for inst in stage.instructions:
                if inst.directive == "RUN" and "apt-get install" in inst.raw_args:
                    if "rm -rf /var/lib/apt/lists" not in inst.raw_args:
                        findings.append(
                            LintFinding(
                                rule_id="RULE-006",
                                severity="MEDIUM",
                                line_number=inst.line_number,
                                message="apt-get install executed without cleaning '/var/lib/apt/lists/*'. Bloats image size.",
                                remediation="Chain command with '&& rm -rf /var/lib/apt/lists/*' in the same RUN layer.",
                            )
                        )

        # Rule 7: Python unbuffered output check
        has_unbuffered = False
        for inst in final_stage.instructions:
            if inst.directive == "ENV" and "PYTHONUNBUFFERED" in inst.raw_args:
                has_unbuffered = True
        if not has_unbuffered:
            findings.append(
                LintFinding(
                    rule_id="RULE-007",
                    severity="LOW",
                    line_number=1,
                    message="PYTHONUNBUFFERED is not set. Log output will be buffered and delayed in container stdout.",
                    remediation="Add 'ENV PYTHONUNBUFFERED=1' to ensure real-time log streaming.",
                )
            )

        # Rule 8: Container Healthcheck
        has_healthcheck = any(inst.directive == "HEALTHCHECK" for inst in final_stage.instructions)
        if not has_healthcheck:
            findings.append(
                LintFinding(
                    rule_id="RULE-008",
                    severity="MEDIUM",
                    line_number=final_stage.instructions[-1].line_number if final_stage.instructions else 1,
                    message="No HEALTHCHECK directive declared. Orchestrators cannot verify internal application health.",
                    remediation="Declare HEALTHCHECK instruction (e.g. 'HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1').",
                )
            )

        # Rule 9: Sensitive environment variables hardcoded
        sensitive_keywords = ["PASSWORD", "SECRET", "API_KEY", "PRIVATE_KEY", "TOKEN"]
        for stage in stages:
            for inst in stage.instructions:
                if inst.directive in ("ENV", "ARG"):
                    found_kw = next((kw for kw in sensitive_keywords if kw in inst.raw_args.upper()), None)
                    if found_kw:
                        findings.append(
                            LintFinding(
                                rule_id="RULE-009",
                                severity="CRITICAL",
                                line_number=inst.line_number,
                                message=f"Possible sensitive secret hardcoded in {inst.directive} (matched '{found_kw}'): '{inst.raw_args}'.",
                                remediation="Pass secrets at runtime via environment files, docker secrets, or BuildKit secret mounts.",
                            )
                        )

        # Rule 10: .dockerignore validation
        critical_ignores = [".git", ".env", "__pycache__", ".venv"]
        for ci in critical_ignores:
            if not any(ci in rule for rule in dockerignore_rules):
                findings.append(
                    LintFinding(
                        rule_id="RULE-010",
                        severity="HIGH",
                        line_number=0,
                        message=f".dockerignore is missing critical pattern: '{ci}'. Risk of secret/junk file leakage.",
                        remediation=f"Add '{ci}' to .dockerignore.",
                    )
                )

        return findings


# ==============================================================================
# 5. REFERENCE PRODUCTION DOCKERFILES FOR SIMULATION
# ==============================================================================

NAIVE_SINGLE_STAGE_DOCKERFILE = """
FROM python:3.11

# Running as root
WORKDIR /app

# Copying all files before requirements (bad cache order)
COPY . /app

RUN apt-get update && apt-get install -y build-essential libpq-dev
RUN pip install -r requirements.txt

ENV SECRET_API_KEY=supersecretpassword123

EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port 8000
"""

PRODUCTION_MULTI_STAGE_DOCKERFILE = """
# ==============================================================================
# STAGE 1: Builder
# ==============================================================================
FROM python:3.11.8-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# STAGE 2: Hardened Production Runner
# ==============================================================================
FROM python:3.11.8-slim-bookworm AS runner

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home appuser

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
COPY --chown=appuser:appgroup . /app

USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""


# ==============================================================================
# 6. INTEGRATION VERIFICATION & BENCHMARK SUITE
# ==============================================================================

def run_layer_cache_and_security_diagnostics() -> None:
    """Executes end-to-end linting, layer cache simulation, and size comparisons."""
    print("=" * 80)
    print(" DAY 61 DOCKER DIAGNOSTICS: AST LINTING & LAYER CACHE SIMULATION")
    print("=" * 80)

    parser = DockerfileParser()
    linter = DockerfileLinter()

    # Workspace mock state
    mock_files = {
        "requirements.txt": "a3f5c71d8e901234",
        "main.py": "b9012345e6789abc",
        "core/config.py": "c123456789abcdef",
    }
    mock_dockerignore_good = [".git", ".env", "__pycache__", ".venv", "*.log"]
    mock_dockerignore_bad = ["*.log"]

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 1: LINT NAIVE SINGLE-STAGE DOCKERFILE
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 1] Analyzing Anti-Pattern Single-Stage Dockerfile...")
    naive_stages = parser.parse(NAIVE_SINGLE_STAGE_DOCKERFILE)
    naive_findings = linter.lint(naive_stages, mock_dockerignore_bad)

    print(f"Total Security & Best-Practice Findings: {len(naive_findings)}")
    for f in naive_findings:
        print(f"  -> [{f.severity}] {f.rule_id} (Line {f.line_number}): {f.message}")
        print(f"     Fix: {f.remediation}")

    assert any(f.rule_id == "RULE-001" for f in naive_findings), "Expected single-stage warning"
    assert any(f.rule_id == "RULE-002" for f in naive_findings), "Expected root user warning"
    assert any(f.rule_id == "RULE-005" for f in naive_findings), "Expected exec form warning"
    assert any(f.rule_id == "RULE-009" for f in naive_findings), "Expected hardcoded secret warning"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 2: LINT PRODUCTION MULTI-STAGE DOCKERFILE
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 2] Analyzing Hardened Production Multi-Stage Dockerfile...")
    prod_stages = parser.parse(PRODUCTION_MULTI_STAGE_DOCKERFILE)
    prod_findings = linter.lint(prod_stages, mock_dockerignore_good)

    print(f"Total Security & Best-Practice Findings: {len(prod_findings)}")
    if prod_findings:
        for f in prod_findings:
            print(f"  -> [{f.severity}] {f.rule_id}: {f.message}")
    else:
        print("  ✓ ZERO DEFECTS: Dockerfile complies with 100% of production standards!")

    assert len(prod_findings) == 0, f"Expected 0 findings on production Dockerfile, got {len(prod_findings)}"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 3: LAYER CACHE SIMULATION & INVALIDATION CASCADE
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 3] Simulating BuildKit Layer Caching & Invalidation Flow...")
    cache_sim = LayerCacheSimulator(mock_files)

    # Initial Build (Cold Cache)
    initial_build = cache_sim.simulate_build(prod_stages[1])
    print("\nInitial Cold Build (Stage: runner):")
    cached_digests = []
    total_cold_size = 0.0
    for layer in initial_build:
        cached_digests.append(layer.layer_digest)
        total_cold_size += layer.estimated_size_mb
        status = "HIT " if layer.cache_hit else "MISS"
        print(f"  Layer {layer.layer_index:02d} [{status}] ({layer.estimated_size_mb:6.2f} MB) {layer.directive:<12} {layer.command:<35} | {layer.reason}")

    # Rebuild when only application code changes (main.py updated, requirements.txt untouched)
    print("\nIncremental Build: Application source updated (main.py changed, requirements unchanged):")
    mock_files_updated = dict(mock_files)
    mock_files_updated["main.py"] = "ffffffffffffffff"  # Changed hash

    cache_sim_incremental = LayerCacheSimulator(mock_files_updated)
    incremental_build = cache_sim_incremental.simulate_build(prod_stages[1], cached_layer_digests=cached_digests)

    for layer in incremental_build:
        status = "HIT " if layer.cache_hit else "MISS"
        print(f"  Layer {layer.layer_index:02d} [{status}] ({layer.estimated_size_mb:6.2f} MB) {layer.directive:<12} {layer.command:<35} | {layer.reason}")

    # Invariant Verification: Dependency layers must be cached hits
    venv_copy_layer = next(l for l in incremental_build if "opt/venv" in l.command)
    assert venv_copy_layer.cache_hit is True, "Dependency venv layer must hit cache when only code changes!"

    # --------------------------------------------------------------------------
    # DIAGNOSTIC 4: SINGLE-STAGE VS MULTI-STAGE IMAGE SIZE COMPARISON
    # --------------------------------------------------------------------------
    print("\n[DIAGNOSTIC 4] Production Size & Attack Surface Benchmark:")
    naive_build = cache_sim.simulate_build(naive_stages[0])
    naive_total_size = sum(l.estimated_size_mb for l in naive_build)

    runner_build = cache_sim.simulate_build(prod_stages[1])
    runner_total_size = sum(l.estimated_size_mb for l in runner_build)
    reduction_pct = ((naive_total_size - runner_total_size) / naive_total_size) * 100.0

    print(f"  Single-Stage Naive Image Size : {naive_total_size:8.1f} MB  (Contains build-essential, gcc, dev headers)")
    print(f"  Multi-Stage Hardened Size     : {runner_total_size:8.1f} MB  (Slim runtime, non-root, minimal libraries)")
    print(f"  Disk & Bandwidth Reduction    : {reduction_pct:8.1f}% SAVINGS")

    # --------------------------------------------------------------------------
    # BENCHMARK: PARSER THROUGHPUT
    # --------------------------------------------------------------------------
    print("\n[BENCHMARK] Measuring AST Parser & Linter Execution Throughput...")
    iterations = 2000
    t0 = time.perf_counter()
    for _ in range(iterations):
        parsed = parser.parse(PRODUCTION_MULTI_STAGE_DOCKERFILE)
        _ = linter.lint(parsed, mock_dockerignore_good)
    elapsed = time.perf_counter() - t0
    ops_sec = iterations / elapsed
    print(f"  Processed {iterations} full Dockerfile parses in {elapsed:.3f}s ({ops_sec:,.0f} ops/sec)")
    print(f"  Mean latency per validation: {(elapsed / iterations) * 1000.0:.3f} ms")

    print("\n" + "=" * 80)
    print(" ALL DAY 61 DIAGNOSTIC & VERIFICATION SUITES PASSED CLEANLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_layer_cache_and_security_diagnostics()
