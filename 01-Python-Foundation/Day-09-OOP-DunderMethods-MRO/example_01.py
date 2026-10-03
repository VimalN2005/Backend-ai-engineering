"""Day 09: Core OOP Mechanics, Dunder Protocols, Memory Slots, and MRO.

This module demonstrates the core mechanics of Python's object model:
1. Object Allocation vs Initialization (__new__ vs __init__) for Interning/Singletons.
2. The Hash & Equality Invariant (__eq__ and __hash__) in Value Objects.
3. Memory Optimization with __slots__ (eliminating __dict__ overhead).
4. Operator Overloading Protocol (__matmul__ for dot-product calculation).
5. The Diamond Inheritance Problem and C3 Linearization MRO with cooperative super().
"""

import math
import sys
from functools import total_ordering
from typing import Any, Dict, List, Tuple


# =====================================================================
# 1. OBJECT LIFECYCLE: __new__ VS __init__ (Flyweight Interning)
# =====================================================================
class ModelRegistry:
    """Flyweight pattern using __new__ to ensure only ONE instance per model name exists.

    WHY?
    Instantiating heavy AI model configurations or connection pools repeatedly
    wastes memory and CPU. By intercepting __new__ before memory is allocated,
    we can check an internal cache and return an existing instance if available.
    """

    _instances: Dict[str, "ModelRegistry"] = {}

    def __new__(cls, model_name: str, context_window: int) -> "ModelRegistry":
        # Check if an instance with this model_name was already created
        if model_name in cls._instances:
            # Return existing instance; CPython will still call __init__, so __init__
            # must be idempotent or guard against re-initialization.
            return cls._instances[model_name]

        # Allocate brand-new instance via object.__new__
        new_instance = super().__new__(cls)
        cls._instances[model_name] = new_instance
        return new_instance

    def __init__(self, model_name: str, context_window: int) -> None:
        # Avoid re-initialization if already initialized
        if hasattr(self, "_initialized"):
            return
        self.model_name = model_name
        self.context_window = context_window
        self._initialized = True

    def __repr__(self) -> str:
        return f"ModelRegistry(model_name={self.model_name!r}, context_window={self.context_window})"


# =====================================================================
# 2. MEMORY OPTIMIZATION: __slots__ VS __dict__
# =====================================================================
class StandardEmbedding:
    """Standard Python class that creates an instance __dict__."""

    def __init__(self, vector_id: str, values: Tuple[float, ...]):
        self.vector_id = vector_id
        self.values = values


class SlottedEmbedding:
    """Memory-optimized class using __slots__ to eliminate __dict__ overhead.

    WHY?
    In AI systems indexing millions of vector embeddings, a standard Python
    instance allocates a dynamic dictionary (__dict__) of ~152 bytes minimum.
    Using __slots__ replaces __dict__ with a compact C-level struct array,
    saving ~60-70% of memory per instance.
    """

    __slots__ = ("vector_id", "values")

    def __init__(self, vector_id: str, values: Tuple[float, ...]):
        self.vector_id = vector_id
        self.values = values


# =====================================================================
# 3. EQUALITY, HASHING & OPERATOR OVERLOADING (Value Objects)
# =====================================================================
@total_ordering
class DenseVector:
    """Mathematical dense vector supporting hashing, ordering, and dot products."""

    __slots__ = ("_values", "_magnitude")

    def __init__(self, values: Tuple[float, ...]):
        self._values = tuple(values)
        self._magnitude = math.sqrt(sum(v * v for v in self._values))

    @property
    def magnitude(self) -> float:
        return self._magnitude

    # WHY __eq__ and __hash__ must align?
    # Invariant: If a == b, then hash(a) == hash(b).
    # If __eq__ is overridden without __hash__, CPython sets __hash__ = None,
    # making instances unhashable. Both must be based on immutable properties!
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DenseVector):
            return NotImplemented
        return self._values == other._values

    def __hash__(self) -> int:
        return hash(self._values)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, DenseVector):
            return NotImplemented
        # Order vectors by their Euclidean norm (magnitude)
        return self._magnitude < other._magnitude

    # Overload the matrix multiplication operator: vector1 @ vector2
    # WHY? The '@' operator is the idiomatic Python operator for dot products and matrix operations.
    def __matmul__(self, other: "DenseVector") -> float:
        if not isinstance(other, DenseVector):
            return NotImplemented
        if len(self._values) != len(other._values):
            raise ValueError(f"Dimension mismatch: {len(self._values)} vs {len(other._values)}")
        # Compute dot product: sum(a_i * b_i)
        return sum(a * b for a, b in zip(self._values, other._values))

    def cosine_similarity(self, other: "DenseVector") -> float:
        """Compute cosine similarity: (A . B) / (||A|| * ||B||)."""
        dot_product = self @ other
        norm_product = self._magnitude * other._magnitude
        if norm_product == 0.0:
            return 0.0
        return dot_product / norm_product

    def __repr__(self) -> str:
        return f"DenseVector({self._values!r})"


# =====================================================================
# 4. DIAMOND INHERITANCE & C3 LINEARIZATION (MRO)
# =====================================================================
class BaseService:
    def __init__(self, **kwargs: Any) -> None:
        # Cooperatively forward unhandled keyword arguments to object
        super().__init__()
        self.status = "INITIALIZED"

    def execute(self, payload: Dict[str, Any]) -> None:
        payload["audit_trail"] = ["BaseService"]


class AuthAuditMixin(BaseService):
    def execute(self, payload: Dict[str, Any]) -> None:
        # WHY super().execute()?
        # Cooperative super() calls the NEXT class in runtime MRO, NOT necessarily BaseService!
        super().execute(payload)
        payload["audit_trail"].append("AuthAuditMixin")


class RateLimitMixin(BaseService):
    def execute(self, payload: Dict[str, Any]) -> None:
        super().execute(payload)
        payload["audit_trail"].append("RateLimitMixin")


class ProductionAIGateway(AuthAuditMixin, RateLimitMixin):
    """Diamond inheritance: Inherits from both AuthAuditMixin and RateLimitMixin."""

    def execute(self, payload: Dict[str, Any]) -> None:
        super().execute(payload)
        payload["audit_trail"].append("ProductionAIGateway")


# =====================================================================
# DEMONSTRATION RUNNER
# =====================================================================
def run_demonstrations() -> None:
    print("=" * 65)
    print("1. OBJECT INSTANTIATION & FLYWEIGHT INTERNING (__new__)")
    print("=" * 65)
    model_a = ModelRegistry("gpt-4o", context_window=128000)
    model_b = ModelRegistry("gpt-4o", context_window=128000)

    print(f"[*] model_a is model_b: {model_a is model_b} (Exact same memory address)")
    print(f"[*] Identity of model_a: {id(model_a)}")
    print(f"[*] Identity of model_b: {id(model_b)}")

    print("\n" + "=" * 65)
    print("2. MEMORY FOOTPRINT: __slots__ VS STANDARD __dict__")
    print("=" * 65)
    vals = (0.12, 0.45, 0.78, 0.99, -0.34)
    std_obj = StandardEmbedding("vec_001", vals)
    slot_obj = SlottedEmbedding("vec_001", vals)

    print(f"[*] Standard object base size : {sys.getsizeof(std_obj)} bytes")
    print(f"[*] Standard object __dict__  : {sys.getsizeof(std_obj.__dict__)} bytes")
    print(f"[*] Total Standard Footprint  : {sys.getsizeof(std_obj) + sys.getsizeof(std_obj.__dict__)} bytes")
    print(f"[*] Slotted object footprint  : {sys.getsizeof(slot_obj)} bytes (No __dict__!)")
    print(f"[*] Memory Savings            : ~{(1 - (sys.getsizeof(slot_obj) / (sys.getsizeof(std_obj) + sys.getsizeof(std_obj.__dict__)))) * 100:.1f}% per object")

    print("\n" + "=" * 65)
    print("3. OPERATOR OVERLOADING & VALUE OBJECT INVARIANTS")
    print("=" * 65)
    v1 = DenseVector((1.0, 2.0, 3.0))
    v2 = DenseVector((1.0, 2.0, 3.0))
    v3 = DenseVector((4.0, 5.0, 6.0))

    # Test equality and hashing contract
    print(f"[*] v1 == v2 : {v1 == v2}")
    print(f"[*] hash(v1) == hash(v2) : {hash(v1) == hash(v2)}")

    # Store in set to prove hash table compatibility
    vector_set = {v1, v2, v3}
    print(f"[*] Unique vectors in set : {len(vector_set)} (Deduplicated v1 and v2)")

    # Test dot product via @ operator
    dot_result = v1 @ v3
    print(f"[*] Dot Product (v1 @ v3) : {dot_result} (1*4 + 2*5 + 3*6 = 32.0)")

    # Test cosine similarity
    sim = v1.cosine_similarity(v3)
    print(f"[*] Cosine Similarity     : {sim:.4f}")

    print("\n" + "=" * 65)
    print("4. C3 LINEARIZATION & COOPERATIVE MRO")
    print("=" * 65)
    print("[*] ProductionAIGateway Method Resolution Order (MRO):")
    for i, cls_item in enumerate(ProductionAIGateway.__mro__, start=1):
        print(f"    Step {i}: {cls_item.__name__}")

    gateway = ProductionAIGateway()
    test_payload: Dict[str, Any] = {}
    gateway.execute(test_payload)
    print(f"[*] Dynamic Cooperative Execution Trail: {' -> '.join(test_payload['audit_trail'])}")


if __name__ == "__main__":
    run_demonstrations()
