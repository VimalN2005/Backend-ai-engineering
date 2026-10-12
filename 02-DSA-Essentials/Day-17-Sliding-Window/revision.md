# Day 17: 5-Minute Rapid Revision Cheat Sheet

A concise, high-yield reference guide to the Sliding Window Technique, Monotonic Deques, and Production Rate Limiters.

---

## 1. Sliding Window Pattern Taxonomy

```
                      SLIDING WINDOW PATTERNS
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
    Fixed-Size Window       Dynamic-Size Window      Monotonic Deque Window
  (Incremental State)      (Constraint Contraction)  (Sliding Window Max)
  • Moving Averages        • Longest Substring       • Peak Resource Tracker
  • Max Sum Subarray K     • Min Window Substring    • Outlier Filter
  • Fixed Time Buckets     • Min Size Subarray Sum   • Queue Latency Highs
```

---

## 2. Core Reusable Code Templates

### Template A: Fixed-Size Window ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Space)
```python
def fixed_window(arr: list[int], k: int) -> int:
    state = sum(arr[:k])
    best = state
    for i in range(k, len(arr)):
        state += arr[i] - arr[i - k]  # Add incoming, subtract outgoing
        best = max(best, state)
    return best
```

### Template B: Dynamic Variable-Size Window ($\mathcal{O}(N)$ Amortized)
```python
def dynamic_window(arr: list[int], target: int) -> int:
    left = 0
    state = 0
    best_len = float("inf")
    for right in range(len(arr)):
        state += arr[right]
        # Greedily shrink left while constraint is satisfied
        while state >= target:
            best_len = min(best_len, right - left + 1)
            state -= arr[left]
            left += 1
    return 0 if best_len == float("inf") else int(best_len)
```

### Template C: Sliding Window Maximum via Monotonic Deque ($\mathcal{O}(N)$ Time)
```python
from collections import deque

def sliding_window_max(nums: list[int], k: int) -> list[int]:
    d = deque()  # Stores indices in monotonically decreasing value order
    out = []
    for i, n in enumerate(nums):
        # 1. Pop out-of-window indices from front
        while d and d[0] <= i - k:
            d.popleft()
        # 2. Pop smaller elements from back
        while d and nums[d[-1]] <= n:
            d.pop()
        d.append(i)
        # 3. Front is always maximum
        if i >= k - 1:
            out.append(nums[d[0]])
    return out
```

---

## 3. Rate Limiter Algorithms Quick Comparison

| Algorithm | Memory | Precision | Burst Handling | Best Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Fixed Window** | $\mathcal{O}(1)$ | Poor (Boundary Flaw) | Allows $2\times$ burst | Low-risk internal microservices |
| **Sliding Window Log** | $\mathcal{O}(\text{Reqs})$ | **100% Precise** | Strictly smooth | Auth login, SMS OTP, payment fraud |
| **Sliding Window Counter**| $\mathcal{O}(1)$ | ~99.9% Approximate | Interpolated | Cloudflare, high-scale API gateways |
| **Token Bucket** | $\mathcal{O}(1)$ | High | Controlled burst size | AWS / Stripe developer APIs |

---

## 4. Key Formulas & Invariants

1. **Window Size Formula**:
   $$\text{Length} = \text{right} - \text{left} + 1$$
2. **Incremental State Equation**:
   $$\text{State}_{t} = \text{State}_{t-1} + \text{Incoming} - \text{Outgoing}$$
3. **Sliding Window Counter Interpolation**:
   $$\text{Requests} = \left(\text{Count}_{\text{prev}} \times \frac{W - t}{W}\right) + \text{Count}_{\text{curr}}$$

---

## 5. The Golden Rules for Backend Engineers
1. **Never re-slice arrays** inside loops; always maintain running scalar state ($\text{State} = \text{State} + \text{In} - \text{Out}$).
2. **Beware negative numbers**: Sliding window breaks without monotonicity; switch to Prefix Sums + Hash Map.
3. **Use Monotonic Deque** for window maximums ($\mathcal{O}(N)$ time), never a Max-Heap ($\mathcal{O}(N \log K)$).
4. **Shrink with `while`, not `if`** when multiple elements must be evicted to restore valid state.
5. **Always prune expired client keys** in sliding window rate limiters to avoid memory leaks.
