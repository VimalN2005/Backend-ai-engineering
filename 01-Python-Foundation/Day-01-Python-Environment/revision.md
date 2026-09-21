# Day 01: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or coding sessions:

---

- ⚡ **Execution Pipeline:**
  `Source (.py)` ➔ `Lexing & Parsing` ➔ `Abstract Syntax Tree (AST)` ➔ `Bytecode (.pyc)` ➔ `PVM Loop (ceval.c)` ➔ `Machine Execution`.

- ⚡ **Purpose of Bytecode:**
  Cross-platform portability and faster application startup. `.pyc` files do not speed up execution loops; they bypass source re-parsing.

- ⚡ **Memory Architecture:**
  - Python variables are **pointers/references**, not storage boxes.
  - `PyObject_HEAD` = `ob_refcnt` (8 bytes) + `ob_type` (8 bytes) = 16-byte minimum header on 64-bit platforms.
  - Deterministic deallocation: As soon as `ref_count == 0`, memory is immediately freed.

- ⚡ **Identity vs. Equality:**
  - `a == b` ➔ Value equivalence (invokes `a.__eq__(b)`).
  - `a is b` ➔ Memory address identity (`id(a) == id(b)`).
  - Always use `is` for singletons (`None`, `True`, `False`).

- ⚡ **Interning Optimization:**
  - Small Integers: Range `[-5, 256]` are cached permanently as singletons during interpreter startup.
  - String Interning: Immutable alphanumeric string literals are interned by CPython for fast pointer comparisons in dictionary lookups.

- ⚡ **Virtual Environment Rule:**
  - `sys.prefix != sys.base_prefix` indicates an active virtual environment.
  - `pyvenv.cfg` records the base Python home directory.
  - Setting `PYTHONDONTWRITEBYTECODE=1` is recommended for stateless production Docker containers.

- ⚡ **Git Hygiene:**
  - Never commit `.env`, `venv/`, `__pycache__/`, or `*.pyc` files.
