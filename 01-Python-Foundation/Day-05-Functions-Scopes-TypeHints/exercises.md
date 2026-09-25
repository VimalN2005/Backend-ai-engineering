# Day 05: Practical Hands-On Exercises

Solve these three production-level challenges to master function boundaries, scopes, closures, and type introspection.

---

## Exercise 1: Strict LLM API Invoker Signature (Easy-Medium)

### Problem Statement:
Design a function `invoke_llm_model` that enforces the following strict signature contract:
1. `model_name` must be **Positional-Only** (must be passed positionally, never as a keyword).
2. `prompt` can be passed either positionally or by keyword.
3. `temperature`, `max_tokens`, and `stream` must be **Keyword-Only** (must be explicitly named at call site).
4. `temperature` must default to `0.7`, `max_tokens` to `1000`, and `stream` to `False`.
5. Include comprehensive type annotations adhering to Python 3.10+ standards.

### Starter Template:
```python
from typing import Dict, Any, Literal

# TODO: Define invoke_llm_model with '/', '*', and precise type hints
def invoke_llm_model(
    # Implement parameter boundaries here
):
    """
    Executes mock LLM invocation with strict parameter contracts.
    """
    pass

# Test Cases:
# Valid call:
result = invoke_llm_model("gpt-4o", "Hello AI", temperature=0.5, stream=True)
print("Valid invocation succeeded:", result)

# Invalid call 1 (Calling model_name as keyword should raise TypeError):
# invoke_llm_model(model_name="gpt-4o", prompt="Hello")

# Invalid call 2 (Passing temperature positionally should raise TypeError):
# invoke_llm_model("gpt-4o", "Hello", 0.5)
```

---

## Exercise 2: Late-Binding Closure Bug Resolver (Medium)

### Problem Statement:
In an AI agent workflow, multiple metric calculation functions are dynamically constructed in a loop:
```python
def make_multipliers():
    return [lambda x: x * i for i in range(1, 4)]
```
Calling these functions with `x=10` outputs `[30, 30, 30]` instead of the intended `[10, 20, 30]` due to Python's late-binding closure behavior.
Write a function `build_calibrated_evaluators(factors: list[float]) -> list[Callable[[float], float]]` that resolves this late-binding trap using default argument binding or helper factory functions.

### Starter Code:
```python
from typing import Callable, List

def build_calibrated_evaluators(factors: List[float]) -> List[Callable[[float], float]]:
    """
    Returns a list of evaluator functions where each evaluator scales input
    by its corresponding factor, avoiding late-binding closure bugs.
    """
    # TODO: Implement closure list without late-binding bugs
    pass

# Verification:
evaluators = build_calibrated_evaluators([1.5, 2.0, 3.5])
test_input = 10.0
results = [ev(test_input) for ev in evaluators]
print(results)  # Expected output: [15.0, 20.0, 35.0]
```

---

## Exercise 3: Runtime Type Validator Decorator (Advanced)

### Problem Statement:
While Python type hints are ignored by the CPython interpreter at runtime, backend security middlewares often need to enforce runtime type compliance on internal RPC or tool endpoints.
Write a decorator `@enforce_types` that:
1. Inspects the wrapped function's signature and type hints using `typing.get_type_hints()`.
2. Intercepts incoming positional and keyword arguments.
3. Compares each argument's runtime type against its declared type annotation.
4. Raises a `TypeError` with an informative message if an argument does not match (e.g., `Expected 'user_id' to be <class 'int'>, got <class 'str'>`).

### Starter Code:
```python
import inspect
from functools import wraps
from typing import get_type_hints, Callable, Any

def enforce_types(func: Callable) -> Callable:
    """
    Runtime decorator that asserts arguments match their declared type hints.
    """
    hints = get_type_hints(func)
    sig = inspect.signature(func)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # TODO: Bind arguments, iterate, and check isinstance(val, expected_type)
        pass

    return wrapper

# Verification Test:
@enforce_types
def register_agent(agent_name: str, max_iterations: int) -> str:
    return f"Agent {agent_name} registered with {max_iterations} max iterations."

print(register_agent("ResearchBot", 15))  # Sells successfully

try:
    register_agent("ResearchBot", "fifteen")  # Must raise TypeError!
except TypeError as err:
    print("Caught expected type error:", err)
```
