"""
Day 51: Programmatic Alembic Pipeline & Automated Schema Tracking
==================================================================

Enterprise Database Migration Architecture:
- Programmatic Alembic configuration and execution (alembic.command)
- Dynamic environment scaffolding (alembic.ini, env.py, script.py.mako)
- Async-compatible migration runner using run_sync
- Autogeneration of revisions from SQLAlchemy 2.0 DeclarativeBase
- Automated schema tracking via the 'alembic_version' table
- Multi-revision forward upgrades (upgrade head)
- Safe deterministic rollbacks (downgrade -1)
- Automatic cleanup of temporary migration artifacts
"""

import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    inspect,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# 1. Programmatic Alembic Environment Scaffolding
# ---------------------------------------------------------------------------

MAKO_TEMPLATE = """\"\"\"${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

\"\"\"
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
"""

ENV_PY_CONTENT = """import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

# Alembic Config object
config = context.config

# Import target metadata from the active runner
import sys
if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "target_metadata"):
    target_metadata = sys.modules["__main__"].target_metadata
else:
    import os
    sys.path.insert(0, os.getcwd())
    from example_01 import target_metadata

def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    url = config.get_main_option("sqlalchemy.url")
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online():
    # Run migrations using the current event loop or asyncio.run
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Running inside an existing event loop: execute synchronously via runner
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool_exec:
            pool_exec.submit(asyncio.run, run_async_migrations()).result()
    else:
        asyncio.run(run_async_migrations())

if context.is_offline_mode():
    raise NotImplementedError("Offline mode not configured for this demo.")
else:
    run_migrations_online()
"""


def scaffold_alembic_directory(base_dir: Path, db_url: str) -> Config:
    """
    Programmatically creates an isolated Alembic directory structure.
    WHY: In unit tests and standalone CLI runners, programmatically scaffolding
    guarantees hermetic test environments without contaminating developer repositories.
    """
    alembic_dir = base_dir / "alembic"
    versions_dir = alembic_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    # Write script.py.mako
    mako_path = alembic_dir / "script.py.mako"
    mako_path.write_text(MAKO_TEMPLATE, encoding="utf-8")

    # Write env.py
    env_path = alembic_dir / "env.py"
    env_path.write_text(ENV_PY_CONTENT, encoding="utf-8")

    # Create alembic.ini configuration programmatically
    alembic_ini_path = base_dir / "alembic.ini"
    ini_content = f"""[alembic]
script_location = {alembic_dir.as_posix()}
file_template = %%(rev)s_%%(slug)s
prepend_sys_path = .
timezone = UTC
sqlalchemy.url = {db_url}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

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
datefmt = %H:%M:%S
"""
    alembic_ini_path.write_text(ini_content, encoding="utf-8")

    cfg = Config(str(alembic_ini_path))
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


# ---------------------------------------------------------------------------
# 2. Dynamic Declarative Models (V1 and V2 Schemas)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass

# Shared target_metadata exported for env.py reflection
target_metadata: MetaData = Base.metadata

# --- Schema Version 1: Initial Models ---
class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserAccount(id={self.id}, username='{self.username}', email='{self.email}')>"


# ---------------------------------------------------------------------------
# 3. Migration Runner & Inspection Helpers
# ---------------------------------------------------------------------------

async def inspect_db_schema(engine: AsyncEngine) -> dict:
    """
    Reflects tables and columns from the live database using SQLAlchemy's Inspector.
    """
    def _inspect(connection):
        insp = inspect(connection)
        tables = insp.get_table_names()
        columns_by_table = {}
        for table_name in tables:
            columns_by_table[table_name] = [col["name"] for col in insp.get_columns(table_name)]
        return {"tables": tables, "columns": columns_by_table}

    async with engine.connect() as conn:
        return await conn.run_sync(_inspect)


async def get_current_alembic_version(engine: AsyncEngine) -> Optional[str]:
    """
    Directly queries the alembic_version tracking table.
    """
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = res.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# 4. End-to-End Programmatic Pipeline Execution
# ---------------------------------------------------------------------------

async def run_pipeline():
    print("=" * 75)
    print("Alembic Automated Schema Tracking & Programmatic Pipeline")
    print("=" * 75)

    temp_workspace = Path(tempfile.mkdtemp(prefix="alembic_day51_"))
    db_file = temp_workspace / "production_store.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    print(f"[SETUP] Temporary workspace: {temp_workspace}")
    print(f"[SETUP] Database URL: {db_url}")

    # Scaffold Alembic config
    alembic_cfg = scaffold_alembic_directory(temp_workspace, db_url)
    engine = create_async_engine(db_url, echo=False)

    try:
        # Step 1: Verify Initial Empty Database State
        print("\n--- STEP 1: Verifying Initial Database State ---")
        schema_v0 = await inspect_db_schema(engine)
        print(f"Tables in Database: {schema_v0['tables']}")
        assert len(schema_v0["tables"]) == 0, "Database should initially be empty"

        # Step 2: Autogenerate Revision 1 (UserAccount Table)
        print("\n--- STEP 2: Autogenerating Revision 1 (Initial UserAccount) ---")
        command.revision(
            alembic_cfg,
            message="create_user_accounts_table",
            autogenerate=True,
        )
        versions_dir = temp_workspace / "alembic" / "versions"
        rev_files_v1 = list(versions_dir.glob("*.py"))
        print(f"Generated Revision File: {rev_files_v1[0].name}")

        # Step 3: Apply Migration (Upgrade Head)
        print("\n--- STEP 3: Applying Migration 1 (alembic upgrade head) ---")
        command.upgrade(alembic_cfg, "head")

        schema_v1 = await inspect_db_schema(engine)
        current_rev_v1 = await get_current_alembic_version(engine)
        print(f"Active alembic_version: '{current_rev_v1}'")
        print(f"Tables in Database: {schema_v1['tables']}")
        print(f"Columns in 'user_accounts': {schema_v1['columns'].get('user_accounts')}")
        assert "user_accounts" in schema_v1["tables"]
        assert "alembic_version" in schema_v1["tables"]

        # Step 4: Insert Data into V1 Schema
        print("\n--- STEP 4: Inserting Data into UserAccount Table ---")
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO user_accounts (username, email) VALUES (:u, :e)"),
                [{"u": "alice_dev", "e": "alice@cloud.io"}, {"u": "bob_arch", "e": "bob@cloud.io"}],
            )
            res = await conn.execute(text("SELECT id, username, email FROM user_accounts"))
            print(f"Rows inserted: {res.fetchall()}")

        # Step 5: Evolve Schema to V2 (Add Column 'is_active' and New Table 'audit_logs')
        print("\n--- STEP 5: Evolving Declarative Models (V2 Schema) ---")
        # Dynamically add column to UserAccount in memory
        new_col = Column("is_active", Integer, server_default=text("1"), nullable=False)
        UserAccount.__table__.append_column(new_col)

        # Dynamically register AuditLog table on Base.metadata
        audit_logs_table = Table(
            "audit_logs",
            Base.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("user_id", Integer, ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False),
            Column("action", String(100), nullable=False),
            Column("timestamp", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
        )

        print("Updated Declarative Metadata: added 'is_active' column and 'audit_logs' table.")

        # Step 6: Autogenerate Revision 2
        print("\n--- STEP 6: Autogenerating Revision 2 ---")
        command.revision(
            alembic_cfg,
            message="add_is_active_and_audit_logs",
            autogenerate=True,
        )
        rev_files_v2 = sorted(list(versions_dir.glob("*.py")), key=os.path.getmtime)
        print(f"Generated Revision File 2: {rev_files_v2[-1].name}")

        # Step 7: Apply Migration 2 (Upgrade to Head)
        print("\n--- STEP 7: Applying Migration 2 (alembic upgrade head) ---")
        print("Generated Migration Content:")
        print(rev_files_v2[-1].read_text(encoding="utf-8"))
        command.upgrade(alembic_cfg, "head")

        schema_v2 = await inspect_db_schema(engine)
        current_rev_v2 = await get_current_alembic_version(engine)
        print(f"Active alembic_version: '{current_rev_v2}'")
        print(f"Tables in Database: {schema_v2['tables']}")
        print(f"Columns in 'user_accounts': {schema_v2['columns'].get('user_accounts')}")
        print(f"Columns in 'audit_logs': {schema_v2['columns'].get('audit_logs')}")
        assert "audit_logs" in schema_v2["tables"]
        assert "is_active" in schema_v2["columns"]["user_accounts"]

        # Step 8: Verify Data Preservation Across Schema Evolutions
        print("\n--- STEP 8: Verifying Data Preservation ---")
        async with engine.connect() as conn:
            res = await conn.execute(text("SELECT id, username, email, is_active FROM user_accounts"))
            rows = res.fetchall()
            print("Existing Rows with Server Default 'is_active':")
            for row in rows:
                print(f"  User {row[0]}: {row[1]} ({row[2]}) -> is_active={row[3]}")
                assert row[3] == 1, "Existing rows should receive server default value"

        # Step 9: Reversible Rollback (Downgrade -1)
        print("\n--- STEP 9: Testing Deterministic Rollback (alembic downgrade -1) ---")
        command.downgrade(alembic_cfg, "-1")

        schema_v1_reverted = await inspect_db_schema(engine)
        current_rev_reverted = await get_current_alembic_version(engine)
        print(f"Active alembic_version after rollback: '{current_rev_reverted}'")
        print(f"Tables in Database: {schema_v1_reverted['tables']}")
        assert "audit_logs" not in schema_v1_reverted["tables"], "audit_logs should be dropped after downgrade"
        assert current_rev_reverted == current_rev_v1, "Alembic version should return to Revision 1"
        print("[SUCCESS] Rollback verified: audit_logs dropped, active version reverted cleanly.")

        print("\n" + "=" * 75)
        print("ALL PROGRAMMATIC ALEMBIC PIPELINE TESTS PASSED!")
        print("=" * 75)

    finally:
        await engine.dispose()
        # Clean up temporary directory
        if temp_workspace.exists():
            shutil.rmtree(temp_workspace, ignore_errors=True)
            print(f"\n[CLEANUP] Hermetic workspace '{temp_workspace}' removed.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
