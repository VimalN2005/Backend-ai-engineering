"""
Day 51: Advanced Alembic Migrations - Branch Merging & Data Backfilling
=======================================================================

Production Enterprise Scenarios:
1. Multi-Branching & Diamond Merge Resolution:
   - Simulating two independent developer branches branching from the same parent.
   - Programmatically executing `alembic merge` to reconcile multi-head conflicts.
2. In-Migration Data Transformation (Zero-Downtime Backfill):
   - Safely splitting a legacy 'full_name' column into 'first_name' and 'last_name'.
   - Using Core table() bindings to prevent brittle ORM model dependencies.
3. Offline SQL Generation (--sql mode):
   - Emitting raw DDL for DBA review without establishing an active database connection.
"""

import asyncio
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List

from alembic import command, op
from alembic.config import Config
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    select,
    text,
)

# ---------------------------------------------------------------------------
# 1. Environment Scaffolding Helpers
# ---------------------------------------------------------------------------

ENV_PY_CONTENT = """from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import sys

config = context.config

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
"""

MAKO_TEMPLATE = """\"\"\"${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

\"\"\"
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
"""


def setup_alembic_workspace(base_dir: Path, db_url: str) -> Config:
    alembic_dir = base_dir / "alembic"
    versions_dir = alembic_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    (alembic_dir / "script.py.mako").write_text(MAKO_TEMPLATE, encoding="utf-8")
    (alembic_dir / "env.py").write_text(ENV_PY_CONTENT, encoding="utf-8")

    ini_path = base_dir / "alembic.ini"
    ini_content = f"""[alembic]
script_location = {alembic_dir.as_posix()}
file_template = %%(rev)s_%%(slug)s
sqlalchemy.url = {db_url}

[loggers]
keys = root,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
"""
    ini_path.write_text(ini_content, encoding="utf-8")

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


# ---------------------------------------------------------------------------
# 2. Demonstration 1: Multi-Branch & Diamond Merge Resolution
# ---------------------------------------------------------------------------

def demonstrate_branch_merge_resolution(workspace: Path, db_url: str):
    print("\n" + "=" * 75)
    print("DEMO 1: Multi-Branch Divergence & Diamond Merge Resolution")
    print("=" * 75)

    cfg = setup_alembic_workspace(workspace, db_url)
    versions_dir = workspace / "alembic" / "versions"

    # Step 1: Create Base Revision (Common Ancestor)
    print("\n[STEP 1] Generating Root Migration (rev_001_base)...")
    rev_base = command.revision(cfg, message="base_schema")
    base_file = list(versions_dir.glob("*_base_schema.py"))[0]
    base_id = base_file.name.split("_")[0]

    base_file.write_text(f"""\"\"\"base_schema
Revision ID: {base_id}
Revises: None
\"\"\"
from alembic import op
import sqlalchemy as sa

revision = '{base_id}'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False)
    )

def downgrade() -> None:
    op.drop_table('customers')
""", encoding="utf-8")

    command.upgrade(cfg, base_id)
    print(f"Applied Base Migration: {base_id}")

    # Step 2: Developer A branches from base (Feature: Billing)
    print("\n[STEP 2] Developer A branches from Base: Creating 'billing_accounts'...")
    rev_billing = command.revision(cfg, message="feature_billing", head=base_id)
    billing_file = sorted(list(versions_dir.glob("*_feature_billing.py")))[0]
    billing_id = billing_file.name.split("_")[0]

    billing_file.write_text(f"""\"\"\"feature_billing
Revision ID: {billing_id}
Revises: {base_id}
\"\"\"
from alembic import op
import sqlalchemy as sa

revision = '{billing_id}'
down_revision = '{base_id}'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'billing_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id')),
        sa.Column('balance', sa.Float(), server_default='0.0')
    )

def downgrade() -> None:
    op.drop_table('billing_accounts')
""", encoding="utf-8")

    # Step 3: Developer B branches from base (Feature: Notifications)
    print("\n[STEP 3] Developer B branches concurrently from Base: Creating 'notifications'...")
    rev_notif = command.revision(cfg, message="feature_notifications", head=base_id, splice=True)
    notif_file = sorted(list(versions_dir.glob("*_feature_notifications.py")))[0]
    notif_id = notif_file.name.split("_")[0]

    notif_file.write_text(f"""\"\"\"feature_notifications
Revision ID: {notif_id}
Revises: {base_id}
\"\"\"
from alembic import op
import sqlalchemy as sa

revision = '{notif_id}'
down_revision = '{base_id}'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id')),
        sa.Column('channel', sa.String(20), nullable=False)
    )

def downgrade() -> None:
    op.drop_table('notifications')
""", encoding="utf-8")

    # Step 4: Detect Multiple Heads
    print("\n[STEP 4] Detecting Branch Heads:")
    print(f"  Branch Head 1 (Billing)      : {billing_id}")
    print(f"  Branch Head 2 (Notifications): {notif_id}")

    # Step 5: Execute Alembic Merge to Reconcile the Diamond Dependency
    print("\n[STEP 5] Executing 'alembic merge' to Reconcile Heads...")
    merge_rev = command.merge(
        cfg,
        revisions=[billing_id, notif_id],
        message="merge_billing_and_notifications",
    )
    merge_file = sorted(list(versions_dir.glob("*_merge_billing_and_notifications.py")))[0]
    merge_id = merge_file.name.split("_")[0]
    print(f"Generated Reconciled Merge Revision: {merge_id}")
    print(f"  Merge down_revision tuple: ({billing_id}, {notif_id})")

    # Step 6: Upgrade to Reconciled Head
    print("\n[STEP 6] Applying Unified Upgrade to Head...")
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    insp = inspect(engine)
    tables = insp.get_table_names()
    print(f"Tables Present in Database: {tables}")
    assert "customers" in tables
    assert "billing_accounts" in tables
    assert "notifications" in tables
    print("[SUCCESS] Multi-branch diamond reconciliation verified.")


# ---------------------------------------------------------------------------
# 3. Demonstration 2: In-Migration Data Transformation (Zero-Downtime)
# ---------------------------------------------------------------------------

def demonstrate_data_migration_backfill(workspace: Path, db_url: str):
    print("\n" + "=" * 75)
    print("DEMO 2: Zero-Downtime In-Migration Data Backfill")
    print("=" * 75)

    cfg = setup_alembic_workspace(workspace, db_url)
    versions_dir = workspace / "alembic" / "versions"
    engine = create_engine(db_url)

    # Step 1: Base Table with legacy 'full_name' column
    print("\n[STEP 1] Creating Legacy Users Table with 'full_name'...")
    rev_users = command.revision(cfg, message="create_legacy_users")
    users_file = list(versions_dir.glob("*_create_legacy_users.py"))[0]
    users_id = users_file.name.split("_")[0]

    users_file.write_text(f"""\"\"\"create_legacy_users
Revision ID: {users_id}
Revises: None
\"\"\"
from alembic import op
import sqlalchemy as sa

revision = '{users_id}'
down_revision = None

def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('full_name', sa.String(150), nullable=False)
    )

def downgrade() -> None:
    op.drop_table('users')
""", encoding="utf-8")

    command.upgrade(cfg, "head")

    # Step 2: Seed Legacy Records
    print("\n[STEP 2] Seeding Legacy User Records...")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (full_name) VALUES (:n)"),
            [
                {"n": "Alan Turing"},
                {"n": "Ada Lovelace"},
                {"n": "Grace Hopper"},
                {"n": "Margaret Hamilton"},
            ],
        )
        res = conn.execute(text("SELECT id, full_name FROM users")).fetchall()
        print(f"Initial Legacy Rows: {res}")

    # Step 3: Migration Splitting 'full_name' into 'first_name' and 'last_name' with backfill
    print("\n[STEP 3] Applying In-Migration Backfill (Splitting 'full_name')...")
    rev_split = command.revision(cfg, message="split_name_columns")
    split_file = sorted(list(versions_dir.glob("*_split_name_columns.py")))[0]
    split_id = split_file.name.split("_")[0]

    # Writing safe data migration that binds Core table constructs inside the script
    split_file.write_text(f"""\"\"\"split_name_columns
Revision ID: {split_id}
Revises: {users_id}
\"\"\"
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column

revision = '{split_id}'
down_revision = '{users_id}'

def upgrade() -> None:
    # 1. Expand: Add new columns as nullable
    op.add_column('users', sa.Column('first_name', sa.String(75), nullable=True))
    op.add_column('users', sa.Column('last_name', sa.String(75), nullable=True))

    # 2. Backfill: Bind a historical Core table construct (DO NOT import application models!)
    users_table = table(
        'users',
        column('id', sa.Integer),
        column('full_name', sa.String),
        column('first_name', sa.String),
        column('last_name', sa.String)
    )

    connection = op.get_bind()
    # Read existing records
    results = connection.execute(sa.select(users_table.c.id, users_table.c.full_name)).fetchall()
    for row in results:
        user_id = row[0]
        full_name = row[1]
        parts = full_name.split(' ', 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ''
        connection.execute(
            users_table.update()
            .where(users_table.c.id == user_id)
            .values(first_name=first, last_name=last)
        )

def downgrade() -> None:
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'first_name')
""", encoding="utf-8")

    command.upgrade(cfg, "head")

    # Step 4: Verify Data Backfill Results
    print("\n[STEP 4] Verifying Backfilled Columns in Database:")
    with engine.begin() as conn:
        res = conn.execute(text("SELECT id, full_name, first_name, last_name FROM users")).fetchall()
        for row in res:
            print(f"  User {row[0]}: Full='{row[1]}' -> First='{row[2]}', Last='{row[3]}'")
            assert row[2] and row[3], "Both first and last names should be backfilled"

    print("[SUCCESS] Zero-downtime data migration backfill verified without application models.")


# ---------------------------------------------------------------------------
# 4. Demonstration 3: Offline SQL Generation (--sql Mode)
# ---------------------------------------------------------------------------

def demonstrate_offline_sql_generation(workspace: Path, db_url: str):
    print("\n" + "=" * 75)
    print("DEMO 3: Offline SQL Generation (--sql Mode for DBA Auditing)")
    print("=" * 75)

    cfg = setup_alembic_workspace(workspace, db_url)
    versions_dir = workspace / "alembic" / "versions"

    rev = command.revision(cfg, message="audit_log_ddl")
    rev_file = list(versions_dir.glob("*_audit_log_ddl.py"))[0]
    rev_id = rev_file.name.split("_")[0]

    rev_file.write_text(f"""\"\"\"audit_log_ddl
Revision ID: {rev_id}
Revises: None
\"\"\"
from alembic import op
import sqlalchemy as sa

revision = '{rev_id}'
down_revision = None

def upgrade() -> None:
    op.create_table(
        'security_audits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ip_address', sa.String(45), nullable=False),
        sa.Column('payload', sa.Text(), nullable=True)
    )

def downgrade() -> None:
    op.drop_table('security_audits')
""", encoding="utf-8")

    # Capture stdout during offline upgrade
    stdout_capture = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = stdout_capture

    try:
        # Run upgrade with sql=True (Offline mode)
        command.upgrade(cfg, "head", sql=True)
    finally:
        sys.stdout = original_stdout

    generated_sql = stdout_capture.getvalue()
    print("Generated Offline SQL Script (Ready for DBA Approval):")
    print("-" * 75)
    print(generated_sql.strip())
    print("-" * 75)

    assert "CREATE TABLE security_audits" in generated_sql
    assert "CREATE TABLE alembic_version" in generated_sql
    print("[SUCCESS] Offline SQL generation verified: DDL exported without socket connection.")


# ---------------------------------------------------------------------------
# 5. Main Orchestration
# ---------------------------------------------------------------------------

def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="alembic_day51_adv_"))
    try:
        ws1 = temp_dir / "ws_branches"
        ws2 = temp_dir / "ws_backfill"
        ws3 = temp_dir / "ws_offline"
        ws1.mkdir()
        ws2.mkdir()
        ws3.mkdir()

        demonstrate_branch_merge_resolution(ws1, f"sqlite:///{ws1 / 'db_merge.db'}")
        demonstrate_data_migration_backfill(ws2, f"sqlite:///{ws2 / 'db_backfill.db'}")
        demonstrate_offline_sql_generation(ws3, f"sqlite:///{ws3 / 'db_offline.db'}")

        print("\n" + "=" * 75)
        print("ALL ADVANCED ENTERPRISE ALEMBIC DEMOS COMPLETED SUCCESSFULLY!")
        print("=" * 75)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"[CLEANUP] Hermetic workspace '{temp_dir}' removed.")


if __name__ == "__main__":
    main()
