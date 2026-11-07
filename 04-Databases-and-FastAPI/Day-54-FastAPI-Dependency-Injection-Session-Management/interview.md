# Day 54: Senior & Staff Technical Interview Mastery

This guide presents the top 5 advanced architectural interview questions focusing on **FastAPI Dependency Injection, Directed Acyclic Graph (DAG) Resolution, Yield Teardown Semantics, and Session Lifecycle Management**.

---

### Question 1: How does FastAPI resolve the Dependency Injection DAG under the hood, and how are execution order and topological dependencies calculated?

#### Architectural Answer
FastAPI's dependency injection container builds a static **Directed Acyclic Graph (DAG)** of dependencies at application startup and traverses it dynamically per request.

```
                    [ Route Signature / Parameters ]
                                  |
                                  v
+-------------------------------------------------------------------+
| FastAPI Dependency Resolver (solve_dependencies)                  |
| 1. Extracts parameter definitions and nested Depends() markers.   |
| 2. Builds a flat list of sub-dependencies.                        |
| 3. Resolves independent leaf nodes (Query/Header/Path params).    |
| 4. Iteratively resolves dependent nodes in topological order.      |
| 5. If `use_cache=True`, checks request-scoped cache dictionary:   |
|    `async_exit_stack` manages context managers and generators.    |
+-------------------------------------------------------------------+
                                  |
                                  v
                    [ Resolved Endpoint Arguments ]
```

#### Low-Level Execution Mechanics
1. **Introspection Phase (Startup)**: When an endpoint is registered, FastAPI inspects the signature using Python's `inspect` module and Pydantic field analyzers. It constructs a `Dependant` tree representing all nested callables.
2. **Runtime Resolution (`solve_dependencies`)**:
   - For every incoming request, FastAPI initializes an `AsyncExitStack` and a `values` cache dictionary.
   - It performs a depth-first traversal of the `Dependant` tree.
   - If a dependency is a generator (`yield`), it enters the generator via `await stack.enter_async_context()` or `contextlib.asynccontextmanager`.
   - Results are injected into child dependencies until the final route arguments are satisfied.
3. **Short-Circuiting**: If any node raises an exception (e.g., `HTTPException(401)`), graph traversal stops immediately. Child nodes are never evaluated, and the `AsyncExitStack` unwinds previously opened context managers in reverse order.

---

### Question 2: Explain the exact lifecycle and execution sequencing of a `yield` dependency when an uncaught exception is raised inside a route handler.

#### Architectural Answer
A common misconception is that if an endpoint crashes with an unhandled 500 error, cleanup code after `yield` will not run.

FastAPI delegates generator execution to Python's **`AsyncExitStack`**, guaranteeing that teardown code in `finally` blocks runs on **100% of requests**:

```
Client Request
      |
      v
[ setup code in get_db() ] (Opens DB connection)
      |
      +---> yield session (Injected into route)
               |
               v
     [ Route Handler Executes ]
               |
               +---> CRASH: raise RuntimeError("Database deadlock")
                        |
                        v
     [ AsyncExitStack Unwinds ]
                        |
                        v
[ get_db() resumes at yield ] (Receives the exception via generator.throw())
      |
      +---> except Exception:
      |         await session.rollback() (Rolls back dirty writes!)
      |
      +---> finally:
                await session.close()    (Guaranteed socket release!)
                        |
                        v
[ Exception propagates to Starlette ServerErrorMiddleware ]
                        |
                        v
Client receives HTTP 500 Internal Server Error
```

#### Staff-Level Insight
The generator receives the exception via `generator.throw(exc)`. Therefore, developers must:
1. Always wrap the `yield` statement in `try...finally`.
2. If catching `Exception` to perform a rollback, **always re-raise** (`raise`). Failing to re-raise swallows the exception, causing Starlette to return an erroneous `200 OK` with an empty response.

---

### Question 3: How does `use_cache=True` function in FastAPI, and what subtle bugs can occur if it is misused on stateful or non-idempotent providers?

#### Architectural Answer
FastAPI's dependency resolver maintains an in-memory dictionary during the lifecycle of a single request:
$$\text{Request Cache} = \{(\text{dependency\_callable}, \text{sub\_dependency\_tuple}): \text{resolved\_instance}\}$$

By default, `use_cache=True`. When multiple endpoints or sub-dependencies call `Depends(get_current_user)`, FastAPI executes `get_current_user()` once and reuses the exact same object reference for the rest of the request.

#### Failure Modes of Misusing `use_cache=True`
1. **Non-Idempotent Value Generation**:
   If a dependency generates a cryptographic nonce, a unique idempotency key, or a transaction UUID:
   ```python
   def get_nonce() -> str:
       return str(uuid.uuid4())
   ```
   If two sub-dependencies request `Depends(get_nonce)`, with `use_cache=True`, both receive the **exact same UUID**, destroying uniqueness invariants!
2. **Stateful Resources Requiring Isolation**:
   If an application requires two distinct database connections (e.g., an isolated connection for an autonomous audit log that must commit even if the primary business transaction rolls back), `use_cache=True` will inject the same connection into both sites.
3. **Remediation**: Explicitly set `use_cache=False` on any provider that must evaluate uniquely per injection point.

---

### Question 4: Why is `app.dependency_overrides` the premier pattern for integration testing in FastAPI, and how does it compare to `unittest.mock.patch`?

#### Architectural Comparison Table

| Attribute | `app.dependency_overrides` | `unittest.mock.patch` |
| :--- | :--- | :--- |
| **Mechanism** | Framework-level dictionary swap in FastAPI's DI container | Python runtime bytecode and namespace manipulation |
| **Scope of Impact** | Strictly isolated to the specific `FastAPI` instance | Global process-wide mutation of module imports |
| **Refactoring Safety** | High (Targeted by function reference, resilient to internal renames) | Low (Fragile string paths e.g. `"app.services.order.db"`) |
| **Concurrency Safety** | Safe across isolated test client instances | Thread-unsafe; can leak across parallel test runners |
| **Cleanup Protocol** | `app.dependency_overrides.clear()` in fixture teardown | Context manager or `@patch` decorator |

#### Staff-Level Architectural Recommendation
Use `app.dependency_overrides` for all architectural boundaries:
- Swapping PostgreSQL with in-memory SQLite.
- Bypassing real OAuth2 / JWT identity providers with pre-authenticated synthetic test users.
- Mocking external payment gateways (Stripe, Adyen).

Reserve `unittest.mock.patch` exclusively for external third-party SDKs that are not wired through the dependency injection graph.

---

### Question 5: How do you architect database session management in a high-concurrency FastAPI service to prevent connection pool exhaustion and deadlocks?

#### Architectural Answer
Database connection exhaustion in async microservices is rarely caused by database hardware limits; it is caused by **mismanaged session lifecycles in the application layer**.

```
+---------------------------------------------------------------------------------+
| High-Concurrency Session Architecture                                          |
|                                                                                 |
| 1. Strict Per-Request Scope:                                                    |
|    AsyncSession is instantiated on-demand and closed via yield dependencies.   |
|                                                                                 |
| 2. Zero Network Calls Inside Active Transactions:                              |
|    NEVER call external APIs (Stripe, OpenAI, S3) while holding a DB lock!       |
|    Fetch external data FIRST, then enter the DB session transaction.            |
|                                                                                 |
| 3. Explicit Pool Sizing & Timeouts:                                            |
|    pool_size=20, max_overflow=10, pool_timeout=10, pool_pre_ping=True.          |
|                                                                                 |
| 4. Offloading Heavy Queries:                                                    |
|    Long-running analytical queries route to read replicas via separate engines. |
+---------------------------------------------------------------------------------+
```

#### Core Production Guidelines
1. **Never Hold Transactions Across External HTTP Calls**:
   ```python
   # DEADLOCK TRAP: Holding a DB transaction open while waiting for OpenAI API!
   async with session.begin():
       user = await session.get(User, user_id)
       # If OpenAI takes 4 seconds, this DB connection is held idle, starving the pool!
       llm_res = await openai_client.generate(...)
       user.summary = llm_res
   ```
   *Remediation*: Call external APIs outside transaction blocks.
2. **Aggressive `pool_timeout`**: In async backends, setting `pool_timeout=30s` is dangerous. If the pool is exhausted, tasks queue up and consume RAM. Set `pool_timeout=5s` or `10s` to fail fast and trigger backpressure.
3. **Heartbeat Verification**: Enable `pool_pre_ping=True` to prune disconnected or terminated sockets before handing them to request coroutines.

---
*Senior interviewers evaluate candidates on their ability to explain DAG resolution, teardown guarantees, and connection pooling under extreme traffic.*
