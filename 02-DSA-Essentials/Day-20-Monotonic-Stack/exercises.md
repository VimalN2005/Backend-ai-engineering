# Day 20: Practical Hands-On DSA Challenges

Master Monotonic Stacks, boundary elimination, and histogram optimization with these 3 production-level challenges.

---

## Challenge 1: Online Stock Span Engine (Amortized $\mathcal{O}(1)$ Stream Processing)

### Objective
In algorithmic trading systems and real-time market data ingestion engines, analyzing price momentum is a core operational requirement. The **span** of a stock's price today is defined as the maximum number of consecutive days (starting from today and going backward) for which the stock price was less than or equal to today's price.

Design and implement a class `StockSpanner` that receives a stream of daily stock prices and returns the span of the stock's price for the current day.

### Requirements
1. **Time Complexity**: Strictly amortized $\mathcal{O}(1)$ per `next(price: int) -> int` call.
2. **Space Complexity**: $\mathcal{O}(N)$ where $N$ is the number of prices ingested.
3. **Monotonic Stack Structure**:
   - Maintain a monotonic decreasing stack storing `(price, accumulated_span)` tuples.
   - When a new price arrives that is $\ge$ `stack[-1][0]`, pop the smaller price and add its `accumulated_span` to the current day's span.
4. **Test Fixture**:
   ```python
   spanner = StockSpanner()
   assert spanner.next(100) == 1  # [100] -> span 1
   assert spanner.next(80) == 1   # [100, 80] -> span 1
   assert spanner.next(60) == 1   # [100, 80, 60] -> span 1
   assert spanner.next(70) == 2   # [100, 80, 60, 70] -> 60 <= 70, span = 1 + 1 = 2
   assert spanner.next(60) == 1   # [100, 80, (70, 2), 60] -> span 1
   assert spanner.next(75) == 4   # 60 <= 75 and 70 <= 75 -> span = 1 + 1 + 2 = 4
   assert spanner.next(85) == 6   # 75 <= 85 and 80 <= 85 -> span = 1 + 4 + 1 = 6
   ```

---

## Challenge 2: Remove K Digits for Smallest Number (Greedy Monotonic Pruning)

### Objective
In AI model weight quantization and high-density integer compression, a common preprocessing step is pruning the most significant non-optimal digits while preserving the relative sequence order.

Given a string `num` representing a non-negative integer and an integer `k`, return the smallest possible integer string after removing `k` digits from `num`.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(N)$ where $N = \text{len}(num)$.
2. **Space Complexity**: $\mathcal{O}(N)$ for the monotonic stack.
3. **Greedy Monotonic Invariant**:
   - To make a number as small as possible, smaller digits must be placed as far to the left (highest place value) as possible.
   - Use a **Monotonic Increasing Stack**: while $k > 0$ and the current digit is smaller than the stack top (`stack[-1] > digit`), pop the stack top and decrement $k$.
4. **Edge Cases**:
   - If $k \ge \text{len}(num)$, all digits are removed; return `"0"`.
   - If digits remain monotonically increasing (e.g. `"12345"` with $k=2$), pop the remaining $k$ digits from the right.
   - Strip all leading zeroes from the final result (e.g. `"0200"` $\to$ `"200"`). If the result becomes empty, return `"0"`.
5. **Test Cases**:
   - `remove_k_digits("1432219", 3)` $\to$ `"1219"`
   - `remove_k_digits("10200", 1)` $\to$ `"200"`
   - `remove_k_digits("10", 2)` $\to$ `"0"`
   - `remove_k_digits("9", 1)` $\to$ `"0"`
   - `remove_k_digits("112", 1)` $\to$ `"11"`

---

## Challenge 3: Maximal Rectangle in 2D Binary Matrix ($\mathcal{O}(R \times C)$ Time)

### Objective
In cloud infrastructure and distributed memory grids, contiguous rectangular allocations of healthy resources are required for multi-node GPU clusters. Given a 2D binary matrix `matrix` filled with `'0'`s and `'1'`s, find the largest rectangle containing only `'1'`s and return its area.

### Requirements
1. **Time Complexity**: Strictly $\mathcal{O}(R \times C)$ where $R$ is rows and $C$ is columns.
2. **Space Complexity**: $\mathcal{O}(C)$ auxiliary space for the height array and monotonic stack.
3. **Algorithm Design**:
   - Maintain a 1D array `heights` of size $C$, initialized to zeroes.
   - For each row in `matrix`:
     - If `matrix[row][col] == '1'`, increment `heights[col] += 1`.
     - If `matrix[row][col] == '0'`, reset `heights[col] = 0` (the rectangle cannot span across a '0' cell).
     - Run `largest_rectangle_in_histogram(heights)` on the updated row.
     - Track and update the global maximum area.
4. **Test Cases**:
   - Input:
     ```python
     matrix = [
       ["1","0","1","0","0"],
       ["1","0","1","1","1"],
       ["1","1","1","1","1"],
       ["1","0","0","1","0"]
     ]
     ```
     Output: `6` (formed by rows 1 and 2, columns 2 to 4).
   - Input: `[["0"]]` $\to$ `0`
   - Input: `[["1"]]` $\to$ `1`
   - Input: `[["0","0"]]` $\to$ `0`
