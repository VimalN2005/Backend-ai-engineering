# Database Transactions & Concurrency Practical Exercises

This document provides three senior-level database engineering challenges designed to reinforce mastery of Wait-For Graphs, SSI rw-antidependency cycle detection, and deadlock-free multi-threaded resource ordering. Complete reference solutions in pure Python standard library are included.

---

## Challenge 1: The Wait-For Graph (WFG) Deadlock Detector

### Problem Specification
When transactions block waiting for row or table locks held by other sessions, dependencies form a directed graph called the **Wait-For Graph (WFG)**. If a cycle forms ($T_1 \to T_2 \to \dots \to T_1$), the transactions are deadlocked and will hang indefinitely.

Implement a class `WaitForGraphDetector` that:
1. Adds wait dependencies: `add_wait(waiting_xid: int, blocking_xid: int)`.
2. Removes wait dependencies upon lock release or commit: `remove_wait(waiting_xid: int, blocking_xid: int)` and `remove_transaction(xid: int)`.
3. Detects whether adding an edge creates a directed cycle using Depth-First Search (DFS).
4. If a cycle is detected, returns the full cycle path: `[T_start, ..., T_start]`.
5. Implements a victim selection heuristic: selects the youngest transaction (highest XID) to abort.

### Reference Solution & Verification Test

```python
import collections
from typing import Dict, Set, List, Optional

class WaitForGraphDetector:
    def __init__(self):
        # Map[waiting_xid -> Set[blocking_xid]]
        self.adj: Dict[int, Set[int]] = collections.defaultdict(set)

    def add_wait(self, waiting_xid: int, blocking_xid: int) -> Optional[List[int]]:
        """
        Registers a dependency edge: waiting_xid is blocked by blocking_xid.
        Returns the cycle path if this edge introduced a deadlock, else None.
        """
        self.adj[waiting_xid].add(blocking_xid)
        cycle = self.detect_cycle(waiting_xid)
        return cycle

    def remove_wait(self, waiting_xid: int, blocking_xid: int) -> None:
        if waiting_xid in self.adj:
            self.adj[waiting_xid].discard(blocking_xid)

    def remove_transaction(self, xid: int) -> None:
        """Removes all incoming and outgoing edges for an aborted/committed transaction."""
        if xid in self.adj:
            del self.adj[xid]
        for waiting_xid, blocking_set in list(self.adj.items()):
            blocking_set.discard(xid)

    def detect_cycle(self, start_xid: int) -> Optional[List[int]]:
        """Executes DFS from start_xid to detect directed circular wait cycles."""
        visited: Set[int] = set()
        path: List[int] = []

        def dfs(current: int) -> Optional[List[int]]:
            visited.add(current)
            path.append(current)

            for neighbor in self.adj.get(current, set()):
                if neighbor == start_xid:
                    path.append(start_xid)
                    return list(path)
                if neighbor not in visited:
                    res = dfs(neighbor)
                    if res:
                        return res

            path.pop()
            return None

        return dfs(start_xid)

    def select_victim(self, cycle: List[int]) -> int:
        """Selects the youngest transaction (highest XID) as the victim."""
        unique_nodes = set(cycle)
        return max(unique_nodes)

# Verification Test
if __name__ == "__main__":
    detector = WaitForGraphDetector()
    
    # T100 waits on T101
    assert detector.add_wait(100, 101) is None
    # T101 waits on T102
    assert detector.add_wait(101, 102) is None
    # T102 waits on T100 -> Deadlock! Cycle: 102 -> 100 -> 101 -> 102
    cycle = detector.add_wait(102, 100)
    assert cycle is not None
    print(f"Deadlock Cycle Detected: {' -> '.join(map(str, cycle))}")
    victim = detector.select_victim(cycle)
    assert victim == 102
    print(f"Victim Selected for Abort: Transaction {victim} [PASSED]")
```

---

## Challenge 2: Serializable Snapshot Isolation (SSI) Dangerous Structure Tracker

### Problem Specification
Write an SSI serialization graph engine `SSIGraphTracker` that tracks:
1. `SIREAD` locks when transactions read rows: `record_read(xid: int, row_id: str)`.
2. Modifications: `record_write(writer_xid: int, row_id: str)`.
3. Creates directed $rw$-antidependency edges ($T_{\text{reader}} \xrightarrow{rw} T_{\text{writer}}$).
4. Detects dangerous structures (cycles of $rw$-antidependencies) that cause anomalies like Write Skew.
5. Aborts conflicting transactions with a custom `SerializationFailure` exception.

### Reference Solution & Verification Test

```python
import collections
from typing import Dict, Set, List, Optional

class SerializationFailure(Exception):
    pass

class SSIGraphTracker:
    def __init__(self):
        # row_id -> Set[xid] (who read this row)
        self.siread_locks: Dict[str, Set[int]] = collections.defaultdict(set)
        # rw-antidependencies: Map[reader_xid -> Set[writer_xid]]
        self.rw_edges: Dict[int, Set[int]] = collections.defaultdict(set)

    def record_read(self, xid: int, row_id: str) -> None:
        self.siread_locks[row_id].add(xid)

    def record_write(self, writer_xid: int, row_id: str) -> None:
        # Check if another transaction read row_id prior to this write
        readers = self.siread_locks.get(row_id, set())
        for reader_xid in readers:
            if reader_xid != writer_xid:
                # reader_xid read a version that writer_xid is overwriting!
                self.rw_edges[reader_xid].add(writer_xid)

    def check_and_commit(self, xid: int) -> None:
        """Validates that xid is not part of a directed rw-cycle before allowing commit."""
        visited: Set[int] = set()

        def dfs(node: int, origin: int) -> bool:
            visited.add(node)
            for neighbor in self.rw_edges.get(node, set()):
                if neighbor == origin:
                    return True
                if neighbor not in visited:
                    if dfs(neighbor, origin):
                        return True
            return False

        if dfs(xid, xid):
            raise SerializationFailure(f"Transaction {xid} cannot commit: rw-cycle detected (Write Skew anomaly prevented)!")

# Verification Test (Write Skew Scenario)
if __name__ == "__main__":
    ssi = SSIGraphTracker()
    
    # Alice (T1) and Bob (T2) both read doctors table (alice, bob)
    ssi.record_read(1, "alice")
    ssi.record_read(1, "bob")
    ssi.record_read(2, "alice")
    ssi.record_read(2, "bob")

    # Alice sets herself off call (writes "alice") -> Bob read "alice", so Bob --rw--> Alice
    ssi.record_write(1, "alice")

    # Bob sets himself off call (writes "bob") -> Alice read "bob", so Alice --rw--> Bob
    ssi.record_write(2, "bob")

    # Cycle exists: Bob --rw--> Alice --rw--> Bob!
    # Bob attempts to commit:
    try:
        ssi.check_and_commit(2)
        assert False, "SSI should have aborted Bob with SerializationFailure!"
    except SerializationFailure as ex:
        print(f"SSI Engine Caught Write Skew: {ex} [PASSED]")
```

---

## Challenge 3: Deadlock-Free Concurrent Bank Transfer Service with Jitter Retry

### Problem Specification
Build a thread-safe banking transfer service `BankingService` that:
1. Executes money transfers between accounts concurrently across multiple worker threads.
2. Eliminates deadlocks by enforcing **Global Resource Ordering** (sorting account IDs before acquiring locks).
3. Implements an automatic retry loop with exponential backoff and randomized jitter to handle transient contention.
4. Asserts that the total bank reserves remain strictly constant (conservation of money invariant).

### Reference Solution & Verification Test

```python
import random
import threading
import time
from typing import Dict

class Account:
    def __init__(self, id: int, balance: float):
        self.id = id
        self.balance = balance
        self.lock = threading.Lock()

class BankingService:
    def __init__(self, num_accounts: int = 10):
        self.accounts: Dict[int, Account] = {
            i: Account(i, 1000.0) for i in range(1, num_accounts + 1)
        }

    def transfer_safe(self, from_id: int, to_id: int, amount: float, max_retries: int = 5) -> bool:
        """
        Transfers amount from from_id to to_id.
        Enforces strict primary key ordering to make deadlocks mathematically impossible!
        """
        first_id, second_id = (from_id, to_id) if from_id < to_id else (to_id, from_id)
        acct_first = self.accounts[first_id]
        acct_second = self.accounts[second_id]

        acct_from = self.accounts[from_id]
        acct_to = self.accounts[to_id]

        for attempt in range(max_retries):
            # Attempt to acquire both locks in global sorted order
            if acct_first.lock.acquire(timeout=0.05):
                try:
                    if acct_second.lock.acquire(timeout=0.05):
                        try:
                            if acct_from.balance >= amount:
                                acct_from.balance -= amount
                                acct_to.balance += amount
                                return True
                            return False
                        finally:
                            acct_second.lock.release()
                finally:
                    acct_first.lock.release()

            # Exponential backoff with full jitter before retry
            sleep_time = random.uniform(0, 0.01 * (2 ** attempt))
            time.sleep(sleep_time)

        return False

# Verification Test
if __name__ == "__main__":
    service = BankingService(num_accounts=10)
    initial_reserves = sum(a.balance for a in service.accounts.values())

    def worker_transfers():
        for _ in range(50):
            u1 = random.randint(1, 10)
            u2 = random.randint(1, 10)
            while u2 == u1:
                u2 = random.randint(1, 10)
            service.transfer_safe(u1, u2, 10.0)

    threads = [threading.Thread(target=worker_transfers) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    final_reserves = sum(a.balance for a in service.accounts.values())
    assert abs(initial_reserves - final_reserves) < 1e-4
    print(f"Concurrent Banking Transfers Complete! Total Reserves Conserved: ${final_reserves:,.2f} [PASSED]")
```
