# Day 23: Senior Backend Interview Mastery - Binary Trees & AST Compilation

Master these 5 senior-level technical interview questions covering traversal uniqueness theorems, AST bottom-up query evaluation, Morris threaded tree mechanics, CPython call stack frame limits, and path bifurcation invariants.

---

### Q1: Why does uniquely reconstructing a binary tree require the Inorder traversal alongside Preorder or Postorder? Why does Preorder + Postorder fail to produce a unique tree?

#### Answer:

A single traversal sequence (or the combination of Preorder and Postorder alone) is mathematically insufficient to reconstruct a unique binary tree.

#### 1. Why Inorder is Non-Negotiable:
- In **Preorder** (`Root -> Left -> Right`), the first element is the root.
- In **Postorder** (`Left -> Right -> Root`), the last element is the root.
- However, neither Preorder nor Postorder reveals where the **Left Subtree ends** and the **Right Subtree begins**.
- **Inorder** (`Left -> Root -> Right`) provides the directional separation:
  Once the root value is located in the Inorder sequence, all elements to the left of the root **MUST** belong to the Left Subtree, and all elements to the right **MUST** belong to the Right Subtree:
  $$\underbrace{L_1, L_2, \dots, L_k}_{\text{Left Subtree}} \quad \big| \quad \text{ROOT} \quad \big| \quad \underbrace{R_1, R_2, \dots, R_m}_{\text{Right Subtree}}$$

#### 2. Why Preorder + Postorder Fails:
Consider two different trees with single-child nodes:

```
Tree A (Left child):      Tree B (Right child):
        1                         1
       /                           \
      2                             2
```
- For Tree A:
  - Preorder: `[1, 2]`
  - Postorder: `[2, 1]`
- For Tree B:
  - Preorder: `[1, 2]`
  - Postorder: `[2, 1]`

Both trees produce **identical** Preorder and Postorder traversals! Without Inorder, it is impossible to determine whether node `2` is a left child or a right child.
*(Exception: If the binary tree is guaranteed to be a Full Binary Tree where every node has either 0 or 2 children, Preorder + Postorder can reconstruct the tree uniquely).*

---

### Q2: How do you evaluate an Abstract Syntax Tree (AST) in an enterprise query engine or rule evaluator? Why is Postorder Traversal used instead of Inorder or Preorder?

#### Answer:

Modern query engines (e.g., PostgreSQL query planners, Django ORM filter compilation, Open Policy Agent) parse boolean and arithmetic expressions into an **Abstract Syntax Tree (AST)**:

```
Query: (status == 200 AND latency < 250) OR role == 'admin'

                    [ Operator: OR ]
                   /                \
        [ Operator: AND ]     [ Predicate: role == 'admin' ]
       /                 \
[ Pred: status == 200 ]  [ Pred: latency < 250 ]
```

#### 1. The Necessity of Postorder Traversal:
In an AST:
- **Leaf Nodes** are atomic predicates or operands (`status == 200`, `latency < 250`).
- **Internal Nodes** are operators (`AND`, `OR`, `+`, `*`).
An operator node cannot compute its result until **both of its child operands have been evaluated**:
$$\text{Result}(\text{AND}) = \text{Evaluate}(\text{Left}) \land \text{Evaluate}(\text{Right})$$
This dependency graph dictates a **Bottom-Up Postorder Traversal** (`Left -> Right -> Root`):
1. Evaluate left child predicate.
2. Evaluate right child predicate.
3. Apply parent operator to the children's results and bubble the boolean up.

#### 2. Short-Circuiting in Production Engines:
While mathematically postorder, production AST engines implement **lazy short-circuit evaluation**:
- For an `AND` operator: If the left subtree evaluates to `False`, the right subtree is never evaluated.
- For an `OR` operator: If the left subtree evaluates to `True`, the right subtree is skipped.
This prevents unnecessary database queries or expensive attribute lookups in complex rule graphs.

---

### Q3: Explain Morris Traversal and Threaded Binary Trees. How does it achieve $\mathcal{O}(N)$ time and $\mathcal{O}(1)$ auxiliary space without memory allocation?

#### Answer:

Standard recursive and iterative traversals require $\mathcal{O}(H)$ memory ($\mathcal{O}(\log N)$ balanced, $\mathcal{O}(N)$ skewed) to remember the path back to ancestor nodes.

**Morris Traversal** eliminates this memory requirement entirely by temporarily exploiting the **null right pointers of leaf nodes** (creating a Threaded Binary Tree):

#### The Algorithm:
For the current node `curr`:
1. If `curr.left` is `None`:
   - Visit `curr.val`.
   - Advance `curr = curr.right`.
2. If `curr.left` exists:
   - Find `curr`'s **Inorder Predecessor** (the rightmost node in `curr`'s left subtree):
     ```python
     pred = curr.left
     while pred.right and pred.right is not curr:
         pred = pred.right
     ```
   - **Case A (`pred.right is None`)**:
     The predecessor has not been linked yet. Create a temporary thread:
     `pred.right = curr`
     Move `curr = curr.left`.
   - **Case B (`pred.right is curr`)**:
     The thread already exists, meaning the left subtree has been completely visited!
     Sever the thread: `pred.right = None` (restoring tree topology).
     Visit `curr.val`.
     Move `curr = curr.right`.

#### Complexity Proof:
- **Auxiliary Space**: Strictly $\mathcal{O}(1)$. Zero stacks, zero recursion frames, zero memory allocation.
- **Time Complexity**: While finding predecessors seems to repeat work, every edge in the tree is traversed at most **3 times**:
  1. Once when locating the predecessor to build the thread.
  2. Once when traversing down to visit nodes.
  3. Once when locating the predecessor to sever the thread.
  $$\text{Total Operations} \le 3 \times (\text{Edges}) \le 3N = \mathcal{O}(N)$$

---

### Q4: How do CPython's recursion limits and call stack frames affect recursive tree algorithms at scale? How do you transition a recursive divide-and-conquer algorithm into an iterative production system?

#### Answer:

#### 1. CPython Call Stack Mechanics:
In CPython, each function call allocates a `PyFrameObject` on the C runtime stack:
- Memory footprint: ~300 to 500 bytes per frame (local variable dict, instruction pointers, execution stack).
- Safety limit: CPython enforces `sys.getrecursionlimit()` (default **1,000 frames**) to prevent C-level stack overflow crashes (which would trigger an OS segmentation fault `SIGSEGV`).
- If an unbalanced tree with $N = 1{,}500$ nodes is processed recursively, CPython immediately halts execution with `RecursionError`.

#### 2. Transitioning to Iterative Systems:
In enterprise backend services, converting recursive tree traversals to iterative algorithms provides deterministic reliability by moving state from the limited C execution stack to the **unbounded Python heap**:

```
CPython Call Stack (Strict Limit: 1,000 frames) ──► Crashes with RecursionError!
                              VS
Heap-Allocated Python List (Bounded only by RAM) ──► Safely processes 10,000,000+ nodes!
```

#### Universal Explicit Stack Pattern:
Instead of relying on the implicit instruction pointer of the CPU, simulate the call stack using a state tuple `(node, visited_flag)`:
```python
def iterative_universal(root: Optional[TreeNode]) -> list[int]:
    if not root:
        return []
    result = []
    # Stack stores (node, is_visited)
    stack = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if not node:
            continue
        if visited:
            result.append(node.val)  # Process node
        else:
            # For Inorder: push Right, Node (visited=True), Left
            stack.append((node.right, False))
            stack.append((node, True))
            stack.append((node.left, False))
    return result
```

---

### Q5: In the "Binary Tree Maximum Path Sum" problem (LeetCode 124), explain the critical difference between the value returned to the parent node and the value used to update the global maximum path sum.

#### Answer:

A path in a binary tree is defined as a sequence of nodes where each adjacent pair is connected by an edge, and **no node appears more than once**.

#### 1. Path Bifurcation Restriction:
A path **CANNOT bifurcate** (fork) in two directions when continuing upward to a parent:

```
            ( Parent )
                │
             ( Node )   <-- Path can only connect Parent to ONE child branch!
             /      \
        ( Left )  ( Right )

Valid Path passing through Node to Parent:
    Parent -> Node -> Left    OR    Parent -> Node -> Right

Invalid Bifurcated "Path":
    Parent -> Node -> (both Left AND Right)  <-- This is a tree/fork, NOT a path!
```

#### 2. The Dual-Role Invariant:
At every node `curr` during bottom-up postorder traversal:

1. **Role 1: Peak of a Turnaround Path (Global Maximum Update)**:
   A valid path can start in the left subtree, pass through `curr`, and terminate in the right subtree:
   $$\text{Turnaround Path Sum} = \text{curr.val} + \max(0, \text{left\_gain}) + \max(0, \text{right\_gain})$$
   This turnaround path **cannot be extended to `curr`'s parent**, but it represents a complete valid path. We update the global maximum:
   $$\text{global\_max} = \max(\text{global\_max}, \text{Turnaround Path Sum})$$

2. **Role 2: Sub-branch Contributor to Parent (Return Value)**:
   To extend the path upward to `curr`'s parent, `curr` must choose at most **one** child branch:
   $$\text{Return to Parent} = \text{curr.val} + \max(0, \max(\text{left\_gain}, \text{right\_gain}))$$

Failing to separate the global update from the parent return value is the single most common conceptual flaw in recursive tree design.
