"""
Day 05: Functions, Scopes (LEGB) & Production Type Hinting
File: example_01.py - Bytecode Lookups, Scope Binding & Signature Contracts

COMMENT PHILOSOPHY:
Comments explain *WHY* namespaces and function execution frames operate the way
they do inside CPython, not simply *WHAT* the code is executing.
"""

import sys
import dis
from typing import get_type_hints, Callable, Dict, Any


# Module-level Global Namespace
GLOBAL_RATE_LIMIT = 100


def demonstrate_legb_bytecode_optimization() -> None:
    """
    Demonstrates LEGB scope resolution and C-level opcode differences.
    
    WHY THIS MATTERS:
    CPython optimizes local variable access at compile time.
    Local variables are stored in a fixed-size C array on the call stack frame (fastlocals),
    yielding O(1) array indexing via LOAD_FAST (~15 nanoseconds).
    
    Global and Built-in variables require hash table lookups in the module's __dict__
    via LOAD_GLOBAL (~45 nanoseconds). In high-throughput AI inference loops processing
    millions of vectors, caching globals into local variables yields measurable speedups.
    """
    print("=" * 65)
    print("1. LEGB SCOPE BYTECODE DISASSEMBLY")
    print("=" * 65)

    def calculate_score(prompt_len: int) -> float:
        # local_weight is allocated in fastlocals array
        local_weight = 1.5
        # GLOBAL_RATE_LIMIT requires a dictionary lookup in globals()
        # len() requires a fallback dictionary lookup in builtins
        return (prompt_len * local_weight) / GLOBAL_RATE_LIMIT

    dis.dis(calculate_score)


def demonstrate_parameter_boundaries() -> None:
    """
    Demonstrates PEP 570 Positional-Only (/) and Keyword-Only (*) parameters.
    
    WHY THIS MATTERS:
    In production API libraries (e.g. LiteLLM, OpenAI SDK), parameter boundaries
    enforce clean caller contracts:
    1. Positional-only (/) prevents callers from relying on internal argument names,
       allowing library maintainers to refactor parameter names without breaking downstream code.
    2. Keyword-only (*) forces callers to explicitly name arguments, preventing disastrous
       mix-ups when functions take multiple boolean flags or optional configuration values.
    """
    print("\n" + "=" * 65)
    print("2. POSITIONAL-ONLY (/) AND KEYWORD-ONLY (*) ENFORCEMENT")
    print("=" * 65)

    def dispatch_llm_call(
        model_name: str,          # Positional-only (left of /)
        /,
        prompt: str,              # Positional or Keyword (between / and *)
        *,
        temperature: float = 0.7, # Keyword-only (right of *)
        stream: bool = False      # Keyword-only (right of *)
    ) -> Dict[str, Any]:
        return {
            "model": model_name,
            "prompt": prompt,
            "temperature": temperature,
            "stream": stream
        }

    # Valid call:
    config = dispatch_llm_call("gpt-4o", prompt="Summarize this text", temperature=0.2, stream=True)
    print(f"Valid invocation result: {config}")

    # Testing Positional-only violation:
    try:
        # Calling model_name by keyword fails because it is to the left of /
        dispatch_llm_call(model_name="gpt-4o", prompt="Hello")
    except TypeError as err:
        print(f"Caught expected Positional-Only error: {err}")

    # Testing Keyword-only violation:
    try:
        # Calling temperature positionally fails because it is to the right of *
        dispatch_llm_call("gpt-4o", "Hello", 0.2, True)
    except TypeError as err:
        print(f"Caught expected Keyword-Only error: {err}")


def demonstrate_closure_and_nonlocal() -> None:
    """
    Demonstrates stateful closures and the 'nonlocal' scope binding.
    
    WHY THIS MATTERS:
    Closures allow functions to capture and retain state from their enclosing scope
    without polluting module-level global namespaces or requiring full OOP class boilerplate.
    The 'nonlocal' keyword informs CPython that variable rebinding targets the nearest
    enclosing execution frame's cell object rather than creating a new local variable.
    """
    print("\n" + "=" * 65)
    print("3. STATEFUL CLOSURES WITH 'nonlocal' (Token Bucket Simulation)")
    print("=" * 65)

    def create_token_limiter(max_tokens: int) -> Callable[[int], bool]:
        tokens_remaining = max_tokens  # Enclosing scope variable (cell object)

        def consume_tokens(requested: int) -> bool:
            nonlocal tokens_remaining  # WHY: Allows mutation of enclosing frame variable
            if requested <= tokens_remaining:
                tokens_remaining -= requested
                print(f"Granted {requested} tokens. Remaining: {tokens_remaining}")
                return True
            print(f"Rejected! Requested {requested} tokens, but only {tokens_remaining} available.")
            return False

        return consume_tokens

    limiter = create_token_limiter(max_tokens=100)
    limiter(40)
    limiter(50)
    limiter(30)  # Rejection


def demonstrate_runtime_type_introspection() -> None:
    """
    Demonstrates how modern frameworks (FastAPI, Pydantic) inspect type annotations.
    
    WHY THIS MATTERS:
    CPython does not enforce type annotations at runtime; it merely records them
    in the function's __annotations__ dictionary.
    Frameworks like FastAPI inspect this metadata using typing.get_type_hints()
    to automatically generate OpenAPI documentation, cast query parameters, and validate schemas.
    """
    print("\n" + "=" * 65)
    print("4. RUNTIME TYPE INTROSPECTION (FastAPI Reflection Model)")
    print("=" * 65)

    def query_vector_database(query: str, top_k: int = 5, min_score: float = 0.75) -> list[str]:
        return ["doc_1", "doc_2"]

    # WHY: Always use typing.get_type_hints() rather than raw func.__annotations__
    # because get_type_hints() correctly resolves forward references and stringized annotations.
    hints = get_type_hints(query_vector_database)
    print("Inspected Type Signatures:")
    for param_name, param_type in hints.items():
        print(f"  Parameter: {param_name.ljust(12)} -> Type: {param_type}")


if __name__ == "__main__":
    demonstrate_legb_bytecode_optimization()
    demonstrate_parameter_boundaries()
    demonstrate_closure_and_nonlocal()
    demonstrate_runtime_type_introspection()
