# Day 87: AI Agents & LangGraph — 7 Critical Enterprise Antipatterns

Designing autonomous multi-turn agents and graph-based state machines exposes systems to runtime infinite loops, state corruption, and catastrophic unintended side effects. Below are 7 critical antipatterns and production remediations.

---

### 1. Unbounded ReAct Execution Loops (No Circuit Breaker)
- **The Mistake**: Running an autonomous while-loop without a strict `max_iterations` ceiling or loop-detection heuristics.
- **The Damage**: When an external API returns a transient error or the model enters an ambiguous reasoning state, the agent loops 100+ times, consuming \$50–\$200 in API tokens in minutes and locking user threads.
- **The Fix**: Enforce a hard ceiling (`max_iterations = 5` to `10`). Implement cycle detection: if the same `(action_name, action_input)` pair repeats twice consecutively, break the loop and route to a fallback human escalation node.

---

### 2. Ephemeral In-Memory State Without Checkpointing
- **The Mistake**: Storing agent state solely in Python process memory (e.g., local lists or dictionaries).
- **The Damage**: If the container restarts, worker crashes, or network times out during turn 4 of a 6-turn agent workflow, the entire state is lost. Long-running asynchronous tasks cannot survive pod redeployments.
- **The Fix**: Implement a durable checkpointer (e.g., `PostgresSaver` or Redis checkpointer) that persists an immutable state snapshot after every node execution keyed by `(thread_id, checkpoint_id)`.

---

### 3. Monolithic "God Agent" with Too Many Tools
- **The Mistake**: Registering 40+ diverse tools into a single agent prompt (e.g., SQL query, payment gateway, HR docs, code execution, customer support).
- **The Damage**: High tool hallucination rate (>50%). The model struggles with parameter confusion, tool selection errors, and diluted system prompt instructions.
- **The Fix**: Adopt the **Supervisor-Worker (Sub-Agent)** pattern. A root Router Agent classifies intent and delegates to specialized subgraphs (e.g., `BillingSubGraph`, `DevOpsSubGraph`, `SupportSubGraph`) exposing only 3–5 hyper-focused tools each.

---

### 4. Direct State Mutation Instead of State Reducers
- **The Mistake**: Allowing parallel graph nodes or branch workers to overwrite keys in a shared state dictionary directly (`state["messages"] = new_msgs`).
- **The Damage**: Race conditions and lost updates. When two parallel sub-agents finish simultaneously, one sub-agent's updates completely overwrite the other's.
- **The Fix**: Enforce **State Reducers** (e.g., `Annotated[List[BaseMessage], operator.add]` in LangGraph or append-only queues). State updates must be functional, deterministic merge operations.

---

### 5. Absence of Human-in-the-Loop (HITL) Gates on Irreversible Mutations
- **The Mistake**: Giving an autonomous agent unilateral permission to invoke destructive mutations (e.g., deleting database rows, issuing refunds, deploying infrastructure, sending external emails).
- **The Damage**: Prompt injection attacks or reasoning drift result in catastrophic real-world damage without human oversight.
- **The Fix**: Implement explicit policy gates. High-risk tool calls must transition the state to `SUSPENDED_FOR_APPROVAL` and emit an interrupt event. The workflow resumes only when an authenticated supervisor signs off.

---

### 6. Scratchpad Flooding with Unfiltered Tool Outputs
- **The Mistake**: Appending entire raw HTTP/JSON payloads (e.g., 500 KB API responses) directly into the agent's message history.
- **The Damage**: Severe context window exhaustion, quadratic attention slowdown, and distraction of the reasoning model by irrelevant metadata.
- **The Fix**: Implement an extractive projection or summarization layer between tool execution and state recording. Extract only the specific fields required to satisfy the user prompt.

---

### 7. Graph Deadlocks and Ping-Pong Conditional Routing
- **The Mistake**: Configuring conditional edges where Node A routes to Node B on failure, and Node B routes back to Node A without tracking attempt history.
- **The Damage**: The state graph enters an infinite ping-pong cycle between two nodes, deadlocking the executor until memory or timeouts terminate the worker.
- **The Fix**: Maintain an explicit `hop_count` or `retry_counter` in the graph state. Conditional edge functions must inspect retry counters and route to a terminal `ERROR` node once retry thresholds are breached.
