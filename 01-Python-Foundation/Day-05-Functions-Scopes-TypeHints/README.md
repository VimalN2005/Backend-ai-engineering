# Day 05: Functions, Scopes (LEGB) & Production Type Hinting

---

## 1. Definition
In Python, **functions are first-class citizens** (`PyFunctionObject`), meaning they can be passed as arguments, assigned to variables, stored in data structures, and returned from other functions.
- **Function Execution Context**: Invoking a function pushes a new **Call Stack Frame** (`PyFrameObject`) containing local variables, bytecode instruction pointers, and value stacks.
- **Scope Resolution (LEGB)**: The deterministic order in which CPython searches namespaces for variable references: **L**ocal ➔ **E**nclosing ➔ **G**lobal ➔ **B**uilt-in.
- **Modern Type Hints (PEP 484 / PEP 604)**: Static annotations (`int`, `str | None`, `Annotated`, `Callable`) that provide IDE autocompletion, static analysis via `mypy`, and runtime validation in frameworks like **FastAPI** and **Pydantic V2**.

---

## 2. Why? (Problem it Solves in AI & Backend Engineering)
1. **Contract Enforcement in API Frameworks**: Modern frameworks like **FastAPI** read type hints at startup via reflection (`typing.get_type_hints()`) to automatically generate OpenAPI documentation, validate incoming JSON payloads, and cast query strings without boilerplate code.
2. **Preventing Side-Effect Contamination**: Understanding LEGB scopes and parameter mutability prevents fatal production bugs such as mutable default arguments leaking state across concurrent web requests.
3. **Strict Function Signatures**: Positional-only (`/`) and keyword-only (`*`) parameters allow API designers (e.g. LiteLLM, LangChain SDKs) to change internal parameter names without breaking external client integrations.

---

## 3. How? (Under the Hood / Working Principle)

### 3.1 The LEGB Scope Resolution Order
When a variable is referenced inside a function, CPython resolves the identifier in four consecutive namespaces:

```text
[ 1. Local (L) ]     ── Current function's execution frame (fast array lookup in C)
       │ (if not found)
       ▼
[ 2. Enclosing (E) ] ── Any enclosing/outer functions (closures; cell objects)
       │ (if not found)
       ▼
[ 3. Global (G) ]    ── Current module namespace (module __dict__)
       │ (if not found)
       ▼
[ 4. Built-in (B) ]  ── Built-in functions and exceptions (builtins module: len, int, range)
       │ (if not found)
       ▼
  NameError: name 'xyz' is not defined
```

- **`LOAD_FAST` vs. `LOAD_GLOBAL`**: Variables in the **Local** scope are indexed directly in a fixed-size C array (`fastlocals`) at compile time. Local lookups take ~15 nanoseconds, whereas Global and Built-in lookups require dictionary queries, which take ~30–45 nanoseconds.
- **`global` keyword**: Binds a variable explicitly to the module-level namespace.
- **`nonlocal` keyword**: Binds a variable to the nearest enclosing (outer) function's scope, essential for stateful closures.

### 3.2 Positional-Only (`/`) and Keyword-Only (`*`) Parameters (PEP 570)
```text
def configure_llm_pipeline(model_name, /, temperature=0.7, *, api_key, max_tokens=1024):
                           ──────────  ─  ───────────────  ─  ──────────────────────────
                           Positional     Positional or       Keyword-Only
                           Only           Keyword             Only
```
- **Left of `/`**: Must be passed by position (e.g., `configure_llm_pipeline("gpt-4o")`). Cannot be called as `model_name="gpt-4o"`.
- **Between `/` and `*`**: Can be passed positionally or by keyword.
- **Right of `*`**: Must be passed explicitly by keyword (e.g., `api_key="sk-..."`). Prevents bugs where callers mix up positional arguments in functions with many parameters.

### 3.3 How Type Hints Work
Type hints are purely **metadata** stored in the function's `__annotations__` dictionary.
- CPython **does not enforce** type hints at runtime. Calling `add("a", "b")` on `def add(x: int, y: int) -> int:` executes without error.
- Static analyzers (`mypy`, `pyright`) verify types before deployment.
- Runtime frameworks (**FastAPI**, **Pydantic**) inspect `__annotations__` using `typing.get_type_hints()` to perform data validation, type casting, and schema generation.

---

## 4. Syntax & Basic Contract
```python
from typing import Callable, Optional, Union, Literal, Annotated

# Modern Python 3.10+ Union syntax: 'X | None' instead of 'Optional[X]'
def generate_embedding(
    text: str,
    /,  # Positional-only parameter
    model: Literal["text-embedding-3-small", "text-embedding-3-large"] = "text-embedding-3-small",
    *,  # Keyword-only boundary
    dimensions: int | None = None,
    callback: Callable[[float], None] | None = None
) -> list[float]:
    """Generates embedding vectors with strict parameter boundaries."""
    return [0.1, 0.2, 0.3]
```

---

## 5. Example 1: Conceptual Walkthrough
Demonstrating LEGB scope resolution and `LOAD_FAST` bytecode optimization:

```python
import dis

global_counter = 100

def test_scope(param_a: int) -> int:
    local_val = 5
    return param_a + local_val + global_counter

# Notice the bytecode instructions:
# LOAD_FAST 0 (param_a)   <- Instant C array lookup
# LOAD_FAST 1 (local_val) <- Instant C array lookup
# LOAD_GLOBAL 0 (global_counter) <- Dictionary lookup in globals()
dis.dis(test_scope)
```

---

## 6. Example 2: Edge Cases & Gotchas

### Gotcha A: Mutable Default Arguments
```python
# ANTIPATTERN: Default list is instantiated ONCE when function is defined in RAM:
def add_to_token_cache(token: str, cache: list = []) -> list:
    cache.append(token)
    return cache

print(add_to_token_cache("chunk_1")) # ['chunk_1']
print(add_to_token_cache("chunk_2")) # ['chunk_1', 'chunk_2'] -> Memory leaked across calls!

# PRODUCTION FIX: Use None as default sentinel:
def add_to_token_cache(token: str, cache: list | None = None) -> list:
    if cache is None:
        cache = []
    cache.append(token)
    return cache
```

### Gotcha B: Late Binding in Closures
```python
# ANTIPATTERN: Lambdas look up 'i' in enclosing scope AT CALL TIME, not definition time:
handlers = [lambda x: x + i for i in range(3)]
print([h(10) for h in handlers]) # [12, 12, 12] (i was 2 when loop completed!)

# PRODUCTION FIX: Bind current loop value as default argument:
handlers = [lambda x, i=i: x + i for i in range(3)]
print([h(10) for h in handlers]) # [10, 11, 12]
```

---

## 7. Production-Grade Example
An Extensible LLM Tool Registry & Schema Generator used in AI agent architectures (LangGraph, OpenAI Tool Calling) that inspects function signatures and type hints to dynamically generate JSON function schemas.
*(See complete runnable code in [example_02.py](example_02.py))*

```python
import inspect
from typing import get_type_hints, Callable, Dict, Any

class LLMToolRegistry:
    """Dispatches tool calls and auto-generates JSON function schemas from type hints."""

    def __init__(self):
        self._registry: Dict[str, Callable] = {}

    def register_tool(self, func: Callable) -> Callable:
        name = func.__name__
        self._registry[name] = func
        return func

    def generate_schema(self, func_name: str) -> Dict[str, Any]:
        func = self._registry[func_name]
        hints = get_type_hints(func)
        sig = inspect.signature(func)
        
        properties = {
            param: {"type": "integer" if hints[param] is int else "string"}
            for param in sig.parameters if param != "return"
        }
        return {"name": func_name, "parameters": {"properties": properties}}
```

---

## 8. Common Mistakes & Antipatterns
- ❌ **Mistake**: Using `global` variables inside concurrent web request handlers.
  - ✅ **Correction**: Pass database sessions and request state via function arguments or dependency injection.
- ❌ **Mistake**: Overusing `Any` as a type hint across backend services.
  - ✅ **Correction**: Use specific types, `TypedDict`, `Protocol`, or Pydantic models.
- ❌ **Mistake**: Forgetting `*` in functions with boolean flags: `deploy(True, False, True)`.
  - ✅ **Correction**: Use keyword-only arguments: `deploy(*, dry_run=True, force=False)`.
- ❌ **Mistake**: Thinking Python type hints enforce runtime type casting automatically without Pydantic.
  - ✅ **Correction**: Use Pydantic V2 or manual runtime checks for untrusted user inputs.

---

## 9. Performance & Complexity Analysis
- **Local Variable Access (`LOAD_FAST`)**: $\approx 15 \text{ ns}$ (indexed C array in `PyFrameObject`).
- **Global / Built-in Access (`LOAD_GLOBAL`)**: $\approx 35 \text{–} 50 \text{ ns}$ (hash table lookup in `f->f_globals` and `f->f_builtins`).
- **Function Call Overhead**: Python function calls push a C stack frame (~100–150 bytes). For micro-benchmarks with millions of calls, inlining or local caching (`len_func = len`) speeds up loops.
- **Type Hints Overhead**: Zero runtime execution cost; evaluated only once at module import time.

---

## 10. Security Implications
1. **Unchecked `**kwargs` Injection**: Unpacking untrusted JSON dictionaries directly into internal functions (`update_user_record(**payload)`) allows clients to overwrite unintended fields like `is_admin=True`. Always validate payloads using Pydantic or explicit keyword arguments.
2. **Dynamic Function Invocation (`getattr` / `eval`)**: Never map user-supplied strings directly to function names without an explicit whitelist registry (e.g. `LLMToolRegistry`).

---

## 11. When to Use?
- **Positional-Only (`/`)**: When parameter names have no semantic value (e.g., `abs(x)` or `len(obj)`), or to preserve backwards compatibility when renaming arguments.
- **Keyword-Only (`*`)**: When functions take boolean flags, optional parameters, or when ambiguity could cause data corruption (e.g., `delete_account(*, user_id, confirm=True)`).
- **Type Annotations**: Everywhere in production code—especially API endpoints, database repositories, and service layer methods.

---

## 12. When NOT to Use?
- **Do NOT use `global` to share state**: Never use module-level globals in multi-threaded or asynchronous backends (FastAPI/Celery) as it causes race conditions.
- **Do NOT nest closures 4+ levels deep**: Leads to unreadable code and tricky memory retention issues.

---

## 13. Top Interview Questions
1. *How does Python's LEGB scope resolution work under the hood, and what makes Local variable access faster than Global access?*
2. *What are Positional-Only (`/`) and Keyword-Only (`*`) parameters, and why were they added in PEP 570?*
3. *Why do mutable default arguments retain state across subsequent function invocations in CPython?*
4. *What is the difference between the `global` and `nonlocal` keywords?*
5. *Do Python type hints provide runtime type validation? How does FastAPI leverage them?*

---

## 14. Practice Problems
1. Refactor a messy function signature containing 6 arguments into a strictly bounded signature using positional-only and keyword-only dividers.
2. Fix a late-binding closure bug in a dynamically generated list of tool validators.
3. Build a runtime `@type_checked` decorator that validates arguments against `get_type_hints()` at runtime.

---

## 15. 5-Minute Revision Notes
- Scopes resolve in **LEGB order**: Local ➔ Enclosing ➔ Global ➔ Built-in.
- Local variables use fast C arrays (`LOAD_FAST`); globals use dictionary lookups (`LOAD_GLOBAL`).
- `/` defines **Positional-Only** parameters (left of `/`).
- `*` defines **Keyword-Only** parameters (right of `*`).
- **Never use mutable defaults** (`def f(x=[])`); use `None` and initialize inside the function.
- `global` binds to the module scope; `nonlocal` binds to the nearest enclosing closure scope.
- Python type hints are metadata (`__annotations__`); they do not enforce runtime types unless inspected by frameworks like FastAPI or Pydantic.
