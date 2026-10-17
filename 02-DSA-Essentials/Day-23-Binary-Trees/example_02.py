"""
Day 23: Example 02 - Production Architecture: AST Query Engine & Decision Tree Classifier

WHY THIS MATTERS IN PRODUCTION BACKEND & AI SYSTEMS:
In enterprise backend frameworks (e.g., Django Q objects, Open Policy Agent, SQL parsers)
and machine learning systems (e.g., XGBoost, LightGBM, scikit-learn decision trees),
complex decision logic is represented as a Binary Tree:

1. Abstract Syntax Tree (AST) Filter Compiler:
   Dynamic authorization (ABAC) and API gateway routing rules express conditions like:
       (status == 200 AND latency < 250) OR (role == 'admin' AND is_internal == True)
   Evaluating these expressions using `eval()` or naive string regexes is insecure and slow.
   Compiling expressions into a Binary AST allows Postorder Bottom-Up evaluation in pure
   sub-microsecond time, with zero code injection vulnerability.

2. Machine Learning Binary Decision Tree Classifier:
   In low-latency AI inference (e.g. fraud scoring, risk assessment at 500,000 req/sec),
   compiled binary decision trees evaluate feature splits (feature <= threshold) using
   preorder/inorder branch routing, predicting class probabilities with near-zero latency.

This module implements:
1. `ASTNode`: Abstract syntax tree hierarchy supporting Logical Operators and Comparison Predicates.
2. `ASTFilterEngine`: Postorder bottom-up boolean query evaluator with rule compilation.
3. `BinaryDecisionTree`: High-speed binary tree classifier evaluating tabular feature vectors.
4. Comprehensive verification suite and 100,000-event telemetry evaluation benchmark.
"""

from __future__ import annotations

import operator
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# ============================================================================
# 1. ABSTRACT SYNTAX TREE (AST) DATA STRUCTURES
# ============================================================================

class BaseASTNode(ABC):
    """Abstract base class for all AST nodes."""
    @abstractmethod
    def evaluate(self, context: Dict[str, Any]) -> bool:
        """Evaluates node against incoming request context."""
        pass


@dataclass(frozen=True)
class ComparisonPredicateNode(BaseASTNode):
    """
    Leaf node representing an atomic comparison: context[field] <op> target_value.
    Example: latency_ms < 250.0 or role == 'admin'.
    """
    field_name: str
    comparator: str
    target_value: Any

    def evaluate(self, context: Dict[str, Any]) -> bool:
        if self.field_name not in context:
            return False  # Missing attribute evaluates to False

        actual_value = context[self.field_name]

        if self.comparator == "==":
            return actual_value == self.target_value
        elif self.comparator == "!=":
            return actual_value != self.target_value
        elif self.comparator == "<":
            return actual_value < self.target_value
        elif self.comparator == "<=":
            return actual_value <= self.target_value
        elif self.comparator == ">":
            return actual_value > self.target_value
        elif self.comparator == ">=":
            return actual_value >= self.target_value
        elif self.comparator == "in":
            return actual_value in self.target_value
        else:
            raise ValueError(f"Unsupported comparator: {self.comparator}")


class LogicalOperatorNode(BaseASTNode):
    """
    Internal binary branch node representing logical AND / OR operators.
    Uses Postorder Bottom-Up evaluation: evaluates left and right children first.
    """
    __slots__ = ("operator", "left", "right")

    def __init__(
        self,
        operator: str,
        left: BaseASTNode,
        right: BaseASTNode,
    ) -> None:
        if operator not in ("AND", "OR"):
            raise ValueError(f"Invalid logical operator: {operator}")
        self.operator = operator
        self.left = left
        self.right = right

    def evaluate(self, context: Dict[str, Any]) -> bool:
        # Short-circuit postorder evaluation for performance
        left_val = self.left.evaluate(context)
        
        if self.operator == "AND":
            if not left_val:
                return False  # Short-circuit AND
            return self.right.evaluate(context)
        elif self.operator == "OR":
            if left_val:
                return True   # Short-circuit OR
            return self.right.evaluate(context)

        return False


# ============================================================================
# 2. AST FILTER COMPILER & EXECUTION ENGINE
# ============================================================================

class ASTFilterEngine:
    """
    High-performance rule engine that compiles and executes binary AST filters.
    """

    def __init__(self, root: BaseASTNode) -> None:
        self.root = root

    def matches(self, context: Dict[str, Any]) -> bool:
        """Evaluates whether the given context satisfies the compiled AST filter."""
        return self.root.evaluate(context)

    def count_nodes(self) -> int:
        """Returns total number of AST nodes using Postorder traversal."""
        def postorder_count(node: BaseASTNode) -> int:
            if isinstance(node, ComparisonPredicateNode):
                return 1
            elif isinstance(node, LogicalOperatorNode):
                return 1 + postorder_count(node.left) + postorder_count(node.right)
            return 0
        return postorder_count(self.root)


# ============================================================================
# 3. MACHINE LEARNING BINARY DECISION TREE CLASSIFIER
# ============================================================================

class DecisionNode:
    """
    Internal binary split node in a Decision Tree: feature <= threshold.
    """
    __slots__ = ("feature", "threshold", "left", "right")

    def __init__(
        self,
        feature: str,
        threshold: float,
        left: Union[DecisionNode, LeafNode],
        right: Union[DecisionNode, LeafNode],
    ) -> None:
        self.feature = feature
        self.threshold = threshold
        self.left = left    # Branch taken if feature <= threshold
        self.right = right  # Branch taken if feature > threshold


class LeafNode:
    """Terminal node representing class prediction and probability."""
    __slots__ = ("prediction", "confidence")

    def __init__(self, prediction: str, confidence: float) -> None:
        self.prediction = prediction
        self.confidence = confidence


class BinaryDecisionTree:
    """
    Ultra-fast tabular decision tree classifier (modeled after tree estimators in XGBoost).
    """

    def __init__(self, root: Union[DecisionNode, LeafNode]) -> None:
        self.root = root

    def predict(self, features: Dict[str, float]) -> Tuple[str, float]:
        """
        Traverses decision branches from root to leaf in O(H) time.
        """
        curr = self.root
        while isinstance(curr, DecisionNode):
            feature_val = features.get(curr.feature, 0.0)
            if feature_val <= curr.threshold:
                curr = curr.left
            else:
                curr = curr.right

        # Reached leaf
        return curr.prediction, curr.confidence


# ============================================================================
# 4. VERIFICATION SUITE & PRODUCTION SIMULATION
# ============================================================================

def run_tests() -> None:
    """Validates AST compilation, boolean short-circuiting, and decision tree inference."""
    print("======================================================================")
    print("RUNNING UNIT TESTS FOR AST QUERY ENGINE & DECISION TREE")
    print("======================================================================")

    # 1. Construct AST:
    # (status == 200 AND latency_ms < 300) OR role == 'admin'
    cond1 = ComparisonPredicateNode("status", "==", 200)
    cond2 = ComparisonPredicateNode("latency_ms", "<", 300.0)
    branch_and = LogicalOperatorNode("AND", cond1, cond2)
    cond3 = ComparisonPredicateNode("role", "==", "admin")
    ast_root = LogicalOperatorNode("OR", branch_and, cond3)

    engine = ASTFilterEngine(ast_root)
    assert engine.count_nodes() == 5

    # Test cases:
    ctx1 = {"status": 200, "latency_ms": 150.0, "role": "user"}
    assert engine.matches(ctx1) is True, "Should match: 200 and latency 150"

    ctx2 = {"status": 500, "latency_ms": 150.0, "role": "admin"}
    assert engine.matches(ctx2) is True, "Should match via admin role"

    ctx3 = {"status": 200, "latency_ms": 450.0, "role": "user"}
    assert engine.matches(ctx3) is False, "Should fail: latency too high and not admin"
    print("[PASS] AST Filter Engine postorder evaluation verified.")

    # 2. Binary Decision Tree Classifier: Fraud Detector
    # Split: risk_score <= 0.70
    #   ├── Left (risk <= 0.70):
    #   │     Split: tx_amount <= 5000.0
    #   │       ├── Left  -> Leaf('APPROVE', 0.98)
    #   │       └── Right -> Leaf('MANUAL_REVIEW', 0.85)
    #   └── Right (risk > 0.70):
    #         Leaf('REJECT', 0.99)
    dt_root = DecisionNode(
        feature="risk_score",
        threshold=0.70,
        left=DecisionNode(
            feature="tx_amount",
            threshold=5000.0,
            left=LeafNode("APPROVE", 0.98),
            right=LeafNode("MANUAL_REVIEW", 0.85),
        ),
        right=LeafNode("REJECT", 0.99),
    )
    dt = BinaryDecisionTree(dt_root)

    pred1, conf1 = dt.predict({"risk_score": 0.25, "tx_amount": 1200.0})
    assert pred1 == "APPROVE" and conf1 == 0.98

    pred2, conf2 = dt.predict({"risk_score": 0.50, "tx_amount": 8000.0})
    assert pred2 == "MANUAL_REVIEW" and conf2 == 0.85

    pred3, conf3 = dt.predict({"risk_score": 0.95, "tx_amount": 50.0})
    assert pred3 == "REJECT" and conf3 == 0.99
    print("[PASS] Binary Decision Tree Classifier verified.")

    print("All unit tests passed successfully!\n")


def run_benchmark() -> None:
    """Benchmarks AST filter evaluation over 100,000 real-time request events."""
    print("======================================================================")
    print("BENCHMARK: 100,000 AST FILTER EVALUATIONS IN PURE PYTHON")
    print("======================================================================")

    # Compile production filter:
    # (status == 200 AND latency_ms < 200.0) OR (is_internal == True AND role in ['admin', 'sre'])
    ast = LogicalOperatorNode(
        "OR",
        LogicalOperatorNode(
            "AND",
            ComparisonPredicateNode("status", "==", 200),
            ComparisonPredicateNode("latency_ms", "<", 200.0),
        ),
        LogicalOperatorNode(
            "AND",
            ComparisonPredicateNode("is_internal", "==", True),
            ComparisonPredicateNode("role", "in", {"admin", "sre"}),
        ),
    )
    engine = ASTFilterEngine(ast)

    events_count = 100_000
    # Pre-generate telemetry events
    events = [
        {
            "status": 200 if i % 2 == 0 else 500,
            "latency_ms": 100.0 if i % 3 == 0 else 350.0,
            "is_internal": (i % 5 == 0),
            "role": "admin" if i % 7 == 0 else "user",
        }
        for i in range(events_count)
    ]

    start_time = time.perf_counter()
    matches_count = 0
    for evt in events:
        if engine.matches(evt):
            matches_count += 1
    elapsed = time.perf_counter() - start_time

    throughput = events_count / elapsed if elapsed > 0 else 0.0

    print(f"Total Evaluated Events:  {events_count:,}")
    print(f"Total Matched Events:    {matches_count:,}")
    print(f"Elapsed Execution Time:  {elapsed * 1000:8.2f} ms")
    print(f"Engine Throughput:       {throughput:10,.0f} evaluations/sec")
    print(f"Average Decision Time:   {(elapsed / events_count) * 1_000_000:8.4f} microseconds/eval")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
    run_benchmark()
