# Article Index with pgvector-rag

A semantic search and RAG (Retrieval-Augmented Generation) system for articles using PostgreSQL with pgvector and Ollama for local AI processing.

## Features

- **Semantic Search**: Find articles using vector similarity search
- **RAG Q&A**: Ask questions and get answers based on your article content
- **Local AI Processing**: Uses Ollama for embeddings and text generation (no external API fees)
- **Batch Processing**: Process multiple articles efficiently
- **Background Tasks**: Async processing with Celery workers
- **Performance Optimized**: Vector indexes for fast similarity search

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env if needed (defaults work for Docker)
```

### 2. Start the Services

```bash
docker compose up -d
# Wait for services to be ready (~15s)
sleep 15
```

Schema and indexes are applied automatically via `schema.sql` and `indexes.sql` in `docker-entrypoint-initdb.d/`.

### 3. Start Ollama and Pull Models

```bash
ollama serve
ollama pull nomic-embed-text
ollama pull llama3.2
```

### 4. Smoke Test

```bash
./scripts/smoke_test.sh
# Or with custom API base: API_BASE=http://localhost:8001 ./scripts/smoke_test.sh
```

### 5. Manual API Test

All write/admin endpoints require `X-API-Key` header (from `.env` API_KEY).

```bash
# Health (no auth)
curl http://localhost:8001/health

# Create article (sync)
curl -X POST http://localhost:8001/articles/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-long-random" \
  -d '{"title": "Test", "content": "AI and machine learning."}'

# Async ingestion (returns task_id)
curl -X POST http://localhost:8001/articles/async \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-long-random" \
  -d '{"title": "Async Test", "content": "Background processing."}'

# URL ingestion (fetches, extracts text, enqueues)
curl -X POST http://localhost:8001/articles/url/async \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-long-random" \
  -d '{"url": "https://example.com/article"}'

# Task status (no auth)
curl http://localhost:8001/tasks/<task_id>

# Search (no auth)
curl -X POST http://localhost:8001/search \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "limit": 5}'

# RAG (no auth)
curl -X POST http://localhost:8001/rag \
  -H "Content-Type: application/json" \
  -d '{"question": "What is AI?"}'
```

### Reset

```bash
docker-compose down -v
docker-compose up -d
# Wait, then run smoke test
sleep 15 && ./scripts/smoke_test.sh
```

## API Endpoints

### Articles (all write endpoints require `X-API-Key`)
- `POST /articles/` - Create article (sync, blocks until done)
- `POST /articles/async` - Enqueue article for background processing (returns `task_id`)
- `POST /articles/url/async` - Fetch URL, extract text, enqueue (SSRF-protected)
- `POST /articles/html` - Create article from HTML (sync)
- `POST /articles/html/async` - Enqueue HTML article (async)
- `POST /articles/batch` - Create multiple articles
- `GET /articles/` - List articles with pagination
- `GET /articles/{id}` - Get specific article with chunks

### Tasks
- `GET /tasks/{task_id}` - Get async task status and result

### Search & RAG
- `POST /search` - Semantic search (chunks or documents)
- `POST /rag` - Question answering with RAG

### Admin & Health
- `GET /health` - System health check
- `GET /stats` - Database statistics
- `POST /admin/reindex/{id}` - Reindex article embeddings
- `POST /admin/models/check` - Check Ollama models

## Configuration

Copy `.env.example` to `.env` and adjust settings:

```bash
cp .env.example .env
```

Key environment variables:
- `DATABASE_URL`: PostgreSQL connection string
- `API_KEY`: Required for write/admin endpoints (must be set)
- `REDIS_URL`: Redis connection for Celery
- `OLLAMA_HOST`: Ollama server URL
- `RAG_EMBEDDING_MODEL`: Model for embeddings (default: nomic-embed-text)
- `RAG_CHAT_MODEL`: Model for generation (default: llama3.2)

## Architecture

```
Article → Chunking → Embedding → PostgreSQL Storage
                                         ↓
Query → Embedding → Similarity Search → Context Retrieval → LLM Response
```

### Components

1. **Database Schema** (`schema.sql`)
   - `intelligence.documents`: Full articles with metadata
   - `intelligence.chunks`: Smaller text pieces for better retrieval
   - Similarity search functions with configurable thresholds

2. **API Service** (`api/`)
   - FastAPI with async endpoints
   - Ollama client for embeddings and generation
   - Database repository with vector operations

3. **Worker Service** (`worker/`)
   - Celery tasks for background processing
   - Batch article processing
   - Embedding updates and maintenance

## Phase 2: Confidence-Driven Routing

The RAG pipeline implements a four-path execution model based on calibrated confidence scores:

### Confidence Bands & Execution Paths

| Confidence Band | Score Range | Behavior | Latency |
|---|---|---|---|
| **High** | >= 0.85 | Fast path: Base retrieval only, direct generation | Lowest |
| **Medium** | 0.65-0.84 | Standard path: Conditional reranking via uncertainty gates | Medium |
| **Low** | 0.45-0.64 | Cautious path: Expanded retrieval + mandatory reranking | Highest |
| **Insufficient** | < 0.45 | Abstain path: No generation, return error | Fast (early exit) |

### Standard Path Uncertainty Gates

For medium-confidence queries, the system checks numeric gates before deciding whether to rerank:

1. **Score Gap Gate**: If top-1 and top-2 scores differ by < 0.15, invoke reranker
2. **Top Strength Gate**: If top-1 score < 0.6, invoke reranker
3. **Conflict Gate**: If contradictory passages detected, invoke reranker

If all gates pass, use base evidence without reranking.

### Configuration

Tunable thresholds in `.env`:
- `CONFIDENCE_HIGH` (default: 0.85)
- `CONFIDENCE_MEDIUM` (default: 0.65)
- `CONFIDENCE_LOW` (default: 0.45)
- `UNCERTAINTY_SCORE_GAP_THRESHOLD` (default: 0.15)
- `UNCERTAINTY_MIN_TOP_STRENGTH` (default: 0.6)

### Observability

Telemetry fields track Phase 2 behavior:
- `execution_path`: Which path was taken (fast/standard/cautious/abstain)
- `confidence_band`: Which confidence band triggered routing
- `reranker_invoked`: Whether reranker was called
- `reranker_reason`: Why (score_gap, weak_evidence, conflict, cautious_path_mandatory)
- `tokens_generated`/`tokens_total`: Token usage by path

## Migrations

For existing databases, run migrations manually:

```bash
# Add content_hash for duplicate detection
docker exec -i article_index-db psql -U article_index -d article_index \
  < migrations/001_add_content_hash.sql
```

## Performance Optimization

After adding data, create vector indexes for better performance:

```bash
# Connect to database
docker exec -it article_index_db_1 psql -U articles -d articles

# Create performance indexes
\i /docker-entrypoint-initdb.d/indexes.sql
```

## Monitoring

- Check API health: `GET /health`
- Monitor database stats: `GET /stats`
- Worker health: Celery provides built-in monitoring
- **Flower**: Celery dashboard at `http://localhost:5555` (queue health, tasks, workers)

## Troubleshooting

### Common Issues

1. **"type vector does not exist"**
   - Ensure pgvector extension is installed (included in Docker image)

2. **"Connection refused" to Ollama**
   - Start Ollama: `ollama serve`
   - Check OLLAMA_HOST environment variable

3. **"Model not found"**
   - Pull models: `ollama pull nomic-embed-text` and `ollama pull llama3.2`

4. **Slow queries**
   - Create vector indexes (see Performance Optimization)
   - Adjust similarity thresholds

5. **Memory issues**
   - Reduce batch sizes
   - Adjust worker concurrency

### Logs

```bash
docker logs article_index-api
docker logs article_index-worker
docker logs article_index-db
```

## Development

### Adding New Features

1. **New Search Types**: Extend the similarity functions in `schema.sql`
2. **Custom Processing**: Modify `processor.py` for new text processing logic
3. **Additional Models**: Update environment variables and Ollama client

### Testing

```bash
pip install -r requirements-dev.txt

# Integration tests (requires running stack + Ollama)
make test
# or: API_BASE=http://localhost:8001 pytest tests/ -v -m integration

# Smoke test
make smoke

# Manual testing
bash test_api.sh
```

## License

MIT License - see LICENSE file for details.
