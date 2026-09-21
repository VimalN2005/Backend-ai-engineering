# Day 01: Common Mistakes & Antipatterns (Python Environment & Memory)

Here are the 7 most critical pitfalls that junior and intermediate engineers encounter when dealing with Python runtime environments, memory allocation, and project isolation:

---

### 1. Using `is` Instead of `==` for Value Comparison
- ❌ **The Mistake:**
  ```python
  status = input("Enter status: ")  # User types "SUCCESS"
  if status is "SUCCESS":
      process_order()
  ```
- ⚠️ **The Problem:** The `is` operator tests object identity (`id(a) == id(b)`), whereas `==` tests value equality (`__eq__`). CPython does not intern dynamically generated strings or strings containing whitespace. This code may pass in a local terminal test but will fail silently in production with external API payloads.
- ✅ **The Correction:**
  ```python
  if status == "SUCCESS":
      process_order()
  ```
  > **Rule of Thumb:** Reserve `is` strictly for Python singletons (`None`, `True`, `False`), e.g., `if result is None:`.

---

### 2. Mutating an Object via a Reference Alias
- ❌ **The Mistake:**
  ```python
  default_config = {"timeout": 30, "retries": 3}
  user_config = default_config
  user_config["timeout"] = 60

  # default_config is also mutated to 60!
  print(default_config["timeout"])  # 60
  ```
- ⚠️ **The Problem:** `user_config = default_config` does not create a clone. It creates a second reference pointer pointing to the identical dictionary object in heap memory.
- ✅ **The Correction:**
  ```python
  import copy

  # Shallow copy for flat dictionaries:
  user_config = default_config.copy()

  # Deep copy if config contains nested dictionaries/lists:
  user_config = copy.deepcopy(default_config)
  ```

---

### 3. Installing Dependencies Globally on Host Servers
- ❌ **The Mistake:**
  ```bash
  pip install django fastapi celery
  ```
- ⚠️ **The Problem:** Pollutes the operating system's global `site-packages`. On Linux distributions (Debian/Ubuntu/CentOS), essential OS automation tools (such as `apt`, `yum`, or `cloud-init`) depend on system Python binaries. Overwriting these shared packages can destabilize or crash the entire host OS.
- ✅ **The Correction:**
  ```bash
  python -m venv .venv
  # Windows:
  .venv\Scripts\activate
  # Linux / macOS:
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

---

### 4. Committing `.env` and `__pycache__` to Git
- ❌ **The Mistake:**
  Executing `git add .` without a `.gitignore`, pushing database credentials, secret keys, and compiled `.pyc` files to public or shared repositories.
- ⚠️ **The Problem:**
  1. API keys and credentials are leaked into Git history permanently.
  2. Compiled `.pyc` files create binary merge conflicts and bytecode mismatch issues across different OS architectures.
- ✅ **The Correction:**
  Always maintain a clean `.gitignore` at the repository root containing:
  ```gitignore
  .env
  .venv/
  __pycache__/
  *.py[cod]
  ```

---

### 5. Misinterpreting `sys.getsizeof()` for Nested Containers
- ❌ **The Mistake:**
  ```python
  import sys
  large_list = ["a" * 1000000, "b" * 1000000]
  print(sys.getsizeof(large_list))  # Reports only ~72 bytes!
  ```
- ⚠️ **The Problem:** `sys.getsizeof()` measures only the container structure itself (the list of 8-byte C pointers), not the referenced string objects stored elsewhere on the heap.
- ✅ **The Correction:** Use deep memory profilers like `tracemalloc` or the `pympler.asizeof` library for true memory audits.

---

### 6. Using Mutable Default Arguments in Functions
- ❌ **The Mistake:**
  ```python
  def append_to_cache(item, cache=[]):
      cache.append(item)
      return cache

  print(append_to_cache(1))  # [1]
  print(append_to_cache(2))  # [1, 2] -> Previous execution state leaked!
  ```
- ⚠️ **The Problem:** Default argument expressions are evaluated **once at function definition time**, not upon each invocation. The same list object persists in memory across subsequent calls.
- ✅ **The Correction:**
  ```python
  def append_to_cache(item, cache=None):
      if cache is None:
          cache = []
      cache.append(item)
      return cache
  ```

---

### 7. Hardcoding Host-Specific File Paths
- ❌ **The Mistake:**
  ```python
  config_path = "C:\\Users\\admin\\project\\config.json"
  ```
- ⚠️ **The Problem:** Hardcoded Windows paths crash immediately when deployed to Linux containers or cloud environments like AWS EC2/ECS.
- ✅ **The Correction:** Use cross-platform path resolution via `pathlib.Path`:
  ```python
  from pathlib import Path
  BASE_DIR = Path(__file__).resolve().parent.parent
  config_path = BASE_DIR / "config.json"
  ```
