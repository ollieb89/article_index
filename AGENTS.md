# Article Index - AI Coding Agent Guide

## Project Overview

Article Index is a semantic search and RAG (Retrieval-Augmented Generation) system for articles using PostgreSQL with pgvector and Ollama for local AI processing. The system enables:

- **Semantic Search**: Find articles using vector similarity search
- **RAG Q&A**: Ask questions and get answers based on article content
- **Local AI Processing**: Uses Ollama for embeddings and text generation (no external API fees)
- **Batch Processing**: Process multiple articles efficiently
- **Background Tasks**: Async processing with Celery workers
- **RSS Feed Ingestion**: Automatically ingest and process RSS feeds

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐
│   Client    │────▶│  FastAPI    │────▶│   PostgreSQL    │
│             │◀────│   (API)     │◀────│   + pgvector    │
└─────────────┘     └──────┬──────┘     └─────────────────┘
                           │
                           ▼
                    ┌─────────────┐     ┌─────────────────┐
                    │    Redis    │────▶│  Celery Worker  │
                    │  (Queue)    │◀────│   (async jobs)  │
                    └─────────────┘     └─────────────────┘
                           │                     │
                           └─────────────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Ollama    │
                    │  (Local AI) │
                    └─────────────┘
```

### Services (Docker Compose)

| Service | Image/Build | Port | Description |
|---------|-------------|------|-------------|
| `db` | `pgvector/pgvector:pg16` | 5432 | PostgreSQL with pgvector extension |
| `redis` | `redis:7` | 6379 | Message broker and result backend for Celery |
| `api` | `api/Dockerfile` | 8000 | FastAPI REST service (exposed on 8001) |
| `worker` | `worker/Dockerfile` | - | Celery worker for background tasks |
| `flower` | `worker/Dockerfile` | 5555 | Celery monitoring dashboard |

## Directory Structure

```
article_index/
├── api/                    # FastAPI service
│   ├── app.py             # Main FastAPI application (endpoints)
│   ├── auth.py            # API key authentication
│   ├── requirements.txt   # API-specific dependencies
│   └── Dockerfile         # API service container
├── worker/                 # Celery worker service
│   ├── app.py             # Worker entry point
│   ├── celery_app.py      # Celery configuration
│   ├── tasks.py           # Background task definitions
│   ├── requirements.txt   # Worker-specific dependencies
│   └── Dockerfile         # Worker container
├── shared/                 # Shared modules (mounted in both services)
│   ├── database.py        # PostgreSQL repository and connection
│   ├── ollama_client.py   # Ollama API client + text processing
│   ├── processor.py       # Article chunking and embedding pipeline
│   ├── rss_parser.py      # RSS feed parsing
│   ├── url_ingestion.py   # URL fetching with SSRF protection
│   └── celery_client.py   # Celery client for API
├── tests/                  # Integration tests
│   ├── conftest.py        # Pytest configuration
│   ├── test_async_ingestion.py
│   └── test_async_failure.py
├── scripts/                # Utility scripts
│   └── smoke_test.sh      # Smoke test script
├── migrations/             # Database migrations
│   ├── 001_add_content_hash.sql
│   └── 003_add_rss_support.sql
├── schema.sql             # Database schema (auto-applied on startup)
├── indexes.sql            # Performance indexes (auto-applied)
├── docker-compose.yml     # Multi-service orchestration
├── Makefile               # Development commands
├── requirements-shared.txt # Shared Python dependencies
├── requirements-dev.txt   # Development dependencies
└── .env.example           # Environment configuration template
```

## Technology Stack

- **Python**: 3.11+ (async/await throughout)
- **FastAPI**: 0.104.1 - REST API framework
- **Celery**: 5.3.4 - Distributed task queue
- **PostgreSQL**: 16 with pgvector extension - Vector database
- **Redis**: 7 - Message broker and caching
- **Ollama**: Local AI for embeddings (nomic-embed-text) and chat (llama3.2)
- **SQLAlchemy**: 2.0.23 - ORM (async via asyncpg)
- **Pydantic**: 2.5.0 - Data validation

## Build and Test Commands

Use the Makefile for common operations:

```bash
# Start all services (builds images if needed)
make up

# Stop all services
make down

# Rebuild images
make build

# Run integration tests (requires running stack + Ollama)
make test

# Run smoke test
make smoke

# Tail logs from API, worker, and flower
make logs

# Reset everything (down with volumes, then up)
make reset

# Override API base URL
API_BASE=http://localhost:8001 make test
```

### Manual Testing

```bash
# Health check (no auth)
curl http://localhost:8001/health

# Create article (sync) - requires API key
curl -X POST http://localhost:8001/articles/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-long-random" \
  -d '{"title": "Test", "content": "AI and machine learning."}'

# Search (no auth)
curl -X POST http://localhost:8001/search \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "limit": 5}'

# RAG query (no auth)
curl -X POST http://localhost:8001/rag \
  -H "Content-Type: application/json" \
  -d '{"question": "What is AI?"}'
```

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

### Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://article_index:article_index@db:5432/article_index` | PostgreSQL connection string |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `API_KEY` | `change-me-long-random` | Required for write/admin endpoints |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama server URL |
| `RAG_EMBEDDING_MODEL` | `nomic-embed-text` | Model for embeddings |
| `RAG_CHAT_MODEL` | `llama3.2` | Model for text generation |
| `RAG_SIMILARITY_THRESHOLD` | `0.7` | Minimum similarity for search results |
| `RAG_CONTEXT_LIMIT` | `5` | Max chunks for RAG context |
| `MAX_CONCURRENT_PROCESSING` | `3` | Worker concurrency limit |
| `CHUNK_SIZE` | `500` | Text chunk size (tokens) |
| `CHUNK_OVERLAP` | `50` | Chunk overlap (tokens) |

## API Endpoints

### Public Endpoints (no authentication)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | System health check with Ollama status |
| GET | `/stats` | Database statistics |
| GET | `/articles/` | List articles with pagination |
| GET | `/articles/{id}` | Get specific article with chunks |
| GET | `/feeds/` | List RSS feeds |
| GET | `/feeds/{id}/stats` | Get feed statistics |
| GET | `/tasks/{task_id}` | Check async task status |
| POST | `/search` | Semantic search (chunks or documents) |
| POST | `/rag` | RAG question answering |

### Protected Endpoints (requires `X-API-Key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/articles/` | Create article (sync) |
| POST | `/articles/async` | Enqueue article (async) |
| POST | `/articles/html` | Create from HTML (sync) |
| POST | `/articles/html/async` | Enqueue HTML article |
| POST | `/articles/url/async` | Fetch URL and enqueue |
| POST | `/articles/batch` | Create multiple articles |
| POST | `/feeds/async` | Process RSS feed |
| POST | `/admin/reindex/{id}` | Reindex article embeddings |
| POST | `/admin/models/check` | Check Ollama models |

## Code Style Guidelines

### Python Style

- **Async-first**: All I/O operations use `async`/`await`
- **Type hints**: Use typing for function signatures (`Optional`, `Dict`, `List`, etc.)
- **Docstrings**: Google-style docstrings for classes and functions
- **Logging**: Use `logging.getLogger(__name__)` throughout
- **Error handling**: Use specific exceptions, log errors with context

### Naming Conventions

- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions/Variables**: `snake_case`
- **Constants**: `UPPER_CASE`
- **Private**: `_leading_underscore`

### Code Organization

```python
# Standard library imports
import logging
from typing import Optional, Dict, Any

# Third-party imports
import httpx
from fastapi import FastAPI

# Local imports
from shared.database import document_repo
from shared.ollama_client import OllamaClient

# Logger setup
logger = logging.getLogger(__name__)

# Constants
DEFAULT_CHUNK_SIZE = 500

# Classes
class ArticleProcessor:
    """Docstring describing the class."""
    pass

# Functions
async def process_article(title: str) -> Dict[str, Any]:
    """Docstring describing the function."""
    pass
```

## Testing Strategy

### Test Types

1. **Unit Tests**: None currently (would test individual functions in isolation)
2. **Integration Tests**: Located in `tests/`, require full running stack
   - `test_async_ingestion.py`: Tests async article processing pipeline
   - `test_async_failure.py`: Tests error handling

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run integration tests (requires docker compose up + Ollama)
make test

# Run specific test file
API_BASE=http://localhost:8001 pytest tests/test_async_ingestion.py -v

# Run smoke test
./scripts/smoke_test.sh
```

### Test Markers

- `@pytest.mark.integration`: Marks tests requiring running stack

## Database Schema

### Tables (in `intelligence` schema)

**documents** - Stores full articles
- `id` (SERIAL PRIMARY KEY)
- `title` (TEXT)
- `content` (TEXT) - Full article text
- `metadata` (JSONB) - Flexible metadata
- `embedding` (VECTOR(768)) - Document embedding
- `content_hash` (TEXT) - SHA256 for duplicate detection
- `source_url` (TEXT) - Original source URL
- `created_at`, `updated_at` (TIMESTAMPTZ)

**chunks** - Stores text chunks for retrieval
- `id` (SERIAL PRIMARY KEY)
- `document_id` (INTEGER, FK)
- `content` (TEXT) - Chunk text
- `embedding` (VECTOR(768)) - Chunk embedding
- `chunk_index` (INTEGER) - Position in document
- `created_at` (TIMESTAMPTZ)

**feeds** - RSS feed tracking
- `id`, `url`, `title`, `description`
- `last_fetched_at`, `fetch_interval_minutes`
- `is_active`, `total_entries_fetched`, etc.

**feed_entries** - Individual RSS entries
- `id`, `feed_id` (FK), `entry_url`, `entry_hash`
- `published_at`, `processed_at`, `status`
- `document_id` (FK, nullable)

### Key SQL Functions

- `intelligence.find_similar_chunks(embedding, limit, threshold)`
- `intelligence.find_similar_documents(embedding, limit, threshold)`
- `intelligence.get_rag_context(embedding, limit, threshold)`
- `intelligence.upsert_feed(url, title, ...)`
- `intelligence.is_entry_processed(feed_id, hash, url)`
- `intelligence.record_feed_entry(...)`

## Security Considerations

### Authentication

- **API Key**: All write/admin endpoints require `X-API-Key` header
- Key is configured via `API_KEY` environment variable
- No key = 500 error, wrong key = 401 error

### SSRF Protection

URL ingestion (`POST /articles/url/async`) has SSRF protection:

```python
# Blocked networks in url_ingestion.py
127.0.0.0/8      # loopback
10.0.0.0/8       # private
172.16.0.0/12    # private
192.168.0.0/16   # private
169.254.0.0/16   # link-local
::1/128          # IPv6 loopback
fe80::/10        # IPv6 link-local
fc00::/7         # IPv6 unique local
```

### Environment Variables

- Never commit `.env` files
- Use `.env.example` for templates
- All secrets should come from environment

### Docker

- Services run as non-root where possible
- Databases use persistent volumes
- No sensitive data in container images

## Development Workflow

### Adding a New Endpoint

1. Add Pydantic model in `api/app.py` (if needed)
2. Implement endpoint function with proper auth decorator
3. Add business logic in `shared/` if reusable
4. Add tests in `tests/`
5. Update `README.md` if public API

### Adding a New Task

1. Define task in `worker/tasks.py`
2. Use `@celery_app.task(bind=True, max_retries=N)` decorator
3. Handle async-to-sync conversion with event loop
4. Add retry logic with exponential backoff
5. Import task in `worker/celery_app.py`

### Database Migrations

1. Create new SQL file in `migrations/` (e.g., `004_feature.sql`)
2. Run manually on existing databases:
   ```bash
   docker exec -i article_index-db psql -U article_index -d article_index < migrations/004_feature.sql
   ```
3. Update `schema.sql` for new deployments

## Common Issues

### "type vector does not exist"
- Ensure pgvector extension is installed (included in Docker image)
- Check `CREATE EXTENSION IF NOT EXISTS vector;` in schema.sql

### "Connection refused" to Ollama
- Start Ollama: `ollama serve`
- Check `OLLAMA_HOST` environment variable
- Ensure models are pulled: `ollama pull nomic-embed-text llama3.2`

### Slow Queries
- Create vector indexes: `indexes.sql` is auto-applied on startup
- Adjust similarity thresholds
- Check database stats: `GET /stats`

### Memory Issues
- Reduce `MAX_CONCURRENT_PROCESSING` in worker
- Reduce `CHUNK_SIZE` for smaller batches
- Monitor Flower dashboard at `http://localhost:5555`

## External Dependencies

### Ollama Models

Required models (auto-pulled on startup):
- `nomic-embed-text`: 768-dimensional embeddings
- `llama3.2`: Text generation for RAG

Manual pull if needed:
```bash
ollama pull nomic-embed-text
ollama pull llama3.2
```

### Python Packages

See `requirements-shared.txt`, `api/requirements.txt`, `worker/requirements.txt` for full dependency lists.

Key packages:
- `asyncpg==0.29.0` - Async PostgreSQL
- `psycopg2-binary==2.9.9` - Sync PostgreSQL (for Celery tasks)
- `httpx==0.25.2` - Async HTTP client
- `tiktoken==0.5.2` - Token counting for chunking
- `beautifulsoup4==4.12.2` - HTML parsing
- `feedparser==6.0.10` - RSS feed parsing
- `numpy==1.24.3` - Numerical operations
