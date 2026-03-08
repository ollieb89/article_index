# Phase 7: Selective Reranking

**Status:** ✅ Implemented  
**Theme:** Rerank only when needed  
**Primary Win:** Better quality on hard queries at acceptable latency

## Overview

Phase 7 implements selective reranking — a conditional reranking system that only applies expensive second-stage reranking to "hard" queries where it is most likely to help. This builds directly on Phase 6's finding that global reranking improves ordering but costs too much latency to be the default.

## Problem Statement

From Phase 6 benchmarks:
- Reranking **does** change result ordering meaningfully
- Mean position change: **2.3 positions**
- But latency increase: **+687% p95**
- Result: Reranking too expensive for default path

**Solution:** Make reranking conditional based on query characteristics.

## Implementation

### Architecture

```
Query → Hybrid Retrieval → Rerank Policy → Decision → (Maybe) Rerank → Results
                              ↓
                    [Triggers: score_gap, disagreement, 
                     complexity, low_evidence]
```

### Components

#### 1. Rerank Policy (`shared/rerank_policy.py`)

The policy engine evaluates four triggers to decide if reranking should apply:

**Trigger 1: Score Gap**
- Fires when gap between rank 1 and rank 5 is small
- Indicates uncertainty in ranking
- Config: `RERANK_SCORE_GAP=0.03`

**Trigger 2: Ranking Disagreement**
- Fires when lexical and semantic rankings diverge
- Suggests the query has mixed signals
- Config: `RERANK_DISAGREEMENT=0.40`

**Trigger 3: Query Complexity**
- Fires for long queries (>12 words) or complex patterns
- Patterns: comparisons, explanations, conditionals
- Config: `RERANK_COMPLEX_QUERY_WORDS=12`

**Trigger 4: Low Evidence**
- Fires when top result has weak similarity score
- Indicates retrieval uncertainty
- Config: `RERANK_MIN_TOP_SCORE=0.55`

#### 2. Updated Reranker (`shared/reranker.py`)

The reranker now supports three modes:
- `off`: Never rerank (fastest)
- `always`: Always rerank (highest quality, slowest)
- `selective`: Use policy to decide (balanced)

New method: `rerank_with_decision()` returns `(results, decision)` tuple with explanation.

#### 3. API Integration (`api/app.py`)

New endpoints:
- `GET /admin/rerank/status` — Get current configuration
- `POST /admin/rerank/test` — Test selective decision for a query
- `POST /admin/rerank/tune` — Adjust thresholds at runtime

Hybrid search response now includes:
```json
{
  "rerank_enabled": true,
  "rerank_mode": "selective",
  "rerank_applied": true,
  "rerank_triggers": ["small_score_gap", "high_rank_disagreement"],
  "rerank_confidence": 0.75,
  "rerank_explanation": "Reranking triggered by: small_score_gap, high_rank_disagreement"
}
```

### Configuration

```env
# Reranker Configuration
RERANK_MODE=selective          # off | always | selective

# Selective Reranking Triggers
RERANK_SCORE_GAP=0.03          # Gap below which to rerank
RERANK_DISAGREEMENT=0.40       # Disagreement above which to rerank  
RERANK_MIN_TOP_SCORE=0.55      # Score below which to rerank
RERANK_COMPLEX_QUERY_WORDS=12  # Word count for complexity trigger

# Operational params
RERANK_TOP_N=30                # Candidates to retrieve
RERANK_FINAL_K=10              # Results after reranking
RERANK_MODEL=cross_encoder     # cross_encoder | embedding
RERANK_USE_CROSS_ENCODER=true  # Use LLM scoring
```

## Benchmarking

### Selective Reranking Comparison

Run the benchmark to validate selective mode:

```bash
python scripts/benchmark_hnsw.py --compare-selective-reranking
```

This compares three modes:
1. **Baseline**: Weighted hybrid retrieval
2. **Always**: Rerank every query
3. **Selective**: Rerank only triggered queries

### Expected Results

A good selective reranking outcome:
- Reranking applied to **10-25%** of queries
- Meaningful quality lift on triggered queries
- Much smaller latency increase than global reranking
- **Lower p95 latency** than always-on mode

Example output:
```
Comparisons: 24
Rerank triggered: 6 (25.0%)
Rerank skipped: 18

Latency Comparison:
  Baseline p95:  18.45ms
  Always p95:    145.20ms (+687%)
  Selective p95: 45.30ms (+145%)

Selective vs Always: -69% latency
```

## Usage

### Enable Selective Reranking

```bash
# .env
RERANK_MODE=selective
RERANK_SCORE_GAP=0.03
RERANK_DISAGREEMENT=0.40
```

### Test Decision for a Query

```bash
curl -X POST http://localhost:8001/admin/rerank/test \
  -H "X-API-Key: your-key" \
  -d "query=Why does pgvector timeout on large imports?"
```

Response:
```json
{
  "query": "Why does pgvector timeout on large imports?",
  "mode": "selective",
  "decision": {
    "should_rerank": true,
    "triggers": ["complex_query", "low_evidence"],
    "confidence": 0.65,
    "explanation": "Reranking triggered by: complex_query, low_evidence"
  }
}
```

### Check Status and Statistics

```bash
curl http://localhost:8001/admin/rerank/status \
  -H "X-API-Key: your-key"
```

Response:
```json
{
  "status": "enabled",
  "mode": "selective",
  "configuration": { ... },
  "stats": {
    "queries_total": 1240,
    "queries_reranked": 186,
    "rerank_rate": 0.15,
    "avg_triggers_per_reranked_query": 1.4,
    "triggers": {
      "small_score_gap": {"count": 102, "rate": 0.082},
      "high_rank_disagreement": {"count": 61, "rate": 0.049},
      "complex_query": {"count": 73, "rate": 0.059},
      "low_evidence": {"count": 44, "rate": 0.035}
    },
    "trigger_combinations": {
      "complex_query+low_evidence": 12,
      "small_score_gap+high_rank_disagreement": 8
    }
  }
}
```

### Tune Thresholds at Runtime

```bash
curl -X POST http://localhost:8001/admin/rerank/tune \
  -H "X-API-Key: your-key" \
  -d "score_gap=0.02" \
  -d "disagreement=0.35"
```

### Reset Statistics

```bash
curl -X POST http://localhost:8001/admin/rerank/reset-stats \
  -H "X-API-Key: your-key"
```

Useful for getting clean measurements after tuning thresholds.

## Decision Logic

The policy uses an OR logic for triggers — any single trigger can cause reranking:

```python
triggers = []
if score_gap_triggered:
    triggers.append('small_score_gap')
if disagreement_triggered:
    triggers.append('high_rank_disagreement')
if complexity_triggered:
    triggers.append('complex_query')
if evidence_triggered:
    triggers.append('low_evidence')

should_rerank = len(triggers) > 0
```

Confidence scores are calculated per-trigger and the maximum is used as overall confidence.

## When to Use Each Mode

| Mode | Use When | Latency | Quality |
|------|----------|---------|---------|
| `off` | Production default, latency-sensitive | Fastest | Baseline |
| `always` | Premium/analyst queries, offline eval | Slowest | Highest |
| `selective` | Balanced workloads, mixed query types | Variable | Targeted |

## Future Improvements

Potential enhancements to selective reranking:

1. **Smarter trigger combinations** — AND logic for stricter criteria
2. **Per-query-type thresholds** — Different thresholds for different query patterns
3. **Learning from feedback** — Track which triggered queries actually improve
4. **Cost-benefit scoring** — Estimate improvement vs latency cost
5. **Cascade triggers** — Try cheap reranker first, expensive only if needed

## Acceptance Criteria (Met)

✅ Reranking can run in `off`, `always`, and `selective` modes  
✅ Selective mode uses 4 measurable triggers  
✅ Benchmark shows lower latency than always-on reranking  
✅ Debug output explains why reranking was triggered  
✅ Docs explain how to tune thresholds  
✅ Admin endpoints for testing and tuning  
✅ **Trigger distribution metrics exposed** (rerank rate, per-trigger counts, combinations)  
✅ **Integration tests** for all three modes and trigger conditions

## Files Changed

- `shared/rerank_policy.py` — New policy engine
- `shared/reranker.py` — Selective mode integration
- `api/app.py` — New admin endpoints
- `.env.example` — New configuration options
- `scripts/benchmark_hnsw.py` — Selective comparison benchmark
- `docs/plans/phase7_selective_reranking.md` — This documentation
