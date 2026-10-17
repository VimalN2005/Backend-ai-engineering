# Day 23: Practical Hands-On DSA Challenges

Master Binary Tree Recursion, Subtree Aggregations, and AST Traversal Mechanics with these 3 production-level challenges.

---

## Challenge 1: Binary Tree Maximum Path Sum ($\mathcal{O}(N)$ Postorder Divide-and-Conquer)

### Objective
In network topology routing and distributed computing DAGs, calculating the maximum throughput path between any two interconnected nodes is a fundamental problem. A **path** in a binary tree is any sequence of nodes from some starting node to any node along parent-child connections. A node can only appear in the sequence at most once, and the path does **not** need to pass through the root.

Given the root of a binary tree, return the **maximum path sum** of any non-empty path.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$ where $N$ is the number of nodes.
2. **Space Complexity**: $\mathcal{O}(H)$ where $H$ is the tree height.
3. **Subtree vs. Global Path Invariant**:
   - At each node `curr`, a path could potentially peak at `curr` (combining `curr.val + left_branch + right_branch`). This path cannot be extended further up to `curr`'s parent!
   - For `curr` to contribute to its parent's path, it can only pick **one** child branch: `curr.val + max(left_branch, right_branch)`.
   - Any branch contributing a negative sum should be pruned: `max(0, branch_sum)`.
4. **Test Cases**:
   - `[1, 2, 3]` $\to$ `6` (Path: `2 -> 1 -> 3`)
   - `[-10, 9, 20, None, None, 15, 7]` $\to$ `42` (Path: `15 -> 20 -> 7`)
   - `[-3]` $\to$ `-3`
   - `[2, -1]` $\to$ `2`

---

## Challenge 2: Lowest Common Ancestor (LCA) in General Binary Tree

### Objective
In enterprise Role-Based Access Control (RBAC), organizational reporting hierarchies, and Git version control (`git merge-base`), finding the lowest common ancestor of two entities determines the nearest shared authority or common commit ancestor.

Given a binary tree and two distinct nodes `p` and `q`, find their Lowest Common Ancestor (LCA). According to the definition of LCA on Wikipedia: "The lowest common ancestor is defined between two nodes `p` and `q` as the lowest node in `T` that has both `p` and `q` as descendants (where we allow a node to be a descendant of itself)."

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$.
2. **Space Complexity**: $\mathcal{O}(H)$ auxiliary call-stack space.
3. **Divide-and-Conquer Postorder Invariant**:
   - Base Case: If `root is None` or `root == p` or `root == q`, return `root`.
   - Recurse on left and right subtrees:
     ```python
     left = lowest_common_ancestor(root.left, p, q)
     right = lowest_common_ancestor(root.right, p, q)
     ```
   - If both `left` and `right` return non-null, `root` is the split point and therefore the LCA!
   - If only one child returns non-null, propagate that non-null node upward.
4. **Test Cases**:
   - Tree: `[3, 5, 1, 6, 2, 0, 8, None, None, 7, 4]`
     - `p = 5`, `q = 1` $\to$ Returns node `3`
     - `p = 5`, `q = 4` $\to$ Returns node `5` (since node 5 is an ancestor of 4)
     - `p = 7`, `q = 8` $\to$ Returns node `3`

---

## Challenge 3: Lossless Binary Tree Serialization & Deserialization

### Objective
In distributed microservices and caching layers (Redis, Memcached), in-memory binary trees (such as AST query filters or decision tree models) must be serialized to compact strings for network transmission and deserialized losslessly on worker nodes.

Design an algorithm to serialize a binary tree into a string and deserialize that string back into the original binary tree.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$ for both `serialize` and `deserialize`.
2. **Space Complexity**: $\mathcal{O}(N)$ for the serialized string and traversal structures.
3. **Encoding Protocol**:
   - Use Preorder Traversal (`Root -> Left -> Right`) with a delimiter (e.g. `","`).
   - Represent null child pointers with a distinct sentinel token (e.g. `"#"`).
   - Deserialization reads tokens sequentially (e.g. using an iterator or queue), recreating nodes and wiring `node.left` and `node.right` recursively.
4. **Test Cases**:
   - Input: `[1, 2, 3, None, None, 4, 5]`
     - Serialized string: `"1,2,#,#,3,4,#,#,5,#,#"`
     - Deserialization restores exact tree topology.
   - Input: `None`
     - Serialized string: `"#"`
     - Deserialization returns `None`.
   - Input: `[1]`
     - Serialized string: `"1,#,#"`
     - Deserialization returns `TreeNode(1)`.
