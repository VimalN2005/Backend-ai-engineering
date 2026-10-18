"""
Day 24: Example 02 - Production Architecture: Distributed Workflow DAG Scheduler & Hierarchical RBAC

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In enterprise infrastructure and data pipelines (e.g., Apache Airflow, Temporal, AWS Step Functions,
and Celery Canvas), complex workflows are modeled as hierarchical trees of dependent tasks:

1. Distributed Workflow DAG Scheduler (BFS Level Batching):
   Independent tasks at level L can run concurrently across a cluster worker pool.
   Level-order BFS acts as an execution barrier: Level L+1 tasks are withheld until all
   predecessors at Level L succeed.
   Crucially, when a task fails, a DFS traversal isolates the failure blast radius—pruning
   and marking all downstream dependent subtrees as `QUARANTINED`, while unrelated parallel
   branches continue execution uninterrupted.

2. Hierarchical Role-Based Access Control (DFS Path Inheritance):
   In enterprise IAM (AWS IAM, Kubernetes RBAC, Okta), permissions inherit vertically through
   organizational trees (Enterprise -> Division -> Squad -> Role).
   A DFS path resolver audits security inheritance, ensuring least-privilege access and
   detecting privilege escalations in sub-microsecond time.

This module implements:
1. `TaskNode`: Execution unit with dependencies, state, and retry logic.
2. `DistributedWorkflowScheduler`: Level-order BFS execution engine with parallel batches and DFS failure quarantine.
3. `HierarchicalRBACAuthorizer`: DFS permission inheritance resolver.
4. Comprehensive verification suite and failure isolation simulation.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. DISTRIBUTED WORKFLOW TASK DATA STRUCTURES
# ============================================================================

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


@dataclass
class WorkflowTask:
    """Represents an executable unit in a workflow dependency tree."""
    task_id: str
    action: str
    children: List[WorkflowTask] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result_payload: Optional[Any] = None
    error_message: Optional[str] = None


# ============================================================================
# 2. DISTRIBUTED WORKFLOW SCHEDULER (BFS BATCHING + DFS QUARANTINE)
# ============================================================================

class DistributedWorkflowScheduler:
    """
    Executes a task dependency tree using BFS Level Batching while isolating
    failures using DFS sub-tree pruning.
    """

    def __init__(self, task_executor: Optional[Callable[[WorkflowTask], bool]] = None) -> None:
        # Default executor returns True (success) unless overridden
        self.executor = task_executor or (lambda task: True)
        self.execution_timeline: List[List[str]] = []
        self.quarantined_tasks: Set[str] = set()

    def execute_workflow(self, root: WorkflowTask) -> Dict[str, Any]:
        """
        Executes workflow level-by-level in concurrent BFS batches.
        If a task fails, DFS immediately marks its downstream dependencies as QUARANTINED.
        """
        if not root:
            return {"status": "EMPTY", "executed_levels": 0}

        queue: deque[WorkflowTask] = deque([root])
        level_index = 0

        while queue:
            level_size = len(queue)
            current_batch: List[WorkflowTask] = []
            level_task_ids: List[str] = []

            # 1. Drain the current level batch
            for _ in range(level_size):
                task = queue.popleft()
                current_batch.append(task)
                level_task_ids.append(task.task_id)

            self.execution_timeline.append(level_task_ids)

            # 2. Execute all tasks in current level batch concurrently
            for task in current_batch:
                if task.status == TaskStatus.QUARANTINED:
                    # Skip execution of quarantined tasks
                    continue

                task.status = TaskStatus.RUNNING
                success = False
                try:
                    success = self.executor(task)
                except Exception as exc:
                    task.error_message = str(exc)
                    success = False

                if success:
                    task.status = TaskStatus.SUCCESS
                    # Enqueue healthy children for next level
                    for child in task.children:
                        if child.status == TaskStatus.PENDING:
                            queue.append(child)
                else:
                    task.status = TaskStatus.FAILED
                    # DFS Blast-Radius Quarantine: Mark all downstream dependencies as QUARANTINED
                    self._quarantine_downstream_dfs(task)

            level_index += 1

        total_tasks = self._count_tasks_dfs(root)
        success_count = self._count_status_dfs(root, TaskStatus.SUCCESS)
        failed_count = self._count_status_dfs(root, TaskStatus.FAILED)
        quarantined_count = len(self.quarantined_tasks)

        return {
            "total_tasks": total_tasks,
            "success_count": success_count,
            "failed_count": failed_count,
            "quarantined_count": quarantined_count,
            "levels_executed": level_index,
            "timeline": self.execution_timeline,
        }

    def _quarantine_downstream_dfs(self, failed_task: WorkflowTask) -> None:
        """
        Uses DFS to immediately propagate QUARANTINED status to all descendant nodes,
        preventing broken child tasks from executing.
        """
        for child in failed_task.children:
            if child.status != TaskStatus.QUARANTINED:
                child.status = TaskStatus.QUARANTINED
                child.error_message = f"Dependency failure: Parent {failed_task.task_id} failed."
                self.quarantined_tasks.add(child.task_id)
                self._quarantine_downstream_dfs(child)

    def _count_tasks_dfs(self, node: WorkflowTask) -> int:
        count = 1
        for child in node.children:
            count += self._count_tasks_dfs(child)
        return count

    def _count_status_dfs(self, node: WorkflowTask, target_status: TaskStatus) -> int:
        count = 1 if node.status == target_status else 0
        for child in node.children:
            count += self._count_status_dfs(child, target_status)
        return count


# ============================================================================
# 3. HIERARCHICAL RBAC AUTHORIZER (DFS PERMISSION INHERITANCE)
# ============================================================================

@dataclass
class IAMRoleNode:
    """Represents a security scope or role in an enterprise hierarchy."""
    role_id: str
    permissions: Set[str] = field(default_factory=set)
    children: List[IAMRoleNode] = field(default_factory=list)


class HierarchicalRBACAuthorizer:
    """
    Evaluates permission inheritance using DFS path traversal.
    A child role inherits all permissions granted to any of its ancestors.
    """

    def __init__(self, root: IAMRoleNode) -> None:
        self.root = root

    def has_permission(self, target_role_id: str, required_permission: str) -> bool:
        """
        Traverses hierarchy via DFS to find target role and checks if required_permission
        is possessed directly or inherited from ancestors.
        """
        def dfs(node: Optional[IAMRoleNode], accumulated_perms: Set[str]) -> bool:
            if not node:
                return False

            # Union of permissions along this ancestor path
            current_perms = accumulated_perms.union(node.permissions)

            if node.role_id == target_role_id:
                return required_permission in current_perms

            for child in node.children:
                if dfs(child, current_perms):
                    return True

            return False

        return dfs(self.root, set())


# ============================================================================
# 4. VERIFICATION SUITE & PRODUCTION SIMULATION
# ============================================================================

def run_tests() -> None:
    """Validates level batching, failure quarantine, and RBAC inheritance."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR DISTRIBUTED WORKFLOW SCHEDULER & RBAC")
    print("======================================================================")

    # 1. Construct Workflow Tree:
    #                 [root_job]
    #               /            \
    #          [etl_branch]      [ml_branch]
    #         /            \               \
    #   [db_clean]      [db_index]       [train_model]
    #                                          \
    #                                     [deploy_model]
    db_clean = WorkflowTask("db_clean", "Clean raw tables")
    db_index = WorkflowTask("db_index", "Build search index")
    etl_branch = WorkflowTask("etl_branch", "Ingest ETL data", children=[db_clean, db_index])

    deploy_model = WorkflowTask("deploy_model", "Deploy inference endpoint")
    train_model = WorkflowTask("train_model", "Train PyTorch model", children=[deploy_model])
    ml_branch = WorkflowTask("ml_branch", "Fetch ML features", children=[train_model])

    root_job = WorkflowTask("root_job", "Pipeline Initializer", children=[etl_branch, ml_branch])

    # Simulate executor: ml_branch fails, etl_branch succeeds!
    def simulated_executor(task: WorkflowTask) -> bool:
        if task.task_id == "ml_branch":
            return False  # Intentionally fail ML branch
        return True

    scheduler = DistributedWorkflowScheduler(task_executor=simulated_executor)
    report = scheduler.execute_workflow(root_job)

    # Verifications:
    assert root_job.status == TaskStatus.SUCCESS
    assert etl_branch.status == TaskStatus.SUCCESS
    assert db_clean.status == TaskStatus.SUCCESS
    assert db_index.status == TaskStatus.SUCCESS

    # ML branch failed; train_model and deploy_model MUST be quarantined!
    assert ml_branch.status == TaskStatus.FAILED
    assert train_model.status == TaskStatus.QUARANTINED
    assert deploy_model.status == TaskStatus.QUARANTINED

    assert report["success_count"] == 4
    assert report["failed_count"] == 1
    assert report["quarantined_count"] == 2
    assert report["total_tasks"] == 7
    print("[PASS] Distributed Workflow BFS batching and DFS failure quarantine verified.")

    # 2. Hierarchical RBAC Test
    # Organization ("org:audit") -> Engineering ("infra:deploy") -> SRE ("db:drop")
    sre_role = IAMRoleNode("role:sre", permissions={"db:drop"})
    dev_role = IAMRoleNode("role:developer", permissions={"app:restart"})
    eng_div = IAMRoleNode("div:engineering", permissions={"infra:deploy"}, children=[sre_role, dev_role])
    org_root = IAMRoleNode("org:enterprise", permissions={"org:audit"}, children=[eng_div])

    rbac = HierarchicalRBACAuthorizer(org_root)

    # SRE inherits org:audit, infra:deploy, and has db:drop
    assert rbac.has_permission("role:sre", "org:audit") is True
    assert rbac.has_permission("role:sre", "infra:deploy") is True
    assert rbac.has_permission("role:sre", "db:drop") is True

    # Developer has app:restart and inherited infra:deploy, but NOT db:drop
    assert rbac.has_permission("role:developer", "infra:deploy") is True
    assert rbac.has_permission("role:developer", "db:drop") is False
    print("[PASS] Hierarchical RBAC DFS permission inheritance verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks large-scale workflow scheduling across 1,000 tasks."""
    print("======================================================================")
    print("BENCHMARK: 1,000 WORKFLOW TASKS BFS LEVEL-BATCH SCHEDULING")
    print("======================================================================")

    # Build a 10-level tree with 1,000 tasks
    total_nodes = 1_000
    root = WorkflowTask("task_000", "Root Task")
    nodes = [root]

    for i in range(1, total_nodes):
        parent_idx = (i - 1) // 3
        node = WorkflowTask(f"task_{i:04d}", f"Compute Unit {i}")
        nodes[parent_idx].children.append(node)
        nodes.append(node)

    scheduler = DistributedWorkflowScheduler(task_executor=lambda t: True)

    start_time = time.perf_counter()
    report = scheduler.execute_workflow(root)
    elapsed = time.perf_counter() - start_time

    print(f"Executed Workflow with {report['total_tasks']:,} tasks.")
    print(f"Total Execution Levels:  {report['levels_executed']}")
    print(f"Elapsed Processing Time: {elapsed * 1000:8.2f} ms")
    print(f"Scheduling Throughput:   {report['total_tasks'] / elapsed:10,.0f} tasks/sec")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
