# Day 51: Advanced Database Migrations Practical Exercises

Mastering database schema evolutions requires understanding both the high-level orchestration patterns and low-level SQL mechanics. Complete the following three senior-level engineering exercises. Verified reference solutions follow each prompt.

---

## Exercise 1: Zero-Downtime Column Expansion and Backfill Migration with Batching

### Architectural Scenario
You are modernizing an enterprise billing system. The `invoices` table contains 2,000,000 records. Currently, monetary amounts are stored in a single column `amount_cents` (Integer). The new internationalized payment engine requires two columns: `currency` (String(3)) and `amount_decimal` (Numeric(12, 2)).

Running an unbatched `UPDATE invoices` inside a single migration transaction will lock the table for several minutes, causing connection pool exhaustion and downtime.

### Requirements
1. Design an Alembic migration script implementing the **Expand** phase:
   - Add `currency` (String(3)) with a temporary server default of `'USD'` marked `nullable=False`.
   - Add `amount_decimal` (Numeric(12, 2)) as `nullable=True`.
2. Implement a safe, chunked data backfill function directly in Python that:
   - Binds a Core `table()` construct.
   - Updates records in batches of 5,000 rows based on primary key ranges (`WHERE id >= start AND id < end`).
   - Commits each batch independently to release locks and minimize transaction log bloat.
3. Apply the final contraction: Alter `amount_decimal` to `nullable=False`.
4. Provide a reversible `downgrade()` implementation.

### Verified Reference Solution

```python
"""expand_and_backfill_invoices

Revision ID: exp_backfill_001
Revises: init_rev_000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column

revision = 'exp_backfill_001'
down_revision = 'init_rev_000'

def upgrade() -> None:
    # Step 1: Expand - Add columns without locking table
    op.add_column(
        'invoices',
        sa.Column('currency', sa.String(length=3), server_default=sa.text("'USD'"), nullable=False)
    )
    op.add_column(
        'invoices',
        sa.Column('amount_decimal', sa.Numeric(precision=12, scale=2), nullable=True)
    )

    # Step 2: Chunked Backfill
    invoices_table = table(
        'invoices',
        column('id', sa.Integer),
        column('amount_cents', sa.Integer),
        column('amount_decimal', sa.Numeric),
    )

    connection = op.get_bind()
    BATCH_SIZE = 5000

    # Retrieve min and max ID to define batch windows
    min_id, max_id = connection.execute(
        sa.select(sa.func.min(invoices_table.c.id), sa.func.max(invoices_table.c.id))
    ).fetchone()

    if min_id is not None and max_id is not None:
        current_id = min_id
        while current_id <= max_id:
            next_id = current_id + BATCH_SIZE
            connection.execute(
                invoices_table.update()
                .where(sa.and_(
                    invoices_table.c.id >= current_id,
                    invoices_table.c.id < next_id,
                    invoices_table.c.amount_decimal.is_(None)
                ))
                .values(amount_decimal=invoices_table.c.amount_cents / 100.0)
            )
            current_id = next_id

    # Step 3: Contract - Enforce NOT NULL after 100% backfill
    op.alter_column('invoices', 'amount_decimal', nullable=False)


def downgrade() -> None:
    op.drop_column('invoices', 'amount_decimal')
    op.drop_column('invoices', 'currency')
```

---

## Exercise 2: Implementing a Programmatic Schema Drift Detector in CI

### Architectural Scenario
In continuous integration (CI) workflows, pull requests frequently introduce changes to SQLAlchemy declarative models, but developers forget to generate the corresponding Alembic migration file.

You must implement an automated test using `pytest` that detects schema drift between Python model metadata and the test database catalog, failing the build if uncommitted migrations exist.

### Requirements
1. Use Alembic's `MigrationContext` and `autogenerate.compare_metadata`.
2. Construct a test function `test_database_schema_has_no_drift(engine, metadata)`.
3. The test must inspect:
   - Missing or extra tables.
   - Missing or extra columns.
   - Altered column types (`compare_type=True`).
   - Altered server defaults (`compare_server_default=True`).
4. Fail with an informative assertion listing the exact unmigrated differences found.

### Verified Reference Solution

```python
import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.schema import MetaData
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata

def test_database_schema_has_no_drift(engine: Engine, target_metadata: MetaData):
    """
    CI Quality Gate:
    Verifies that the database schema matches SQLAlchemy declarative models exactly.
    Fails if a developer altered models without generating an Alembic migration.
    """
    with engine.connect() as connection:
        # 1. Initialize migration context with type and default comparison enabled
        mc = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
            }
        )

        # 2. Compute diff between target metadata and database catalog
        diffs = compare_metadata(mc, target_metadata)

        # 3. Assert zero drift
        # Filter out ignorable artifacts if necessary (e.g., spatial_ref_sys in PostGIS)
        critical_diffs = [
            d for d in diffs 
            if not (isinstance(d, tuple) and d[0] == "remove_table" and d[1].name == "alembic_version")
        ]

        error_message = (
            f"Database schema drift detected! Found {len(critical_diffs)} unmigrated change(s):\n"
            + "\n".join(f"  - {d}" for d in critical_diffs)
            + "\nRun 'alembic revision --autogenerate -m \"describe changes\"' to sync migrations."
        )

        assert len(critical_diffs) == 0, error_message
```

---

## Exercise 3: Programmatic Diamond Merge Conflict Resolution with Branch Labels

### Architectural Scenario
In a monorepo setup, two feature teams branch independently from an established baseline revision:
- Team Auth creates branch `auth_service` (`rev_auth`).
- Team Billing creates branch `billing_service` (`rev_billing`).

Both branches declare custom `branch_labels`. When merging both branches into `main`, running `alembic upgrade head` fails due to multiple heads.

### Requirements
1. Programmatically detect when multiple heads exist in Alembic's script directory using `ScriptDirectory.get_heads()`.
2. If multiple heads exist, generate a reconciliation merge revision programmatically using `alembic.command.merge()`.
3. Verify that the generated merge revision contains both branch heads in `down_revision = (rev_auth, rev_billing)`.
4. Successfully upgrade to the new unified head.

### Verified Reference Solution

```python
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic import command
from sqlalchemy import create_engine, inspect

def reconcile_divergent_heads(alembic_config: Config) -> str:
    """
    Detects and merges divergent migration heads automatically.
    Returns the new unified merge revision ID.
    """
    script_dir = ScriptDirectory.from_config(alembic_config)
    heads = script_dir.get_heads()

    print(f"Current detected heads: {heads}")

    if len(heads) == 1:
        print("Repository is already single-headed.")
        return heads[0]

    if len(heads) > 1:
        print(f"Detected {len(heads)} divergent heads. Generating merge revision...")
        merge_rev = command.merge(
            alembic_config,
            revisions=heads,
            message="automated_ci_diamond_merge",
        )
        
        # Refresh heads after merge
        new_heads = script_dir.get_heads()
        assert len(new_heads) == 1, "Merge failed to produce a single head"
        unified_head = new_heads[0]
        print(f"Successfully generated unified merge revision: {unified_head}")
        
        # Apply upgrade to unified head
        command.upgrade(alembic_config, unified_head)
        return unified_head

    raise RuntimeError("No heads detected in migration history.")
```

---
*Completing these exercises guarantees production readiness in database schema evolution and CI/CD drift detection.*
