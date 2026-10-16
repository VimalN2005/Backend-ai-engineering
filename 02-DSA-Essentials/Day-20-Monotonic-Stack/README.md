# Day 20: Monotonic Stack: Next Greater Element, Daily Temperatures & Histogram Optimization

In high-throughput backend infrastructure, finding the **nearest boundary elements**, calculating **latency spike spans**, and evaluating **server burst capacity** over sequential metrics are frequent computational bottlenecks. A naive nested loop scanning for the next larger or smaller element requires $\mathcal{O}(N^2)$ time—completely unacceptable for real-time telemetry processing millions of metrics per second.

The **Monotonic Stack Pattern** is one of the most powerful and non-obvious algorithmic techniques in computer science. It maintains a stack whose elements are strictly monotonic (strictly increasing or strictly decreasing), reducing quadratic search spaces into **guaranteed linear $\mathcal{O}(N)$ time**.

---

## 1. The Monotonic Stack Paradigm in Backend Systems

Consider finding the next response time spike for every API request in a streaming log:

```
Naive Nested Loops: O(N^2)
For each request i:
    Scan rightward j = i + 1, i + 2 ... until latency[j] > latency[i]
Repeats the scan for every single element! Total: N * (N - 1) / 2 comparisons

Monotonic Stack: O(N)
Single pass. We maintain a stack of pending unresolved requests in descending order.
When a higher latency spike arrives, it resolves ALL smaller pending requests in O(1)!
Every element is pushed ONCE and popped AT MOST ONCE. Total: <= 2N operations.
```

At scale ($N = 100{,}000$ events):
- $\mathcal{O}(N^2)$ requires $5{,}000{,}000{,}000$ operations (~5–10 seconds in Python).
- $\mathcal{O}(N)$ requires $100{,}000$ operations (< 15 milliseconds).

---

## 2. Invariant Mechanics of Monotonic Stacks

A stack is **Monotonic** if its elements maintain a strict sorting order from bottom to top:

```
    MONOTONIC DECREASING STACK                    MONOTONIC INCREASING STACK
      (Bottom -> Top Decreases)                    (Bottom -> Top Increases)
    ┌───────────────────────────┐                ┌───────────────────────────┐
    │  Top:     2               │                │  Top:     9               │
    │           5               │                │           5               │
    │  Bottom:  8               │                │  Bottom:  1               │
    └───────────────────────────┘                └───────────────────────────┘
    Use: Find NEXT GREATER element               Use: Find NEXT SMALLER element
    Invariant: Incoming larger element           Invariant: Incoming smaller element
    evicts smaller elements from top.            evicts larger elements from top.
```

### The Invariant Rule:
- To find the **Next Greater Element (NGE)**: Use a **Monotonic Decreasing Stack**. As you scan left-to-right, any incoming element that is *larger* than `stack[-1]` pops the smaller element, resolving its NGE!
- To find the **Next Smaller Element (NSE)**: Use a **Monotonic Increasing Stack**. Any incoming element that is *smaller* than `stack[-1]` pops the larger element, resolving its NSE!

---

## 3. The Elimination Principle: Mathematical Pruning Invariant

Why can we safely pop elements from the stack without ever needing to look at them again?

**The Elimination Theorem**:
Suppose we are scanning an array searching for the next greater element, and we encounter $A[i] > A[\text{top}]$.
1. $A[i]$ is the **immediate nearest greater element to the right** of $A[\text{top}]$ by definition of sequential iteration.
2. For any future element $A[j]$ (where $j > i$):
   - $A[i]$ is located *closer* to $A[j]$ than $A[\text{top}]$.
   - $A[i]$ is strictly *larger* than $A[\text{top}]$.
   - Therefore, any future element $A[j]$ that could potentially look back to find a greater element will **always encounter $A[i]$ before $A[\text{top}]$**.
   - $A[\text{top}]$ is permanently "shadowed" by $A[i]$!
3. **Conclusion**: $A[\text{top}]$ will never be the answer for any future element. It can be permanently discarded in $\mathcal{O}(1)$ time.

---

## 4. Amortized $\mathcal{O}(1)$ Complexity Proof (The Aggregate Method)

Engineers often mistake the nested `while` loop inside the `for` loop for $\mathcal{O}(N^2)$:

```python
for i in range(len(nums)):          # Outer loop: executes N times
    while stack and nums[stack[-1]] < nums[i]:  # Inner loop
        stack.pop()
    stack.append(i)
```

### The Mathematical Proof:
1. Every index $i \in [0, N - 1]$ is pushed onto the stack **exactly once** in the outer loop:
   $$\text{Total Pushes} = N$$
2. An element can only be popped if it is currently inside the stack.
3. Once an element is popped, it is never added back.
4. Therefore, the total number of `stack.pop()` operations executed by the inner `while` loop across the entire lifetime of the program cannot exceed $N$:
   $$\text{Total Pops} \le N$$
5. Total stack operations across the algorithm:
   $$\text{Total Operations} = \text{Pushes} + \text{Pops} \le N + N = 2N = \mathcal{O}(N)$$
6. The amortized time per element is $\frac{2N}{N} = \mathcal{O}(1)$.

---

## 5. Pattern 1: Next Greater Element (NGE)

Given array `[2, 1, 2, 4, 3]`, find the first greater element to the right of each position (or `-1` if none exists):

```
Index:    0    1    2    3    4
Array:  [ 2,   1,   2,   4,   3 ]

i=0 (val=2): Stack: [0] (val: 2)
i=1 (val=1): 1 < 2. Stack: [0, 1] (vals: 2, 1)
i=2 (val=2): 2 > 1! Pop 1 -> NGE[1] = 2.
             2 <= 2. Stack: [0, 2] (vals: 2, 2)
i=3 (val=4): 4 > 2! Pop 2 -> NGE[2] = 4.
             4 > 2! Pop 0 -> NGE[0] = 4.
             Stack: [3] (val: 4)
i=4 (val=3): 3 < 4. Stack: [3, 4] (vals: 4, 3)
End of Array: Remaining indices 3 and 4 have no greater elements -> -1
Result: [4, 2, 4, -1, -1]
```

---

## 6. Pattern 2: Daily Temperatures (Span & Distance Calculation)

Given an array of daily temperatures `[73, 74, 75, 71, 69, 72, 76, 73]`, return an array `answer` where `answer[i]` is the number of days you have to wait after the $i$-th day to get a warmer temperature.

Instead of storing values, **store array indices on the stack**:
$$\text{Days Waited} = \text{current\_index} - \text{popped\_index}$$

```python
def daily_temperatures(temperatures: list[int]) -> list[int]:
    n = len(temperatures)
    result = [0] * n
    stack = []  # Stores indices of temperatures in decreasing order
    
    for i, temp in enumerate(temperatures):
        while stack and temperatures[stack[-1]] < temp:
            prev_idx = stack.pop()
            result[prev_idx] = i - prev_idx  # Distance formula!
        stack.append(i)
        
    return result
```

---

## 7. Pattern 3: Circular Array Next Greater Element (NGE II)

In circular buffers (e.g. ring buffers, cyclical scheduling), the search wraps around from the end of the array back to the beginning.

```
Array: [1, 2, 1], Circular: [1, 2, 1 | 1, 2, 1]
NGE of index 2 (val 1) wraps around to index 1 (val 2)!
```

### The Virtual Unrolling Trick (Zero Memory Allocation)
Instead of physically concatenating `nums + nums` ($\mathcal{O}(2N)$ memory allocation), iterate the loop from $0$ to $2N - 1$ and use modular indexing:
$$\text{Virtual Index} = i \pmod N$$

```python
def next_greater_elements_circular(nums: list[int]) -> list[int]:
    n = len(nums)
    res = [-1] * n
    stack = []
    
    # Iterate twice through the array virtually
    for i in range(2 * n):
        idx = i % n
        while stack and nums[stack[-1]] < nums[idx]:
            prev_idx = stack.pop()
            res[prev_idx] = nums[idx]
        if i < n:
            stack.append(idx)
            
    return res
```

---

## 8. Pattern 4: Previous Smaller & Next Smaller Boundaries

For optimization problems (like finding maximum rectangular areas or sub-array minimum sum), an element $A[i]$ serves as the bottleneck (minimum) across a contiguous range $[L, R]$:
- Left Boundary $L$: Nearest smaller element to the left.
- Right Boundary $R$: Nearest smaller element to the right.
- Effective Width: $W = R - L - 1$.
- Area Contribution: $A[i] \times (R - L - 1)$.

A single monotonic increasing stack computes both $L$ and $R$ simultaneously during popping!

---

## 9. Pattern 5: Largest Rectangle in Histogram ($\mathcal{O}(N)$ Stack Solution)

Given $N$ non-negative integers representing bar heights of width 1, find the area of the largest rectangle that can be formed within the histogram.

```
Heights: [ 2,  1,  5,  6,  2,  3 ]

          6 │          █
          5 │        █ █
          4 │        █ █
          3 │        █ █     █
          2 │  █     █ █ █   █
          1 │  █ █   █ █ █ █ █
            └──┴─┴───┴─┴─┴─┴─┴──
            0  1   2 3 4 5
Max Rectangle: Bars 2 & 3 (height 5, width 2) -> Area = 10!
```

### The Monotonic Increasing Stack Algorithm
1. Maintain a stack of bar indices with **monotonically increasing heights**.
2. When current bar $H[i]$ is smaller than $H[\text{stack}[-1]]$:
   - Pop `mid = stack.pop()`. The height of the rectangle is $H[\text{mid}]$.
   - Right boundary: Current index $i$ (the first bar to the right shorter than $H[\text{mid}]$).
   - Left boundary: New `stack[-1]` (the first bar to the left shorter than $H[\text{mid}]$).
   - Width formula:
     $$\text{Width} = i - \text{stack}[-1] - 1 \quad (\text{or } i \text{ if stack is empty})$$
   - $\text{Area} = H[\text{mid}] \times \text{Width}$.
3. Append a dummy sentinel height `0` to the end of `heights` to ensure all bars are completely flushed and computed!

---

## 10. Pattern 6: Maximal Rectangle in 2D Binary Matrix

Given an $M \times N$ matrix filled with `'0'`s and `'1'`s, find the largest rectangle containing only `'1'`s:

```
Matrix:
[ "1", "0", "1", "0", "0" ]  -> Row 0 Histogram: [1, 0, 1, 0, 0]
[ "1", "0", "1", "1", "1" ]  -> Row 1 Histogram: [2, 0, 2, 1, 1]
[ "1", "1", "1", "1", "1" ]  -> Row 2 Histogram: [3, 1, 3, 2, 2]
[ "1", "0", "0", "1", "0" ]  -> Row 3 Histogram: [4, 0, 0, 3, 0]
```

### Reduction to 1D Histogram:
1. Maintain an integer height array of size $N$, initialized to 0.
2. For each row:
   - If `matrix[r][c] == '1'`, increment `height[c] += 1`.
   - If `matrix[r][c] == '0'`, reset `height[c] = 0`.
   - Run the 1D Largest Rectangle in Histogram algorithm on `height`.
3. Total time: $\mathcal{O}(M \times N)$ linear in total matrix cells.

---

## 11. Pattern 7: Trapping Rain Water via Monotonic Stack

In Day 16, we solved Trapping Rain Water using **Two Pointers** (calculating vertical water columns).
The **Monotonic Stack** solves it by accumulating **horizontal rectangular water slices**:

```
Stack: Monotonic Decreasing
When a taller bar arrives:
1. Pop bottom = stack.pop()
2. If stack is empty -> Break (no left wall to trap water).
3. Left wall = stack[-1], Right wall = current index i.
4. Bounded Height = min(height[left], height[right]) - height[bottom]
5. Width = i - left - 1
6. Water += Bounded Height * Width
```

---

## 12. Backend Application 1: Real-Time SLA Latency Spike Watchdog

In API Gateways (Envoy, Kong, Traefik), SLAs mandate alerts when response times degrade:
- Streaming events arrive: `(req_id, latency_ms)`.
- A monotonic decreasing stack tracks pending requests.
- When an API response exceeding 500ms arrives, it pops all smaller preceding requests, recording the exact number of requests processed before the latency breach.

---

## 13. Backend Application 2: Financial Time-Series Trend & Order-Book Spreads

In algorithmic trading, calculating order-book market depth:
- A bid price's "influence span" is the contiguous number of subsequent trading ticks where prices remain below or above key moving thresholds.
- Monotonic stacks compute stock spans in $\mathcal{O}(1)$ amortized time per incoming market tick.

---

## 14. Backend Application 3: Resource Quota & Cluster Capacity Optimization

In Kubernetes scheduler pod packing:
- Server nodes provide contiguous CPU/memory quota allocations.
- Finding the largest contiguous cluster capacity block for burst GPU jobs maps directly to the **Largest Rectangle in Histogram** pattern.

---

## 15. Complexity Comparison Matrix

| Problem / Pattern | Monotonic Type | Invariant Condition | Time Complexity | Space Complexity |
| :--- | :--- | :--- | :--- | :--- |
| **Next Greater Element** | Decreasing | `nums[stack[-1]] < incoming` | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **Daily Temperatures** | Decreasing | `temps[stack[-1]] < incoming` | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **NGE Circular Array** | Decreasing | Virtual $2N$ unrolling (`i % n`) | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **Online Stock Span** | Decreasing | `price >= stack[-1].price` | $\mathcal{O}(1)$ amortized | $\mathcal{O}(N)$ |
| **Largest Rectangle Histogram** | Increasing | `heights[stack[-1]] > incoming` | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |
| **Maximal Rectangle 2D** | Increasing | 2D reduction to Histogram | $\mathcal{O}(M \times N)$ | $\mathcal{O}(N)$ |
| **Trapping Rain Water** | Decreasing | Horizontal slice trapping | $\mathcal{O}(N)$ | $\mathcal{O}(N)$ |

---

## Summary Checklist for Day 20
- [x] Understand the Elimination Theorem: why popped elements can never be future answers.
- [x] Prove the amortized $\mathcal{O}(N)$ aggregate runtime (at most $N$ pushes, $N$ pops).
- [x] Master index storage on the stack instead of raw values.
- [x] Implement the virtual $2N$ unrolling trick for circular array traversals.
- [x] Master the width formula for Histogram: $W = i - \text{stack}[-1] - 1$.
- [x] Append sentinel values (`0`) to cleanly flush stacks without redundant cleanup code.
- [x] Apply monotonic stacks to real-time latency spike tracking and cluster burst capacity sizing.
