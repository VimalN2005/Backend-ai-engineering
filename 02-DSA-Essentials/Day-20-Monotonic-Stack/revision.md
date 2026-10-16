# Day 20: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to Monotonic Stacks, Invariant Mechanics, and Boundary Optimization for backend engineers.

---

## 1. Monotonic Stack Decision Matrix

| Target Objective | Required Stack Monotonicity | Stack Eviction Condition | Top Element Meaning |
| :--- | :--- | :--- | :--- |
| **Next Greater Element (NGE)** | **Monotonic Decreasing** | `incoming > stack[-1]` | Smallest unresolved value waiting for a greater number |
| **Next Smaller Element (NSE)** | **Monotonic Increasing** | `incoming < stack[-1]` | Largest unresolved value waiting for a smaller number |
| **Previous Greater Element (PGE)** | **Monotonic Decreasing** | `stack[-1] <= incoming` (pop smaller) | Nearest previous value greater than current |
| **Previous Smaller Element (PSE)** | **Monotonic Increasing** | `stack[-1] >= incoming` (pop larger) | Nearest previous value smaller than current |
| **Largest Rectangle in Histogram** | **Monotonic Increasing** | `extended[stack[-1]] > incoming` | Bar height bounded left by `stack[-1]` & right by `i` |

---

## 2. Core Architectural Rules

1. **Always Store Indices, Not Values**:
   - Gives constant-time value lookup: `arr[stack[-1]]`
   - Gives constant-time distance/span calculation: `current_idx - prev_idx`
   - Immune to duplicate value ambiguities in hash tables.
2. **The Elimination Theorem**:
   - When an incoming element $A[i]$ exceeds $A[\text{top}]$, $A[i]$ shadows $A[\text{top}]$ for all future queries $j > i$.
   - $A[\text{top}]$ can be permanently pruned in $\mathcal{O}(1)$ time.
3. **Amortized $\mathcal{O}(1)$ Aggregate Proof**:
   - Total Pushes $= N$. Total Pops $\le N$. Total Operations $\le 2N = \mathcal{O}(N)$.
4. **Virtual $2N$ Modulo Unrolling for Circular Buffers**:
   - Loop `for i in range(2 * n): val = nums[i % n]`.
   - Never duplicate arrays (`nums + nums`); avoid heap allocation and GC pauses.
5. **Sentinel Flush for Histograms**:
   - Append `[0]` sentinel to heights to automatically flush all remaining bars without duplicate cleanup code.

---

## 3. High-Yield Code Templates

### Template A: Next Greater Element / Daily Temperatures ($\mathcal{O}(N)$ Time)
```python
def daily_temperatures(temperatures: list[int]) -> list[int]:
    n = len(temperatures)
    result = [0] * n
    stack = []  # Monotonic Decreasing Stack of INDICES

    for curr_idx, curr_temp in enumerate(temperatures):
        while stack and temperatures[stack[-1]] < curr_temp:
            prev_idx = stack.pop()
            result[prev_idx] = curr_idx - prev_idx  # Span distance
        stack.append(curr_idx)

    return result
```

### Template B: Circular Next Greater Element ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Aux Memory)
```python
def next_greater_circular(nums: list[int]) -> list[int]:
    n = len(nums)
    result = [-1] * n
    stack = []  # Stores indices 0 .. n-1

    for i in range(2 * n):
        val = nums[i % n]
        while stack and nums[stack[-1]] < val:
            result[stack.pop()] = val
        if i < n:
            stack.append(i)

    return result
```

### Template C: Largest Rectangle in Histogram ($\mathcal{O}(N)$ Time with Sentinel)
```python
def largest_rectangle(heights: list[int]) -> int:
    extended = heights + [0]  # 0 sentinel forces complete flush
    stack = []  # Monotonic Increasing Stack of INDICES
    max_area = 0

    for i, h in enumerate(extended):
        while stack and extended[stack[-1]] > h:
            popped_idx = stack.pop()
            height = extended[popped_idx]
            width = i if not stack else i - stack[-1] - 1
            max_area = max(max_area, height * width)
        stack.append(i)

    return max_area
```

---

## 4. Complexity Quick Reference

| Problem | Time Complexity | Auxiliary Space | Key Mechanism |
| :--- | :--- | :--- | :--- |
| **Next Greater Element I** | $\mathcal{O}(N + M)$ | $\mathcal{O}(N)$ | Monotonic Decreasing Stack + Hash Map |
| **Daily Temperatures** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Index-based Monotonic Decreasing Stack |
| **Next Greater Element II** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Virtual $2N$ Modulo Unrolling (`i % n`) |
| **Largest Rectangle in Histogram** | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ | Monotonic Increasing Stack + Sentinel `0` |
| **Maximal Rectangle in 2D Matrix**| $\mathcal{O}(R \times C)$ | $\mathcal{O}(C)$ | Dynamic 1D Histogram over 2D Rows |
| **Online Stock Spanner** | Amortized $\mathcal{O}(1)$ | $\mathcal{O}(N)$ | Monotonic Decreasing Stack of `(price, span)` |
