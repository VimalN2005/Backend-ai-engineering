# Day 04: Common Mistakes & Antipatterns (Control Flow & Comprehensions)

Here are the 7 most frequent control flow and comprehension mistakes that cause bugs, unreadable code, and memory leaks in production Python backend and AI pipelines:

---

### 1. The "Arrow Anti-Pattern" (Deeply Nested Conditionals)
- ❌ **The Mistake:**
  ```python
  def execute_llm_tool(user, tool_name, payload):
      if user is not None:
          if user.is_authenticated:
              if user.has_permission(tool_name):
                  if payload and "query" in payload:
                      return run_tool(tool_name, payload)
                  else:
                      return {"error": "Invalid payload"}
              else:
                  return {"error": "Permission denied"}
          else:
              return {"error": "Not authenticated"}
      else:
          return {"error": "User missing"}
  ```
- ⚠️ **The Problem:** The code drifts farther to the right on every condition ("arrow shape"). It creates high cyclomatic complexity, increases cognitive load, and makes testing edge cases difficult.
- ✅ **The Correction:** Use **Guard Clauses** to fail early:
  ```python
  def execute_llm_tool(user, tool_name, payload):
      if not user:
          return {"error": "User missing"}
      if not user.is_authenticated:
          return {"error": "Not authenticated"}
      if not user.has_permission(tool_name):
          return {"error": "Permission denied"}
      if not payload or "query" not in payload:
          return {"error": "Invalid payload"}

      # Happy path stays at root indentation level!
      return run_tool(tool_name, payload)
  ```

---

### 2. Using a List Comprehension Purely for Side Effects
- ❌ **The Mistake:**
  ```python
  # Writing to database or sending notifications inside a list comprehension:
  [send_slack_alert(channel, msg) for msg in alert_messages]
  ```
- ⚠️ **The Problem:** A list comprehension's sole purpose is to **construct and return a new list in memory**. When used for side effects, it allocates a useless list of `[None, None, ...]` in heap RAM and discards it.
- ✅ **The Correction:** Use a standard `for` loop:
  ```python
  for msg in alert_messages:
      send_slack_alert(channel, msg)
  ```

---

### 3. Buffering Massive Datasets in List Comprehensions Instead of Generators
- ❌ **The Mistake:**
  ```python
  # Loading 2,000,000 PDF text nodes into memory at once:
  embeddings = [embedding_model.encode(node.text) for node in massive_pdf_stream]
  ```
- ⚠️ **The Problem:** List comprehensions evaluate eagerly, allocating millions of floating-point vectors into RAM simultaneously. In a containerized environment (Docker/Kubernetes), this triggers an OOM (Out Of Memory) process kill.
- ✅ **The Correction:** Use a lazy **Generator Expression**:
  ```python
  embedding_stream = (embedding_model.encode(node.text) for node in massive_pdf_stream)
  ```

---

### 4. Overly Complex, Multi-Nested Comprehensions
- ❌ **The Mistake:**
  ```python
  result = [
      transform(val)
      for batch in dataset if batch.is_valid
      for record in batch.records if record.is_active
      for val in record.values if val > threshold
  ]
  ```
- ⚠️ **The Problem:** Comprehensions nested 3 levels deep are virtually unreadable, difficult to debug with breakpoints, and impossible to unit test cleanly.
- ✅ **The Correction:** Break into clear generator functions or standard loops:
  ```python
  def extract_active_values(dataset, threshold):
      for batch in dataset:
          if not batch.is_valid:
              continue
          for record in batch.records:
              if not record.is_active:
                  continue
              for val in record.values:
                  if val > threshold:
                      yield transform(val)

  result = list(extract_active_values(dataset, threshold))
  ```

---

### 5. Operator Precedence Bug in Boolean Logic (`and` vs. `or`)
- ❌ **The Mistake:**
  ```python
  # Intended: Allow access if logged in AND is either admin or editor
  if user.is_logged_in and user.role == "admin" or user.role == "editor":
      grant_dashboard_access()
  ```
- ⚠️ **The Problem:** In Python, `and` has higher operator precedence than `or`. Python parses this as:
  `((user.is_logged_in and user.role == "admin") or user.role == "editor")`.
  An unauthenticated user with `role == "editor"` will bypass the login check!
- ✅ **The Correction:** Always use explicit parentheses:
  ```python
  if user.is_logged_in and (user.role == "admin" or user.role == "editor"):
      grant_dashboard_access()
  ```

---

### 6. Misunderstanding `for...else` Execution Triggers
- ❌ **The Mistake:**
  ```python
  for user in users:
      if user.id == target_id:
          print("Found user!")
          break
  else:
      print("User found!")  # Developer expected this to run on successful match!
  ```
- ⚠️ **The Problem:** In Python, the `else` block executes **only if the loop finishes naturally without encountering a `break`**.
- ✅ **The Correction:**
  ```python
  for user in users:
      if user.id == target_id:
          print("Found user!")
          break
  else:
      print("User NOT found in database!")  # Correctly handles the fallback
  ```

---

### 7. Mutating a List While Iterating with `for`
- ❌ **The Mistake:**
  ```python
  numbers = [1, 2, 3, 4, 5, 6]
  for num in numbers:
      if num % 2 == 0:
          numbers.remove(num)  # Modifying list in-place!
  print(numbers)  # [1, 3, 5] ? NO! It outputs [1, 3, 5, 6] because index shifts skipped 6!
  ```
- ⚠️ **The Problem:** Mutating the list shifts internal array pointers. As index $i$ advances, the item shifted into index $i$ is never inspected.
- ✅ **The Correction:** Use a list comprehension to construct a new filtered list:
  ```python
  numbers = [num for num in numbers if num % 2 != 0]
  ```
