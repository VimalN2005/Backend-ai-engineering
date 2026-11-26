# Day 83: Principal AI Systems Interview Questions & Answers

## Question 1: Explain the architectural trade-offs between Chunk Size ($K$) and Downstream LLM Performance. What happens when chunks are too small versus too large?

### Deep Technical Answer
Chunk size is the single most critical parameter governing RAG retrieval precision and generation fidelity:

1. **Failure Modes of Micro-Chunks ($K < 100$ tokens)**:
   - **Semantic Fragmentation**: A single sentence or paragraph often requires preceding background context to be understandable (e.g., pronouns like `"it"`, `"they"`, or relative time references like `"in the following quarter"`).
   - **Loss of Global Intent**: The embedding vector captures only localized syntax rather than the overall subject matter, causing embedding drift.
   - **Context Reassembly Overhead**: The retriever must retrieve 20+ fragments to stitch together an answer, increasing prompt parsing overhead and metadata noise.

2. **Failure Modes of Macro-Chunks ($K > 1,500$ tokens)**:
   - **Signal-to-Noise Ratio (SNR) Dilution**: High-dimensional embedding vectors compress the entire chunk into a single vector of fixed dimensions (e.g. 1536). When a chunk covers five unrelated topics, the vector representation is an average across all five, washing out specific facts and failing high-precision similarity searches.
   - **Context Window Flooding**: Retrieving only 3 chunks consumes 4,500 tokens, crowding out conversational history and forcing costly LLM generation tiers.

**Optimal Production Compromise**:
- Dense Technical / Legal Text: **256 to 512 tokens** with **50-token overlap**.
- High-level Narrative / Articles: **512 to 1024 tokens** with **100-token overlap**.
- Use **Small-to-Big Retrieval (Parent-Child Indexing)**: Embed small 200-token child chunks for precision vector matching, but return the broader 1,000-token parent document chunk to the LLM for full contextual synthesis.

---

## Question 2: What is the "Lost-in-the-Middle" phenomenon, what causes it at the Transformer attention layer, and how do you mitigate it architecturally?

### Deep Technical Answer
In 2023, Liu et al. (Stanford University) demonstrated that Transformer LLMs exhibit a U-shaped performance curve when retrieving information from long contexts: performance is highest when relevant information is at the very beginning or end of the prompt, and degrades significantly when located in the middle.

### Root Cause at the Attention Layer
1. **Positional Encoding Degradation**: Rotary Position Embeddings (RoPE) and Absolute Positional Encodings emphasize relative distances. Attention mechanisms naturally allocate higher softmax weights to initial prompt instructions (Primacy Bias) and recent tokens preceding generation (Recency Bias).
2. **Attention Diffusion**: As sequence length $N$ scales, self-attention matrices ($QK^T$) diffuse probability mass over hundreds of competing tokens, diluting intermediate key-value projections.

### Production Architectural Mitigations
1. **Lost-in-the-Middle Reordering**: Reorder ranked chunks $[1, 2, 3, 4, 5]$ into $[1, 3, 5, 4, 2]$. Rank 1 is placed at the head of the context, Rank 2 at the tail, and lower-confidence chunks in the middle.
2. **Context Compression / Reranking**: Use a Cross-Encoder reranker to filter out low-relevance chunks before prompt injection, keeping total context under 2,000 tokens.
3. **Structured XML / Markdown Enclosure**: Wrap each retrieved document in explicit tags (`<document id="doc_1">...</document>`), providing distinct token fences that sharpen attention boundaries.

---

## Question 3: Compare Fixed-Size Chunking, Recursive Character Chunking, and Semantic Chunking. How do you choose between them in production?

### Deep Technical Answer

| Feature | Fixed-Size Chunking | Recursive Character Chunking | Semantic Chunking |
| :--- | :--- | :--- | :--- |
| **Mechanics** | Splits at fixed token/word intervals with sliding overlap. | Tests separator hierarchy (`\n\n`, `\n`, `" "`) recursively. | Embeds adjacent sentences; splits when cosine distance exceeds threshold percentile. |
| **Boundary Respect** | Poor (slices sentences mid-phrase). | Good (respects paragraphs & sentences). | Exceptional (splits strictly at semantic topic shifts). |
| **Ingestion Cost** | $\mathcal{O}(N)$ text scan (sub-millisecond). | $\mathcal{O}(N)$ string parsing (fast). | Prohibitive ($K$ embedding model calls per document). |
| **Production Fit** | Prototyping, uniform baselines. | **Standard Production Default (90% of RAG systems)**. | High-value, complex legal/medical contracts where topic boundaries are unpredictable. |

---

## Question 4: How do you design an anti-hallucination system prompt and context fence that guarantees the LLM admits ignorance when facts are missing?

### Deep Technical Answer
Eliminating hallucinations requires a 3-pillar prompt fencing strategy:

1. **Negative Constraint & Knowledge Isolation**:
   - Instruct the LLM that its pre-trained parametric knowledge is strictly quarantined.
   - Example directive: *"Treat your internal training memory as untrusted. Base your response exclusively on direct evidence from the [CONTEXT] block."*
2. **Syntactic Refusal Fallback (Escape Hatch)**:
   - Provide an exact verbatim refusal string to prevent polite guessing.
   - Example directive: *"If the context does not contain sufficient facts to answer the question directly, respond strictly with: 'I do not have sufficient information in the provided documentation to answer this question.'"*
3. **Enforced Bracketed Citation Grounding**:
   - Mandate bracketed citations matching document IDs for every factual assertion (`[doc_xyz]`).
   - Run post-generation AST/regex validation to verify that every cited document ID exists in the retrieved set.

---

## Question 5: How do you handle tabular data and code snippets in production RAG pipelines?

### Deep Technical Answer
Standard text chunkers destroy tabular structures by separating headers from row values.

### Production Table Ingestion Patterns
1. **Row-Wise JSON / Key-Value Serialization**:
   - Convert each table row into an independent document:
     `{"Employee": "Alice", "Department": "Security", "Salary": "$150,000"}`.
   - Embed this string representation so each row is a complete semantic unit.
2. **Markdown Table Preservation**:
   - If tables are small ($< 15$ rows), use structural chunkers that detect `|` markdown boundaries and keep the table intact as a single atomic chunk.
3. **Hybrid SQL-RAG (Text-to-SQL)**:
   - For massive structured tables ($> 1,000$ rows), **do not use vector embeddings**. Instead, route tabular queries to a Text-to-SQL engine that executes precise relational aggregations (`SUM`, `AVG`, `GROUP BY`) over PostgreSQL or Snowflake.
