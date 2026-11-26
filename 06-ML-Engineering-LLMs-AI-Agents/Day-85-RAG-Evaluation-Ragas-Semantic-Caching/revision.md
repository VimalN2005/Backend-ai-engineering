# Day 85: Rapid Revision & Flashcards — RAG Evaluation & Semantic Caching

## 5-Minute Core Revision Cheat Sheet

### The RAG Triad Metrics
1. **Faithfulness**: $\frac{\text{Supported Claims in Answer}}{\text{Total Claims in Answer}}$. Measures hallucination against retrieved context.
2. **Answer Relevance**: Semantic similarity between query and answer. Measures answer conciseness and addressing the prompt.
3. **Context Precision**: $\frac{\sum \text{Precision@k} \times \text{rel}(k)}{\sum \text{rel}(k)}$. Measures ranking quality and signal-to-noise ratio in retrieved chunks.
4. **Context Recall**: $\frac{\text{Ground Truth Sentences supported by Context}}{\text{Total Ground Truth Sentences}}$. Measures completeness of retrieved knowledge.

### Two-Tier Semantic Cache Specs
- **Tier 1 (Exact Hash)**: SHA-256 over canonicalized query. Lookups $\mathcal{O}(1)$, $<0.1\text{ms}$.
- **Tier 2 (Vector Semantic)**: Embedding cosine similarity $\ge \tau$ (standard $\tau \in [0.90, 0.93]$). Lookups $\mathcal{O}(\log N)$, $1\text{–}3\text{ms}$.
- **Canonicalization**: Strip whitespace, lowercase, remove punctuation, normalize unicode.
- **Cache Invalidation**: Event-driven inverted index mapping `document_id -> [cache_keys]`.

---

## 10 Active Recall Flashcards

1. **Q: What does the Faithfulness metric measure in Ragas?**
   *A*: The proportion of factual statements or atomic claims made in the generated answer that are directly supported by the retrieved context chunks (detects hallucination).

2. **Q: Why is ROUGE or BLEU score inadequate for measuring RAG quality?**
   *A*: They measure surface n-gram overlap, meaning an answer with one negated word ("Server did not crash" vs "Server crashed") gets high ROUGE despite being completely wrong, and synonymous paraphrases are penalized.

3. **Q: What is the risk of setting semantic cache similarity threshold $\tau$ below 0.88?**
   *A*: Semantic drift: queries with subtly different intents (e.g. "Dev vs Prod", "Refund vs Replace") return incorrect cached responses.

4. **Q: What is Context Precision@K?**
   *A*: A metric that evaluates whether relevant context chunks are ranked higher than irrelevant distractor chunks in the retrieved context set.

5. **Q: How does Answer Relevance differ from Faithfulness?**
   *A*: Faithfulness checks if the answer is grounded in retrieved context (no hallucinations); Answer Relevance checks if the answer actually answers what the user asked.

6. **Q: What are the two main layers in a Two-Tier Response Cache?**
   *A*: Tier 1 is an Exact Hash Cache (SHA-256 over normalized text, $\mathcal{O}(1)$); Tier 2 is a Vector Semantic Cache (Embedding cosine similarity index).

7. **Q: What is Position Bias in LLM-as-a-Judge evaluations?**
   *A*: The tendency of evaluation models to prefer the candidate presented first in the prompt, regardless of quality. Mitigated by swapping positions and averaging scores.

8. **Q: How does Canonicalization improve Tier 1 cache hit rates?**
   *A*: It collapses whitespace, lowercases text, and strips punctuation, ensuring queries with identical text but cosmetic formatting differences hit the exact same hash key.

9. **Q: What is a Cache Stampede (Thundering Herd)?**
   *A*: When a popular cached item expires and hundreds of concurrent requests simultaneously hit the backend LLM, causing latency spikes and rate limiting.

10. **Q: How does Probabilistic Early Expiration (XFetch) prevent cache stampedes?**
    *A*: It randomly triggers an asynchronous cache refresh in a small time window before the TTL expires based on compute duration and access frequency, keeping the cache perpetually warm.
