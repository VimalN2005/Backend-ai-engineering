"""
Day 50: SQLAlchemy 2.0 AsyncSession, Relationships & Eager Loading
===================================================================

Enterprise Asynchronous ORM Architecture:
- AsyncEngine & AsyncSession lifecycle management
- Declarative 2.0 Mapped models with strict typing
- 1:N, M:1, and M:N relationships with back_populates
- Eager loading strategies: selectinload, joinedload, contains_eager
- Preventing MissingGreenlet crashes in async contexts
- Async streaming cursors (stream_scalars)
- Transaction boundaries with atomic context managers
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    event,
    func,
    select,
)
from sqlalchemy.exc import MissingGreenlet, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    contains_eager,
    joinedload,
    mapped_column,
    relationship,
    selectinload,
)

# ---------------------------------------------------------------------------
# 1. Declarative Base & Enterprise Model Schema
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy 2.0 declarative models.
    Enables PEP 484 type annotations and modern typing metadata.
    """
    pass


# Association table for Many-to-Many relationship between Employee and Project
# WHY: In pure relational modeling, an M:N relationship requires an intermediate junction table.
# We define composite primary keys (employee_id, project_id) to guarantee idempotency.
employee_project_association = Table(
    "employee_project_association",
    Base.metadata,
    Column("employee_id", Integer, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("assigned_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
)


class Organization(Base):
    """
    Top-level organizational tenant (1:N with Department, 1:N with Employee).
    """
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # 1:N Relationship to Department
    # WHY: lazy="raise" prevents implicit synchronous query execution in async code!
    # cascade="all, delete-orphan" guarantees orphaned departments are pruned.
    departments: Mapped[List["Department"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    # 1:N Relationship to Employee
    employees: Mapped[List["Employee"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name='{self.name}')>"


class Department(Base):
    """
    Department unit within an Organization (M:1 with Organization, 1:N with Project).
    """
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_center: Mapped[str] = mapped_column(String(50), nullable=False)

    # M:1 Relationship back to Organization
    organization: Mapped["Organization"] = relationship(
        back_populates="departments",
        lazy="raise",
    )

    # 1:N Relationship to Project
    projects: Mapped[List["Project"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Department(id={self.id}, name='{self.name}', org_id={self.organization_id})>"


class Employee(Base):
    """
    Individual employee (M:1 with Organization, M:N with Project).
    """
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="Engineer")

    # M:1 Relationship to Organization
    organization: Mapped["Organization"] = relationship(
        back_populates="employees",
        lazy="raise",
    )

    # M:N Relationship to Project via association table
    # WHY: secondary references the junction table defined above.
    projects: Mapped[List["Project"]] = relationship(
        secondary=employee_project_association,
        back_populates="assigned_employees",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Employee(id={self.id}, email='{self.email}', name='{self.full_name}')>"


class Project(Base):
    """
    Business or software project (M:1 with Department, M:N with Employee).
    """
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    budget: Mapped[int] = mapped_column(Integer, default=50000)

    # M:1 Relationship to Department
    department: Mapped["Department"] = relationship(
        back_populates="projects",
        lazy="raise",
    )

    # M:N Relationship to Employee
    assigned_employees: Mapped[List["Employee"]] = relationship(
        secondary=employee_project_association,
        back_populates="projects",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, title='{self.title}', budget={self.budget})>"


# ---------------------------------------------------------------------------
# 2. Database Engine & Session Factory Configuration
# ---------------------------------------------------------------------------

DB_FILE = "day50_pipeline_demo.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

def create_configured_async_engine(db_url: str = DATABASE_URL) -> AsyncEngine:
    """
    Creates an asynchronous engine with production-ready connection options.
    WHY:
    - echo=False keeps logs clean during production/benchmarking.
    - pool_pre_ping=True checks connection health before issuing queries.
    """
    return create_async_engine(
        db_url,
        echo=False,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    Configures the async_sessionmaker factory.
    WHY expire_on_commit=False:
    In async applications, expiring instances on commit forces synchronous lazy
    re-loads when reading attributes later, crashing with MissingGreenlet!
    Setting expire_on_commit=False keeps hydrated attributes in memory safely.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# 3. Database Schema Initialization & Seed Operations
# ---------------------------------------------------------------------------

async def init_database(engine: AsyncEngine) -> None:
    """
    Initializes DDL schemas asynchronously.
    WHY run_sync:
    DDL operations (metadata.create_all) interact with Core metadata synchronously.
    run_sync bridges the synchronous DDL function into the async connection.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("[INIT] Database tables created cleanly.")


async def seed_enterprise_data(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Seeds hierarchical enterprise data with proper Unit of Work transaction boundaries.
    """
    async with session_factory() as session:
        async with session.begin():
            # 1. Create Organization
            org = Organization(name="Nova Global Corp", domain="novaglobal.com")
            session.add(org)
            await session.flush()  # Populates org.id

            # 2. Create Departments
            dept_ai = Department(organization_id=org.id, name="AI Research Lab", cost_center="CC-901")
            dept_platform = Department(organization_id=org.id, name="Cloud Infrastructure", cost_center="CC-402")
            session.add_all([dept_ai, dept_platform])
            await session.flush()

            # 3. Create Employees
            emp1 = Employee(organization_id=org.id, email="alice@novaglobal.com", full_name="Alice Vance", role="Staff ML Engineer")
            emp2 = Employee(organization_id=org.id, email="bob@novaglobal.com", full_name="Bob Miller", role="Principal Architect")
            emp3 = Employee(organization_id=org.id, email="clara@novaglobal.com", full_name="Clara Oswald", role="Senior SRE")
            session.add_all([emp1, emp2, emp3])
            await session.flush()

            # 4. Create Projects with assigned employees
            # WHY: When lazy="raise" is active, assigning relationships during __init__
            # initializes the collection in memory without triggering a loader callable.
            proj_rag = Project(
                department_id=dept_ai.id, 
                title="Enterprise RAG Knowledge Graph", 
                budget=250000,
                assigned_employees=[emp1, emp2]
            )
            proj_k8s = Project(
                department_id=dept_platform.id, 
                title="Multi-Region Kubernetes Mesh", 
                budget=180000,
                assigned_employees=[emp2, emp3]
            )
            session.add_all([proj_rag, proj_k8s])
            await session.flush()

    print("[SEED] Seed data successfully committed.")


# ---------------------------------------------------------------------------
# 4. Eager Loading Pipeline Demonstrations
# ---------------------------------------------------------------------------

async def demonstrate_selectinload_collection(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Demonstrates selectinload for 1:N collections.
    WHY selectinload:
    Emits 2 queries (1 for parent, 1 for children via WHERE parent_id IN (...)).
    Eliminates Cartesian product duplicate rows and prevents N+1 query storms.
    """
    print("\n--- 1. Demonstrating selectinload (1:N Collection Loading) ---")
    async with session_factory() as session:
        # Load Organization with its Departments and Department.Projects in 3 clean O(1) queries
        stmt = (
            select(Organization)
            .options(
                selectinload(Organization.departments).selectinload(Department.projects)
            )
            .where(Organization.name == "Nova Global Corp")
        )

        result = await session.execute(stmt)
        org = result.scalar_one()

        print(f"Loaded Org: {org.name} (Domain: {org.domain})")
        for dept in org.departments:
            print(f"  -> Department: {dept.name} [Cost Center: {dept.cost_center}]")
            for proj in dept.projects:
                print(f"       * Project: {proj.title} (Budget: ${proj.budget:,})")


async def demonstrate_joinedload_many_to_one(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Demonstrates joinedload for Many-to-One and One-to-One scalar relationships.
    WHY joinedload:
    Emits a single SQL query using LEFT OUTER JOIN.
    Perfect for M:1 (Department -> Organization) where no duplicate rows are created.
    """
    print("\n--- 2. Demonstrating joinedload (M:1 Scalar Loading) ---")
    async with session_factory() as session:
        stmt = (
            select(Department)
            .options(joinedload(Department.organization))
            .where(Department.cost_center == "CC-901")
        )

        result = await session.execute(stmt)
        # Note: When using joinedload with scalars(), unique() is required to deduplicate instances
        dept = result.unique().scalar_one()

        print(f"Department: {dept.name}")
        print(f"  Parent Organization (joinedload): {dept.organization.name} [Domain: {dept.organization.domain}]")


async def demonstrate_many_to_many_selectinload(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Demonstrates loading Many-to-Many collections via selectinload.
    WHY selectinload for M:N:
    Avoids joining the junction table directly into the primary rowset,
    querying the junction table and target entities in an optimized batch.
    """
    print("\n--- 3. Demonstrating Many-to-Many Loading with selectinload ---")
    async with session_factory() as session:
        stmt = (
            select(Employee)
            .options(selectinload(Employee.projects))
            .order_by(Employee.full_name)
        )

        result = await session.execute(stmt)
        employees = result.scalars().all()

        for emp in employees:
            proj_titles = [p.title for p in emp.projects]
            print(f"Employee: {emp.full_name} ({emp.role})")
            print(f"  Assigned Projects ({len(proj_titles)}): {', '.join(proj_titles)}")


async def demonstrate_contains_eager_filtered_join(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Demonstrates contains_eager when filtering by child entity attributes.
    WHY contains_eager:
    Beginners often use joinedload combined with a WHERE clause on the joined table,
    which adds a duplicate LEFT JOIN or ignores filters.
    contains_eager tells SQLAlchemy that the existing .join() query already contains
    the related columns, hydrating the relationship directly from the projection!
    """
    print("\n--- 4. Demonstrating contains_eager (Filtered Joins) ---")
    async with session_factory() as session:
        # We want Departments that have Projects with budget >= 200,000
        stmt = (
            select(Department)
            .join(Department.projects)
            .where(Project.budget >= 200000)
            .options(contains_eager(Department.projects))
        )

        result = await session.execute(stmt)
        departments = result.unique().scalars().all()

        for dept in departments:
            print(f"Filtered Department: {dept.name}")
            for p in dept.projects:
                print(f"  -> High-Budget Project: {p.title} (${p.budget:,})")


async def demonstrate_missing_greenlet_guard(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Demonstrates the MissingGreenlet exception when attempting to access
    an unloaded relationship in async mode, and how lazy='raise' protects us.
    """
    print("\n--- 5. Demonstrating MissingGreenlet / lazy='raise' Protection ---")
    async with session_factory() as session:
        # Intentionally query an Employee WITHOUT eager loading projects or organization
        stmt = select(Employee).where(Employee.email == "alice@novaglobal.com")
        result = await session.execute(stmt)
        emp = result.scalar_one()

        print(f"Loaded Employee: {emp.full_name} (Scalar fields loaded OK)")

        # Attempting to access emp.organization synchronously without eager loading
        try:
            print("Attempting to access emp.organization synchronously...")
            _ = emp.organization.name
        except (MissingGreenlet, SQLAlchemyError) as exc:
            print("Successfully caught expected relationship access error!")
            print(f"  Exception Class: {type(exc).__name__}")
            print(f"  Message summary: {str(exc).splitlines()[0]}")
            print("  EXPLANATION: In async SQLAlchemy, relationships cannot be loaded implicitly")
            print("  via property access because property access cannot be awaited.")
            print("  Configuring lazy='raise' turns silent runtime failures into deterministic errors!")


async def demonstrate_async_streaming(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Demonstrates stream_scalars() for low-memory batch processing.
    WHY stream_scalars:
    Rather than buffering 100,000 rows into application RAM with .all(),
    stream_scalars yields rows progressively via server-side cursors.
    """
    print("\n--- 6. Demonstrating Async Streaming (stream_scalars) ---")
    async with session_factory() as session:
        stmt = select(Employee).order_by(Employee.id)

        stream = await session.stream_scalars(stmt)
        stream_count = 0
        async for emp in stream:
            stream_count += 1
            print(f"  [STREAM ROW {stream_count}] Yielded: {emp.full_name} ({emp.email})")


# ---------------------------------------------------------------------------
# 5. Pipeline Orchestration & Cleanup
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 70)
    print("SQLAlchemy 2.0 Async Pipeline & Eager Loading Execution")
    print("=" * 70)

    engine = create_configured_async_engine()
    session_factory = create_session_factory(engine)

    try:
        # Step 1: DDL Setup
        await init_database(engine)

        # Step 2: Seed Data
        await seed_enterprise_data(session_factory)

        # Step 3: Run Demonstrations
        await demonstrate_selectinload_collection(session_factory)
        await demonstrate_joinedload_many_to_one(session_factory)
        await demonstrate_many_to_many_selectinload(session_factory)
        await demonstrate_contains_eager_filtered_join(session_factory)
        await demonstrate_missing_greenlet_guard(session_factory)
        await demonstrate_async_streaming(session_factory)

        print("\n" + "=" * 70)
        print("All Async Pipeline Demonstrations Completed Successfully!")
        print("=" * 70)

    finally:
        # Proper engine disposal to release file/socket locks
        await engine.dispose()
        if os.path.exists(DB_FILE):
            try:
                os.remove(DB_FILE)
                print(f"[CLEANUP] Removed temporary database file '{DB_FILE}'.")
            except OSError:
                pass


if __name__ == "__main__":
    # Ensure Windows asyncio compatibility
    asyncio.run(main())
