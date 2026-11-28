# Day 90: Rapid Revision & Flashcards — Master Interview Marathon & Portfolio Polish

## 5-Minute Core Curriculum Revision Cheat Sheet

### The 6 Core Engineering Pillars
1. **Python Core & Concurrency**: CPython memory hierarchy (`PyMalloc` Arenas/Pools/Blocks), reference counting + generational cyclic GC (`tp_traverse`), GIL constraints, `asyncio` non-blocking epoll event loops.
2. **Networking & High-Throughput APIs**: HTTP/2 multiplexing, gRPC binary Protocol Buffers serialization, WebSockets persistent full-duplex communication.
3. **Database Engineering**: B-Trees (in-place page updates, $\mathcal{O}(\log N)$ reads) vs LSM-Trees (Memtable + WAL sequential writes + SSTable compaction), PostgreSQL MVCC (`xmin`/`xmax` dead tuple vacuuming), Redis in-memory caching.
4. **Distributed Systems**: CAP & PACELC theorems, Apache Kafka log partitions, zero-copy `sendfile()`, idempotent producers & 2PC transactional consumers (EOS).
5. **Cloud, Security & Observability**: OpenTelemetry GenAI semantic spans (`gen_ai.usage.prompt_tokens`), PII tokenization (Presidio pattern), high-entropy egress secret scrubbing, mTLS zero-trust boundaries.
6. **ML Engineering & AI Agents**: Dense vector embeddings, pgvector HNSW indexing, BM25 + Dense Reciprocal Rank Fusion ($RRF$), LangGraph cyclic state machines with reducers and checkpointing.

---

## 10 Master Active Recall Flashcards

1. **Q: Why does CPython need both Reference Counting and Generational GC?**
   *A*: Reference counting frees memory immediately when counts hit zero ($\mathcal{O}(1)$), but cannot detect cyclic references. The generational GC periodically scans container objects to break and collect circular references.

2. **Q: How does gRPC multiplexing solve HTTP/1.1 Head-of-Line (HoL) blocking?**
   *A*: By splitting requests and responses into binary frames multiplexed over independent logical streams on a single persistent TCP connection.

3. **Q: When should you choose an LSM-Tree database over a B-Tree database?**
   *A*: When the workload is write-heavy (e.g. timeseries, event logging, high-throughput analytics) because LSM-trees turn random writes into fast sequential disk appends.

4. **Q: How does PostgreSQL MVCC avoid reader-writer blocking?**
   *A*: Writes create a new version of the row tuple with `xmin` set to the transaction ID, while readers view a snapshot of tuples matching their transaction horizon. Readers never block writers, and writers never block readers.

5. **Q: What is the Kafka Page Cache and Zero-Copy architecture?**
   *A*: Kafka writes to the Linux OS page cache and uses the `sendfile()` system call to transmit bytes directly from disk cache to the network socket buffer without copying data into application memory.

6. **Q: What is the Reciprocal Rank Fusion (RRF) formula, and why is it used?**
   *A*: $RRF(d) = \sum \frac{1}{k + r_m(d)}$. It merges ranked lists from sparse (BM25) and dense (vector) retrievers based purely on ordinal rank positions without requiring fragile score normalization.

7. **Q: How do State Reducers prevent race conditions in LangGraph?**
   *A*: They enforce pure functional merge operations (e.g. `operator.add` for appending messages) so concurrent parallel node outputs merge deterministically without overwriting shared state.

8. **Q: What is the Presidio bi-directional PII masking pattern?**
   *A*: Anonymizing sensitive entities (emails, SSNs) with typed tokens (`[EMAIL_1]`) before model inference, and re-hydrating the tokens with original user values upon response delivery.

9. **Q: What is the difference between CAP theorem and PACELC theorem?**
   *A*: CAP states that in a partition (P), a system must choose Consistency (C) or Availability (A). PACELC extends this: even when there is no partition (Else), the system must choose between Latency (L) and Consistency (C).

10. **Q: What is the single most important habit in a Senior/Principal technical interview?**
    *A*: Clarifying constraints and scale numbers upfront (QPS, latency SLAs, storage growth) and defending architectural choices through explicit technical tradeoff analysis.
