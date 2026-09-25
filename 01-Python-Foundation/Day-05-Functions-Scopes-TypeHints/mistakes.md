# Day 05: Common Mistakes & Antipatterns (Functions, Scopes & Typing)

Here are the 7 most damaging function, scope, and typing mistakes that cause production bugs, silent data corruption, and security vulnerabilities in Python backend systems:

---

### 1. Mutable Default Arguments in Function Signatures
- ❌ **The Mistake:**
  ```python
  def append_prompt_history(prompt: str, history: list = []):
      history.append(prompt)
      return history
  ```
- ⚠️ **The Problem:** In Python, default arguments are evaluated **once at function definition time**, not upon each call. The empty list `[]` resides permanently in heap memory. Calls from different users/requests will mutate the exact same list instance, cross-contaminating user data!
- ✅ **The Correction:** Use `None` as the default sentinel and initialize inside:
  ```python
  def append_prompt_history(prompt: str, history: list[str] | None = None) -> list[str]:
      if history is None:
          history = []
      history.append(prompt)
      return history
  ```

---

### 2. The "Late Binding" Closure Trap in Loops
- ❌ **The Mistake:**
  ```python
  tool_validators = [lambda data: data * i for i in range(3)]
  # Expected: [0, 10, 20] when called with data=10
  print([v(10) for v in tool_validators])  # Prints: [20, 20, 20]!
  ```
- ⚠️ **The Problem:** Python closures look up variables in enclosing scopes **at call time**, not at definition time. When the lambdas are executed later, the loop has already terminated with `i = 2`.
- ✅ **The Correction:** Bind the current value into a default parameter at definition time:
  ```python
  tool_validators = [lambda data, i=i: data * i for i in range(3)]
  print([v(10) for v in tool_validators])  # Prints: [0, 10, 20]
  ```

---

### 3. Assuming Type Hints Enforce Runtime Type Validation
- ❌ **The Mistake:**
  ```python
  def process_payment(amount: float) -> None:
      # Developer assumes amount is guaranteed to be a float!
      charge_card(amount * 100)

  # Caller passes a string:
  process_payment("50.00")  # TypeError: can't multiply sequence by non-int of type 'int'
  ```
- ⚠️ **The Problem:** Python type hints are purely annotations for static analyzers (`mypy`). CPython does **not** validate or cast types at runtime unless you use a runtime validator like **Pydantic V2**.
- ✅ **The Correction:** Validate input payloads using Pydantic or explicit type casting:
  ```python
  from pydantic import BaseModel, Field

  class PaymentRequest(BaseModel):
      amount: float = Field(gt=0)
  ```

---

### 4. Overusing `Any` Everywhere
- ❌ **The Mistake:**
  ```python
  def dispatch_agent_event(event: Any, payload: Any) -> Any:
      return payload.execute(event)
  ```
- ⚠️ **The Problem:** Sprinkling `Any` everywhere silences IDE autocompletion, disables static type checkers, and eliminates the primary safety benefits of type hinting.
- ✅ **The Correction:** Use precise type constructs: `TypedDict`, `Protocol`, `Union`, or Generics:
  ```python
  from typing import Protocol, TypeVar

  class Executable(Protocol):
      def execute(self, event_name: str) -> dict: ...

  def dispatch_agent_event(event: str, payload: Executable) -> dict:
      return payload.execute(event)
  ```

---

### 5. Modifying Global Variables in Web Handlers
- ❌ **The Mistake:**
  ```python
  active_request_counter = 0

  @app.post("/chat")
  async def chat():
      global active_request_counter
      active_request_counter += 1  # Race condition under concurrent requests!
  ```
- ⚠️ **The Problem:** Module-level global state is shared across all concurrent async requests and worker threads. Modifying global variables introduces non-deterministic race conditions and memory leaks.
- ✅ **The Correction:** Use Redis counters or thread-safe atomic primitives.

---

### 6. Calling Functions with Unchecked `**kwargs`
- ❌ **The Mistake:**
  ```python
  @app.put("/users/{user_id}")
  async def update_user(user_id: int, request_json: dict):
      # Blindly unpacking user input into internal DB method:
      db_update_user(user_id, **request_json)
  ```
- ⚠️ **The Problem:** If an attacker includes `"is_admin": True` or `"role": "superuser"` in the incoming JSON, the function accepts the keyword arguments and overwrites privileged fields in the database!
- ✅ **The Correction:** Use Pydantic schemas with strict field definitions to whitelist allowed fields:
  ```python
  class UserUpdateSchema(BaseModel):
      first_name: str | None = None
      email: str | None = None

  @app.put("/users/{user_id}")
  async def update_user(user_id: int, payload: UserUpdateSchema):
      db_update_user(user_id, **payload.model_dump(exclude_unset=True))
  ```

---

### 7. Missing Keyword-Only Separator (`*`) for Ambiguous Arguments
- ❌ **The Mistake:**
  ```python
  def delete_document(doc_id: str, soft_delete: bool = True, force: bool = False):
      pass

  # In another file, caller mixes up boolean flags:
  delete_document("doc_102", True, True)  # Hard to read; which boolean is which?
  ```
- ⚠️ **The Problem:** Positional boolean arguments make call sites unreadable and prone to inverted flag bugs during refactoring.
- ✅ **The Correction:** Force keyword-only arguments using `*`:
  ```python
  def delete_document(doc_id: str, *, soft_delete: bool = True, force: bool = False):
      pass

  # Caller is forced to be explicit:
  delete_document("doc_102", soft_delete=True, force=False)
  ```
