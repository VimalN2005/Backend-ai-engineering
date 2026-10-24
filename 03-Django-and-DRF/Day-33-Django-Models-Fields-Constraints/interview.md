# Day 33: Top 5 Senior Backend Interview Questions — Django Models, Constraints & Indexing

---

### Question 1: How does the Leftmost Prefix Rule dictate how composite B-Tree indexes are leveraged by PostgreSQL, and how do you determine optimal column ordering using the Equality-Sort-Range (ESR) guideline?

#### Architectural Answer:
A composite B-Tree index on multiple columns $(C_1, C_2, C_3)$ is structured as an ordered tree where rows are sorted lexicographically: first by $C_1$; then for equal values of $C_1$, sorted by $C_2$; and for equal values of $(C_1, C_2)$, sorted by $C_3$.

#### The Leftmost Prefix Rule:
Because of this physical ordering, a B-Tree search can only navigate down the tree if the query filter provides constraints for the **leading (leftmost) columns without gaps**:
- ✅ `WHERE C1 = ?` (Uses index)
- ✅ `WHERE C1 = ? AND C2 = ?` (Uses index for both columns)
- ✅ `WHERE C1 = ? AND C2 = ? AND C3 = ?` (Full index scan)
- ⚠️ `WHERE C1 = ? AND C3 = ?` (Uses index for $C_1$ only; $C_3$ acts as an index filter, not an index seek)
- ❌ `WHERE C2 = ?` or `WHERE C3 = ?` (Cannot use the index; triggers a sequential table scan)

#### The Equality-Sort-Range (ESR) Guideline:
To maximize index selectivity and eliminate memory sorting (`Using filesort` / Disk Sorts), order composite index columns using the **ESR rule**:
1. **Equality (`E`)**: Place columns filtered with exact matches (`=`) first (e.g. `tenant_id`, `status`). This narrows the candidate search space to a tight subtree.
2. **Sort (`S`)**: Place columns used in `ORDER BY` next (e.g. `created_at DESC`). Since rows within the equality matches are already physically sorted by this column, the database reads rows directly in required order, completely eliminating the sort phase.
3. **Range (`R`)**: Place columns filtered with inequality operators (`>`, `<`, `BETWEEN`, `IN`) last. Once a range comparison is evaluated, subsequent index columns cannot be used for efficient b-tree navigation.

---

### Question 2: Why is `null=True` on string-based fields (`CharField`, `TextField`) considered an enterprise antipattern, and how does the SQL specification for `NULL` equality impact `unique=True` constraints?

#### Architectural Answer:
Django officially designates `null=True` on string fields as an antipattern because it creates the **"Dual Empty State"** dilemma.

In Python and Django, empty form fields and DRF serializer inputs default to the empty string (`""`). If `null=True` is enabled:
- Some rows store `""` (empty string).
- Other rows store `NULL`.

#### Impact on Queries and Aggregations:
1. **Query Inconsistency**: A developer writing `User.objects.filter(bio__isnull=True)` misses all users whose bio is `""`. A developer writing `filter(bio="")` misses all users whose bio is `NULL`.
2. **Aggregations**: In ANSI SQL, `COUNT(column)` ignores `NULL` rows, but includes rows containing `""`.
3. **Three-Valued Logic**: In SQL, `NULL = NULL` evaluates to `UNKNOWN` (falsy), not `TRUE`.

#### The `unique=True` SQL Hazard:
Under ANSI SQL standards, `NULL != NULL`. Consequently:
- If a column has `unique=True, null=True`, PostgreSQL allows **infinite rows** with `column = NULL`.
- However, PostgreSQL strictly allows **only one row** with `column = ""`.
- If two users submit empty string values, the second write crashes with a database `IntegrityError`.

#### Enterprise Remediation:
Use `blank=True` with `default=""`. String fields should always store `""` when empty. If an optional field requires uniqueness, use modern partial constraints:
```python
models.UniqueConstraint(
    fields=["phone_number"],
    condition=models.Q(phone_number__gt=""),
    name="unique_phone_when_present"
)
```

---

### Question 3: Compare auto-incrementing integers (`BigAutoField`), `UUIDv4`, and `UUIDv7` as primary keys in high-throughput Django backends. Detail the B-tree page split and cache locality implications.

#### Architectural Answer:

```
+-------------------------------------------------------------------------------------------+
|                              PRIMARY KEY ARCHITECTURE AUDIT                               |
+-------------------------------------------------------------------------------------------+
| Attribute             | BigAutoField (64-bit Int) | UUIDv4 (Random) | UUIDv7 (Time-Ordered)|
+-----------------------+---------------------------+-----------------+---------------------+
| Size                  | 8 bytes                   | 16 bytes        | 16 bytes            |
| Key Monotonicity      | Strictly Monotonic        | Completely Random| Monotonically Increasing|
| B-Tree Leaf Insertion | Always Rightmost Leaf     | Random Leaf Page| Rightmost Leaf Page |
| Index Fragmentation   | 0% (Clean packing)        | Severe (30–50%) | Minimal (<5%)       |
| Buffer Cache Locality | Exceptional               | Atrocious       | High                |
| Security (Enumeration)| Vulnerable (IDOR risk)    | Immune          | Immune              |
+-----------------------+---------------------------+-----------------+---------------------+
```

#### The B-Tree Page Split Disaster with UUIDv4:
A standard PostgreSQL B-Tree leaf page is 8 KB. When using auto-incrementing integers or `UUIDv7`, new keys are monotonically increasing. Every new row is inserted into the **rightmost leaf page**. When the page fills, PostgreSQL allocates a new page at the end without touching existing pages.

With `UUIDv4`, keys are uniform random hashes. A new insert lands at a random leaf page anywhere across the multi-gigabyte index:
1. If that 8 KB page is full, PostgreSQL must perform an **expensive 50/50 page split**: allocating a new page, moving half the keys over, and updating parent pointer nodes.
2. Random inserts constantly evict active pages from the database buffer pool (`shared_buffers`), destroying cache hit ratios and driving heavy disk I/O.
3. UUIDv4 B-Trees end up with 30–50% dead space (internal fragmentation).

#### The Enterprise Consensus:
- Use **`UUIDv7`** (or `BigAutoField` with an external public UUID/slug) for distributed high-write enterprise tables. It provides unguessable security and offline client generation without the B-tree page split penalty.

---

### Question 4: How does Django's validation lifecycle operate (`clean_fields`, `clean`, `validate_unique`, `full_clean`), why does `Model.save()` bypass it, and how do you guarantee database-level integrity using `CheckConstraint` and `UniqueConstraint`?

#### Architectural Answer:
Django separates application-level validation from database persistence.

#### The `full_clean()` Pipeline:
When an object is validated (e.g. by a ModelForm), Django calls `model_instance.full_clean()`, which executes in 4 stages:
1. `clean_fields()`: Validates each field descriptor against its type, `max_length`, `null`, `blank`, and registered validators.
2. `clean()`: Developer hook for cross-field business logic (e.g. validating `end_date >= start_date`).
3. `validate_unique()`: Queries the database to check if any field with `unique=True` or `unique_together` already exists.
4. `validate_constraints()`: Evaluates declarative `models.Constraint` instances.

#### Why `Model.save()` Bypasses `full_clean()`:
By design, Django's `Model.save()` **does not call `full_clean()`**. Calling `full_clean()` on every `.save()` would:
- Issue redundant SQL `SELECT` queries for `validate_unique()` on every single row update.
- Severely degrade bulk throughput (`bulk_create`, `bulk_update`).

#### The Vulnerability & The Defense-in-Depth Solution:
Because direct programmatic calls (`Model.objects.create()`, API views, Celery workers) bypass `full_clean()`, relying solely on Python `clean()` methods leaves your database vulnerable to dirty data and race conditions (e.g. two concurrent requests pass `validate_unique` simultaneously).

**Senior Remediation**: Enforce rules at the database engine level via declarative constraints:
```python
class Meta:
    constraints = [
        # Database rejects violations at ACID commit time regardless of how the row was created
        models.CheckConstraint(
            check=models.Q(end_date__gte=models.F("start_date")),
            name="check_dates_chronological"
        ),
        models.UniqueConstraint(
            fields=["tenant", "slug"],
            name="unique_tenant_slug"
        ),
    ]
```

---

### Question 5: When should an enterprise backend choose a GIN or BRIN index over a standard B-Tree index in PostgreSQL, and what are the write-amplification tradeoffs?

#### Architectural Answer:

#### 1. GIN (Generalized Inverted Index):
- **Mechanism**: An inverted index where key elements (e.g. words in text, keys/values in JSONB, elements in an array) point to a posting list of row IDs that contain them.
- **When to Use**:
  - Semi-structured `JSONField` (using `@>`, `?`, `?&` operators).
  - PostgreSQL array fields (`ArrayField`).
  - Full-text search vectors (`tsvector`).
- **Trade-off (Write Amplification)**:
  - Inserting a single row with 10 tags updates the GIN index 10 times (one entry per tag).
  - Writes are significantly slower than B-Tree. PostgreSQL mitigates this with `fastupdate = on` (a pending write buffer), but GIN is strictly optimized for read-heavy workloads.

#### 2. BRIN (Block Range Index):
- **Mechanism**: Instead of indexing every single row, BRIN divides the physical table into block ranges (default: 128 disk pages / 1 MB) and records only the **minimum and maximum value** for each range.
- **When to Use**:
  - Extremely large tables (100 million+ rows, 50 GB+) where data is physically stored in natural sequential order (e.g. append-only audit logs, IoT telemetry, clickstreams with `created_at` or auto-incrementing IDs).
- **Trade-off (Storage & Speed)**:
  - Consumes $<1\%$ of the RAM and disk space of a B-Tree (e.g. 10 MB vs 10 GB for a B-tree).
  - Slightly slower than B-tree for single-row lookups (must scan the 128-page block range), but dramatically faster for range scans and drastically reduces database memory footprint.
