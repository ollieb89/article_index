# Architecture

## Pattern

**Multi-service event-driven microservices** with a shared library layer.

The system is split into four runtime services (API, Worker, DB, Redis) backed by a `shared/` Python package that is volume-mounted into both the API and Worker containers. This avoids code duplication while keeping service boundaries clean. Communication between the API and Worker is asynchronous via a Redis-backed Celery task queue.

The RAG pipeline inside the system is implemented as a sophisticated, policy-driven chain: query classification → hybrid retrieval → reranking → evidence scoring → contextual routing → answer generation.

---

## Service Layers

| Service | Image / Build | Role |
|---------|--------------|------|
| `db` | `pgvector/pgvector:pg16` | PostgreSQL 16 with the `vector` extension. Stores documents, chunks, feeds, feed_entries, and policy tables in the `intelligence` schema. Initialised from `schema.sql` + `indexes.sql`. |
| `redis` | `redis:7` | Message broker (DB 0) and Celery result backend (DB 1). Decouples API from Worker. |
| `api` | `api/Dockerfile` | FastAPI REST service. Handles sync article ingestion, async task dispatch, search, and end-to-end RAG queries. Exposed on host port 8001. |
| `worker` | `worker/Dockerfile` | Celery worker. Runs CPU/IO-heavy article processing tasks in the background. Bridges async `shared/` code via `asyncio.new_event_loop()`. |
| `flower` | `worker/Dockerfile` | Celery Flower monitoring dashboard. Exposed on host port 5555. |
| `ollama` | External / host | Local AI model server (host port 11434). Provides `nomic-embed-text` (768-dim embeddings) and `llama3.2` (text generation). Reached via `host.docker.internal`. |

---

## Data Flow

### Sync Article Ingestion (`POST /articles/`)

```
Client → FastAPI → ArticleProcessor.process_article()
  → TextProcessor.clean_text()           (normalise)
  → SHA-256 duplicate check (DB)
  → DocumentRepository.create_document() (DB INSERT)
  → OllamaClient.generate_embedding()    (Ollama API)
  → DocumentRepository.update_document_embedding()
  → TextProcessor.chunk_text()           (token-based chunking)
  → [for each chunk] OllamaClient.generate_embedding()
  → DocumentRepository.create_chunks()   (bulk INSERT)
  → return {document_id, chunk_count, ...}
```

### Async Article Ingestion (`POST /articles/async`)

```
Client → FastAPI → celery_app.send_task('process_article_task')
  → return {task_id}

Worker (Celery) ← Redis queue
  → process_article_task(title, content, metadata)
  → asyncio.new_event_loop().run_until_complete(ArticleProcessor.process_article())
  → (same pipeline as sync path above)
  → result stored in Redis result backend
```

### Hybrid RAG Query (`POST /rag`)

```
Client → FastAPI /rag
  1. QueryClassifier.classify()               → QueryType (exact_fact, summarization, …)
  2. QueryTransformer.transform()              → expanded / reformulated query variants
  3. OllamaClient.generate_embedding(query)   → query_embedding (768-d)
  4. HybridRetriever.retrieve()
       a. PostgreSQL FTS (tsvector)            → lexical_candidates
       b. pgvector cosine similarity            → vector_candidates
       c. Merge + weighted score blend (or RRF)
  5. Reranker.rerank_with_decision()
       a. RerankPolicy.should_rerank()         → selective rerank decision
       b. OllamaClient cross-encoder scoring   → reranked chunks
  6. ContextFilter.filter()                   → drop low-quality chunks
  7. EvidenceScorer.score_evidence()          → ConfidenceScore (score + band)
  8. EvidenceShapeExtractor.extract()         → EvidenceShape metadata
  9. RetrievalStateLabeler.label()            → RetrievalState (SOLID/FRAGILE/CONFLICTED/…)
 10. ContextualRouter.route()                 → RouteDecision (action + execution_path)
 11. ContextBuilder.build()                   → formatted context string + CitationTracker
 12. OllamaClient.generate_text(prompt)       → answer text  (prompt chosen by action)
 13. PolicyTrace logged to DB                 → telemetry for closed-loop optimisation
  → return HybridRAGResponse {answer, source_citations, confidence, …}
```

### RSS Feed Ingestion (`POST /feeds/async`)

```
Client → FastAPI → celery_app.send_task('process_feed_task')

Worker:
  → RSSFeedParser.fetch_and_parse(url)
  → [for each new entry] process_article_task.delay(title, content)
  → record_feed_entry() in DB
```

---

## Key Abstractions

### `shared/database.py`

- **`DatabaseManager`** – connection factory supporting both `asyncpg` (async) and `psycopg2` (sync for Celery). Normalises `postgresql+asyncpg://` SQLAlchemy URLs to plain `postgresql://`.
- **`DocumentRepository`** – repository for CRUD on `intelligence.documents`, `intelligence.chunks`, and similarity search. Wraps all SQL, including calls to stored functions like `intelligence.find_similar_chunks()` and `intelligence.get_rag_context()`.
- **`PolicyRepository`** – CRUD for versioned `RAGPolicy` objects stored in the DB.

### `shared/processor.py`

- **`ArticleProcessor`** – orchestrates the full ingest pipeline: clean → hash → deduplicate → embed → chunk → embed chunks → store.
- **`EmbeddingManager`** – wrapper around `OllamaClient` for model availability checks and test generation.

### `shared/ollama_client.py`

- **`OllamaClient`** – async HTTP client (`httpx`) for Ollama REST API. Methods: `generate_embedding()`, `generate_text()`, `list_models()`.
- **`TextProcessor`** – text cleaning (`clean_text`) and token-based chunking (`chunk_text` via `tiktoken`).

### `shared/hybrid_retriever.py`

- **`HybridRetriever`** – two-stage retrieval: PostgreSQL FTS lexical candidates + pgvector vector candidates, merged via weighted score blending or Reciprocal Rank Fusion (RRF). Auto-detects query type (version numbers, acronyms, exact phrases) and adjusts `lexical_weight`/`semantic_weight` accordingly.

### `shared/reranker.py` + `shared/rerank_policy.py`

- **`Reranker`** – optional third stage: re-scores top-N hybrid results using Ollama similarity. Decision is delegated to `RerankPolicy`.
- **`RerankPolicy`** – supports three modes: `off`, `always`, `selective`. Selective mode examines query signals to decide whether reranking would help.

### `shared/evidence_scorer.py`

- **`EvidenceScorer`** – computes a `ConfidenceScore(score, band)` from top-k retrieval scores, score decay, lexical/vector agreement, rerank confidence, and query-transform diversity.
- **`ConfidenceBand`** – enum: `HIGH (>0.75)`, `MEDIUM (0.50–0.75)`, `LOW (0.25–0.50)`, `INSUFFICIENT (<0.25)`.

### `api/query_classifier.py`

- **`QueryClassifier`** – regex-based classification of user queries into `QueryType` (EXACT_FACT, COMPARISON, SUMMARIZATION, PROCEDURAL, MULTI_HOP, AMBIGUOUS, LIKELY_NO_ANSWER, UNKNOWN).

### `api/routing.py`

- **`ContextualRouter`** – takes a `RoutingContext` (query type + confidence band + retrieval state + policy) and returns a `RouteDecision` (action + execution_path + reason). Actions: `standard`, `expanded_retrieval`, `conservative_prompt`, `abstain`.

### `shared/policy.py`

- **`RAGPolicy`** – versioned dataclass holding confidence thresholds (per band), routing rules (per query type × band), contextual threshold overrides, and latency budgets. Loaded from DB at startup; hot-reloadable via admin endpoint.

### `shared/telemetry.py`

- **`PolicyTrace`** – per-request telemetry struct capturing query type, confidence, action, execution path, retrieval state, latency, and answer quality metrics. Persisted to DB as the feedback signal for closed-loop policy optimisation.

### `api/evidence_shape.py` + `api/retrieval_state.py`

- **`EvidenceShapeExtractor`** – extracts structural features (score distribution, coverage, lexical overlap) from retrieved chunks.
- **`RetrievalStateLabeler`** – maps evidence shape to a `RetrievalState` label (SOLID, FRAGILE, CONFLICTED, SPARSE, RECOVERABLE) used by the contextual router.

---

## Entry Points

| Entry Point | File | Description |
|-------------|------|-------------|
| FastAPI app | `api/app.py` – `lifespan()` + `app = FastAPI(...)` | HTTP REST API. Initialises DB connections, loads active `RAGPolicy`, builds Phase-14 module instances (QueryClassifier, EvidenceShapeExtractor, etc.). |
| Celery worker | `worker/app.py` (starts `celery_app`) | Background task runner. Imports task definitions from `worker/tasks.py`. |
| Celery config | `worker/celery_app.py` | Configures broker/backend URLs, task serialisation, and imports task modules. |
| DB schema init | `schema.sql`, `indexes.sql` | Auto-applied by PostgreSQL container on first start via `docker-entrypoint-initdb.d/`. |
| CLI / dev | `main.py` | Thin local dev entry point. |
