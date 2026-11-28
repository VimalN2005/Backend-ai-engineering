# Day 87: Rapid Revision & Flashcards — AI Agents & LangGraph

## 5-Minute Core Revision Cheat Sheet

### Agent Architecture Primitives
- **ReAct Loop**: `Thought` (deduction) $\to$ `Action` (tool call) $\to$ `Observation` (environment output). Prevents reasoning-only hallucinations and action-only blindness.
- **Graph State Machine**: State schema + Nodes (compute functions) + Edges (transitions) + Conditional Edges (dynamic routing).
- **Cyclic Graphs**: Agents require loops to retry, repair parameters, and evaluate exit conditions, which standard DAG engines (Airflow) cannot handle natively.
- **State Reducers**: Functional rules (e.g., `operator.add`) that specify how updates from concurrent nodes or sequential turns merge without race conditions.
- **Checkpointing**: Durable snapshots keyed by `(thread_id, checkpoint_id)`. Enables time-travel debugging, recovery from failure, and pause/resume.
- **Human-in-the-Loop (HITL)**: Suspending graph execution at sensitive boundaries (`SUSPENDED_FOR_APPROVAL`) without holding open blocking worker threads.

---

## 10 Active Recall Flashcards

1. **Q: What are the three phases of the ReAct paradigm?**
   *A*: Thought (natural language reasoning decomposition), Action (structured tool execution), and Observation (environment feedback returned to the model).

2. **Q: Why are standard DAG engines like Apache Airflow insufficient for autonomous AI agents?**
   *A*: DAGs are strictly acyclic; agents inherently require cyclic loops to retry failed actions, self-correct arguments, and dynamically iterate until goal criteria are met.

3. **Q: What is a State Reducer in LangGraph?**
   *A*: A function that specifies how new updates from nodes are merged into the central state (e.g. appending messages to a list rather than overwriting the entire list).

4. **Q: How does Checkpointing enable Human-in-the-Loop (HITL) workflows?**
   *A*: It serializes the exact state snapshot to a database (PostgreSQL/Redis) and pauses execution cleanly, allowing a human supervisor to approve or edit state before the graph resumes.

5. **Q: What is Time-Travel debugging in stateful agent graphs?**
   *A*: Loading a prior checkpoint snapshot `(thread_id, checkpoint_k)`, modifying the state or message history, and branching forward along an alternative execution path.

6. **Q: What is the risk of a "God Agent" with 40+ registered tools?**
   *A*: Extreme hallucination rates, tool selection confusion, and parameter errors due to context dilution. Mitigated by using a Supervisor with specialized sub-agents.

7. **Q: How do you prevent infinite loops in an autonomous agent?**
   *A*: Enforce a hard `max_iterations` counter and implement circuit breakers that detect duplicate consecutive `(action, args)` signatures.

8. **Q: What is the Reflexion architecture?**
   *A*: An agent pattern where the model verbally critiques its own output against unit tests or evaluation criteria and stores the reflection in memory to guide subsequent attempts.

9. **Q: Why should HITL gates never use `time.sleep()` in worker processes?**
   *A*: Blocking sleep holds expensive worker threads, risks timeout eviction by Kubernetes/Celery, and crashes lose all in-memory state. HITL must be event-driven via persistent checkpoints.

10. **Q: What is the difference between a static Edge and a Conditional Edge in LangGraph?**
    *A*: A static edge always transitions from Node A to Node B; a conditional edge evaluates a router function over the current state to dynamically decide the next node (or `END`).
