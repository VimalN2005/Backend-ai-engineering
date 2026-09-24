# Day 04: 5-Minute Rapid Revision Notes

Review these high-yield bullet points before interviews or system design rounds:

---

- ⚡ **Comprehension vs. Loop Performance:**
  - List comprehensions run ~25–35% faster because CPython compiles them into the specialized `LIST_APPEND` bytecode opcode, bypassing `.append` method table lookups.
  - Never use list comprehensions purely for side effects (e.g. `[log(x) for x in data]`); use standard `for` loops.

- ⚡ **Short-Circuit Evaluation Rules:**
  - `A and B`: Returns `A` if `A` is falsy; otherwise returns `B`.
  - `A or B`: Returns `A` if `A` is truthy; otherwise returns `B`.
  - Does not coerce to boolean; returns the determining operand directly.

- ⚡ **Guard Clause Architecture:**
  - Invert conditions and return errors early at the top of functions.
  - Eliminates the "Arrow Anti-pattern" and keeps the happy path flat at root indentation.

- ⚡ **Memory Allocation (List Comp vs. Generator):**
  - List Comprehension `[...]`: Eagerly creates all objects in RAM ($\mathcal{O}(N)$ memory).
  - Generator Expression `(...)`: Lazy on-demand iterator with constant $\mathcal{O}(1)$ memory (~112 bytes). Use generators for LLM token streaming and RAG document ingestion.

- ⚡ **Loop Control & `for...else`:**
  - The `else` block on a `for` loop executes **only** if the loop exits naturally without encountering a `break`.
  - Perfect for connection retry loops and exhaustive search fallbacks.

- ⚡ **Operator Precedence Caution:**
  - `and` has higher precedence than `or`. Always use parentheses in security/authorization checks: `is_auth and (is_admin or is_editor)`.
