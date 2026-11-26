# Day 83: Rapid Revision & Active Recall Cheat Sheet

## 1. High-Yield Architectural Summary (5-Minute Scan)

- **The RAG 101 Pipeline**:
  $$\text{Documents} \to \text{Chunking} \to \text{Embeddings} \to \text{Vector DB} \to \text{Top-K Retrieval} \to \text{Context Injection} \to \text{LLM}$$
- **Chunking Fundamentals**:
  - **Chunk Size ($K$)**: Balancing semantic coherence ($K \approx 256 - 512$ tokens) against context window saturation.
  - **Chunk Overlap**: Always enforce **$10\% - 15\%$ sliding overlap** to prevent facts spanning boundaries from fracturing.
- **Chunking Strategies**:
  - **Fixed-Size**: Uniform token/word slices with overlap.
  - **Recursive Character**: Multi-tier separator hierarchy (`\n\n` $\to$ `\n` $\to$ `" "` $\to$ `""`). Industry default for general text.
  - **Markdown / Structural**: Splits at `#` heading boundaries and attaches breadcrumbs to metadata.
  - **Semantic**: Splits on sentence embedding cosine distance threshold dips.
- **Lost-in-the-Middle Phenomenon**:
  - Attention is U-shaped: LLMs retrieve evidence best at the start and end of prompt context; performance drops $30\% - 40\%$ in the middle.
  - **Mitigation**: Reorder chunks alternatingly: $[1, 2, 3, 4, 5] \to [1, 3, 5, 4, 2]$.
- **Anti-Hallucination Fencing**:
  - Directives must forbid pre-training extrapolation.
  - Mandate explicit bracketed citations: `[Doc #xyz]`.
  - Provide an explicit refusal sentence fallback for missing evidence.

---

## 2. Chunking Strategy Decision Matrix

| Strategy | Speed / Cost | Semantic Integrity | Best For |
| :--- | :--- | :--- | :--- |
| **Fixed-Size** | Ultra-Fast / Zero Cost | Low (cuts words/sentences) | Rapid prototypes, baseline benchmarks |
| **Recursive Character** | Fast / Zero Cost | High (preserves paragraphs) | Production default for docs, blogs, manuals |
| **Markdown Header** | Fast / Zero Cost | Very High (preserves structure) | Technical documentation, wikis, Gitbooks |
| **Semantic Chunking** | Slow / High Embedding Cost | Highest (topic-aligned) | Complex legal contracts, dense research papers |

---

## 3. 10 Active Recall Flashcards

### Card 1
- **Q**: What fundamental problem does RAG solve for Large Language Models?
- **A**: The Knowledge Gap: static training cut-off dates, lack of access to private enterprise data, and hallucinations on domain-specific facts.

### Card 2
- **Q**: Why is sliding chunk overlap (e.g., 50–100 tokens) mandatory in production chunking?
- **A**: Crucial facts and sentences frequently span chunk boundaries; without overlap, boundary facts are fractured into two partial fragments that fail similarity searches.

### Card 3
- **Q**: How does Recursive Character Chunking decide where to split text?
- **A**: It iterates through an ordered hierarchy of natural separators (`["\n\n", "\n", " ", ""]`), using the largest separator that produces chunks within the target size.

### Card 4
- **Q**: What is the "Lost-in-the-Middle" phenomenon in long-context LLMs?
- **A**: Transformer attention naturally focuses on tokens at the beginning and end of the context window; information placed in the middle experiences a $30\% - 40\%$ retrieval degradation.

### Card 5
- **Q**: How does alternating context reordering mitigate the "Lost-in-the-Middle" effect?
- **A**: It places the highest-ranked chunk (Rank 1) at the start of the context and the second-highest (Rank 2) at the very end: $[1, 2, 3, 4, 5] \to [1, 3, 5, 4, 2]$.

### Card 6
- **Q**: What are the trade-offs of using micro-chunks ($< 100$ tokens) versus macro-chunks ($> 1,500$ tokens)?
- **A**: Micro-chunks lose vital surrounding context and semantic intent; macro-chunks dilute vector signal-to-noise ratio and flood the LLM context window.

### Card 7
- **Q**: What is Small-to-Big (Parent-Child) retrieval?
- **A**: Slicing documents into small child chunks for high-precision embedding searches, while linking them to larger parent chunks that are returned to the LLM for synthesis.

### Card 8
- **Q**: Why should Markdown headers be preserved in chunk metadata rather than stripped?
- **A**: Header breadcrumbs (e.g. `"API > Enterprise > Limits"`) provide essential hierarchical context that disambiguates isolated statements.

### Card 9
- **Q**: What three components must every anti-hallucination RAG system prompt include?
- **A**: 1) Strict instruction to use only provided context, 2) Mandated bracketed citations (`[doc_id]`), 3) An explicit refusal fallback phrase when facts are missing.

### Card 10
- **Q**: How should massive structured tabular data be handled in enterprise RAG systems?
- **A**: Do not pass huge tables through standard text chunkers; use Text-to-SQL routing over relational databases or row-wise JSON serialization.
