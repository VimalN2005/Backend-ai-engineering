# Day 09: Practical Hands-On Exercises

Master OOP internals, dunder protocols, descriptors, and cooperative multiple inheritance by solving these 3 production challenges.

---

### Exercise 1: Slotted Immutable Vector Embedding with Custom Operators (Medium)
Build an immutable, memory-optimized vector class `EmbeddingVector` that:
1. Uses `__slots__ = ("_values", "_magnitude")` to ensure minimal memory footprint.
2. Accepts an iterable of floats and converts them into an immutable tuple.
3. Implements sequence protocols:
   - `__len__` returning vector dimension.
   - `__getitem__` supporting both indexing (`vec[0]`) and slicing (`vec[1:3]`).
4. Implements operator overloading:
   - `@` (`__matmul__`) for dot product computation: $\sum a_i \cdot b_i$.
   - `+` (`__add__`) for element-wise addition: $(a_1 + b_1, a_2 + b_2, \dots)$.
5. Implements value object invariants:
   - `__eq__` and `__hash__` based on `_values` so instances can be stored in sets and used as dict keys.

---

### Exercise 2: Composable Cooperative Middleware Pipeline (Medium)
Build an extensible request processing pipeline using cooperative multiple inheritance and `super()`:
1. Define a base class `BaseEndpoint` with a method `dispatch(request: dict) -> dict`.
2. Define three independent mixins:
   - `AuthenticationMixin`: Validates `request["headers"]["Authorization"]`, raises `PermissionError` if missing.
   - `RateLimitingMixin`: Rejects requests if client IP exceeds 5 requests per second.
   - `TelemetryAuditMixin`: Injects execution start time and latency metrics into the response dictionary.
3. Combine all three mixins into a concrete class `SecureChatEndpoint(AuthenticationMixin, RateLimitingMixin, TelemetryAuditMixin, BaseEndpoint)` and verify that all mixins execute in deterministic order without duplicating calls.

---

### Exercise 3: Type & Range Validated Model Hyperparameter Descriptor (Advanced)
Implement a reusable descriptor `HyperparameterDescriptor(data_type: type, min_value=None, max_value=None)` that:
1. Implements `__set_name__` to automatically name private backing attributes (`_attr_name`).
2. Implements `__set__` to enforce:
   - Strict runtime type checking against `data_type` (e.g., must be `int` or `float`).
   - Boundary checks against `min_value` and `max_value`.
3. Maintains an internal mutation audit log on each instance: `instance._hyperparameter_history[name] = [old_val, new_val]`.
4. Attaches this descriptor to an `LLMConfig` class managing `context_length` (`int`, 512 to 128,000) and `top_p` (`float`, 0.0 to 1.0).
