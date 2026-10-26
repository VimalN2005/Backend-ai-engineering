r"""
Day 36: Example 01 - Django Migration Engine, Dependency Graph (DAG) & SchemaEditor
===================================================================================

This module implements a pure-Python, zero-dependency architectural simulation of
Django's migration framework, including declarative operations (`CreateModel`, `AddField`,
`RunPython`), dependency graph resolution using a Directed Acyclic Graph (DAG) with
topological sorting, cycle detection, and bidirectional schema editing (`SchemaEditor`).

WHY THIS ARCHITECTURE MATTERS:
1. Declarative AST Operations: Django migrations don't store raw SQL; they store an
   abstract operation tree that can be compiled across different database engines or
   reversed during rollbacks.
2. The Dependency DAG: In multi-app architectures, App A may depend on a model in App B.
   Django resolves all inter-app migration dependencies into a single linear execution
   plan using Topological Sort, preventing foreign key constraint failures.
3. Historical Model Isolation: In `RunPython`, accessing models via `apps.get_model()`
   provides frozen historical state as of that migration, preventing future code changes
   from breaking legacy migration runs.
"""

from __future__ import annotations

import collections
import copy
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


class MigrationError(Exception):
    pass


class CircularDependencyError(MigrationError):
    pass


class IrreversibleError(MigrationError):
    pass


# -----------------------------------------------------------------------------
# Simulated Schema State & Migration Operations
# -----------------------------------------------------------------------------

class SchemaState:
    """Represents the complete database table schema at a specific point in time."""
    def __init__(self):
        # Maps: table_name -> {column_name: column_type}
        self.tables: Dict[str, Dict[str, str]] = {}
        # Records in tables: table_name -> list of row dicts
        self.data: Dict[str, List[Dict[str, Any]]] = {}

    def clone(self) -> SchemaState:
        return copy.deepcopy(self)


class MigrationOperation:
    """Base class for all schema and data operations."""
    def state_forwards(self, state: SchemaState) -> None:
        raise NotImplementedError

    def database_forwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        raise NotImplementedError

    def database_backwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        raise NotImplementedError


class CreateModel(MigrationOperation):
    """Creates a new database table and initializes its schema."""
    def __init__(self, name: str, fields: Dict[str, str]):
        self.name = name.lower()
        self.fields = fields

    def state_forwards(self, state: SchemaState) -> None:
        state.tables[self.name] = dict(self.fields)
        state.data.setdefault(self.name, [])

    def database_forwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        cols = ", ".join(f"{col} {ctype}" for col, ctype in self.fields.items())
        schema_editor.execute(f"CREATE TABLE {self.name} ({cols});")
        self.state_forwards(state)

    def database_backwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        schema_editor.execute(f"DROP TABLE {self.name};")
        state.tables.pop(self.name, None)
        state.data.pop(self.name, None)


class AddField(MigrationOperation):
    """Adds a new column to an existing table."""
    def __init__(self, model_name: str, name: str, field_type: str, default: Any = None):
        self.model_name = model_name.lower()
        self.name = name
        self.field_type = field_type
        self.default = default

    def state_forwards(self, state: SchemaState) -> None:
        state.tables[self.model_name][self.name] = self.field_type

    def database_forwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        schema_editor.execute(f"ALTER TABLE {self.model_name} ADD COLUMN {self.name} {self.field_type};")
        self.state_forwards(state)
        # Populate default value on existing rows
        for row in state.data.get(self.model_name, []):
            row[self.name] = self.default

    def database_backwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        schema_editor.execute(f"ALTER TABLE {self.model_name} DROP COLUMN {self.name};")
        state.tables[self.model_name].pop(self.name, None)
        for row in state.data.get(self.model_name, []):
            row.pop(self.name, None)


class RunPython(MigrationOperation):
    """Executes custom data migration logic with forward and reverse callbacks."""
    def __init__(
        self,
        code: Callable[[SchemaState, SchemaEditor], None],
        reverse_code: Optional[Callable[[SchemaState, SchemaEditor], None]] = None,
        elidable: bool = False,
    ):
        self.code = code
        self.reverse_code = reverse_code
        self.elidable = elidable

    def state_forwards(self, state: SchemaState) -> None:
        pass  # Data operations don't mutate the schema metadata

    def database_forwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        schema_editor.execute("-- [RunPython] Executing forward data migration callback")
        self.code(state, schema_editor)

    def database_backwards(self, schema_editor: SchemaEditor, state: SchemaState) -> None:
        if self.reverse_code is None:
            raise IrreversibleError("This data migration cannot be reversed because no reverse_code was supplied.")
        schema_editor.execute("-- [RunPython] Executing reverse data migration callback")
        self.reverse_code(state, schema_editor)


# -----------------------------------------------------------------------------
# SchemaEditor (SQL Compiler & Execution Logger)
# -----------------------------------------------------------------------------

class SchemaEditor:
    """Simulates Django's engine-specific SchemaEditor (e.g. DatabaseSchemaEditor)."""
    def __init__(self):
        self.executed_sql: List[str] = []

    def execute(self, sql: str) -> None:
        self.executed_sql.append(sql)

    def clear(self) -> None:
        self.executed_sql.clear()


# -----------------------------------------------------------------------------
# Migration Class & DAG Graph Compiler
# -----------------------------------------------------------------------------

class Migration:
    """Represents a single migration file in an application."""
    def __init__(
        self,
        app_label: str,
        name: str,
        dependencies: Optional[List[Tuple[str, str]]] = None,
        operations: Optional[List[MigrationOperation]] = None,
    ):
        self.app_label = app_label
        self.name = name
        self.key = (app_label, name)
        self.dependencies = dependencies or []
        self.operations = operations or []

    def __repr__(self) -> str:
        return f"<Migration: {self.app_label}.{self.name}>"


class MigrationGraph:
    """
    Manages the Directed Acyclic Graph (DAG) of migrations across all apps.
    Resolves execution order via Topological Sorting.
    """
    def __init__(self):
        self.nodes: Dict[Tuple[str, str], Migration] = {}
        self.dependencies: Dict[Tuple[str, str], Set[Tuple[str, str]]] = collections.defaultdict(set)
        self.applied: Set[Tuple[str, str]] = set()

    def add_migration(self, migration: Migration) -> None:
        self.nodes[migration.key] = migration
        for dep in migration.dependencies:
            self.dependencies[migration.key].add(dep)

    def compute_plan(self) -> List[Migration]:
        """
        Calculates the linear execution plan using Kahn's Topological Sort algorithm.
        Raises CircularDependencyError if cycles are present.
        """
        in_degree: Dict[Tuple[str, str], int] = {k: 0 for k in self.nodes}
        adj_list: Dict[Tuple[str, str], List[Tuple[str, str]]] = collections.defaultdict(list)

        for node_key, deps in self.dependencies.items():
            for dep in deps:
                if dep not in self.nodes:
                    raise MigrationError(f"Missing migration dependency: {dep} required by {node_key}")
                adj_list[dep].append(node_key)
                in_degree[node_key] += 1

        # Queue of nodes with no pending dependencies
        queue = collections.deque([k for k, deg in in_degree.items() if deg == 0])
        plan: List[Migration] = []

        while queue:
            curr_key = queue.popleft()
            plan.append(self.nodes[curr_key])

            for neighbor in adj_list[curr_key]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(plan) != len(self.nodes):
            unresolved = [k for k, deg in in_degree.items() if deg > 0]
            raise CircularDependencyError(f"Circular dependency detected between migrations: {unresolved}")

        return plan


# -----------------------------------------------------------------------------
# Migration Executor & Rollback Engine
# -----------------------------------------------------------------------------

class MigrationExecutor:
    """Executes migrations forward or backward against simulated schema state."""
    def __init__(self, graph: MigrationGraph, schema_editor: SchemaEditor):
        self.graph = graph
        self.schema_editor = schema_editor
        self.state = SchemaState()

    def apply_plan(self, plan: List[Migration]) -> None:
        """Apply a sequence of migrations forward."""
        for migration in plan:
            if migration.key in self.graph.applied:
                continue
            self.schema_editor.execute(f"-- Applying migration {migration.app_label}.{migration.name}")
            for op in migration.operations:
                op.database_forwards(self.schema_editor, self.state)
            self.graph.applied.add(migration.key)

    def rollback(self, migration: Migration) -> None:
        """Roll back a single applied migration using reverse operations."""
        if migration.key not in self.graph.applied:
            return
        self.schema_editor.execute(f"-- Rolling back migration {migration.app_label}.{migration.name}")
        # Execute operations in REVERSE order during rollback!
        for op in reversed(migration.operations):
            op.database_backwards(self.schema_editor, self.state)
        self.graph.applied.remove(migration.key)


# -----------------------------------------------------------------------------
# Verification & Self-Testing Suite
# -----------------------------------------------------------------------------

def run_tests() -> None:
    print("[*] Running Day 36 Migration DAG & Schema Engine Verification Suite...")

    editor = SchemaEditor()
    graph = MigrationGraph()

    # 1. Setup multi-app migrations
    # auth 0001
    m_auth = Migration(
        app_label="auth",
        name="0001_initial",
        dependencies=[],
        operations=[
            CreateModel(name="users", fields={"id": "SERIAL PRIMARY KEY", "email": "VARCHAR(255)"})
        ],
    )

    # orders 0001 depends on auth 0001
    m_orders_1 = Migration(
        app_label="orders",
        name="0001_initial",
        dependencies=[("auth", "0001_initial")],
        operations=[
            CreateModel(name="orders", fields={"id": "SERIAL PRIMARY KEY", "user_id": "INTEGER", "amount": "NUMERIC(10,2)"})
        ],
    )

    # orders 0002 adds status and backfills data
    def backfill_order_status(state: SchemaState, schema_editor: SchemaEditor) -> None:
        for r in state.data.get("orders", []):
            r["status"] = "COMPLETED"

    def reverse_order_status(state: SchemaState, schema_editor: SchemaEditor) -> None:
        for r in state.data.get("orders", []):
            r["status"] = "PENDING"

    m_orders_2 = Migration(
        app_label="orders",
        name="0002_add_status",
        dependencies=[("orders", "0001_initial")],
        operations=[
            AddField(model_name="orders", name="status", field_type="VARCHAR(20)", default="PENDING"),
            RunPython(code=backfill_order_status, reverse_code=reverse_order_status, elidable=True),
        ],
    )

    graph.add_migration(m_orders_2)
    graph.add_migration(m_orders_1)
    graph.add_migration(m_auth)

    # 2. Test DAG Topological Sorting
    plan = graph.compute_plan()
    plan_keys = [m.key for m in plan]
    # auth.0001 must run before orders.0001, which runs before orders.0002
    assert plan_keys == [
        ("auth", "0001_initial"),
        ("orders", "0001_initial"),
        ("orders", "0002_add_status"),
    ]
    print("  [+] Topological sort correctly linearized multi-app dependency DAG.")

    # 3. Test Forward Migration Execution
    executor = MigrationExecutor(graph, editor)
    executor.apply_plan(plan)

    assert "users" in executor.state.tables
    assert "orders" in executor.state.tables
    assert "status" in executor.state.tables["orders"]
    assert len(graph.applied) == 3
    print("  [+] Forward schema migration and RunPython callback applied successfully.")

    # 4. Test Bidirectional Reversibility (Rollback)
    executor.rollback(m_orders_2)
    assert "status" not in executor.state.tables["orders"]
    assert ("orders", "0002_add_status") not in graph.applied
    print("  [+] Backward migration rollback verified (column dropped and state restored).")

    # 5. Test Circular Dependency Detection
    cyclic_graph = MigrationGraph()
    cyclic_graph.add_migration(Migration("app_a", "0001", dependencies=[("app_b", "0001")]))
    cyclic_graph.add_migration(Migration("app_b", "0001", dependencies=[("app_a", "0001")]))
    try:
        cyclic_graph.compute_plan()
        assert False, "Should have detected circular dependency cycle!"
    except CircularDependencyError as exc:
        assert "Circular dependency detected" in str(exc)
    print("  [+] Circular dependency detection verified.")


def benchmark_graph_resolution(num_apps: int = 50, migrations_per_app: int = 20) -> None:
    """Benchmark DAG dependency resolution throughput across 1,000 migrations."""
    graph = MigrationGraph()
    total_nodes = num_apps * migrations_per_app

    # Generate synthetic multi-app DAG
    for app_idx in range(num_apps):
        app_name = f"app_{app_idx}"
        for m_idx in range(migrations_per_app):
            m_name = f"{m_idx:04d}_migration"
            deps = []
            if m_idx > 0:
                deps.append((app_name, f"{m_idx-1:04d}_migration"))
            elif app_idx > 0:
                # First migration depends on previous app's initial migration
                deps.append((f"app_{app_idx-1}", "0000_migration"))
            
            graph.add_migration(Migration(app_name, m_name, dependencies=deps))

    start = time.perf_counter()
    iterations = 100
    for _ in range(iterations):
        _ = graph.compute_plan()
    elapsed = time.perf_counter() - start

    latency_ms = (elapsed / iterations) * 1000
    ops_per_sec = iterations / elapsed

    print("\n" + "=" * 65)
    print("DAY 36: MIGRATION DAG RESOLUTION BENCHMARK")
    print("=" * 65)
    print(f"Total Migration Nodes:     {total_nodes:,} nodes ({num_apps} apps x {migrations_per_app} steps)")
    print(f"Total Iterations:          {iterations:,}")
    print(f"Total Time:                {elapsed:.4f} seconds")
    print(f"Average Plan Latency:      {latency_ms:.2f} ms per 1,000-node graph resolution")
    print(f"Throughput:                {ops_per_sec:,.0f} complete DAG resolutions/sec")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
    benchmark_graph_resolution()
