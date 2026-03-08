# External Integrations

## Databases

### PostgreSQL 16 + pgvector

- **Image**: `pgvector/pgvector:pg16` (Docker)
- **Connection**: via `DATABASE_URL` env var; normalised from optional SQLAlchemy format in `shared/database.py`
- **Async driver**: `asyncpg` (all API/FastAPI paths)
- **Sync driver**: `psycopg2-binary` (Celery task paths — no running event loop)
- **Schema**: `intelligence` schema, auto-created from `schema.sql` on first boot

#### Tables

| Table | Purpose |
|-------|---------|
| `intelligence.documents` | Full articles — `VECTOR(768)` embedding, `content_hash` dedup, JSONB metadata |
| `intelligence.chunks` | 500-token text chunks — `VECTOR(768)` embedding + generated `tsvector` for hybrid search |
| `intelligence.feeds` | RSS feed registry |
| `intelligence.feed_entries` | Per-entry tracking with status and `document_id` FK |

#### Vector Index

HNSW index on `chunks.embedding` using cosine ops (defined in `schema.sql`):

```sql
CREATE INDEX idx_chunks_embedding_hnsw
ON intelligence.chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

`hnsw.ef_search` is tunable per-session via `DatabaseManager.set_search_params()`.

#### Full-Text Search

`chunks.search_tsv` is a `GENERATED ALWAYS AS` stored `tsvector` with weighted FTS:

```sql
setweight(to_tsvector('english', title),   'A')  -- weight 1.0
|| setweight(to_tsvector('english', content), 'D')  -- weight 0.1
```

GIN index: `idx_chunks_search_tsv`.

#### Key SQL Functions (in `intelligence` schema)

- `find_similar_chunks(embedding, limit, threshold)` — vector similarity
- `find_similar_documents(embedding, limit, threshold)` — document-level search
- `get_rag_context(embedding, limit, threshold)` — RAG context assembly
- `upsert_feed(url, title, ...)` — RSS feed management
- `is_entry_processed(feed_id, hash, url)` — duplicate detection
- `record_feed_entry(...)` — entry ingestion tracking

### Redis 7

- **Image**: `redis:7` (Docker), exposed on port 6380 (host) → 6379 (container)
- **Purpose 1**: Celery message broker (`CELERY_BROKER_URL` → DB index 0)
- **Purpose 2**: Celery result backend (`CELERY_RESULT_BACKEND` → DB index 1)
- **Client**: `redis==5.0.1`
- No application-layer caching beyond Celery; no explicit key-expiry patterns in codebase

---

## External Services

### Ollama (Local AI)

- **Connection**: HTTP REST via `httpx` in `shared/ollama_client.py`
- **URL**: `OLLAMA_HOST` env var (default `http://host.docker.internal:11434` for Docker → host bridge)
- **No external API keys** — fully local inference

#### Embedding Model
- **Default**: `nomic-embed-text` (`RAG_EMBEDDING_MODEL`)
- **Dimensions**: 768
- **Endpoint called**: `POST /api/embeddings` with `{"model": ..., "prompt": ...}`
- **Timeout**: 60 seconds

#### Chat / Generation Model
- **Default**: `llama3.2` (`RAG_CHAT_MODEL`)
- **Endpoint called**: `POST /api/generate` with `{"model": ..., "prompt": ..., "stream": false}`
- **Timeout**: 120 seconds
- **Context format**: `Context: {chunks}\n\nQuestion: {query}\n\nAnswer:`

#### Model Availability Check
`OllamaClient.check_model_available()` calls `GET /api/tags` and scans the `models[].name` list. Called at worker startup via `embedding_manager.ensure_models_available()`.

### Sentence-Transformers (Local Reranking)

- **Package**: `sentence-transformers` (pyproject.toml)
- **Used in**: `shared/reranker.py` — cross-encoder reranking of hybrid search candidates
- **No network calls** — model weights loaded locally
- **Policy**: `shared/rerank_policy.py` implements `selective` / `always` / `off` modes

---

## Message Queue / Workers

### Celery

- **Version**: 5.3.4
- **Broker**: Redis (DB 0)
- **Backend**: Redis (DB 1)
- **Config file**: `worker/celery_app.py`

#### Celery Configuration

```python
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,        # 30 min hard limit
    task_soft_time_limit=25 * 60,   # 25 min soft limit
    worker_prefetch_multiplier=1,   # one task at a time per worker process
    worker_max_tasks_per_child=1000,
    broker_connection_retry_on_startup=True,
)
```

#### Task Definitions (`worker/tasks.py`)

Tasks handle async-to-sync conversion by spawning a new event loop (`asyncio.new_event_loop()`), since Celery workers run in synchronous processes.

#### Async Task Dispatch (from API)

`shared/celery_client.py` provides a `celery_app` instance used by the FastAPI service to enqueue tasks via `.delay()`. Task status is polled via `GET /tasks/{task_id}` using `celery.result.AsyncResult`.

#### Flower Monitoring

- **Port**: 5555
- **Image**: same `worker/Dockerfile` with `flower` extra
- **Access**: `http://localhost:5555`

---

## Authentication

### Static API Key

- **Mechanism**: Single shared API key in `X-API-Key` HTTP header
- **Implementation**: `api/auth.py` — `require_api_key` FastAPI dependency
- **Configuration**: `API_KEY` environment variable
- **Applied to**: All write and admin endpoints via `Depends(require_api_key)`

```python
# api/auth.py
async def require_api_key(x_api_key: str | None = Header(..., alias="X-API-Key")) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

**No key configured** → 500 error  
**Wrong key** → 401 Unauthorized  
**Public endpoints** (search, RAG, health, stats, list) → no authentication required

### SSRF Protection

URL ingestion (`POST /articles/url/async`) validates fetched URLs against blocked CIDR ranges in `shared/url_ingestion.py`:

```
127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
169.254.0.0/16, ::1/128, fe80::/10, fc00::/7
```

---

## RSS Feed Ingestion

- **Parser**: `feedparser==6.0.10` in `shared/rss_parser.py`
- **Endpoint**: `POST /feeds/async` (authenticated) → queues Celery task
- **Dedup**: `intelligence.is_entry_processed()` checks `entry_hash` + `entry_url` before processing
- **State tracking**: `intelligence.feed_entries` table records status (`pending` / `processed` / `failed`)

---

## Telemetry / Observability

Internal-only telemetry collected in `shared/telemetry.py` as `PolicyTrace` dataclass:

- Per-request: `query_id` (UUID), `query_type`, `confidence_score`, `confidence_band`
- Retrieval metadata: `chunks_retrieved`, `retrieval_mode`, `latency_ms`
- Quality signals: `groundedness_score`, `citation_accuracy`, `quality_score`

Stored in-process; no external observability backend (no OpenTelemetry, Datadog, etc.).

Policy evaluation and calibration scripts in `scripts/` (`calibration_report.py`, `tune_thresholds.py`) process telemetry offline against `evaluation/default_test_suite.json`.
