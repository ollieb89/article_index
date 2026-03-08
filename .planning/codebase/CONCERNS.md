# Technical Concerns

## Technical Debt

### Critical: Broken `lifespan()` Function — Double Yield
**File:** `api/app.py`, lines ~155–350

The FastAPI `lifespan` async context manager contains **two `yield` statements**. The entire core initialization block — `HybridRetriever`, `QueryTransformer`, `ContextFilter`, `Reranker`, `ContextBuilder`, HNSW parameters, and `USE_HYBRID_RAG` flag — appears **after the first `yield`**, placing it inside the shutdown block rather than startup. Key consequences:

- `app.state.hybrid_retriever`, `query_transformer`, `context_filter`, `reranker`, `context_builder` are never set during startup.
- Any request to `/search/hybrid` or hybrid RAG that checks `request.app.state.hybrid_retriever` will either raise `AttributeError` or fall back to `None` via `getattr()` guards (silently degrading).
- The second `yield` at the end is invalid in `asynccontextmanager` and will produce `RuntimeError: generator didn't stop after throw()` on app shutdown.
- The "Startup complete" log and `set_search_params()` call also run during shutdown, misleadingly.

This appears to be a refactoring artifact where initialization code was added after an earlier `yield` and a second `yield` was added assuming the first was removed.

### Dead / Unreachable Code in `database.py`
**File:** `shared/database.py`, line 526

Inside `PolicyRepository.get_route_distribution()`, the `except` block contains:
```python
        except Exception as e:
            logger.error(f"Failed to get route distribution: {e}")
            return []
            return str(result['query_id'])   # ← UNREACHABLE; 'result' undefined here
```
The second `return` is unreachable and references a variable (`result`) that doesn't exist in this scope. It is dead code left by a cut-and-paste error.

### Stub Task That Does Nothing
**File:** `worker/tasks.py`, lines 163–173

`cleanup_old_embeddings_task` is declared with a placeholder body that logs a message and returns `"cleaned_items": 0`. No actual cleanup is performed. This task should either be implemented or removed.

### Hardcoded Chunk/Overlap in URL Ingestion Endpoint
**File:** `api/app.py` (the `/articles/url/async` handler)

The handler hard-codes `chunk_size=500, chunk_overlap=50` when enqueuing, ignoring any global environment config for `CHUNK_SIZE` / `CHUNK_OVERLAP`. Other async endpoints correctly forward these from the request model.

### Legacy Env Var Compatibility Layer
**File:** `api/app.py` (lifespan)

Two legacy compatibility shims add complexity without documentation of removal timelines:
- `HYBRID_USE_RRF` → `HYBRID_RANKING_MODE`
- `RERANK_ENABLED` → `RERANK_MODE`

Neither has a deprecation warning or a sunset plan.

### Runtime Mutation of Shared Retriever State
**File:** `api/app.py`, `/search/hybrid` handler

```python
retriever.lexical_weight = query.lexical_weight
retriever.lexical_limit = query.lexical_limit
```

The single `app.state.hybrid_retriever` instance is mutated per-request with per-query weights and limits. Under concurrent load, one request's settings will overwrite another's in a race condition.

---

## Security

### CORS Wildcard Origin
**File:** `api/app.py`, line 352

```python
allow_origins=["*"],  # Configure appropriately for production
```
`CORSMiddleware` is configured to allow all origins. The comment acknowledges this is wrong for production but there is no environment-variable-controlled override.

### Default Credentials in `.env.example`
**File:** `.env.example`, lines 3–5, 15

```
POSTGRES_PASSWORD=article_index
DATABASE_URL=postgresql://article_index:article_index@db:5432/article_index
API_KEY=change-me-long-random
```
If `.env.example` is copied as-is to `.env` (the documented workflow: `cp .env.example .env`), the system ships with trivially guessable database credentials and a placeholder API key.

### SSRF Bypass via Redirect Following
**File:** `shared/url_ingestion.py`, line 54

```python
async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
```
`validate_url()` checks only the initial URL's hostname. `httpx` with `follow_redirects=True` will silently follow an HTTP redirect from a public host to a private/loopback IP, bypassing the SSRF protection entirely.

### SQL Built with f-String
**File:** `shared/database.py`, `get_route_distribution()` (~line 481)

```python
query = f"""
    ...
    WHERE created_at > NOW() - INTERVAL '{days} days'
    ...
"""
```
`days` is an `int` parameter, so in practice this is safe, but the pattern is fragile and forbidden by parameterized query conventions. A future refactor that changes the type could introduce SQL injection.

### No Rate Limiting
No rate limiting is applied to any endpoint — public or protected. The `/search`, `/rag`, and `/search/hybrid` endpoints can be called freely, causing unbounded Ollama + database load.

### Uncapped URL Fetch Size
**File:** `shared/url_ingestion.py`

`fetch_url_text` reads the full response body in memory (`response.text`) with no content-length check or streaming limit. A server returning gigabytes of data will be buffered completely before extraction.

### Auth Error Leaks Server Configuration State
**File:** `api/auth.py`, lines 11–16

When `API_KEY` env var is not set, the server returns HTTP 500 with `"API key is not configured"`. This reveals a configuration gap to unauthenticated callers rather than returning 401/403.

---

## Performance

### No Database Connection Pooling
**File:** `shared/database.py`, `DatabaseManager`

Every database operation calls `asyncpg.connect()` and closes the connection on exit. There is no connection pool (`asyncpg.create_pool()`). Under any meaningful concurrency this creates N × M connection setup/teardown overhead per request, and risks exceeding PostgreSQL's `max_connections`.

### Sequential Per-Chunk Embedding Generation
**File:** `shared/processor.py`, lines 74–83

```python
for i, chunk_content in enumerate(chunks):
    chunk_embedding = await self.ollama.generate_embedding(chunk_content)
```
Each chunk embedding is fetched in a sequential `await` loop. For a 10-chunk article this is 10 serial HTTP round-trips to Ollama. `asyncio.gather()` with a semaphore, or Ollama's native batch endpoint, could reduce this substantially.

### Per-Request `OllamaClient` Instantiation
**Files:** `api/app.py` (multiple endpoints), `api/app.py` `_rag_hybrid()`, `_rag_vector_only()`

`OllamaClient()` is re-instantiated on every call to `/search`, `/health`, `/search/hybrid`, and inside the RAG helpers. An `httpx.AsyncClient` is also created and torn down per embedding call inside `OllamaClient.generate_embedding()`. The top-level `lifespan` creates `ollama_client` but it is not consistently used by the endpoints.

### Per-Row INSERT for Chunks
**File:** `shared/database.py`, `DocumentRepository.create_chunks()`, lines ~170–200

Chunks are inserted one at a time in a Python `for` loop. A single `executemany()` or `COPY` / multi-values `INSERT` would be significantly faster for large articles.

### Four Separate COUNT Queries in `get_stats()`
**File:** `shared/database.py`, `DocumentRepository.get_stats()`, lines ~347–366

Four independent `SELECT COUNT(*)` queries are issued in separate connections (via `asynccontextmanager`) instead of a single combined query or a single connection reuse.

### HNSW `ef_search` Set on a Transient Connection
**File:** `shared/database.py`, `DatabaseManager.set_search_params()` and `api/app.py` lifespan

`SET hnsw.ef_search = N` is a session-local GUC. With no connection pool, each new `asyncpg.connect()` call creates a fresh session with the default `ef_search` (typically 40), ignoring the value configured at startup. The tuning endpoint `/admin/vector-index/tune` also sets this only for one connection.

### `health_check` Runs Embedding + Generation Tests on Every Call
**File:** `api/app.py`, `/health` endpoint

Every `/health` call actually invokes Ollama to test both embedding and generation. This is expensive and inappropriate for liveness probes used by orchestrators (Kubernetes, ECS) that may poll every few seconds.

---

## Reliability

### Celery Tasks Create New Event Loops per Call
**File:** `worker/tasks.py`, all task functions

Every Celery task creates a new event loop (`asyncio.new_event_loop()`), runs async code, then closes it. This is a known anti-pattern that prevents proper resource reuse (e.g., connection caching, worker state). A single reusable event loop per worker process would be more stable.

### Silent Degradation in `hybrid_retriever.py` Failure Paths
**File:** `shared/hybrid_retriever.py`, lines 141, 171

Lexical or vector fetch failures are caught and an empty list is returned silently:
```python
except Exception as e:
    logger.error(f"Lexical search failed: {e}")
    return []
```
The caller in `retrieve()` receives a partial result with no indication that one search leg failed. Queries issued during a DB index rebuild or OOM may silently return only half the results without surfacing an error to the client.

### Telemetry Logging Silently Fails
**File:** `api/app.py`, `log_policy_telemetry()`

```python
async def log_policy_telemetry(trace: PolicyTrace):
    try:
        await policy_repo.log_telemetry(trace.to_dict())
    except Exception as e:
        logger.error(f"Telemetry logging failed: {e}")
```
If the `policy_telemetry` table is unavailable (migration not run, disk full, schema mismatch) telemetry is dropped silently. There is no circuit-breaker or metric tracking how often this happens.

### No Deduplication Lock — Race Condition on Concurrent Ingestion
**File:** `shared/processor.py`, `process_article()`, lines 38–50

The duplicate check (`get_document_by_content_hash()`) and the subsequent `create_document()` are not atomic. Two concurrent requests for the same article can both pass the check and both create duplicate documents. No `INSERT ... ON CONFLICT` or advisory lock is used.

### Retry Logic Bypassed in Batch Processing
**File:** `worker/tasks.py`, `batch_process_articles_task()`

The batch task retries the entire batch on failure (up to `max_retries=2`). If one article in a batch of 10 fails repeatedly, the other 9 are re-processed across retries — potentially creating duplicates (absent the hash check race above fixing them) or wasting compute.

### Worker Has No Celery Beat / Periodic Task Support
The `cleanup_old_embeddings_task` and feed re-fetch patterns suggest periodic work is intended, but no Celery Beat schedule is configured anywhere in `worker/celery_app.py` or `docker-compose.yml`. RSS feeds are only re-fetched when explicitly triggered.

---

## Fragile Areas

### `shared/` Mounted as Volume in Worker Container
**File:** `docker-compose.yml` (referenced in AGENTS.md)

The `shared/` directory is bind-mounted into both the `api` and `worker` containers. Any change to `shared/` immediately affects both services without a rebuild, which is convenient in development but means production deployments of the worker and API must always be updated together or risk schema/behavior divergence.

### `app.state` Used as Global Config Store
**File:** `api/app.py`, throughout

Component configuration is stored and retrieved from `request.app.state` using `getattr()` guards:
```python
query_transformer = getattr(request.app.state, 'query_transformer', None)
```
The set of expected attributes is never formally declared (no dataclass, no typed state object). Adding a new component requires updating every handler that might want to use it, with silent `None` fallback if forgotten.

### Runtime Threshold Mutation Without Persistence
**Files:** `api/app.py`, `/admin/rerank/tune`, `/admin/query-transform/tune`, `/admin/evidence/tune`

Admin endpoints mutate live in-memory thresholds on `app.state` objects. These changes are lost on restart. There is no mechanism to persist tuned values back to `.env` or the database, so manual recalibration is required after every deployment.

### Lifespan Initializes `ollama_client` Before `hybrid_retriever`
**File:** `api/app.py`, lifespan (see also Critical bug above)

The `ollama_client` singleton is initialized and `initialize()` is called before any of the retrieval components are set up. The retrieval components each instantiate their own `OllamaClient()` inline instead of sharing the lifespan-managed instance. If the lifespan bug is fixed, there is still unnecessary duplication.

### `evidenceshape.py` Contains Placeholder Logic
**File:** `api/evidence_shape.py`, line 71

```python
# (Placeholder for more advanced logic)
```
The `EvidenceShapeExtractor` is wired into the Phase 14 contextual routing path and influences routing decisions, but its implementation is incomplete.

---

## Missing Features

### No Connection Pooling (Repeat — Architectural Gap)
Implementing `asyncpg.create_pool()` would resolve multiple performance and reliability concerns but requires restructuring `DatabaseManager`, `DocumentRepository`, and `PolicyRepository` to acquire pooled connections rather than creating them at context manager entry.

### No Prometheus / Metrics Endpoint
Despite a sophisticated telemetry system logging to `policy_telemetry`, there is no `/metrics` endpoint for operational monitoring (request rates, P99 latency, Ollama error rates, queue depths). Observability relies entirely on reading the telemetry table via SQL or the Flower dashboard.

### No Structured Logging
`logging.basicConfig(level=logging.INFO)` is used throughout. Log lines are plain text with no JSON formatting, correlation IDs, or request tracing context. The `trace.query_id` exists but is not injected into log lines for that request's duration.

### No Unit Tests
The `tests/` directory contains only integration tests requiring a full running stack. There are no unit tests for individual modules (`hybrid_retriever`, `context_filter`, `evidence_scorer`, `reranker`, `query_transformer`), making refactoring risky.

### Missing Pagination `total` Count
`GET /articles/` returns articles and `"total": len(result)"` (the count of the current page, not the total in the database). Clients cannot build pagination UIs without a separate `COUNT(*)` query.

### No Request Size Limit
FastAPI is started without a configured `max_request_body_size`. A large content payload in `POST /articles/` or `POST /articles/html` could exhaust memory in the API container.

### Feed Re-Fetch Not Scheduled
RSS feeds are only processed on explicit `POST /feeds/async`. There is no automated periodic re-fetch, despite `fetch_interval_minutes` being stored per feed. Celery Beat is not configured.

### Ollama Model Pull is Fire-and-Forget
**File:** `shared/ollama_client.py`, `pull_model()`

`pull_model()` returns a bool but the response body (which is a streaming JSONL for Ollama) is not consumed. A `pull` returning `200` does not mean the model is ready; only the final status line confirms success. Model availability checks at startup log warnings but do not block startup, so a degraded or missing model may only surface on the first embedding request.
