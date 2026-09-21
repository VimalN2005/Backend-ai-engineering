# 🧭 Complete 90-Day Master Roadmap: Python Backend + AI Systems

This roadmap condenses 180 days of comprehensive curriculum into 90 rigorous, highly focused daily milestones (~15–20 core concepts per day grouped under a unified theme).

---

## 📅 Phase 1: Python Core, Internals & Concurrency (Days 01–15)

### Day 01: Python Environment, Execution Flow & Memory References
- Python installation, versions, interpreter architecture, CPython vs PyPy.
- Bytecode compilation (`.pyc`), Python Virtual Machine (PVM) loop.
- PATH environment variable, shebang, REPL mechanics, VS Code interpreter selection.
- `pip`, `venv`, isolated site-packages, `requirements.txt`, lockfiles, `.gitignore`.
- Variables, objects, memory addresses, `id()`, `type()`, `isinstance()`.
- Mutable vs immutable primitives, reference assignment vs copying.

### Day 02: Strings, Unicode, Regular Expressions & Slicing
- String immutability, interned strings, memory footprint of ASCII vs UTF-8.
- Indexing, negative indexing, step slicing `[start:stop:step]`.
- String operations: `strip`, `split`, `join`, `replace`, `find`, `partition`.
- Modern string interpolation: f-strings vs `str.format()` vs `%` formatting.
- Unicode encoding, decoding, bytes vs strings, ASCII table boundaries.
- Regular expressions (`re` module): pattern matching, compile, match, search, findall, sub, capture groups.

### Day 03: Data Structures Internals (Lists, Tuples, Sets, Dicts)
- Dynamic array mechanics: List over-allocation, amortized $\mathcal{O}(1)$ appends vs $\mathcal{O}(N)$ inserts.
- Tuple packing, unpacking, named tuples (`collections.namedtuple`), tuple immutability.
- Hash sets: Hash function, bucket collisions, open addressing, membership testing $\mathcal{O}(1)$.
- Hash map / Dictionary internals: Compact dict architecture, hash tables, key collision resolution.
- Dictionary operations: `get()`, `setdefault()`, `update()`, dictionary comprehension.
- Mutability gotchas: Hashable vs unhashable types, nested structures copying.

### Day 04: Control Flow, Iteration & Comprehensions
- Truthy vs falsy values, short-circuit evaluation (`and`, `or`, `not`).
- Ternary expressions, comparison chaining, guard clause pattern for clean code.
- `for` loops, `while` loops, `range()`, `enumerate()`, `zip()`, `reversed()`.
- Loop control: `break`, `continue`, `pass`, and the `for...else` construct.
- List, dictionary, and set comprehensions with multiple filters and nested iterations.
- Comprehensions vs generator expressions: memory footprint comparison.

### Day 05: Functions, Scopes & Production Type Hinting
- Function definitions, first-class citizen properties, invocation stack frames.
- Positional, keyword, default parameters, variable arguments (`*args`, `**kwargs`).
- Keyword-only arguments (`*`), positional-only arguments (`/`).
- Scope resolution rules (LEGB: Local, Enclosing, Global, Built-in), `global` and `nonlocal`.
- Modern Python Type Hints: `typing` module, `Optional`, `Union`, `Literal`, `Annotated`.
- Static analysis concept with `mypy` and PEP 484 compliance.

### Day 06: Closures, Decorators & Functional Programming
- Lexical scoping, closure creation, cell objects, `__closure__` introspection.
- Decorator anatomy: function-wrapping functions, metadata loss and `@functools.wraps`.
- Decorators accepting arguments (3-tier nested wrapper pattern).
- Class-based decorators with `__call__`.
- Higher-order functions: `map()`, `filter()`, `functools.reduce()`, `functools.partial()`.
- Production decorator use cases: execution timer, retry mechanism, rate limiter, cache memoization.

### Day 07: Exception Handling & Context Managers
- Python Exception hierarchy: `BaseException` vs `Exception`, built-in exceptions.
- `try`, `except`, `else`, `finally` control blocks and execution flow.
- Raising exceptions, custom exception classes, exception chaining (`raise ... from err`).
- Traceback extraction and inspection via `sys.exc_info()`.
- Context management protocol: `__enter__()` and `__exit__()` dunder methods.
- The `contextlib` standard library: `@contextmanager` generator-based managers.
- Production use cases: database connections, file handlers, acquired threading locks.

### Day 08: File I/O, Pathlib & High-Performance Serialization
- Standard file modes (`r`, `w`, `a`, `b`, `+`), text vs binary streams, buffer sizes.
- `pathlib.Path`: modern filesystem manipulation, paths concatenation, globbing.
- Structured data parsing: `csv` module, delimiter sniffing, dictionary readers.
- JSON serialization: `json.loads()`, `json.dumps()`, custom encoder for datetime/UUID.
- Binary serialization: `pickle` mechanics and its critical security vulnerability risks.
- Safe atomic file write pattern in production environments.

### Day 09: Object-Oriented Programming & Dunder Protocol
- Classes, objects, instantiation lifecycle: `__new__()` vs `__init__()`.
- Instance variables vs class variables, instance methods vs `@classmethod` vs `@staticmethod`.
- Properties: `@property`, setters, deleters, encapsulation.
- Inheritance, Method Resolution Order (MRO), C3 Linearization, `super()`.
- Abstract Base Classes (ABC) via `abc.ABC` and `@abstractmethod`.
- Magic / Dunder methods: `__str__`, `__repr__`, `__eq__`, `__len__`, `__hash__`, `__getitem__`.

### Day 10: Modern Dataclasses & Strict Pydantic V2 Models
- `dataclasses.dataclass`, `field()`, `default_factory`, frozen dataclasses.
- Dataclass inheritance, comparisons, slot-based optimization (`slots=True`).
- Pydantic V2 Core: `BaseModel`, schema definition, runtime validation.
- Field validation: `Field()`, `@field_validator`, `@model_validator`.
- Serialization & Deserialization: `model_dump()`, `model_dump_json()`, `model_validate()`.
- Dynamic typing vs runtime validation trade-offs in high-throughput backends.

### Day 11: Iterators, Generators & Lazy Evaluation
- Iterable protocol (`__iter__`) vs Iterator protocol (`__next__`, `StopIteration`).
- Generator functions, `yield` statement, generator state suspension.
- `yield from` subgenerator delegation.
- Coroutine-like generator features: `.send()`, `.throw()`, `.close()`.
- Streaming large files (10GB+ CSVs/Logs) without RAM spikes.
- Performance and memory benchmarking: `sys.getsizeof()` analysis.

### Day 12: Concurrency: Threading, Multiprocessing & The GIL
- Concurrency vs Parallelism, CPU-bound vs I/O-bound tasks.
- Global Interpreter Lock (GIL) internals: Why it exists, thread switching, bytecode counts.
- `threading` module, `Thread`, daemon threads, race conditions.
- Synchronization primitives: `Lock`, `RLock`, `Semaphore`, `Event`.
- `multiprocessing` module, `Process`, Inter-Process Communication (IPC), `Queue`.
- `concurrent.futures`: `ThreadPoolExecutor` and `ProcessPoolExecutor` pools.

### Day 13: Asyncio: Event Loop, Coroutines & Asynchronous I/O
- Synchronous blocking I/O vs non-blocking asynchronous event loop.
- `async` / `await` syntax, coroutines, futures, tasks (`asyncio.create_task`).
- Concurrency with `asyncio.gather()`, `asyncio.wait()`, exception handling in tasks.
- Async context managers (`async with`) and async iterators (`async for`).
- Avoid blocking the loop: running sync code in executor (`loop.run_in_executor`).
- Real-world async HTTP calls with `aiohttp` or `httpx.AsyncClient`.

### Day 14: Unit Testing (pytest), Mocking & Production Logging
- Testing philosophy: Unit vs Integration vs E2E tests, Test-Driven Development (TDD).
- `pytest` runner, test discovery, assertions, pytest command flags (`-v`, `-s`, `-k`).
- Pytest fixtures, fixture scopes (`function`, `module`, `session`), autouse.
- Parametrized tests (`@pytest.mark.parametrize`), test markers.
- Mocking with `unittest.mock`: `Mock`, `MagicMock`, `@patch`, patching imports vs objects.
- Structured logging (`logging` module): Loggers, Handlers, Formatters, Log levels, JSON logging.

### Day 15: Advanced Git, GitHub Actions & Linux CLI + Phase 1 Review
- Git plumbing vs porcelain: commits, tree objects, blobs, SHA-1 hashes.
- Advanced branching: `merge --no-ff`, `rebase -i`, `cherry-pick`, `stash pop`.
- Merge conflicts resolution, detached HEAD recovery, `git reflog`.
- Linux backend essentials: `systemd`, `grep`, `awk`, `sed`, `curl`, `netstat`, `htop`, permissions (`chmod`).
- GitHub Actions CI workflow: Running linters (Ruff/Flake8) and test suite on push.
- **Phase 1 Master Review & Self-Assessment**.

---

## 📅 Phase 2: Practical Backend DSA Patterns (Days 16–30)

### Day 16: Two Pointers & In-Place Array Manipulation
- Two Pointers paradigm: Left/Right boundaries vs Fast/Slow runners.
- Two Sum on sorted array, Remove Duplicates in-place, Move Zeroes.
- Container With Most Water ($\mathcal{O}(N)$ greedy shrinkage).
- 3Sum problem (sorting + two pointer combo).
- Backend application: In-memory sorted log scanning, deduplication without extra memory.

### Day 17: Sliding Window Technique (Fixed & Dynamic)
- Fixed window: Maximum sum subarray of size K.
- Dynamic window: Longest substring without repeating characters.
- Minimum size subarray sum exceeding target.
- Sliding window with Hash Map: Permutation in string.
- Backend application: Rolling window API rate limiting, time-series metrics aggregation.

### Day 18: HashMaps & HashSets: Collision Handling & Lookups
- $\mathcal{O}(1)$ average lookup mechanics, bucket distribution, load factor.
- Group Anagrams (Sorted key vs frequency tuple key).
- Subarray Sum Equals K (Prefix sum + Hash Map pattern).
- Longest Consecutive Sequence in $\mathcal{O}(N)$.
- Backend application: In-memory session stores, foreign key lookups, deduplication caches.

### Day 19: Stacks & Queues: Order Preservation & Processing
- LIFO vs FIFO characteristics, list vs `collections.deque` performance.
- Valid Parentheses validation (compiler syntax checker).
- Evaluate Reverse Polish Notation (stack evaluation).
- Implement Queue using Stacks and Stack using Queues.
- Backend application: Undo/Redo buffers, request middleware call stack unwinding.

### Day 20: Monotonic Stack Patterns
- Monotonic stack concept: Maintaining strictly increasing/decreasing order.
- Next Greater Element I & II.
- Daily Temperatures problem.
- Largest Rectangle in Histogram ($\mathcal{O}(N)$ stack solution).
- Backend application: High/Low stock price trend calculation, latency spike alerts.

### Day 21: Linked Lists: Fast & Slow Pointers
- Singly vs Doubly linked lists, node pointers, memory overhead.
- In-place reversal of Linked List (iterative and recursive).
- Floyd's Cycle-Finding Algorithm (Hare and Tortoise).
- Merge Two Sorted Lists & Reorder List.
- Backend application: Database undo logs, chained middleware execution pipeline.

### Day 22: Binary Search & Search Space Reduction
- Classic Binary Search on sorted array ($\mathcal{O}(\log N)$).
- Search in Rotated Sorted Array.
- Find First and Last Position of Element in Sorted Array.
- Binary Search on answer space: Koko Eating Bananas.
- Backend application: Searching indexed logs, binary search on timestamp ranges.

### Day 23: Binary Trees: Traversals (DFS)
- Tree node structures, binary tree properties, depth vs height.
- Inorder traversal (Left, Root, Right) - iterative & recursive.
- Preorder and Postorder traversals.
- Invert Binary Tree & Maximum Depth of Binary Tree.
- Backend application: Hierarchical file systems, AST (Abstract Syntax Tree) traversal.

### Day 24: Trees: Breadth-First Search (Level Order)
- BFS queue-based exploration algorithm.
- Binary Tree Level Order Traversal.
- Binary Tree Right Side View.
- Lowest Common Ancestor (LCA) in Binary Search Tree.
- Backend application: Role-Based Access Control (RBAC) hierarchy permissions resolution.

### Day 25: Heaps & Priority Queues
- Binary Heap properties: Min-heap vs Max-heap, array representation.
- Python `heapq` module: `heappush`, `heappop`, `heapify`, `nlargest`.
- Kth Largest Element in an Array ($\mathcal{O}(N \log K)$).
- Top K Frequent Elements (Bucket sort vs Heap).
- Backend application: Priority job queues, Top-K metric streaming engines.

### Day 26: Intervals & Sorting Mechanics
- Interval overlap conditions: `start1 <= end2 and start2 <= end1`.
- Merge Intervals.
- Insert Interval.
- Non-overlapping Intervals.
- Backend application: Calendar appointment scheduling, database time range overlap locks.

### Day 27: Graph Fundamentals: BFS & DFS
- Adjacency Matrix vs Adjacency List representations.
- Graph DFS: Recursive path exploration, cycle detection.
- Graph BFS: Shortest path in unweighted graph.
- Number of Islands (Grid traversal).
- Backend application: Social graph followers, microservices network reachability.

### Day 28: Topological Sort (DAG Dependency Resolution)
- Directed Acyclic Graphs (DAG), indegrees, Kahn's Algorithm (BFS).
- Course Schedule I & II (Detecting cycle in dependencies).
- Alien Dictionary / Build order resolution.
- Backend application: Celery task dependency graphs, database migration ordering.

### Day 29: System DSA: LRU Cache Design
- Cache eviction algorithms: LRU, LFU, FIFO.
- Designing LRU Cache using Hash Map + Doubly Linked List for $\mathcal{O}(1)$ `get` and `put`.
- Python's `collections.OrderedDict` mechanics.
- Thread-safe LRU Cache considerations.
- Backend application: In-memory application cache, Redis eviction policy mechanics.

### Day 30: System DSA: Rate Limiting Algorithms + Phase 2 Review
- Token Bucket algorithm implementation.
- Leaky Bucket algorithm.
- Fixed Window Counter vs Sliding Window Log.
- Consistent Hashing concept (Hash ring, virtual nodes).
- **Phase 2 Comprehensive Review & Algorithmic Problem Solving Sprint**.

---

## 📅 Phase 3: Web Fundamentals, Django & DRF Production (Days 31–45)

### Day 31: Web Protocols, HTTP 1.1/2/3 & REST Standards
- OSI 7-Layer model vs TCP/IP stack, DNS lookup lifecycle, IP routing, sockets.
- HTTP Request & Response lifecycle, URI/URL parsing.
- HTTP Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD. Idempotency contracts.
- Status codes: 2xx (Success), 3xx (Redirects), 4xx (Client errors), 5xx (Server errors).
- HTTP Headers: `Authorization`, `Content-Type`, `Cache-Control`, `ETag`, `Origin`.
- CORS (Cross-Origin Resource Sharing) handshake, Preflight requests (`OPTIONS`).

### Day 32: Django Setup, Project Architecture & Settings
- Django philosophy: "Batteries Included", MTV (Model-Template-View) pattern.
- `django-admin` vs `manage.py`, project directory anatomy.
- Modular settings architecture (`base.py`, `local.py`, `production.py`).
- Environment variables management with `django-environ` / `python-decouple`.
- Django App registry, `apps.py`, pluggable app structure.
- WSGI vs ASGI entrypoints (`wsgi.py`, `asgi.py`).

### Day 33: Django Models & Database Mapping
- `models.Model`, Django ORM field types: `CharField`, `TextField`, `IntegerField`, `DateTimeField`, `UUIDField`.
- Field attributes: `null`, `blank`, `default`, `choices`, `db_index`, `unique`.
- Model `Meta` options: `ordering`, `indexes`, `unique_together`, `constraints`.
- Primary keys: Integer AutoField vs UUID primary keys (trade-offs).
- Timestamped abstract base models (`created_at`, `updated_at`).

### Day 34: Django ORM Queries & Aggregations
- The QuerySet: Lazy evaluation, caching, slicing, chaining.
- Query methods: `all()`, `filter()`, `exclude()`, `get()`, `order_by()`, `values()`, `values_list()`.
- Complex lookups with `Q` objects (`AND`, `OR`, `NOT`).
- Aggregations and Annotations with `F` expressions, `Count`, `Sum`, `Avg`.
- Database query logging and query count auditing.

### Day 35: ORM Relationships & The N+1 Query Problem
- Relationships: `OneToOneField`, `ForeignKey`, `ManyToManyField`.
- Reverse lookups, `related_name`, `related_query_name`.
- On-delete policies: `CASCADE`, `PROTECT`, `SET_NULL`, `DO_NOTHING`.
- The N+1 query problem diagnosis.
- Optimizations: `select_related()` (SQL JOIN) vs `prefetch_related()` (Separate queries + Python lookup).

### Day 36: Django Migrations Masterclass
- How migrations work: migration files, dependencies graph, `django_migrations` table.
- `makemigrations`, `migrate`, `showmigrations`, `sqlmigrate`.
- Custom data migrations (`RunPython`, `RunSQL`).
- Safe schema evolution: Adding non-null fields, zero-downtime migration strategies.
- Handling migration conflicts in team git workflows.

### Day 37: Views, Request Flow & Custom Middleware
- Function-Based Views (FBV) vs Class-Based Views (CBV).
- `HttpRequest` and `HttpResponse` objects, `JsonResponse`, status codes.
- Django Middleware pipeline architecture.
- Writing custom middleware: Request timing, custom header injection, IP whitelisting.
- Middleware ordering and exception handling in the chain.

### Day 38: Custom Authentication & User Models
- AbstractUser vs AbstractBaseUser (When to use which).
- Replacing default User model with Email-based custom user before initial migration.
- Password hashing mechanics (PBKDF2, Argon2, bcrypt).
- Django Permission system: Permissions, Groups, User permissions.
- Custom authentication backends.

### Day 39: Django REST Framework (DRF) Basics & Serializers
- DRF architecture: Serialization, Deserialization, Validation.
- `Serializer` vs `ModelSerializer`.
- Field validations: `validate_<field_name>()`, `validate()` object-level validation.
- Read-only vs Write-only fields, nested representation serializers.
- DRF Browsable API configuration.

### Day 40: DRF Views, ViewSets & Routing
- `APIView`: Raw request handling and explicit HTTP methods.
- Generic API Views: `ListCreateAPIView`, `RetrieveUpdateDestroyAPIView`.
- ViewSets: `ViewSet`, `ModelViewSet`, `ReadOnlyModelViewSet`.
- Routers: `DefaultRouter`, `SimpleRouter`, dynamic URL generation.
- Custom actions on ViewSets (`@action(detail=True/False)`).

### Day 41: DRF Authentication: JWT & Permissions
- Token authentication vs Session authentication vs JWT.
- JSON Web Token (JWT) architecture: Header, Payload, Signature.
- Integrating `djangorestframework-simplejwt`.
- Access token vs Refresh token lifecycle, token rotation and blacklisting.
- Custom DRF Permissions: `BasePermission`, `has_permission`, `has_object_permission`.

### Day 42: DRF Advanced Validation, Pagination & Filtering
- Complex cross-model validation rules in serializers.
- Custom exception handling: Intercepting DRF exceptions, RFC 7807 error schema.
- Pagination strategies: `PageNumberPagination`, `LimitOffsetPagination`, `CursorPagination`.
- Filtering with `django-filter`, search filtering (`SearchFilter`), ordering (`OrderingFilter`).
- Rate limiting / Throttling in DRF (`UserRateThrottle`, `AnonRateThrottle`).

### Day 43: Signals Antipattern vs Service Layer Pattern
- Django Signals: `pre_save`, `post_save`, `pre_delete`, `post_delete`.
- Why signals lead to hidden side-effects, transaction race conditions, and debugging nightmares.
- Clean Service Layer architecture: Separating business logic from views and models.
- Selectors (Query logic) and Services (Mutation logic) pattern.

### Day 44: Redis Caching & Database Concurrency in Django
- Django caching framework configuration with Redis.
- View-level caching (`@cache_page`), template fragment caching, low-level cache API.
- Cache keys invalidation strategies.
- Database transactions: `transaction.atomic()`, rollback triggers.
- Preventing race conditions: Pessimistic locking (`select_for_update()`) vs Optimistic locking.

### Day 45: Project 1: Enterprise Multi-Tenant DRF SaaS Backend + Review
- Architecture: Custom User, Organizations, RBAC permissions, Subscription billing logic.
- Automated tests with `APITestCase` and `factory_boy`.
- Production deployment configuration: Gunicorn + Nginx + Docker.
- **Phase 3 Master Review & Code Refactoring Sprint**.

---

## 📅 Phase 4: Databases (SQL, PostgreSQL, SQLAlchemy) & FastAPI (Days 46–60)

### Day 46: Advanced SQL: Joins, Subqueries, CTEs & Windows
- Deep SQL Execution order: FROM ➔ WHERE ➔ GROUP BY ➔ HAVING ➔ SELECT ➔ ORDER BY ➔ LIMIT.
- Joins: INNER, LEFT OUTER, RIGHT OUTER, FULL OUTER, CROSS, SELF JOIN.
- Subqueries vs Correlated Subqueries (`EXISTS`, `IN`).
- Common Table Expressions (CTEs): Recursive CTEs for hierarchical tree queries.
- Window functions: `ROW_NUMBER()`, `RANK()`, `DENSE_RANK()`, `LEAD()`, `LAG()`, `OVER (PARTITION BY ... ORDER BY ...)`.

### Day 47: PostgreSQL Internals & Index Optimization
- PostgreSQL storage architecture: Heap files, Tuples, MVCC (Multi-Version Concurrency Control).
- VACUUM and AUTOVACUUM mechanics.
- Index types: B-Tree, GIN (for JSONB/Full-text), GiST, Hash, Partial indexes, Composite indexes.
- Query Planner: Reading and interpreting `EXPLAIN (ANALYZE, BUFFERS)`.
- Index selectivity, Sequential scan vs Index scan vs Bitmap index scan.

### Day 48: Database Transactions & ACID Guarantees
- Atomicity, Consistency, Isolation, Durability breakdown.
- Concurrency anomalies: Dirty reads, Non-repeatable reads, Phantom reads, Serialization anomalies.
- Transaction Isolation Levels: Read Uncommitted, Read Committed, Repeatable Read, Serializable.
- Lock types: Table locks, Row locks, Shared vs Exclusive locks.
- Deadlocks detection and avoidance patterns.

### Day 49: SQLAlchemy 2.0 Core & Declarative Models
- SQLAlchemy 2.0 paradigm shift: Imperative vs Declarative.
- `Engine`, `Connection`, `Session` lifecycles.
- Declarative Base (`Mapped`, `mapped_column`), Python type annotations to DB types.
- Select queries with `select()`, filtering with `.where()`, `.order_by()`.
- Insert, Update, Delete statements in SQLAlchemy 2.0 style.

### Day 50: SQLAlchemy 2.0 AsyncSession & Relationships
- `create_async_engine`, `async_sessionmaker`, `AsyncSession`.
- Defining relationships: `relationship()`, `ForeignKey()`, `back_populates`.
- Eager loading strategies: `selectinload()`, `joinedload()`, avoiding async greenlet errors.
- Unit of Work pattern: Flush vs Commit, rollbacks on error.
- Async CRUD repository pattern.

### Day 51: Database Migrations with Alembic
- Alembic architecture: `env.py`, `script.py.mako`, `alembic.ini`.
- Configuring Alembic for Async SQLAlchemy models.
- Generating revisions (`alembic revision --autogenerate -m "..."`).
- Inspecting generated migration scripts, manual adjustments.
- Applying migrations (`alembic upgrade head`), rollbacks (`alembic downgrade -1`).

### Day 52: FastAPI Fundamentals & Request Handling
- FastAPI architecture: Built on Starlette (ASGI) and Pydantic.
- App initialization, routes, path operations (`@app.get()`, `@app.post()`).
- Path parameters, type validation, automatic Swagger/OpenAPI documentation (`/docs`).
- Query parameters: optional, defaults, type casting.
- Request body, Response model filtering (`response_model`, `response_model_exclude_unset`).

### Day 53: Pydantic V2 Deep Dive & Custom Validation
- `BaseModel`, attributes, default values, complex nested schemas.
- `Field()` configurations: regex, minimum/maximum bounds, descriptions.
- Custom field validators (`@field_validator`) with mode `before`/`after`.
- Model validators (`@model_validator`) for multi-field business rules.
- JSON schema export, custom serializers (`@field_serializer`).

### Day 54: FastAPI Dependency Injection System
- The `Depends` pattern: Inversion of Control in FastAPI.
- Function dependencies, Class-based dependencies.
- Sub-dependencies and nested dependency trees.
- Yield dependencies: Providing and cleaning up DB sessions (`async with get_db() as session`).
- Overriding dependencies during testing (`app.dependency_overrides`).

### Day 55: FastAPI Authentication: OAuth2 & JWT
- OAuth2 with Password Grant flow (`OAuth2PasswordBearer`, `OAuth2PasswordRequestForm`).
- Password hashing with `passlib[bcrypt]` / `bcrypt`.
- Generating and verifying JWT tokens with `pyjwt`.
- Current authenticated user dependency (`get_current_user`, `get_current_active_user`).
- Role-based route protection via security scopes.

### Day 56: BackgroundTasks, Middleware & Streaming
- FastAPI native `BackgroundTasks` for lightweight post-response execution.
- Writing ASGI Middleware: Request ID correlation tracking, execution timing headers.
- CORS middleware configuration (`CORSMiddleware`).
- Streaming responses (`StreamingResponse`) for large downloads and Server-Sent Events (SSE).
- Global exception handlers: Catching custom domain exceptions.

### Day 57: Async Testing with Pytest & HTTPX
- Setting up `pytest-asyncio` with SQLite in-memory or test PostgreSQL container.
- `httpx.AsyncClient` with `ASGITransport`.
- Fixtures for test DB initialization, session cleanup, and mock users.
- Testing authenticated routes, dependency overrides.
- Coverage reports with `pytest-cov`.

### Day 58: Redis Caching & Distributed Locks
- Connecting to Redis via `redis-py` async client.
- Key design conventions, TTL (Time-To-Live) expirations.
- Cache-aside pattern implementation with async decorators.
- Redis as a Distributed Lock using `SET NX PX` and Redlock algorithm concept.
- Handling cache stampedes with probabilistic early expiration (XFetch).

### Day 59: Celery Task Queue, Redis Broker & Background Workers
- Why FastAPI BackgroundTasks isn't enough for heavy tasks.
- Celery setup with Redis broker and result backend.
- Defining tasks (`@celery_app.task`), async dispatch (`.delay()`, `.apply_async()`).
- Task retries with exponential backoff, handling task failures.
- Celery Beat for periodic scheduled cron jobs.
- Flower monitoring dashboard.

### Day 60: Project 2: High-Performance Async FastAPI Backend + Review
- Architecture: FastAPI + Async SQLAlchemy + PostgreSQL + Redis Cache + Celery Workers.
- Complete CRUD, Authentication, Rate Limiting, OpenAPI documentation, and test coverage.
- **Phase 4 Master Review & Performance Benchmark**.

---

## 📅 Phase 5: Production Backend, DevOps & High-Scale System Design (Days 61–75)

### Day 61: Docker Containers & Multi-Stage Builds
- Containerization principles: Images vs Containers, namespaces, cgroups.
- Writing production `Dockerfile`: Base image selection (Alpine vs Slim), non-root users.
- Layer caching optimization: Ordering `COPY` commands to leverage build cache.
- Multi-stage Docker builds to minimize image size for Python backends.
- `.dockerignore` best practices.

### Day 62: Docker Compose Multi-Container Orchestration
- `docker-compose.yml` anatomy: Services, networks, volumes, environment variables.
- Multi-container setup: FastAPI app + PostgreSQL + Redis + Celery Worker + Flower.
- Service dependencies (`depends_on` with health checks).
- Named persistent volumes for databases.
- Networking isolation between internal and exposed services.

### Day 63: GitHub Actions CI/CD Pipeline
- Continuous Integration & Continuous Deployment principles.
- GitHub Actions workflow syntax (`.github/workflows/*.yml`).
- Matrix testing across Python versions.
- Linting (Ruff/Flake8), formatting (Black), type checking (Mypy), running Pytest.
- Building and pushing production Docker image to GitHub Container Registry (GHCR) or Docker Hub.

### Day 64: AWS Cloud Fundamentals for Backend Engineers
- AWS Global infrastructure: Regions and Availability Zones (AZs).
- IAM (Identity and Access Management): Users, Groups, Roles, Policies, Principle of Least Privilege.
- Amazon EC2: Virtual servers, security groups, key pairs.
- Amazon S3: Object storage, buckets, access policies, pre-signed URLs for file uploads.
- Amazon RDS: Managed PostgreSQL, automated backups, multi-AZ deployment.

### Day 65: Production Server Deployment: Nginx & SSL
- Setting up Ubuntu server on EC2, firewall (`ufw`) configuration.
- Nginx as Reverse Proxy and Load Balancer.
- Gunicorn / Uvicorn systemd service configuration.
- Securing traffic: Free SSL certificates with Let's Encrypt and Certbot.
- Hardening Nginx with security headers (HSTS, X-Frame-Options, CSP).

### Day 66: System Design Core Principles
- Functional vs Non-Functional requirements.
- Latency vs Throughput, Availability vs Consistency.
- SLA, SLO, and SLI definitions.
- CAP Theorem (Consistency, Availability, Partition Tolerance) and PACELC.
- Single Point of Failure (SPOF) elimination.

### Day 67: Database Scaling Strategies
- Vertical scaling vs Horizontal scaling.
- Database Replication: Master-Replica architecture, read replicas, replication lag.
- Database Partitioning: Range, List, Hash partitioning in PostgreSQL.
- Database Sharding: Routing keys, cross-shard joins challenges.
- Connection pooling with PgBouncer.

### Day 68: Caching Topologies & Failure Modes
- Caching levels: Client, CDN (Cloudflare), Reverse Proxy, Application in-memory, Distributed (Redis).
- Cache Invalidation strategies: Cache-aside, Write-through, Write-back, Write-around.
- Cache Failure modes & defenses:
  - Cache Penetration (Bloom filters).
  - Cache Avalanche (Randomized TTL).
  - Cache Stampede / Thundering Herd (Mutex locking).

### Day 69: Load Balancing & Horizontal Autoscaling
- Layer 4 (Transport) vs Layer 7 (Application) load balancing.
- Balancing algorithms: Round Robin, Weighted Round Robin, Least Connections, IP Hash.
- Health checks, session persistence / sticky sessions.
- Horizontal Pod Autoscaler (HPA) concepts based on CPU/Memory and request rate.

### Day 70: Message Queues & Event-Driven Architecture
- Synchronous HTTP communication pitfalls in microservices.
- Message Broker architecture: Producers, Consumers, Exchanges, Queues.
- RabbitMQ vs Apache Kafka comparison.
- Delivery semantics: At-most-once, At-least-once, Exactly-once.
- Dead Letter Queues (DLQ) for failed message handling.

### Day 71: Apache Kafka Fundamentals
- Kafka architecture: Brokers, Clusters, Topics, Partitions.
- Producers, partition keys, ordering guarantees within a partition.
- Consumer Groups, consumer offsets, partition rebalancing.
- Retention policies and log compaction.
- Python Kafka clients (`confluent-kafka` / `aiokafka`).

### Day 72: Monolith to Microservices & API Gateways
- Monolithic architecture vs Modular Monolith vs Microservices.
- Domain-Driven Design (DDD) basics: Bounded Contexts.
- API Gateway pattern: Routing, Rate limiting, SSL termination, Auth offloading.
- Distributed Transactions: The 2-Phase Commit (2PC) vs The Saga Pattern (Orchestration vs Choreography).

### Day 73: Distributed Systems: Locks, Idempotency & Clocks
- Distributed locks with Redis (Redlock algorithm) and ZooKeeper.
- Idempotency keys in payment and critical mutation APIs.
- Clocks in distributed systems: Physical clocks, NTP drift, Logical clocks (Lamport timestamps).
- Split-brain scenarios in clusters and quorum consensus.

### Day 74: Distributed Resilience Patterns
- Circuit Breaker pattern (Closed, Open, Half-Open states).
- Exponential Backoff with Jitter for network retries.
- Bulkhead pattern to isolate failing services.
- Fallback strategies and graceful degradation under load.

### Day 75: Project 3: Event-Driven Microservices Backend + Review
- Architecture: User Service (Auth), Order Service (FastAPI), Payment Service (Worker), Kafka Broker.
- Docker Compose local environment, end-to-end event stream.
- Observability: Prometheus metrics exporter and Grafana dashboard.
- **Phase 5 Master Review & System Design Architecture Defense**.

---

## 📅 Phase 6: ML Engineering, LLMs, RAG, AI Agents & Career Launch (Days 76–90)

### Day 76: ML Engineering Foundation: NumPy & Pandas for High-Speed ETL
- ML System Engineering vs Data Science research mindset.
- NumPy vectorized operations: Array broadcasting, memory layouts (C-order vs Fortran-order).
- Pandas high-performance ETL: Vectorized operations, `apply` vs vectorized methods, chunked file reading.
- Handling missing data, memory optimization for DataFrames (downcasting numeric types, categoricals).
- Building an automated data ingestion pipeline.

### Day 77: Scikit-Learn Production Pipelines & Feature Engineering
- Feature transformation: Numerical scaling (`StandardScaler`, `RobustScaler`), Categorical encoding (`OneHotEncoder`).
- Scikit-Learn `Pipeline` and `ColumnTransformer` architecture.
- Preventing Data Leakage during preprocessing and cross-validation.
- Hyperparameter tuning pipelines (`GridSearchCV`, `RandomizedSearchCV`).
- Training and validating a classification/regression model for backend integration.

### Day 78: Model Packaging, Serialization & Serving Optimization
- Model persistence: `joblib` vs `pickle` vs `ONNX` (Open Neural Network Exchange).
- Security risks of unpickling untrusted model files.
- Exporting Scikit-Learn / PyTorch models to ONNX Runtime for high-speed inference.
- Model latency benchmarking and memory profiling.
- Packaging model artifacts with metadata and checksums.

### Day 79: Serving Models with FastAPI: Real-Time Inference
- Structuring an ML inference API with FastAPI and Pydantic.
- Model loading on startup via FastAPI `lifespan` context manager.
- Input validation and sanitization before prediction.
- Dynamic request batching for inference endpoints.
- Error handling, latency monitoring, and prediction confidence logging.

### Day 80: MLflow: Tracking, Model Registry & Version Control
- MLflow components: Tracking, Projects, Models, Model Registry.
- Logging parameters, metrics, artifacts, and confusion matrices.
- Model Registry lifecycle: Staging, Production, Archived stages.
- Automated model loading from MLflow Registry in FastAPI backend.
- Concept of Data Drift vs Concept Drift and retraining triggers.

### Day 81: LLM Fundamentals: Tokenization & Streaming APIs
- Large Language Models architecture overview: Transformers, Attention, Next-token prediction.
- Tokenization: BPE (Byte Pair Encoding), Token-to-word ratios, Token cost calculations.
- Context window mechanics, Prompt vs Completion tokens.
- Generation parameters: Temperature, Top-P, Top-K, Presence/Frequency penalties.
- Building Server-Sent Events (SSE) streaming API endpoints with FastAPI for LLM responses.

### Day 82: Vector Embeddings & Vector Databases
- What are vector embeddings? Semantic high-dimensional spaces.
- Similarity metrics: Cosine Similarity, Dot Product, Euclidean Distance.
- Vector database landscape: `pgvector` (PostgreSQL extension), ChromaDB, Qdrant, Pinecone.
- Indexing algorithms for Approximate Nearest Neighbor (ANN): HNSW (Hierarchical Navigable Small World), IVF.
- Hands-on: Setting up `pgvector` inside PostgreSQL and performing similarity queries.

### Day 83: RAG Architecture 101: Ingestion to Generation
- Retrieval-Augmented Generation (RAG) fundamental workflow: Ingest ➔ Chunk ➔ Embed ➔ Store ➔ Retrieve ➔ Prompt ➔ Generate.
- Document parsing: Extracting text from PDFs, Markdown, and HTML.
- Chunking strategies: Fixed-size, Recursive character splitting, Chunk overlap considerations.
- Building a baseline RAG pipeline using FastAPI, `pgvector`, and an LLM API.

### Day 84: Advanced RAG: Hybrid Search, Reranking & Filtering
- Limitations of naive semantic search (exact keyword misses, acronyms).
- Hybrid Search: Combining Dense Semantic Vectors + Sparse Keyword Search (BM25) with Reciprocal Rank Fusion (RRF).
- Cross-Encoder Reranking: Scoring top-K retrieved chunks with a reranker model (Cohere / BGE-Reranker).
- Metadata filtering (filtering by user ID, date, tag) before vector search.
- Contextual compression and parent-document retrieval techniques.

### Day 85: RAG Evaluation & Semantic Caching
- Evaluating RAG pipelines: The Ragas framework (Faithfulness, Answer Relevance, Context Precision, Context Recall).
- Creating synthetic evaluation test sets.
- Cost reduction with Semantic Caching: Using Redis to cache prompt embeddings and return previous answers for semantically identical questions.
- Guarding against hallucinations and ungrounded generation.

### Day 86: LLM Tool Calling & Structured Outputs
- Function Calling / Tool Calling mechanics: How LLMs output structured tool invocations.
- Defining tools with strict Pydantic schemas.
- Parsing and executing tool calls safely in a backend sandbox.
- Feeding tool execution results back to the LLM for final generation.
- Enforcing guaranteed JSON output schemas (`instructor` library / OpenAI structured outputs).

### Day 87: Autonomous AI Agents & LangGraph
- Agent architectures: ReAct (Reasoning + Acting) loop: Thought ➔ Action ➔ Observation.
- Agent memory: Short-term (conversation buffer) vs Long-term (vector database).
- Limitations of linear chains; need for state machines.
- LangGraph fundamentals: State, Nodes, Edges, Conditional Edges.
- Building a stateful multi-step research and code execution agent.

### Day 88: AI Observability & LLM Security
- Tracing LLM and Agent calls with OpenInference / LangSmith / Arize Phoenix.
- Tracking token usage, latency, and monetary cost per user.
- Security vulnerabilities in LLM apps (OWASP Top 10 for LLMs):
  - Prompt Injection (Direct & Indirect).
  - Insecure Output Handling.
  - Excessive Agency & Tool Abuse.
- Implementing guardrails (Llama-Guard / NeMo Guardrails).

### Day 89: Project 4: Enterprise Multimodal RAG & AI Agent Backend + Review
- Architecture:
  - FastAPI Gateway with JWT Auth & Rate Limiting.
  - Multi-tenant Document Upload & Chunking Ingestion Pipeline (Celery + Redis).
  - Hybrid Search Vector Retrieval (`pgvector` + HNSW).
  - LangGraph Autonomous Agent with custom SQL & Web search tools.
  - Full OpenTelemetry tracing and structured logging.
  - Docker Compose production deployment.

### Day 90: Master Interview Marathon, GitHub Polish & Career Launch
- Technical Interview Marathon:
  - 100 Core Python & Internals Questions.
  - 50 Django & ORM Optimization Questions.
  - 50 FastAPI & Async Architecture Questions.
  - 50 SQL, PostgreSQL & Indexing Questions.
  - 30 Docker, DevOps & CI/CD Questions.
  - 40 High-Scale System Design Scenarios.
  - 40 ML Engineering & Serving Questions.
  - 40 LLM, RAG & AI Agent Architecture Questions.
- GitHub profile optimization, README architecture showcases, pinning capstone repositories.
- **Graduation & 90-Day Challenge Completion!**
