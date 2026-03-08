# HNSW Benchmark Guide

Quick reference for running the HNSW vector index benchmark.

## Quick Start

```bash
# Basic benchmark (recommended for first run)
python scripts/benchmark_hnsw.py

# This will:
# - Test ef_search values: 20, 40, 80, 100
# - Compare against exact (brute-force) search
# - Benchmark hybrid search
# - Generate JSON and CSV reports
# - Recommend optimal ef_search setting
```

## Understanding the Output

### Console Output

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                           BENCHMARK RECOMMENDATION                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Recommended default: ef_search=40                                           ║
║                                                                              ║
║  Selection criteria:                                                         ║
║    • Minimum recall threshold: 93%                                           ║
║    • Optimization: Lowest p95 latency among qualifying settings              ║
║                                                                              ║
║  Performance at recommendation:                                              ║
║    • Mean recall@10: 94.5%                                                   ║
║    • Min recall@10:  87.2%                                                   ║
║    • p95 latency:    15.67ms                                                 ║
║                                                                              ║
║  All results (✓ = meets recall threshold):                                   ║
║    ✗ ef_search= 20: recall=88.2%, p95=   8.5ms                               ║
║    ✓ ef_search= 40: recall=94.5%, p95=  15.7ms  ← RECOMMENDED                ║
║    ✓ ef_search= 80: recall=97.8%, p95=  22.1ms                               ║
║    ✓ ef_search=100: recall=98.5%, p95=  26.4ms                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### JSON Report

Full details saved to `benchmark_results/hnsw_benchmark_YYYYMMDD_HHMMSS.json`:

```json
{
  "timestamp": "2024-01-15T10:30:00",
  "environment": {
    "database_chunks": 15420,
    "database_documents": 340,
    "postgresql_version": "16.1",
    "pgvector_version": "0.5.1",
    "hnsw_m": 16,
    "hnsw_ef_construction": 64,
    "cache_warm": false
  },
  "vector_summary": {
    "best_recall_ef_search": 100,
    "best_recall_value": 0.985,
    "best_latency_ef_search": 20,
    "best_latency_p95_ms": 8.5
  },
  "hnsw_benchmarks": [...],
  "outliers": [...]
}
```

### CSV Summary

Tabular data saved to `benchmark_results/hnsw_benchmark_YYYYMMDD_HHMMSS.csv`:

| search_type | ef_search | p50_latency_ms | p95_latency_ms | mean_recall_top10 |
|-------------|-----------|----------------|----------------|-------------------|
| exact | N/A | 245.32 | 289.45 | 1.0000 |
| hnsw | 20 | 8.45 | 12.30 | 0.8820 |
| hnsw | 40 | 10.12 | 15.67 | 0.9450 |
| hybrid | N/A | 18.45 | 25.30 | N/A |

### Ranking Comparison (when using --compare-ranking)

| metric | weighted | rrf | delta |
|--------|----------|-----|-------|
| mean_overlap_pct | - | - | 92.3% |
| p95_latency_ms | 18.45 | 19.12 | +3.6% |
| recommendation | ✓ | - | use weighted |

## Common Scenarios

### Scenario 1: First Production Setup

```bash
# Run full benchmark with default settings
python scripts/benchmark_hnsw.py

# Apply recommended setting to .env
echo "HNSW_EF_SEARCH=40" >> .env
```

### Scenario 2: Tuning for Higher Recall

```bash
# Lower the recall threshold to see more options
python scripts/benchmark_hnsw.py --min-recall 0.88

# Or test higher ef_search values
python scripts/benchmark_hnsw.py --ef-search 40 80 120 160 200
```

### Scenario 3: Debugging Quality Issues

```bash
# Check for outlier queries with poor recall
python scripts/benchmark_hnsw.py

# Then examine the JSON report
jq '.hnsw_benchmarks[] | 
    select(.outlier_count > 0) | 
    {ef_search, outlier_count}' \
    benchmark_results/hnsw_benchmark_*.json

# Look at specific outlier queries
jq '.hnsw_benchmarks[].outliers[] | 
    select(.recall < 0.7) | 
    {query, recall, missing_ids}' \
    benchmark_results/hnsw_benchmark_*.json
```

### Scenario 4: Comparing Hardware/Environment

```bash
# Run on current environment
python scripts/benchmark_hnsw.py --output-dir ./results/before

# Run after changes (upgrade, migration, etc.)
python scripts/benchmark_hnsw.py --output-dir ./results/after

# Mark if cache is warm (for accurate comparisons)
python scripts/benchmark_hnsw.py --cache-warm
```

### Scenario 5: Validating Custom Queries

```bash
# Create queries file
cat > my_queries.json << 'EOF'
{
  "description": "Production query samples",
  "queries": [
    "How do I configure authentication?",
    "What is the error handling pattern?",
    "Explain the data model"
  ]
}
EOF

# Benchmark with custom queries
python scripts/benchmark_hnsw.py --queries-file my_queries.json
```

### Scenario 6: Comparing Ranking Modes (Weighted vs RRF)

```bash
# Compare weighted score blending vs Reciprocal Rank Fusion
python scripts/benchmark_hnsw.py --compare-ranking

# Output:
#   Mean overlap: 92.3%
#   Weighted p95 latency: 18.45ms
#   RRF p95 latency: 19.12ms
#   Latency delta: +3.6%
#
# Recommended ranking mode: weighted
# Reason: RRF latency is 3.6% higher with similar result quality
```

**What this measures:**
- Result overlap between weighted and RRF for same queries
- Latency impact of RRF calculation
- Recommendation for which mode to use

**Decision rule:**
- If RRF latency >10% worse → use weighted
- If results >90% similar and latency similar → use weighted (simpler)
- Otherwise → consider RRF (no weight tuning needed)

### Scenario 7: Evaluating Reranker (Baseline vs Reranked)

```bash
# Compare baseline hybrid vs reranker-enhanced retrieval
python scripts/benchmark_hnsw.py --compare-reranking

# Output:
#   Mean overlap: 85.0%
#   Baseline p95 latency: 18.45ms
#   Reranked p95 latency: 145.20ms
#   Latency delta: +687%
#   Mean position change: 2.3
#
# Recommended reranking: disabled
# Reason: Reranking adds 687% latency overhead
```

**What this measures:**
- Result overlap between baseline and reranked retrieval
- Position changes (how much reordering occurs)
- Latency impact of reranking

**Decision rule:**
- If latency increase > 50% → keep disabled
- If overlap > 95% and minimal reordering → keep disabled
- If meaningful reordering (avg > 2 positions) and acceptable latency → enable

**When to enable reranking:**
- High-value queries where quality matters more than speed
- When hybrid retrieval returns many borderline-relevant results
- After benchmarking confirms quality improvement justifies latency cost

## Interpreting Key Metrics

### Recall

**Definition:** Percentage of exact (brute-force) search results that HNSW also returns.

| Recall | Interpretation |
|--------|----------------|
| 98-100% | Excellent - nearly identical to exact |
| 93-98% | Good - minimal quality loss |
| 88-93% | Acceptable - some results missed |
| <88% | Poor - significant quality degradation |

**Why it matters:** Low recall means potentially missing the best-matching chunks for RAG.

### Latency

**Definition:** Time to execute the vector search query.

| Latency | Interpretation |
|---------|----------------|
| <10ms | Excellent |
| 10-20ms | Good |
| 20-50ms | Acceptable |
| >50ms | Consider optimization |

**Why it matters:** Directly impacts API response time and user experience.

### Outliers

**Definition:** Queries with unusually low recall (<80%).

**Why they matter:**
- Indicate edge cases in data distribution
- May reveal chunking issues
- Help identify query types that need special handling

## Troubleshooting

### "No setting meets minimum recall threshold"

**Cause:** All ef_search values tested have mean recall below threshold.

**Solutions:**
1. Lower threshold: `--min-recall 0.88`
2. Test higher values: `--ef-search 100 150 200`
3. Check HNSW index: `m` may be too low (default 16)
4. Investigate outliers: may indicate data quality issues

### Exact search is very slow

**Expected:** Exact (brute-force) search is O(N) and will be slow on large datasets.

**Benchmarking tip:** Use fewer queries or skip exact with `--skip-hybrid` if you only need HNSW comparison.

### High outlier count

**Investigation:**
```bash
# Get outlier details
jq '.hnsw_benchmarks[].outliers[] | 
    {query, recall, missing_count: (.missing_ids | length)}' \
    benchmark_results/hnsw_benchmark_*.json | head -20
```

**Common causes:**
- Short queries (1-2 words) - HNSW struggles with very sparse vectors
- Out-of-distribution queries - topics not in training data
- Very specific technical terms - may need hybrid search

### Hybrid slower than expected

**Expected:** Hybrid combines two searches, so it's typically 1.5-2x slower than pure HNSW.

**If much slower:**
- Check lexical index: `idx_chunks_search_tsv`
- Verify GIN index exists on `search_tsv`

## Best Practices

1. **Run on representative data:** Benchmark with dataset size similar to production
2. **Use realistic queries:** Sample actual user queries if possible
3. **Cold cache for fairness:** First run should be with cold cache
4. **Document environment:** Note hardware, PostgreSQL version, dataset size
5. **Re-benchmark after changes:** Dataset growth, index rebuilds, version upgrades
6. **Track outliers:** Monitor queries that consistently show low recall
7. **Compare over time:** Save benchmark reports to track regression

## Recommended Workflow

```bash
# 1. Initial benchmark
python scripts/benchmark_hnsw.py --output-dir ./benchmarks/initial

# 2. Apply recommended setting
cp .env .env.backup
echo "HNSW_EF_SEARCH=40" >> .env

# 3. Verify in production (after deployment)
python scripts/benchmark_hnsw.py --output-dir ./benchmarks/production

# 4. Monthly regression check
python scripts/benchmark_hnsw.py --skip-hybrid --output-dir ./benchmarks/monthly
```
