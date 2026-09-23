# Day 03: Common Mistakes & Antipatterns (Collections & Data Structures)

Here are the 7 most frequent data structure mistakes that degrade performance and cause subtle runtime bugs in Python backend systems:

---

### 1. Using a `list` for Frequent Membership Testing
- ❌ **The Mistake:**
  ```python
  # Blacklist of 50,000 banned IP addresses
  banned_ips = ["192.168.1.1", "10.0.0.5", ...] 

  @app.middleware("http")
  async def firewall(request, call_next):
      if request.client.host in banned_ips:  # O(N) linear scan on EVERY request!
          return Response("Forbidden", status_code=403)
  ```
- ⚠️ **The Problem:** In a list, `item in banned_ips` executes a linear scan from index 0 to $N-1$. At 1,000 requests/second, the CPU spends 99% of its cycles traversing list pointers.
- ✅ **The Correction:**
  Convert to a `set` once at startup:
  ```python
  banned_ips = {"192.168.1.1", "10.0.0.5", ...}  # O(1) constant time hash lookup
  ```

---

### 2. Modifying a Dictionary During Iteration
- ❌ **The Mistake:**
  ```python
  active_sessions = {"sess_1": 100, "sess_2": 0, "sess_3": 50}
  for session_id, balance in active_sessions.items():
      if balance == 0:
          del active_sessions[session_id]  # CRASH!
  ```
- ⚠️ **The Problem:** Modifying the size of a dictionary changes the internal indices table while the iterator pointer is traversing it, raising:
  `RuntimeError: dictionary changed size during iteration`.
- ✅ **The Correction:**
  Iterate over a static list copy or build a dictionary comprehension:
  ```python
  # Option A: Iterate over keys snapshot
  for session_id in list(active_sessions.keys()):
      if active_sessions[session_id] == 0:
          del active_sessions[session_id]

  # Option B: Dictionary comprehension
  active_sessions = {k: v for k, v in active_sessions.items() if v > 0}
  ```

---

### 3. Using `list.pop(0)` to Implement a Queue
- ❌ **The Mistake:**
  ```python
  class TaskQueue:
      def __init__(self):
          self.queue = []
      def push(self, task):
          self.queue.append(task)
      def pop(self):
          return self.queue.pop(0)  # O(N) memory shift on every dequeue!
  ```
- ⚠️ **The Problem:** When index 0 is popped from a Python list, CPython must execute an internal `memmove` shifting all remaining pointers 1 position to the left. For a queue of 100,000 items, popping all items requires 5 billion pointer moves.
- ✅ **The Correction:**
  Always use `collections.deque` for FIFO queues:
  ```python
  from collections import deque

  class TaskQueue:
      def __init__(self):
          self.queue = deque()
      def push(self, task):
          self.queue.append(task)
      def pop(self):
          return self.queue.popleft()  # O(1) constant time pointer update
  ```

---

### 4. Expecting `dict.copy()` to Create a Deep Clone
- ❌ **The Mistake:**
  ```python
  original_policy = {"role": "editor", "permissions": ["read", "write"]}
  cloned_policy = original_policy.copy()

  cloned_policy["permissions"].append("delete")

  # original_policy["permissions"] also contains "delete"!
  print(original_policy["permissions"])  # ['read', 'write', 'delete']
  ```
- ⚠️ **The Problem:** `dict.copy()` is a **shallow copy**. It allocates a new dictionary container, but the nested list reference inside is shared between both dictionaries.
- ✅ **The Correction:**
  ```python
  import copy
  cloned_policy = copy.deepcopy(original_policy)
  ```

---

### 5. Inefficient Eager Evaluation in `dict.setdefault()`
- ❌ **The Mistake:**
  ```python
  # Grouping 100,000 events:
  events_by_user = {}
  for event in event_stream:
      # list() is allocated on EVERY single loop, even if the key already exists!
      events_by_user.setdefault(event.user_id, []).append(event)
  ```
- ⚠️ **The Problem:** In Python, function arguments are evaluated before the function executes. `[]` creates a brand new empty list object in memory on *every single iteration*, immediately discarding it if the key exists.
- ✅ **The Correction:**
  Use `collections.defaultdict`:
  ```python
  from collections import defaultdict
  events_by_user = defaultdict(list)
  for event in event_stream:
      events_by_user[event.user_id].append(event)
  ```

---

### 6. Using Mutable Objects as Dictionary Keys
- ❌ **The Mistake:**
  ```python
  cache = {}
  user_coordinates = [37.7749, -122.4194]
  cache[user_coordinates] = "San Francisco"  # TypeError: unhashable type: 'list'
  ```
- ⚠️ **The Problem:** Lists, sets, and dictionaries do not implement `__hash__` because they can mutate in place. If an object mutated after insertion, its computed hash would change, stranding the value in an unreachable bucket.
- ✅ **The Correction:**
  Use an immutable `tuple`:
  ```python
  user_coordinates = (37.7749, -122.4194)
  cache[user_coordinates] = "San Francisco"  # Valid & O(1) hashable
  ```

---

### 7. Unnecessary `.keys()` Lookup in Conditionals
- ❌ **The Mistake:**
  ```python
  if "authorization" in request_headers.keys():
      token = request_headers["authorization"]
  ```
- ⚠️ **The Problem:** Calling `.keys()` creates a `dict_keys` view object and adds unnecessary method dispatch overhead.
- ✅ **The Correction:**
  Query the dictionary directly:
  ```python
  if "authorization" in request_headers:
      token = request_headers["authorization"]
  ```
