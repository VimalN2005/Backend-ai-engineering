# Day 05: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or architecture design rounds:

---

- ⚡ **LEGB Scope Order:**
  - **Local** (`LOAD_FAST`, ~15ns, fixed C array) ➔ **Enclosing** (closures/cell objects) ➔ **Global** (`LOAD_GLOBAL`, ~45ns, dict lookup) ➔ **Built-in**.

- ⚡ **Positional-Only (`/`) vs. Keyword-Only (`*`):**
  - Left of `/` = **Positional-Only** (cannot be called with `name=val`).
  - Right of `*` = **Keyword-Only** (must be called with `name=val`).
  - Between `/` and `*` = Can be positional or keyword.

- ⚡ **Mutable Default Argument Danger:**
  - Defaults are evaluated **once at definition/import time** and stored in `__defaults__`.
  - Mutating a default list/dict leaks state across all calls and concurrent requests. Always use `None` as default.

- ⚡ **Late-Binding Closure Trap:**
  - Closures capture variables by reference, resolving their values **at call time**.
  - In loops, bind loop counters immediately using default arguments: `lambda x, i=i: x + i`.

- ⚡ **`global` vs. `nonlocal`:**
  - `global`: Rebinds to module-level namespace.
  - `nonlocal`: Rebinds to the nearest enclosing function's frame (closure).

- ⚡ **Type Hints Truth:**
  - CPython ignores type hints at runtime (zero execution performance penalty).
  - Frameworks like **FastAPI** and **Pydantic** inspect `__annotations__` at startup to perform automatic validation and generate OpenAPI schemas.
