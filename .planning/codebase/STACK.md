# Technology Stack

## Runtime & Language

- **Python 3.13+** (declared in `pyproject.toml`: `requires-python = ">=3.13"`)
- Async-first codebase using `async`/`await` throughout all I/O paths
- Containerised via Docker with separate images for `api` and `worker` services

## Frameworks

| Framework | Version | Purpose |
|-----------|---------|---------|
| **FastAPI** | 0.104.1 | REST API layer (`api/app.py`, ~2310 lines) |
| **Uvicorn** | 0.24.0 (standard extras) | ASGI server for FastAPI |
| **Celery** | 5.3.4 | Distributed task queue for background article processing |
| **Pydantic** | 2.5.0 | Request/response validation, settings models |
| **Flower** | 2.0.1 | Celery worker monitoring dashboard (port 5555) |

## Key Dependencies

### Database & Storage
| Package | Version | Role |
|---------|---------|------|
| `asyncpg` | 0.29.0 | Async PostgreSQL driver (used in `shared/database.py` for all API-path queries) |
| `psycopg2-binary` | 2.9.9 | Sync PostgreSQL driver (used in Celery task paths where no event loop is available) |
| `redis` | 5.0.1 | Redis client — Celery broker + result backend |

### AI / NLP
| Package | Version | Role |
|---------|---------|------|
| `httpx` | 0.25.2 | Async HTTP client for Ollama API calls (`shared/ollama_client.py`) |
| `tiktoken` | 0.5.2 | Token counting for text chunking (`shared/processor.py`) |
| `numpy` | 1.24.3 | Vector arithmetic, embedding manipulation |
| `rank_bm25` | (pyproject) | BM25 lexical scoring in `shared/hybrid_retriever.py` |
| `sentence-transformers` | (pyproject) | Local cross-encoder reranking in `shared/reranker.py` |
| `scikit-learn` | (pyproject) | ML utilities (calibration, evaluation pipeline) |
| `nltk` | (pyproject) | Natural language pre-processing |

### Web / Ingestion
| Package | Version | Role |
|---------|---------|------|
| `beautifulsoup4` | 4.12.2 | HTML article parsing (`api` HTML ingestion endpoints) |
| `feedparser` | 6.0.10 | RSS feed parsing (`shared/rss_parser.py`) |
| `python-multipart` | 0.0.6 | Multipart form support for FastAPI |

### Utilities
| Package | Version | Role |
|---------|---------|------|
| `python-dotenv` | 1.0.0 | `.env` file loading |
| `sqlalchemy` | 2.0.23 | ORM (used in API service; async via asyncpg) |
| `pandas` | (pyproject) | Evaluation / calibration scripts in `scripts/` |
| `tqdm` | (pyproject) | Progress bars in batch/evaluation scripts |

## Configuration

All configuration is managed through **environment variables**, loaded at startup from a `.env` file (via `python-dotenv`) or injected by Docker Compose.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://article_index:article_index@db:5432/article_index` | PostgreSQL connection (asyncpg/psycopg2 auto-normalised from SQLAlchemy URL format) |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery message broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` | Celery result store (separate DB index) |
| `API_KEY` | `change-me-long-random` | Static API key for protected endpoints |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama server URL |
| `RAG_EMBEDDING_MODEL` | `nomic-embed-text` | 768-dimensional embedding model |
| `RAG_CHAT_MODEL` | `llama3.2` | LLM for RAG answer generation |
| `RAG_SIMILARITY_THRESHOLD` | `0.7` | Minimum cosine similarity for search results |
| `RAG_CONTEXT_LIMIT` | `5` | Max chunks passed to RAG context |
| `CHUNK_SIZE` | `500` | Token target per text chunk |
| `CHUNK_OVERLAP` | `50` | Overlap tokens between adjacent chunks |
| `MAX_CONCURRENT_PROCESSING` | `3` | Celery worker concurrency |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Config Pattern

`shared/database.py` reads `DATABASE_URL` directly via `os.getenv()`. URL normalisation strips SQLAlchemy driver suffixes (`postgresql+asyncpg://` → `postgresql://`) so both `asyncpg` and `psycopg2` can use the same value.

Pydantic `BaseModel` / `pydantic-settings` is used for request validation; runtime config relies on raw `os.getenv()` calls rather than a central settings class.

## Docker / Deployment

Multi-service Docker Compose stack (`docker-compose.yml`):

```
api       → api/Dockerfile      (FastAPI on port 8001→8000)
worker    → worker/Dockerfile   (Celery worker)
flower    → worker/Dockerfile   (Celery Flower on port 5555)
db        → pgvector/pgvector:pg16
redis     → redis:7
```

SQL schema is auto-applied at container start via Docker entrypoint mounts:
- `schema.sql` → `/docker-entrypoint-initdb.d/01-schema.sql`
- `indexes.sql` → `/docker-entrypoint-initdb.d/02-indexes.sql`

Manual migrations live in `migrations/` (e.g., `001_add_content_hash.sql`, `004_add_hybrid_search.sql`).
