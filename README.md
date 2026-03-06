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

### 1. Start the Services

```bash
# Start PostgreSQL, Redis, API, and Worker
docker-compose up -d

# Wait for services to be ready
sleep 10
```

### 2. Initialize the Database Schema

```bash
# Connect to the database
docker exec -it article_index_db_1 psql -U articles -d articles

# Run the schema setup
\i /docker-entrypoint-initdb.d/schema.sql
```

### 3. Start Ollama and Pull Models

```bash
# Start Ollama (if not already running)
ollama serve

# Pull required models
ollama pull nomic-embed-text  # For embeddings
ollama pull llama3.2          # For text generation
```

### 4. Test the API

```bash
# Health check
curl http://localhost:999/health

# Create a test article
curl -X POST http://localhost:999/articles/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Article",
    "content": "This is a test article about artificial intelligence and machine learning. AI is transforming many industries."
  }'

# Search for similar content
curl -X POST http://localhost:999/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning",
    "limit": 5
  }'

# Ask a question with RAG
curl -X POST http://localhost:999/rag \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What industries is AI transforming?"
  }'
```

## API Endpoints

### Articles
- `POST /articles/` - Create a new article
- `POST /articles/html` - Create article from HTML
- `POST /articles/batch` - Create multiple articles
- `GET /articles/` - List articles with pagination
- `GET /articles/{id}` - Get specific article with chunks

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
