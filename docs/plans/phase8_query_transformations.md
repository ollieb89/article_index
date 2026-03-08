# Phase 8: Query Transformations

**Status:** ✅ Implemented  
**Theme:** Improve recall before ranking  
**Primary Win:** Better candidate retrieval for ambiguous/complex queries

## Overview

Phase 8 implements query transformations to improve retrieval recall. While Phase 7 improved **ordering** of retrieved candidates through selective reranking, Phase 8 improves **which candidates get retrieved at all** by expanding ambiguous or narrow queries into multiple retrieval variants.

## Problem Statement

Many retrieval failures are query formulation problems:
- User asks: "Why does pgvector timeout on large imports?"
- System retrieves narrowly on "timeout" + "large imports"
- Misses relevant content about "bulk insert performance", "import optimization"

**Solution:** Transform query into multiple retrieval variants and union results.

## Implementation

### Architecture

```
User Query → Query Transformer → [Query 1, Query 2, Query 3]
                                    ↓
                              Retrieve each → Merge & Deduplicate → Results
```

### Transformation Strategies

#### 1. Multi-Query Expansion

Generates 2-3 alternate phrasings:

```
Original: "Why does pgvector timeout on large imports?"
Expanded:
  - "pgvector timeout large imports"
  - "pgvector bulk insert performance"
  - "postgres vector import optimization"
```

Strategies:
- **Keyword-focused**: Extract key concepts
- **Question-to-statement**: "What is X?" → "X overview"
- **Issue angle**: Add "troubleshooting", "fix" for error queries
- **How-to angle**: Add "tutorial", "example" for config queries

#### 2. Step-Back Reformulation

Creates broader conceptual version:

```
Original: "Why does pgvector timeout on large imports?"
Step-back: "pgvector performance issues"
```

Useful when query is overly specific and misses general relevant content.

### Components

#### 1. Query Transformer (`shared/query_transformer.py`)

**Modes:**
- `off`: No transformation
- `always`: Always transform
- `selective`: Transform based on triggers

**Selective Triggers:**
- Query too short (< 4 words)
- Ambiguous query patterns (vague terms, comparison words)
- Low evidence from initial retrieval
- Complex multi-part queries (≥ 12 words)

**Configuration:**
```python
QueryTransformer(
    mode='selective',
    max_expanded_queries=3,
    enable_multi_query=True,
    enable_step_back=True
)
```

#### 2. Hybrid Retriever Integration

New method `retrieve_with_transform()`:
1. Quick initial retrieval to check for low evidence
2. Transform query if needed
3. Retrieve for each transformed query
4. Merge and deduplicate results
5. Boost scores for chunks found by multiple queries

#### 3. Merge Strategy

Results are merged using:
- Round-robin interleaving (ensures diversity)
- Deduplication by chunk ID
- Score boosting for chunks from multiple queries
- Final sort by boosted hybrid score

### API Integration

**Response Metadata:**
```json
{
  "query_transform_enabled": true,
  "query_transform_mode": "selective",
  "query_transform_applied": true,
  "query_transform_types": ["multi_query", "step_back"],
  "query_transform_reasons": ["ambiguous_query", "low_evidence"],
  "generated_queries": [
    "Why does pgvector timeout on large imports?",
    "pgvector timeout large imports",
    "pgvector bulk insert performance"
  ],
  "query_count": 3
}
```

**Admin Endpoints:**
- `GET /admin/query-transform/status` — Configuration and statistics
- `POST /admin/query-transform/test` — Test transformation for a query
- `POST /admin/query-transform/tune` — Adjust parameters at runtime
- `POST /admin/query-transform/reset-stats` — Reset counters

## Configuration

```env
# Query Transformation Configuration
QUERY_TRANSFORM_MODE=selective          # off | always | selective
QUERY_TRANSFORM_MAX_QUERIES=3           # 2-5 recommended
QUERY_TRANSFORM_MULTIQUERY=true         # Enable multi-query expansion
QUERY_TRANSFORM_STEPBACK=true           # Enable step-back reformulation
QUERY_TRANSFORM_MIN_WORDS=4             # Minimum words before transformation
QUERY_TRANSFORM_AMBIGUITY=1             # Ambiguity indicators to trigger
```

## Usage

### Enable Selective Transformation

```bash
# .env
QUERY_TRANSFORM_MODE=selective
QUERY_TRANSFORM_MAX_QUERIES=3
```

### Test Transformation

```bash
curl -X POST http://localhost:8001/admin/query-transform/test \
  -H "X-API-Key: your-key" \
  -d "query=Why does pgvector timeout on large imports?"
```

Response:
```json
{
  "query": "Why does pgvector timeout on large imports?",
  "mode": "selective",
  "decision": {
    "should_transform": true,
    "transformed_queries": [
      "Why does pgvector timeout on large imports?",
      "pgvector timeout large imports",
      "pgvector bulk insert performance"
    ],
    "transform_types": ["multi_query", "step_back"],
    "trigger_reasons": ["ambiguous_query"],
    "confidence": 0.65
  }
}
```

### Check Statistics

```bash
curl http://localhost:8001/admin/query-transform/status \
  -H "X-API-Key: your-key"
```

Response:
```json
{
  "status": "enabled",
  "mode": "selective",
  "stats": {
    "queries_total": 850,
    "queries_transformed": 195,
    "transform_rate": 0.23,
    "avg_generated_per_transform": 2.1,
    "transform_types": {
      "multi_query": {"count": 180, "rate": 0.212},
      "step_back": {"count": 95, "rate": 0.112}
    }
  }
}
```

## Benchmarking

Expected success criteria:
- **Recall improvement** on ambiguous/short queries
- **Limited latency growth** (capped by max_queries)
- **No duplicate explosion** (deduplication working)
- **Transform applied to 20-30%** of queries in selective mode

Comparison modes:
- Baseline: Original hybrid retrieval
- Transformed: Query transformation + merge
- Transformed+Reranked: Full pipeline

## Integration with Phase 7

Query transformations (Phase 8) and selective reranking (Phase 7) compound:

```
Query → Transform → [Query 1, Query 2, Query 3]
                        ↓
                    Retrieve each → Merge
                        ↓
                    Selective Rerank? → Final Results
```

- Phase 8 improves **recall** (what gets into the pool)
- Phase 7 improves **ordering** (what rises to the top)
- Together: Better candidates in better order

## Acceptance Criteria (Met)

✅ Multi-query expansion (2-3 alternate phrasings)  
✅ Step-back reformulation (broader conceptual versions)  
✅ Three modes: `off`, `always`, `selective`  
✅ Selective triggers for ambiguous/complex queries  
✅ Merge and deduplication of transformed results  
✅ Response metadata showing transformations applied  
✅ Admin endpoints for testing and tuning  
✅ Statistics tracking (transform rate, types, per-trigger counts)  
✅ Conservative limits (max 5 queries, selective by default)

## Files Changed

- `shared/query_transformer.py` — New transformation engine
- `shared/hybrid_retriever.py` — `retrieve_with_transform()` method
- `api/app.py` — Integration and admin endpoints
- `.env.example` — New configuration options
- `.env` — Default configuration
- `docs/plans/phase8_query_transformations.md` — This documentation

## Best Practices

1. **Start conservative**: Use `mode=selective`, `max_queries=3`
2. **Monitor transform rate**: Should be 20-30% for selective mode
3. **Watch latency**: Each added query adds retrieval cost
4. **Check duplicates**: Ensure deduplication is working
5. **Combine with reranking**: Transform for recall, rerank for ordering

## Production-Grade Improvements (Implemented)

### 1. Query-Level Deduplication

Before retrieval, generated queries are normalized to catch near-duplicates:

```python
"pgvector timeout large imports"
"pgvector large import timeout"  # Same after normalization → dropped
```

Normalization: lowercase → remove punctuation → sort tokens

### 2. RRF Score Fusion

Results merged using Reciprocal Rank Fusion instead of simple union:

```
RRF_score = Σ 1/(k + rank) for each query that retrieved the chunk
```

This prevents any single query from dominating and gives better
aggregation than simple score averaging.

### 3. Latency Budget Guard

Hard cap on transformation latency:

```env
QUERY_TRANSFORM_LATENCY_BUDGET_MS=150
```

If budget exceeded:
- Skip remaining query expansions
- Log warning with budget utilization
- Return results from completed retrievals

Early exit heuristic: Stop after 2 queries if <50ms budget remains.

### 4. Cross-Query Overlap Metric

Track result diversity across transformed queries:

```json
{
  "merge_metadata": {
    "result_overlap": 0.35,
    "unique_chunks": 24,
    "multi_query_chunks": 8
  }
}
```

- `result_overlap`: Fraction of chunks found by multiple queries
- High overlap (>70%): Transformations not adding diversity
- Low overlap (<20%): Transformations too divergent

### 5. Detailed Analytics Logging

Per-merge analytics logged at DEBUG level:

```
Query transform analytics for 'Why does pgvector...':
  query_results=[15, 12, 18],
  unique=24,
  multi_query=8,
  avg_pairwise_overlap=42.3%
```

This helps tune transformation strategies based on actual behavior.

## Future Enhancements

- **LLM-based expansion**: Use LLM to generate semantically diverse variants
- **Query-type-specific templates**: Different expansions for how-to vs troubleshooting
- **Feedback learning**: Track which transformations actually improve results
- **Cascade retrieval**: Try cheap expansion first, expensive only if needed
