# Day 36: Safe Database Migrations & Production Strategy Exercises

Three hands-on engineering challenges testing your mastery of migration dependency resolution (DAG), the zero-downtime Expand-and-Contract lifecycle, and pre-flight static migration safety linting.

---

## Exercise 1: Multi-App Migration Dependency DAG & Cycle Resolver

### Problem Statement:
In micro-modular Django applications, models frequently reference tables across different apps. Django builds a global Directed Acyclic Graph (DAG) to determine the exact execution sequence.

Build `MigrationDependencyResolver` that:
1. Accepts a collection of migration descriptors: each defined by `app`, `name`, and a list of `dependencies = [('other_app', '0001_name')]`.
2. Computes the valid linear execution order using topological sorting.
3. Raises `MissingDependencyError` if a declared dependency does not exist.
4. Raises `CircularDependencyError` if a circular cycle exists between apps.

### Complete Solution:

```python
"""
Exercise 1: Multi-App Migration Dependency DAG & Cycle Resolver
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple


class MigrationError(Exception):
    pass


class MissingDependencyError(MigrationError):
    pass


class CircularDependencyError(MigrationError):
    pass


@dataclass(frozen=True)
class MigrationNode:
    app: str
    name: str
    dependencies: Tuple[Tuple[str, str], ...]

    @property
    def key(self) -> Tuple[str, str]:
        return (self.app, self.name)


class MigrationDependencyResolver:
    """Computes linear execution order of migrations across multi-app architectures."""
    def __init__(self):
        self.nodes: Dict[Tuple[str, str], MigrationNode] = {}

    def add(self, node: MigrationNode) -> None:
        self.nodes[node.key] = node

    def resolve_execution_order(self) -> List[Tuple[str, str]]:
        in_degree: Dict[Tuple[str, str], int] = {k: 0 for k in self.nodes}
        adj_list: Dict[Tuple[str, str], List[Tuple[str, str]]] = collections.defaultdict(list)

        for key, node in self.nodes.items():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise MissingDependencyError(
                        f"Migration {key} depends on non-existent migration {dep}."
                    )
                adj_list[dep].append(key)
                in_degree[key] += 1

        queue = collections.deque([k for k, deg in in_degree.items() if deg == 0])
        plan: List[Tuple[str, str]] = []

        while queue:
            curr = queue.popleft()
            plan.append(curr)

            for neighbor in adj_list[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(plan) != len(self.nodes):
            unresolved = [k for k, deg in in_degree.items() if deg > 0]
            raise CircularDependencyError(
                f"Circular dependency cycle detected between migrations: {unresolved}"
            )

        return plan


def test_migration_resolver() -> None:
    print("[*] Testing Exercise 1: Migration Dependency DAG Resolver...")

    resolver = MigrationDependencyResolver()

    # App dependencies:
    # core.0001 -> users.0001 -> billing.0001 -> analytics.0001
    # also billing.0001 depends on core.0001
    resolver.add(MigrationNode("core", "0001_initial", ()))
    resolver.add(MigrationNode("users", "0001_initial", (("core", "0001_initial"),)))
    resolver.add(MigrationNode("billing", "0001_initial", (("users", "0001_initial"), ("core", "0001_initial"))))
    resolver.add(MigrationNode("analytics", "0001_initial", (("billing", "0001_initial"),)))

    plan = resolver.resolve_execution_order()
    assert plan == [
        ("core", "0001_initial"),
        ("users", "0001_initial"),
        ("billing", "0001_initial"),
        ("analytics", "0001_initial"),
    ]
    print("  [+] Multi-app dependency DAG correctly linearized.")

    # Test Missing Dependency Detection
    broken_resolver = MigrationDependencyResolver()
    broken_resolver.add(MigrationNode("app_x", "0001", (("missing_app", "0001"),)))
    try:
        broken_resolver.resolve_execution_order()
        assert False, "Should have raised MissingDependencyError"
    except MissingDependencyError:
        pass
    print("  [+] Missing dependency trapped successfully.")

    # Test Circular Dependency Detection
    cyclic = MigrationDependencyResolver()
    cyclic.add(MigrationNode("app_a", "0001", (("app_b", "0001"),)))
    cyclic.add(MigrationNode("app_b", "0001", (("app_a", "0001"),)))
    try:
        cyclic.resolve_execution_order()
        assert False, "Should have raised CircularDependencyError"
    except CircularDependencyError:
        pass
    print("  [+] Circular dependency cycle trapped successfully.")


if __name__ == "__main__":
    test_migration_resolver()
```

---

## Exercise 2: Zero-Downtime Safe Column Rename Pipeline (Expand & Contract)

### Problem Statement:
Direct column renaming via `RenameField` breaks rolling deployments. Implement an automated simulator for the 3-phase **Expand-and-Contract** protocol that renames `phone` to `contact_phone`.

The pipeline must:
1. **Phase 1 (Expand)**: Add `contact_phone` as nullable. In dual-write mode, incoming writes populate both columns.
2. **Phase 2 (Backfill)**: Chunked iterator backfills existing rows where `contact_phone` is missing.
3. **Phase 3 (Contract)**: Verify all rows have `contact_phone`, switch reads, and safely drop `phone`.

### Complete Solution:

```python
"""
Exercise 2: Zero-Downtime Safe Column Rename Pipeline (Expand & Contract)
"""

from __future__ import annotations

from typing import Any, Dict, List


class ZeroDowntimeRenamer:
    """Executes the Expand-and-Contract column rename lifecycle."""
    def __init__(self, table: List[Dict[str, Any]]):
        self.table = table
        self.schema: Dict[str, bool] = {"id": False, "phone": False}  # col -> nullable

    def phase_1_expand(self) -> None:
        """Add new column as nullable."""
        self.schema["contact_phone"] = True

    def dual_write_insert(self, record_id: int, phone_val: str) -> None:
        """Simulates application writing to both columns during rollout."""
        row = {"id": record_id, "phone": phone_val}
        if "contact_phone" in self.schema:
            row["contact_phone"] = phone_val
        self.table.append(row)

    def phase_2_chunked_backfill(self, batch_size: int = 100) -> int:
        """Backfill existing historical records in chunks."""
        updated = 0
        for i in range(0, len(self.table), batch_size):
            chunk = self.table[i:i + batch_size]
            for row in chunk:
                if row.get("contact_phone") is None:
                    row["contact_phone"] = row.get("phone")
                    updated += 1
        return updated

    def phase_3_contract(self) -> None:
        """Enforce NOT NULL and drop obsolete column."""
        # Pre-flight check: ensure no NULL values exist
        for row in self.table:
            if row.get("contact_phone") is None:
                raise ValueError("Cannot contract: table still contains NULL values in contact_phone!")
        
        self.schema["contact_phone"] = False  # NOT NULL enforced
        self.schema.pop("phone", None)        # Drop legacy column

        for row in self.table:
            row.pop("phone", None)


def test_zero_downtime_renamer() -> None:
    print("[*] Testing Exercise 2: Zero-Downtime Column Renamer...")

    # Seed table with existing rows
    initial_rows = [
        {"id": 1, "phone": "+1-555-0100"},
        {"id": 2, "phone": "+1-555-0200"},
        {"id": 3, "phone": "+1-555-0300"},
    ]
    renamer = ZeroDowntimeRenamer(initial_rows)

    # Phase 1: Expand
    renamer.phase_1_expand()
    assert "contact_phone" in renamer.schema
    assert renamer.schema["contact_phone"] is True

    # Application dual write (new row added while migration is underway)
    renamer.dual_write_insert(4, "+1-555-0400")
    assert renamer.table[-1]["contact_phone"] == "+1-555-0400"

    # Phase 2: Backfill historical rows 1, 2, 3
    backfilled = renamer.phase_2_chunked_backfill(batch_size=2)
    assert backfilled == 3
    assert all(r["contact_phone"] is not None for r in renamer.table)

    # Phase 3: Contract
    renamer.phase_3_contract()
    assert "phone" not in renamer.schema
    assert renamer.schema["contact_phone"] is False  # NOT NULL
    assert all("phone" not in r for r in renamer.table)

    print("  [+] 3-Phase Expand-and-Contract column rename completed with zero data loss.")


if __name__ == "__main__":
    test_zero_downtime_renamer()
```

---

## Exercise 3: Pre-Flight Migration Safety Linter & Guardrail Engine

### Problem Statement:
In continuous integration (CI) workflows, pull requests that introduce database migrations must pass static safety checks before merging into main.

Build `MigrationSafetyLinter` that statically analyzes a migration dictionary and checks for:
1. `DANGEROUS_NOT_NULL`: Adding a column that has `null=False` without a default value or Expand-Contract flow.
2. `LOCK_RISK_INDEX`: Adding a standard index without `AddIndexConcurrently` or missing `atomic = False`.
3. `IRREVERSIBLE_PYTHON`: `RunPython` operation missing `reverse_code`.
4. `INSTANT_RENAME`: `RenameField` or `RenameModel` operations that break rolling deployments.

### Complete Solution:

```python
"""
Exercise 3: Pre-Flight Migration Safety Linter & Guardrail Engine
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class LintViolation:
    rule_id: str
    severity: str  # "ERROR" or "WARNING"
    operation_type: str
    message: str
    remediation: str


class MigrationSafetyLinter:
    """Statically audits migration definitions for production table-lock hazards."""
    
    @classmethod
    def lint_migration(cls, migration: Dict[str, Any]) -> List[LintViolation]:
        violations: List[LintViolation] = []
        is_atomic = migration.get("atomic", True)

        for op in migration.get("operations", []):
            op_type = op.get("type")

            # Rule 1: Dangerous NOT NULL without default
            if op_type == "AddField":
                is_nullable = op.get("null", False)
                has_default = "default" in op
                if not is_nullable and not has_default:
                    violations.append(
                        LintViolation(
                            rule_id="DANGEROUS_NOT_NULL",
                            severity="ERROR",
                            operation_type=op_type,
                            message=f"Field '{op.get('name')}' is added as NOT NULL without a default value.",
                            remediation="Add as null=True first, backfill data, then enforce NOT NULL in a separate migration.",
                        )
                    )

            # Rule 2: Non-concurrent index creation
            if op_type == "AddIndex":
                violations.append(
                    LintViolation(
                        rule_id="LOCK_RISK_INDEX",
                        severity="ERROR",
                        operation_type=op_type,
                        message="Standard AddIndex acquires ShareLock, blocking all concurrent table writes.",
                        remediation="Use AddIndexConcurrently and set atomic = False on the Migration class.",
                    )
                )

            # Rule 3: Concurrent index inside atomic block
            if op_type == "AddIndexConcurrently" and is_atomic:
                violations.append(
                    LintViolation(
                        rule_id="ATOMIC_CONCURRENT_INDEX",
                        severity="ERROR",
                        operation_type=op_type,
                        message="AddIndexConcurrently cannot run inside an atomic transaction block.",
                        remediation="Add 'atomic = False' to the Migration class definition.",
                    )
                )

            # Rule 4: Irreversible RunPython
            if op_type == "RunPython":
                if not op.get("has_reverse_code", False):
                    violations.append(
                        LintViolation(
                            rule_id="IRREVERSIBLE_PYTHON",
                            severity="WARNING",
                            operation_type=op_type,
                            message="RunPython is missing reverse_code. This migration cannot be rolled back.",
                            remediation="Provide a reverse callback or migrations.RunPython.noop.",
                        )
                    )

            # Rule 5: Instant RenameField in rolling deploys
            if op_type == "RenameField":
                violations.append(
                    LintViolation(
                        rule_id="INSTANT_RENAME",
                        severity="ERROR",
                        operation_type=op_type,
                        message="RenameField breaks active containers during rolling deployments.",
                        remediation="Use the Expand-and-Contract protocol (Add new -> Dual write -> Backfill -> Drop old).",
                    )
                )

        return violations


def test_safety_linter() -> None:
    print("[*] Testing Exercise 3: Pre-Flight Migration Safety Linter...")

    # Unsafe migration with 3 dangerous operations
    unsafe_migration = {
        "atomic": True,
        "operations": [
            {"type": "AddField", "name": "credit_score", "null": False},  # No default!
            {"type": "AddIndex", "name": "idx_credit_score"},             # Non-concurrent index!
            {"type": "RunPython", "has_reverse_code": False},             # Irreversible!
            {"type": "RenameField", "old_name": "a", "new_name": "b"},    # Instant rename!
        ],
    }

    violations = MigrationSafetyLinter.lint_migration(unsafe_migration)
    violation_rules = {v.rule_id for v in violations}

    assert "DANGEROUS_NOT_NULL" in violation_rules
    assert "LOCK_RISK_INDEX" in violation_rules
    assert "IRREVERSIBLE_PYTHON" in violation_rules
    assert "INSTANT_RENAME" in violation_rules
    assert len(violations) == 4
    print("  [+] Linter correctly detected all 4 dangerous migration operations.")

    # Safe migration
    safe_migration = {
        "atomic": False,
        "operations": [
            {"type": "AddIndexConcurrently", "name": "idx_concurrent"},
            {"type": "RunPython", "has_reverse_code": True},
        ],
    }
    clean_violations = MigrationSafetyLinter.lint_migration(safe_migration)
    assert len(clean_violations) == 0
    print("  [+] Clean migration passed safety audit with 0 violations.")


if __name__ == "__main__":
    test_safety_linter()
```
