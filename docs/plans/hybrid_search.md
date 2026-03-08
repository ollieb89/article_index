Here’s a detailed implementation plan for **hybrid search (PostgreSQL full-text + pgvector)** in your current stack.

This plan assumes your existing app already uses the `intelligence.chunks` table, embeddings, FastAPI, and Celery/Ollama-style RAG flow, which matches the project notes you shared. The same notes already position hybrid search as a strong next step because pure vectors miss exact tokens like product names, acronyms, and error strings. 

## Goal

Build a retrieval layer that combines:

* **lexical relevance** from PostgreSQL full-text search (`tsvector`, `tsquery`, `ts_rank`)
* **semantic relevance** from pgvector cosine similarity
* **reranking** that produces one final result list for RAG context assembly

The key benefit is better handling of mixed queries such as:

* `llama3.2 timeout error`
* `pgvector hnsw recall`
* `ACME-42 firmware reset`

PostgreSQL’s text search stack is designed around generating a `tsvector` from documents, generating a `tsquery` from user input, and ranking matches with functions such as `ts_rank`; it also supports field weighting with `setweight`, which is exactly what we need for chunk title/body ranking. ([PostgreSQL][1])

## Recommended target architecture

Do **not** make the first production version a single giant SQL query.

Instead, implement **two-stage retrieval**:

1. run a **lexical search** over `search_tsv`
2. run a **vector search** over `embedding`
3. merge the candidate sets in application code
4. normalize/rerank
5. send the top `k` chunks into the prompt

That design is more controllable, easier to tune, and matches the production tweak already identified in your project notes. 

## Phase 1: schema changes

### 1) Add a weighted `tsvector` column

Use a **stored generated column**, not virtual. In current PostgreSQL, generated columns default to virtual unless you specify otherwise, while stored generated columns are computed on write and persisted like normal columns. That makes them the right fit for indexed search materialization. ([PostgreSQL][2])

Use title weighting so title hits outrank body-only hits.

```sql
ALTER TABLE intelligence.chunks
ADD COLUMN search_tsv tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('english', coalesce(content, '')), 'D')
) STORED;
```

Why this version is better than a plain concatenation:

* title gets stronger ranking weight than body
* null-safe with `coalesce`
* follows PostgreSQL’s documented pattern for structured documents using `setweight(...) || setweight(...)` ([PostgreSQL][1])

### 2) Add a GIN index for lexical search

```sql
CREATE INDEX chunks_search_tsv_idx
ON intelligence.chunks
USING gin (search_tsv);
```

### 3) Ensure the vector index path is ready

pgvector supports both exact and approximate nearest-neighbor search in Postgres. If your chunk table is already large enough that brute-force vector scans are noticeable, add HNSW for cosine distance. ([GitHub][3])

```sql
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
ON intelligence.chunks
USING hnsw (embedding vector_cosine_ops);
```

### 4) Add supporting metadata if missing

You will get better retrieval controls if `intelligence.chunks` also has:

* `document_id`
* `chunk_index`
* `title`
* `content`
* `token_count`
* `source_type`
* `created_at`
* `embedding`

These are not required for hybrid search itself, but they matter for reranking, deduplication, and prompt assembly.

## Phase 2: backfill and migration rollout

Because adding a stored generated column and new indexes can be disruptive on large tables, roll this out in a safe sequence.

### Step A: migration

Run:

1. add column
2. create GIN index
3. create HNSW index if needed

For a large production table, schedule this during a low-write window.

### Step B: verify search materialization

Run sanity checks:

```sql
SELECT id, title, search_tsv
FROM intelligence.chunks
WHERE search_tsv IS NOT NULL
LIMIT 5;
```

### Step C: explain plans

Confirm Postgres is using the intended indexes.

Lexical path:

```sql
EXPLAIN ANALYZE
SELECT id
FROM intelligence.chunks
WHERE search_tsv @@ plainto_tsquery('english', 'llama3.2 timeout error')
ORDER BY ts_rank(search_tsv, plainto_tsquery('english', 'llama3.2 timeout error')) DESC
LIMIT 20;
```

Vector path:

```sql
EXPLAIN ANALYZE
SELECT id
FROM intelligence.chunks
ORDER BY embedding <=> $1::vector
LIMIT 20;
```

## Phase 3: retrieval design

## 3.1 Lexical retrieval

Use `websearch_to_tsquery` for user-facing search if you want more natural syntax, or `plainto_tsquery` for stricter behavior. PostgreSQL provides both document parsing and query parsing functions in its full-text search system. ([PostgreSQL][1])

Start with:

```sql
SELECT
  c.id,
  c.document_id,
  c.chunk_index,
  c.title,
  c.content,
  ts_rank(c.search_tsv, plainto_tsquery('english', $1)) AS lexical_score
FROM intelligence.chunks c
WHERE c.search_tsv @@ plainto_tsquery('english', $1)
ORDER BY lexical_score DESC
LIMIT $2;
```

Suggested initial parameters:

* lexical candidate size: `30`
* final lexical contribution: `35%`

## 3.2 Vector retrieval

Use cosine distance with pgvector.

```sql
SELECT
  c.id,
  c.document_id,
  c.chunk_index,
  c.title,
  c.content,
  1 - (c.embedding <=> $1::vector) AS semantic_score
FROM intelligence.chunks c
WHERE c.embedding IS NOT NULL
ORDER BY c.embedding <=> $1::vector
LIMIT $2;
```

Suggested initial parameters:

* vector candidate size: `40`
* final semantic contribution: `65%`

That 35/65 split matches the starting point from your project notes, and it is a good default because semantic retrieval usually carries broader recall while lexical retrieval protects exact matches. 

## 3.3 Merge and rerank

Do this in application code, not SQL, for the first version.

For each candidate chunk id:

* keep best `lexical_score` if present
* keep best `semantic_score` if present
* normalize both scores to 0..1 within the candidate set
* compute final score

Example:

```python
hybrid_score = 0.35 * lexical_norm + 0.65 * semantic_norm
```

Then sort descending and take the final top `k`.

### Why normalize first

`ts_rank` and cosine similarity are not naturally on the same scale. If you add raw values directly, one signal will dominate unpredictably. Normalize per query before blending.

### Better production alternative: reciprocal rank fusion

After the weighted blend is working, test **RRF**:

```python
rrf_score = 1/(60 + lexical_rank) + 1/(60 + vector_rank)
```

RRF is often more stable than raw-score blending when score distributions vary a lot across queries.

## Phase 4: API/service changes

Add a dedicated retrieval service layer rather than embedding the logic inside the endpoint.

Recommended shape:

```python
class HybridRetriever:
    async def retrieve(self, query: str, query_embedding: list[float], k: int = 10):
        lexical_hits = await self.fetch_lexical(query, limit=30)
        vector_hits = await self.fetch_vector(query_embedding, limit=40)
        merged = self.merge_and_rerank(lexical_hits, vector_hits)
        return merged[:k]
```

Then in your RAG flow:

1. receive question
2. generate query embedding
3. call `HybridRetriever.retrieve(...)`
4. build prompt context from top chunks
5. send to Ollama/model
6. return answer with citations if available

This fits your current FastAPI/Ollama layout cleanly and does not require changing the rest of the answer-generation pipeline. Your project notes already show a shared retrieval layer under both `/rag` and `/rag/stream`, which is the right shape for this. 

## Phase 5: prompt assembly rules

Hybrid search improves retrieval, but prompt assembly still matters.

Use these rules:

* max `k = 8` chunks initially
* max per-document chunks: `2`
* collapse adjacent chunks from the same document when possible
* prefer diversity across source documents
* trim by token budget, not character count

That prevents one long document from flooding the context window.

## Phase 6: query handling improvements

After the basic version works, add query-aware behavior.

### Exact-term boost path

If query contains:

* version numbers
* dotted tokens like `llama3.2`
* error strings
* acronyms
* quoted phrases

increase lexical candidate count and lexical weight.

Example policy:

* default: lexical 0.35 / semantic 0.65
* exact-token-heavy query: lexical 0.50 / semantic 0.50

### Fallback path

If lexical search returns zero rows:

* skip lexical branch
* use vector-only search

If vector embedding generation fails:

* return lexical-only search

This makes the system degrade gracefully.

## Phase 7: testing plan

You need three levels of testing.

### 1) SQL correctness tests

Verify:

* `search_tsv` populates correctly
* null title/content do not break generation
* GIN index query returns expected rows
* weighted title hits outrank body-only hits

### 2) Retrieval quality tests

Create a benchmark file with 50–100 real queries:

* exact product names
* acronyms
* stack traces
* mixed natural-language questions
* typo-adjacent technical phrases

For each query, label:

* expected relevant chunk ids
* acceptable document ids
* unacceptable distractors

Track:

* recall@5
* recall@10
* MRR
* nDCG@10

### 3) End-to-end RAG tests

Measure:

* answer groundedness
* citation correctness
* “missed exact term” failures
* hallucination rate when lexical evidence exists

## Phase 8: observability

Instrument the retriever from day one.

Log per request:

* query text
* lexical candidate count
* vector candidate count
* overlap count
* final top-k ids
* retrieval latency by stage
* whether lexical-only/vector-only fallback triggered

Metrics to graph:

* P50/P95 lexical query latency
* P50/P95 vector query latency
* P50/P95 merge latency
* overlap ratio between lexical and vector results
* clickthrough or user-accepted answer rate if you have feedback hooks

## Phase 9: rollout strategy

### Week 1: shadow mode

Run hybrid retrieval in parallel with current vector-only retrieval, but do not change user-visible behavior yet.

Log:

* old top-k
* new top-k
* overlap
* answer quality deltas on sampled queries

### Week 2: partial enablement

Enable hybrid for:

* technical queries with symbols/numbers
* support/error searches
* known weak cases for vector-only retrieval

### Week 3: default on

Make hybrid the default retriever once:

* P95 latency is acceptable
* quality metrics beat vector-only
* no major regressions on broad semantic queries

## Phase 10: tuning checklist

Tune these in order:

1. field weighting in `search_tsv`
2. lexical/vector candidate sizes
3. normalization method
4. final blend weights
5. top-k sent to prompt
6. per-document chunk cap
7. optional RRF instead of weighted fusion

A very good first configuration is:

* title weight `A`
* content weight `D`
* lexical candidates `30`
* vector candidates `40`
* final `k = 8`
* score blend `0.35 lexical + 0.65 semantic`

## Risks and mitigations

### Risk: `ts_rank` becomes expensive

Mitigation:

* rank only the lexical candidate set
* keep lexical limit modest
* use `WHERE search_tsv @@ ...` first, then rank matched rows

### Risk: score blending is unstable

Mitigation:

* normalize per query
* test RRF as a fallback

### Risk: stemming hurts exact terms

Mitigation:

* consider a second text-search config later for less aggressive normalization
* preserve vector branch
* add special handling for symbols and exact-token queries

### Risk: one source dominates results

Mitigation:

* cap chunks per document during final selection

## Concrete first deliverable

The smallest production-worthy version would include:

### Database

```sql
ALTER TABLE intelligence.chunks
ADD COLUMN search_tsv tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('english', coalesce(content, '')), 'D')
) STORED;

CREATE INDEX chunks_search_tsv_idx
ON intelligence.chunks
USING gin (search_tsv);
```

### Lexical query

```sql
SELECT
  c.id,
  c.document_id,
  c.chunk_index,
  c.title,
  c.content,
  ts_rank(c.search_tsv, plainto_tsquery('english', $1)) AS lexical_score
FROM intelligence.chunks c
WHERE c.search_tsv @@ plainto_tsquery('english', $1)
ORDER BY lexical_score DESC
LIMIT 30;
```

### Vector query

```sql
SELECT
  c.id,
  c.document_id,
  c.chunk_index,
  c.title,
  c.content,
  1 - (c.embedding <=> $1::vector) AS semantic_score
FROM intelligence.chunks c
WHERE c.embedding IS NOT NULL
ORDER BY c.embedding <=> $1::vector
LIMIT 40;
```

### App rerank

```python
hybrid_score = 0.35 * lexical_norm + 0.65 * semantic_norm
```

## Recommended implementation order

1. add `search_tsv` and GIN index
2. build lexical retrieval query
3. add merge + rerank in app code
4. benchmark against vector-only
5. enable hybrid in RAG path
6. tune weights and candidate sizes
7. optionally add HNSW if vector latency needs work

That order also lines up with the broader upgrade path already documented in your project notes.

If you want, I’ll turn this into a **project-specific task breakdown** next: migrations, Python service code, repository methods, and FastAPI endpoint changes.

[1]: https://www.postgresql.org/docs/current/textsearch-controls.html "PostgreSQL: Documentation: 18: 12.3. Controlling Text Search"
[2]: https://www.postgresql.org/docs/current/ddl-generated-columns.html "PostgreSQL: Documentation: 18: 5.4. Generated Columns"
[3]: https://github.com/pgvector/pgvector "GitHub - pgvector/pgvector: Open-source vector similarity search for Postgres · GitHub"
