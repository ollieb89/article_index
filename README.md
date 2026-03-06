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
docker-compose up -d
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

```bash
# Health
curl http://localhost:8001/health

# Create article (sync)
curl -X POST http://localhost:8001/articles/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Test", "content": "AI and machine learning."}'

# Async ingestion (returns task_id)
curl -X POST http://localhost:8001/articles/async \
  -H "Content-Type: application/json" \
  -d '{"title": "Async Test", "content": "Background processing."}'

# Task status
curl http://localhost:8001/tasks/<task_id>

# Search
curl -X POST http://localhost:8001/search \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "limit": 5}'

# RAG
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

### Articles
- `POST /articles/` - Create article (sync, blocks until done)
- `POST /articles/async` - Enqueue article for background processing (returns `task_id`)
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
# API logs
docker logs article_index_api_1

# Worker logs
docker logs article_index_worker_1

# Database logs
docker logs article_index_db_1
```

## Development

### Adding New Features

1. **New Search Types**: Extend the similarity functions in `schema.sql`
2. **Custom Processing**: Modify `processor.py` for new text processing logic
3. **Additional Models**: Update environment variables and Ollama client

### Testing

```bash
# Run tests (add test suite)
python -m pytest tests/

# Manual testing with curl scripts
bash test_api.sh
```

## License

MIT License - see LICENSE file for details.
