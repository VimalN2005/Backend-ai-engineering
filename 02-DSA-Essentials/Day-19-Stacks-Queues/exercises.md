# Day 19: Practical Hands-On DSA Challenges

Master Stacks, Queues, and Execution Order Management with these 3 production-level challenges.

---

## Challenge 1: Canonical Unix Path Normalizer ($\mathcal{O}(N)$ Time)

### Objective
In backend web servers and object storage systems (e.g. AWS S3 path routers, static file servers), incoming file paths frequently contain redundant slashes `//`, current directory markers `.`, and directory traversal operators `..` (which pose severe directory traversal security risks).

Implement `simplify_path(path: str) -> str` that canonicalizes an absolute Unix file path using a LIFO Stack.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$ where $N$ is string length.
2. **Space Complexity**: $\mathcal{O}(N)$ stack space.
3. **Canonical Rules**:
   - The path must start with a single slash `/`.
   - Redundant slashes `///` are treated as a single slash.
   - Any single period `.` represents the current directory and must be ignored.
   - Any double period `..` moves up one directory level (pops the top directory from the stack if not empty).
   - Any other format of periods (e.g. `...`) is treated as a valid directory/file name.
   - The path must not end with a trailing `/` unless it is the root directory itself (`/`).
4. **Test Cases**:
   - `"/home/"` $\to$ `"/home"`
   - `"/home//foo/"` $\to$ `"/home/foo"`
   - `"/home/user/Documents/../Pictures"` $\to$ `"/home/user/Pictures"`
   - `"/../"` $\to$ `"/"`
   - `"/.../a/../b/c/../d/./"` $\to$ `"/.../b/d"`

---

## Challenge 2: Nested Token String Decoder (Dual-Stack Parser)

### Objective
In templating engines, compressed data serialization formats, and LLM prompt macro expansion, nested repeat blocks must be parsed into their full representations.

Implement `decode_string(s: str) -> str` that decodes nested encoded strings formatted as `k[encoded_string]`, where `encoded_string` inside square brackets is repeated exactly `k` times.

### Requirements
1. **Time Complexity**: $\mathcal{O}(\text{MaxK}^{\text{Depth}} \times N)$ proportional to output string size.
2. **Space Complexity**: $\mathcal{O}(N)$ stack space.
3. **Dual Stack Architecture**:
   - Maintain a `count_stack` (stores multipliers `k`).
   - Maintain a `string_stack` (stores prefixes accumulated prior to entering bracket).
4. **Multi-Digit Support**:
   - Correctly parse multi-digit integers (e.g. `"12[ab]"`).
5. **Test Cases**:
   - `"3[a]2[bc]"` $\to$ `"aaabcbc"`
   - `"3[a2[c]]"` $\to$ `"accaccacc"`
   - `"2[abc]3[cd]ef"` $\to$ `"abcabccdcdcdef"`
   - `"10[a]"` $\to$ `"aaaaaaaaaa"`

---

## Challenge 3: Distributed Saga Transactional Rollback Buffer

### Objective
In distributed microservices (the Saga Pattern), transactions spanning multiple services (Order Service $\to$ Payment Service $\to$ Inventory Service) cannot execute under a shared database lock. If any step fails, all previous steps must be compensated in strict reverse (LIFO) order.

Build a production class `SagaTransactionManager`:

```python
@dataclass
class SagaStep:
    name: str
    action: Callable[[], Any]
    compensate: Callable[[], None]

class SagaTransactionManager:
    def __init__(self) -> None:
        ...

    def register_step(self, step: SagaStep) -> None:
        ...

    def execute(self) -> Tuple[bool, List[str]]:
        """
        Executes all registered steps in forward order.
        If any step raises an exception:
        - Catches exception.
        - Unwinds the compensation stack in strict LIFO order.
        - Returns (False, [list of compensation logs]).
        If all steps succeed:
        - Returns (True, [list of execution logs]).
        """
        ...
```

### Requirements
1. **LIFO Compensation Stack**: Successful steps must push their compensation callbacks onto an internal Stack.
2. **Exception Containment**: Failures must never crash the service; the rollback must cleanly execute and restore previous states.
3. **Execution Reporting**: Return detailed audit logs of steps executed and compensations triggered.

---

## Solution Guidelines & Architecture Hints
- For Challenge 1, use `path.split('/')` to tokenize path components in a single pass.
- For Challenge 2, reset `current_num = 0` and `current_str = ""` whenever `[` is entered.
- For Challenge 3, ensure compensation callbacks are executed even if an intermediate step raises an unhandled `RuntimeError`.
