# HNSW Vector Index Tuning Guide

This guide explains how to tune the HNSW (Hierarchical Navigable Small World) vector index for optimal performance in the Article Index system.

## Table of Contents

- [Overview](#overview)
- [HNSW Parameters Reference](#hnsw-parameters-reference)
- [Runtime Tuning](#runtime-tuning)
- [Benchmarking](#benchmarking)
- [Memory Usage Estimation](#memory-usage-estimation)
- [Production Rollout](#production-rollout)
- [Performance Verification](#performance-verification)
- [Rollback Procedures](#rollback-procedures)

## Overview

HNSW is a graph-based approximate nearest neighbor search algorithm that provides:
- **Better recall** than IVFFlat (fewer missed results)
- **Faster queries** at high recall levels
- **No training required** (unlike IVFFlat)
- **Incremental index building** (add vectors without rebuilding)

Trade-offs:
- **Higher memory usage** than IVFFlat
- **Slower index build** time

## HNSW Parameters Reference

### Index Build Parameters

These parameters are set at index creation time in `schema.sql` and `indexes.sql`:

| Parameter | Default | Range | Effect |
|-----------|---------|-------|--------|
| `m` | 16 | 2-100 | Higher = better recall, more memory |
| `ef_construction` | 64 | 4-1000 | Higher = better index quality, slower build |

**Default Configuration:**
```sql
WITH (m = 16, ef_construction = 64);
```

**When to Adjust:**
- **Increase `m`** (e.g., to 32 or 64): When you need higher recall and have memory available
- **Increase `ef_construction`** (e.g., to 128 or 256): When building the index offline and want best quality
- **Decrease `m`** (e.g., to 8): When memory-constrained and can tolerate some recall loss

### Search-Time Parameters

| Parameter | Default | Range | Effect |
|-----------|---------|-------|--------|
| `ef_search` | 40 | 1-1000 | Higher = better recall, slower queries |

## Runtime Tuning

### SQL-Level Tuning

Set search parameters per session:

```sql
-- Better recall (recommended for production RAG)
SET hnsw.ef_search = 100;

-- Balanced (default)
SET hnsw.ef_search = 40;

-- Fast queries (may miss some results)
SET hnsw.ef_search = 20;
```

### Application-Level Tuning

The `DatabaseManager` provides a method for setting search parameters:

```python
from shared.database import db_manager

# Set higher ef_search for better recall
await db_manager.set_search_params(ef_search=100)

# Fast search for autocomplete/suggestions
await db_manager.set_search_params(ef_search=20)
```

### Environment Variable Configuration

Add to your `.env` file:

```bash
# HNSW search parameters
HNSW_EF_SEARCH=100
```

The application reads this on startup and applies it to database connections.

## Benchmarking

Use the benchmark tool to measure HNSW performance and select optimal settings:

```bash
# Run full benchmark with defaults
python scripts/benchmark_hnsw.py

# Custom ef_search values
python scripts/benchmark_hnsw.py --ef-search 20 40 60 80 100 150

# Set minimum recall threshold (default: 0.93)
python scripts/benchmark_hnsw.py --min-recall 0.90

# Use custom queries
python scripts/benchmark_hnsw.py --queries-file my_queries.json

# Skip hybrid search (faster)
python scripts/benchmark_hnsw.py --skip-hybrid

# Include RAG quality evaluation
python scripts/benchmark_hnsw.py --eval-rag --rag-sample-size 15
```

### What the Benchmark Measures

**Vector Search Performance:**
- Exact (brute-force) search as baseline
- HNSW search at each ef_search value
- Latency: p50, p95, max, mean
- Recall: overlap with exact search results
- Outliers: queries with unusually low recall

**Hybrid Search Performance:**
- Combined lexical + HNSW retrieval
- Latency compared to pure HNSW
- Lexical vs vector provenance

**RAG Quality (optional):**
- Answer generation success rate
- Citation presence
- Token count efficiency

### Benchmark Output

**JSON Report** (`hnsw_benchmark_YYYYMMDD_HHMMSS.json`):
- Complete environment details
- Per-query results with EXPLAIN plans
- Outlier queries for debugging
- Recommendation with reasoning

**CSV Summary** (`hnsw_benchmark_YYYYMMDD_HHMMSS.csv`):
| search_type | ef_search | p50_latency_ms | p95_latency_ms | mean_recall_top10 | ... |
|-------------|-----------|----------------|----------------|-------------------|-----|
| exact | N/A | 245.32 | 289.45 | 1.0000 | ... |
| hnsw | 20 | 8.45 | 12.30 | 0.8820 | ... |
| hnsw | 40 | 10.12 | 15.67 | 0.9450 | ... |
| hnsw | 80 | 15.23 | 22.10 | 0.9780 | ... |
| hybrid | N/A | 18.45 | 25.30 | N/A | ... |

### Interpreting Results

**Recommendation Algorithm:**
1. Filter to settings meeting minimum recall threshold (default: 93%)
2. Choose lowest p95 latency among qualifying settings

**Example Output:**
```
Recommended default: ef_search=40

Selection criteria:
  • Minimum recall threshold: 93%
  • Optimization: Lowest p95 latency among qualifying settings

Performance at recommendation:
  • Mean recall@10: 94.5%
  • Min recall@10: 87.2%
  • p95 latency: 15.67ms
```

### Environment Capture

Each benchmark run records:
- Database: chunk count, document count
- PostgreSQL version
- pgvector version
- HNSW index parameters (m, ef_construction)
- CPU and platform info
- Cache warm/cold state

This ensures benchmark comparisons are meaningful across runs.

### When to Benchmark

**Run benchmarks when:**
- Setting up production for the first time
- Changing HNSW index parameters (m, ef_construction)
- Dataset size changes significantly (>2x)
- Query patterns change (different use case)
- Comparing hardware/environment changes

**Re-benchmark periodically:**
- Monthly for active systems
- After major version upgrades
- When users report quality issues

### Using Benchmark Results

**To set production default:**
```bash
# Update .env with recommended value
HNSW_EF_SEARCH=40
```

**To investigate outliers:**
```bash
# Check JSON report for queries with recall < 80%
jq '.hnsw_benchmarks[] | select(.outlier_count > 0)' hnsw_benchmark_*.json
```

**To compare runs:**
```bash
# Compare two benchmark CSVs in spreadsheet tool
# Look for: latency regression, recall degradation
```

## Memory Usage Estimation

### Formula

```
HNSW memory ≈ (vector_dim × 4 bytes + overhead) × num_vectors × (1 + m/2)
```

### Examples

**Small Dataset (10K chunks, 768-dim, m=16):**
```
≈ (768 × 4 + 64) × 10,000 × 9
≈ 280 MB
```

**Medium Dataset (100K chunks, 768-dim, m=16):**
```
≈ (768 × 4 + 64) × 100,000 × 9
≈ 2.8 GB
```

**Large Dataset (1M chunks, 768-dim, m=16):**
```
≈ (768 × 4 + 64) × 1,000,000 × 9
≈ 28 GB
```

**With m=32 (higher recall):**
```
≈ 2× memory usage compared to m=16
```

### Checking Actual Memory Usage

```sql
-- Check index sizes
SELECT 
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) as size,
    pg_relation_size(indexrelid) as size_bytes
FROM pg_stat_user_indexes 
WHERE schemaname = 'intelligence' 
  AND indexrelname LIKE '%embedding%';
```

## Production Rollout

### For New Deployments

HNSW is automatically created by `schema.sql`. No action needed.

### For Existing Deployments

**Step 1: Check current index state**
```sql
SELECT 
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes 
WHERE schemaname = 'intelligence' 
  AND indexrelname LIKE '%embedding%';
```

**Step 2: Build HNSW concurrently** (allows writes during build)
```sql
-- WARNING: May take time on large tables
-- Monitor progress with:
-- SELECT * FROM pg_stat_progress_create_index;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_embedding_hnsw 
ON intelligence.chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Step 3: Verify usage**
```sql
-- Check query plan uses HNSW
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id FROM intelligence.chunks 
ORDER BY embedding <=> (SELECT embedding FROM intelligence.chunks LIMIT 1)
LIMIT 5;
-- Should show: "Index Scan using idx_chunks_embedding_hnsw"
```

**Step 4: (Optional) Drop old IVFFlat**
```sql
-- Only after verifying HNSW works correctly
DROP INDEX CONCURRENTLY IF EXISTS idx_chunks_embedding_ivfflat;
```

## Performance Verification

### Check Index Exists

```sql
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE schemaname = 'intelligence' 
  AND indexname LIKE '%embedding%';
```

Expected output:
```
indexname                    | indexdef
-----------------------------|--------------------------------------------------
idx_chunks_embedding_hnsw    | CREATE INDEX idx_chunks_embedding_hnsw ON intelligence.chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)
```

### Verify Query Plan

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT id, document_id, content, 1 - (embedding <=> $1::vector) AS similarity
FROM intelligence.chunks
WHERE embedding IS NOT NULL
ORDER BY embedding <=> $1::vector
LIMIT 10;
```

Expected output:
```
->  Index Scan using idx_chunks_embedding_hnsw on chunks
      Order By: (embedding <=> $1::vector)
```

### API Smoke Test

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

### Monitor Build Progress

If creating the index on a large table:

```sql
SELECT 
    phase, 
    blocks_total, 
    blocks_done, 
    ROUND(100.0 * blocks_done / NULLIF(blocks_total, 0), 1) AS pct_complete
FROM pg_stat_progress_create_index;
```

## Rollback Procedures

If HNSW causes issues, you have several options:

### Option 1: Drop HNSW (Fall back to sequential scan)
```sql
DROP INDEX CONCURRENTLY idx_chunks_embedding_hnsw;
```

### Option 2: Create IVFFlat Instead (Lower memory)
```sql
CREATE INDEX CONCURRENTLY idx_chunks_embedding_ivfflat 
ON intelligence.chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

### Option 3: Reduce ef_search (Faster queries)
```sql
SET hnsw.ef_search = 20;  -- Instead of 40-100
```

### Option 4: Rebuild with Lower m
```sql
DROP INDEX CONCURRENTLY idx_chunks_embedding_hnsw;

CREATE INDEX CONCURRENTLY idx_chunks_embedding_hnsw 
ON intelligence.chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 8, ef_construction = 64);  -- Lower memory
```

## Recommended Configurations

### Configuration A: Balanced (Default)
```sql
m = 16, ef_construction = 64, ef_search = 40
```
- Good for: General purpose, 100K-1M vectors
- Memory: ~2.8 GB per 100K vectors
- Recall: ~95%

### Configuration B: High Recall
```sql
m = 32, ef_construction = 128, ef_search = 100
```
- Good for: Critical RAG applications
- Memory: ~5.6 GB per 100K vectors
- Recall: ~99%

### Configuration C: Memory Constrained
```sql
m = 8, ef_construction = 64, ef_search = 40
```
- Good for: Development, small datasets
- Memory: ~1.4 GB per 100K vectors
- Recall: ~90%

## Quick Reference

### Docker Compose Commands
```bash
# Connect to database
make psql

# Check indexes
\di intelligence.*chunk*embed*

# Check table sizes
\dt+ intelligence.*
```

### Force Index Rebuild
```sql
REINDEX INDEX CONCURRENTLY idx_chunks_embedding_hnsw;
```

### Check Active ef_search
```sql
SHOW hnsw.ef_search;
```

---

For more information, see:
- [pgvector HNSW documentation](https://github.com/pgvector/pgvector#hnsw)
- [HNSW paper (Malkov & Yashunin, 2016)](https://arxiv.org/abs/1603.09320)
