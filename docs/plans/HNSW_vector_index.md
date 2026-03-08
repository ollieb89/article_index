# HNSW Vector Index Implementation Plan

## Current Status Overview

| Component | Status | Notes |
|-----------|--------|-------|
| HNSW in `schema.sql` | ✅ **IMPLEMENTED** | Created with `m=16, ef_construction=64` |
| HNSW in `indexes.sql` | ⚠️ **STALE** | Commented out (IVFFlat active instead) |
| HNSW in migration `004` | ✅ **IMPLEMENTED** | Conditional creation with `IF NOT EXISTS` check |
| SQL search functions | ✅ **IMPLEMENTED** | `find_similar_chunks()`, `find_similar_chunks_semantic()` |
| Hybrid search | ✅ **IMPLEMENTED** | Full lexical + semantic retrieval via `HybridRetriever` |
| Application code | ✅ **COMPATIBLE** | Uses proper vector operators (`<=>`) |

### What's Already Done

1. **Schema definition** (`schema.sql:49-53`): HNSW index is created by default for new deployments
2. **Migration** (`004_add_hybrid_search.sql:45-56`): Conditional HNSW creation for existing databases
3. **Hybrid search** (`shared/hybrid_retriever.py`): Full implementation with RRF and weight blending
4. **Context assembly** (`shared/context_builder.py`): Token budget management with citations
5. **API endpoints** (`api/app.py`): `/search/hybrid` and `/rag?mode=hybrid` are live

---

## Implementation Plan

### Phase 1: Cleanup & Alignment (5 minutes)

**Goal**: Synchronize all index definitions to use HNSW as the primary vector index.

#### 1.1 Update `indexes.sql` to Prefer HNSW

Replace the IVFFlat-as-default with HNSW-as-default:

```sql
-- Vector indexes for performance optimization

-- HNSW index for chunks (RECOMMENDED: better recall, faster queries)
-- Already created in schema.sql for new deployments
-- For existing deployments, run this manually or via migration 004
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_hnsw 
ON intelligence.chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- IVFFlat index for chunks (Alternative: lower memory, faster build)
-- Only use if memory constrained: requires training, less accurate
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_ivfflat 
-- ON intelligence.chunks 
-- USING ivfflat (embedding vector_cosine_ops)
-- WITH (lists = 100);

-- HNSW index for documents (for document-level similarity)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_embedding_hnsw 
ON intelligence.documents 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- GIN index for metadata JSONB
CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin 
ON intelligence.documents 
USING GIN (metadata);

-- GIN index for document title (for text search)
CREATE INDEX IF NOT EXISTS idx_documents_title_gin 
ON intelligence.documents 
USING GIN (to_tsvector('english', title));

-- GIN index for chunk content (for text search)
CREATE INDEX IF NOT EXISTS idx_chunks_content_gin 
ON intelligence.chunks 
USING GIN (to_tsvector('english', content));
```

#### 1.2 Update Migration 004 Comments

Add a header comment to `004_add_hybrid_search.sql`:

```sql
-- MIGRATION STATUS: HNSW index creation is CONDITIONAL
-- This migration safely adds HNSW if not already present.
-- For fresh installs, schema.sql already creates this index.
-- This migration ensures existing databases get the index.
```

---

### Phase 2: Verification & Testing (15 minutes)

**Goal**: Confirm HNSW is being used and measure performance improvement.

#### 2.1 Verify Index Usage

Run this SQL to check if HNSW is active:

```sql
-- Check index exists
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE schemaname = 'intelligence' 
  AND indexname LIKE '%embedding%';

-- Should show:
-- idx_chunks_embedding_hnsw | CREATE INDEX idx_chunks_embedding_hnsw ON intelligence.chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)
```

#### 2.2 Verify Query Plan Uses HNSW

```sql
-- Test with actual vector (requires valid embedding)
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT id, document_id, content, 1 - (embedding <=> '[0.1, ... 768 dims ...]'::vector) AS similarity
FROM intelligence.chunks
WHERE embedding IS NOT NULL
ORDER BY embedding <=> '[0.1, ... 768 dims ...]'::vector
LIMIT 10;

-- Expected output should contain:
-- ->  Index Scan using idx_chunks_embedding_hnsw on chunks
--       Order By: (embedding <=> '[...]'::vector)
```

#### 2.3 Smoke Test via API

```bash
# Test hybrid search (uses HNSW via find_similar_chunks_semantic)
curl -X POST http://localhost:8001/search/hybrid \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning optimization", "limit": 5}'

# Test RAG with hybrid mode
curl -X POST "http://localhost:8001/rag?mode=hybrid" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is vector search?", "context_limit": 5}'

# Health check confirms database status
curl http://localhost:8001/health
```

---

### Phase 3: Tuning Guide (Documentation)

**Goal**: Document HNSW tuning parameters for production workloads.

#### 3.1 HNSW Parameters Reference

| Parameter | Default | Range | Effect |
|-----------|---------|-------|--------|
| `m` | 16 | 2-100 | Higher = better recall, more memory |
| `ef_construction` | 64 | 4-1000 | Higher = better index quality, slower build |
| `ef_search` | N/A | 1-1000 | Runtime setting (see below) |

#### 3.2 Runtime Tuning (`ef_search`)

Set search-time exploration factor per session:

```sql
-- Higher = better recall, slower queries
SET hnsw.ef_search = 100;  -- Default is usually 40

-- Test recall vs speed tradeoff:
SET hnsw.ef_search = 40;   -- Fast, may miss some results
SET hnsw.ef_search = 200;  -- Slower, better recall
```

Application-level tuning (add to `shared/database.py`):

```python
async def set_search_params(self, ef_search: int = 100):
    """Set HNSW search parameters for the session."""
    async with self.db.get_async_connection_context() as conn:
        await conn.execute(f"SET hnsw.ef_search = {ef_search}")
```

#### 3.3 Memory Usage Estimate

```
HNSW memory ≈ (vector_dim × 4 bytes + overhead) × num_vectors × (1 + m/2)

For 100K chunks, 768-dim, m=16:
≈ (768 × 4 + 64) × 100,000 × 9
≈ 2.8 GB (approximate, varies by data distribution)
```

---

### Phase 4: Production Rollout Checklist

For new deployments, HNSW is already active. For existing deployments:

```sql
-- Step 1: Check current index state
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes 
WHERE schemaname = 'intelligence' 
  AND indexrelname LIKE '%embedding%';

-- Step 2: Build HNSW concurrently (allows writes during build)
-- WARNING: May take time on large tables, monitor with:
-- SELECT * FROM pg_stat_progress_create_index;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_hnsw 
ON intelligence.chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Step 3: Verify usage
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id FROM intelligence.chunks 
ORDER BY embedding <=> (SELECT embedding FROM intelligence.chunks LIMIT 1)
LIMIT 5;
-- Should show: "Index Scan using idx_chunks_embedding_hnsw"

-- Step 4: (Optional) Drop old IVFFlat after verification
-- DROP INDEX CONCURRENTLY IF EXISTS idx_chunks_embedding_ivfflat;
```

---

## Architecture Integration

### How HNSW Fits Into the System

```
┌─────────────────────────────────────────────────────────────────┐
│                         Query Flow                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  /search/hybrid or /rag?mode=hybrid                             │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────┐    ┌─────────────────┐                    │
│  │  Lexical Search │    │  Vector Search  │                    │
│  │   (GIN index)   │    │  (HNSW index)   │                    │
│  │                 │    │                 │                    │
│  │ search_tsv @@   │    │ embedding <=>   │                    │
│  │ plainto_tsquery │    │ $1 ORDER BY ... │                    │
│  └────────┬────────┘    └────────┬────────┘                    │
│           │                      │                              │
│           ▼                      ▼                              │
│  ┌─────────────────────────────────────────┐                   │
│  │      HybridRetriever.merge_and_rerank   │                   │
│  │      (RRF or weighted score blending)   │                   │
│  └──────────────────┬──────────────────────┘                   │
│                     ▼                                           │
│  ┌─────────────────────────────────────────┐                   │
│  │      ContextBuilder.build_context       │                   │
│  │  - Diversity filter (max 2 per doc)     │                   │
│  │  - Adjacent chunk collapse              │                   │
│  │  - Token budget trimming                │                   │
│  └──────────────────┬──────────────────────┘                   │
│                     ▼                                           │
│  ┌─────────────────────────────────────────┐                   │
│  │      Ollama (llama3.2) - Generate       │                   │
│  └─────────────────────────────────────────┘                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### File Dependencies

```
schema.sql (HNSW index definition)
    │
    ├──► shared/database.py (SQL functions using <=> operator)
    │         │
    │         ├──► shared/hybrid_retriever.py (HybridRetriever class)
    │         │         │
    │         │         └──► api/app.py (/search/hybrid endpoint)
    │         │
    │         └──► shared/context_builder.py (ContextBuilder class)
    │                   │
    │                   └──► api/app.py (/rag?mode=hybrid endpoint)
    │
    └──► migrations/004_add_hybrid_search.sql (conditional creation)
```

---

## Testing Strategy

### Unit Tests (No DB Required)

```python
# tests/test_hnsw_plan.py
"""Verify HNSW plan assumptions without database."""

def test_schema_sql_contains_hnsw():
    """Verify schema.sql defines HNSW index."""
    with open('schema.sql') as f:
        content = f.read()
    assert 'idx_chunks_embedding_hnsw' in content
    assert 'USING hnsw' in content
    assert 'vector_cosine_ops' in content

def test_indexes_sql_prefers_hnsw():
    """Verify indexes.sql has HNSW as default."""
    with open('indexes.sql') as f:
        content = f.read()
    # HNSW should be uncommented, IVFFlat commented
    hnsw_section = content.split('HNSW index')[1].split('IVFFlat')[0]
    assert not hnsw_section.strip().startswith('--')
```

### Integration Tests (Requires DB)

```python
# tests/test_hnsw_performance.py
import pytest
import time
import asyncio
from shared.database import document_repo
from shared.ollama_client import OllamaClient

@pytest.mark.integration
async def test_hnsw_index_is_used():
    """Verify EXPLAIN shows HNSW index scan."""
    from shared.database import db_manager
    
    async with db_manager.get_async_connection_context() as conn:
        # Get a sample embedding
        row = await conn.fetchrow(
            "SELECT embedding FROM intelligence.chunks WHERE embedding IS NOT NULL LIMIT 1"
        )
        if not row:
            pytest.skip("No embeddings in database")
        
        embedding_str = row['embedding']
        
        # Check query plan
        plan = await conn.fetchval(
            """EXPLAIN (FORMAT TEXT)
               SELECT id FROM intelligence.chunks
               ORDER BY embedding <=> $1::vector
               LIMIT 5""",
            embedding_str
        )
        
        assert 'idx_chunks_embedding_hnsw' in plan or 'hnsw' in plan.lower()

@pytest.mark.integration
async def test_vector_search_performance():
    """Verify vector search completes in reasonable time."""
    ollama = OllamaClient()
    query = "machine learning"
    
    embedding = await ollama.generate_embedding(query)
    
    start = time.time()
    results = await document_repo.find_similar_chunks(
        embedding=embedding,
        limit=10,
        similarity_threshold=0.5
    )
    elapsed = time.time() - start
    
    # HNSW should be sub-100ms even with 100K+ chunks
    assert elapsed < 1.0, f"Search took {elapsed:.2f}s, expected < 1s"
    assert len(results) <= 10
```

---

## Migration Summary

| From | To | Action Required |
|------|-----|-----------------|
| No vector index | HNSW | ✅ Automatic via `schema.sql` |
| IVFFlat | HNSW | ⚠️ Run migration 004 or manual CREATE INDEX CONCURRENTLY |
| HNSW (m=16) | HNSW (m=32) | ⚠️ REINDEX for higher recall (more memory) |

---

## Rollback Plan

If HNSW causes issues:

```sql
-- Option 1: Drop HNSW, fall back to sequential scan
DROP INDEX CONCURRENTLY idx_chunks_embedding_hnsw;

-- Option 2: Create IVFFlat instead (lower memory)
CREATE INDEX CONCURRENTLY idx_chunks_embedding_ivfflat 
ON intelligence.chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Option 3: Reduce ef_search for faster queries
SET hnsw.ef_search = 20;  -- Instead of 40-100
```

---

## Appendix: Quick Reference

### Check HNSW Status
```bash
# Docker compose
make psql

# Then run:
\dt intelligence.*
\di intelligence.*chunk*embed*
```

### Force Index Rebuild
```sql
REINDEX INDEX CONCURRENTLY idx_chunks_embedding_hnsw;
```

### Monitor Build Progress
```sql
SELECT phase, blocks_total, blocks_done, 
       ROUND(100.0 * blocks_done / NULLIF(blocks_total, 0), 1) AS pct_complete
FROM pg_stat_progress_create_index;
```

---

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2026-03-07 | Initial plan created | AI Agent |
| 2026-03-07 | Updated with current status - HNSW already implemented | AI Agent |
