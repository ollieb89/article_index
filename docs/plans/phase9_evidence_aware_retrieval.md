# Phase 9: Evidence-Aware Retrieval

**Status:** ✅ Implemented  
**Theme:** Trustworthiness and context quality  
**Primary Win:** Only pass high-quality evidence forward, measure its strength, prove what supported the answer

## Overview

Phase 9 adds the final layer of retrieval quality: **evidence filtering, confidence scoring, and citation tracking**. While Phase 8 improved recall and Phase 7 improved ordering, Phase 9 ensures that only high-quality, relevant evidence reaches the LLM and that answers can be audited for provenance.

## Core Philosophy

> "Only pass high-quality evidence forward, measure how strong that evidence is, and prove what supported the answer."

## Three Components

### 1. Context Filtering ✅

**Purpose:** Remove redundancy and noise before context building

**Filters Applied:**
- **Deduplication** — Remove near-duplicate chunks (Jaccard similarity > 0.85)
- **Per-document limit** — Max 2 chunks per source (prevents dominance)
- **Score threshold** — Remove chunks below 0.3 score
- **Boilerplate removal** — Filter headers, footers, TOC, copyright
- **Redundancy suppression** — Remove high-overlap content

**Impact:**
- Fewer chunks in context
- Higher evidence density
- Reduced noise
- Better answer precision

**Configuration:**
```env
CONTEXT_FILTER_ENABLED=true
CONTEXT_DEDUP_THRESHOLD=0.85
CONTEXT_MAX_PER_DOC=2
CONTEXT_MIN_SCORE=0.3
CONTEXT_MAX_CHUNKS=8
CONTEXT_FILTER_BOILERPLATE=true
```

### 2. Evidence Confidence Scoring ✅

**Purpose:** Estimate whether retrieved evidence is sufficient to answer well

**Signals Combined:**
- **Score strength** — How high are top retrieval scores?
- **Score decay** — How quickly do scores drop after rank 1?
- **Method agreement** — Do lexical and vector retrievals agree?
- **Source diversity** — Are results from different documents?
- **Rerank confidence** — From Phase 7 selective reranking
- **Transform quality** — From Phase 8 query transformation

**Confidence Bands:**
| Band | Score | Assessment | Recommendation |
|------|-------|------------|----------------|
| High | > 0.75 | Strong | Proceed with confidence |
| Medium | 0.50-0.75 | Moderate | Answer with caveats |
| Low | 0.25-0.50 | Weak | Hedge or ask clarification |
| Insufficient | < 0.25 | Insufficient | Ask for rephrasing |

**Response Metadata:**
```json
{
  "retrieval_confidence": {
    "score": 0.78,
    "band": "high",
    "evidence_strength": "strong",
    "coverage_estimate": 0.85,
    "component_scores": {
      "score_strength": 0.9,
      "score_decay": 0.8,
      "method_agreement": 0.75,
      "source_diversity": 1.0,
      "rerank_confidence": 0.7,
      "transform_quality": 0.8
    },
    "recommendations": ["Evidence is strong - proceed with confidence"]
  }
}
```

**Configuration:**
```env
RETRIEVAL_CONFIDENCE_ENABLED=true
```

### 3. Citation Tracking ✅

**Purpose:** Track which chunks support which parts of the answer

**Features:**
- Maps answer segments to supporting chunks
- Tracks document-level provenance
- Calculates supported claim ratio
- Identifies unsupported text segments
- Generates inline citations [1], [2], etc.

**Citation Structure:**
```python
{
  "chunk_id": 123,
  "document_id": 45,
  "document_title": "PostgreSQL Performance Guide",
  "chunk_index": 3,
  "cited_text": "pgvector supports bulk inserts...",
  "citation_number": 1
}
```

**Citation Report:**
```json
{
  "citations": [...],
  "citation_count": 3,
  "unique_documents": 2,
  "document_ids": [45, 72],
  "supported_claim_ratio": 0.86,
  "unsupported_segments": [...],
  "chunks_cited": 3,
  "chunks_unused": 2
}
```

**Validation:**
- Verify citations reference actual retrieved chunks
- Detect dangling or invalid citations
- Calculate valid citation ratio

## Integration with Phases 7 & 8

**Complete Pipeline:**

```
Query
  ↓
Phase 8: Query Transform (recall expansion)
  ↓
Multi Retrieval
  ↓
Phase 9: Context Filter (quality control)
  ↓
Phase 7: Selective Rerank (ordering)
  ↓
Phase 9: Evidence Scoring (confidence)
  ↓
Phase 9: Citation Tracking (provenance)
  ↓
LLM + Answer Generation
  ↓
Cite Sources
```

## Modes (Consistent with Phases 7 & 8)

```env
EVIDENCE_AWARE_MODE=off       # Fastest, no filtering
EVIDENCE_AWARE_MODE=selective # Balanced (recommended)
EVIDENCE_AWARE_MODE=always    # Always filter and score
```

## API Response Example

```json
{
  "query": "Why does pgvector timeout on large imports?",
  "results": [...],
  "count": 5,
  "config": {
    "query_transform_enabled": true,
    "query_transform_applied": true,
    "generated_queries": [...],
    
    "rerank_enabled": true,
    "rerank_applied": false,
    
    "evidence_aware_enabled": true,
    "evidence_mode": "selective",
    "filters_applied": ["dedup", "per_doc_limit"],
    "chunks_filtered": 3,
    "compression_ratio": 0.62,
    
    "retrieval_confidence": {
      "score": 0.72,
      "band": "medium",
      "evidence_strength": "moderate",
      "coverage_estimate": 0.78
    }
  }
}
```

## Admin Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /admin/evidence/status` | Configuration & statistics |
| `POST /admin/evidence/tune` | Adjust filter parameters |
| `POST /admin/evidence/test-confidence` | Test confidence scoring |
| `POST /admin/evidence/reset-stats` | Reset counters |

## Success Criteria (Met)

✅ Context filtering reduces redundancy (dedup, per-doc limit, boilerplate)  
✅ Evidence confidence scoring correlates with answer quality  
✅ Confidence bands (high/medium/low/insufficient) guide behavior  
✅ Citations track chunk-to-answer provenance  
✅ Unsupported claims are identified  
✅ Response metadata explains filtering and confidence  
✅ Admin endpoints enable tuning and testing  
✅ Consistent mode pattern with Phases 7 & 8  
✅ No major latency regression  

## Files Created/Modified

| File | Purpose |
|------|---------|
| `shared/context_filter.py` | Quality filtering engine |
| `shared/evidence_scorer.py` | Confidence calculation |
| `shared/citation_tracker.py` | Provenance tracking |
| `api/app.py` | Integration & admin endpoints |
| `.env.example` | Configuration options |

## Production Recommendations

### Conservative Start
```env
EVIDENCE_AWARE_MODE=selective
CONTEXT_DEDUP_THRESHOLD=0.90
CONTEXT_MAX_PER_DOC=2
CONTEXT_MAX_CHUNKS=8
```

### Monitoring
Watch these metrics:
- `compression_ratio` — Should be 0.6-0.8 (removing 20-40%)
- `retrieval_confidence.score` — Track distribution
- `chunks_filtered` — Shouldn't be too aggressive
- `supported_claim_ratio` — Target > 0.80

### Tuning Guidance
- **Too aggressive filtering** → Lower dedup threshold, increase max_per_doc
- **Low confidence scores** → Check if query is answerable from corpus
- **High unsupported ratio** → Improve retrieval or add more content

## Future Enhancements

- **Claim-level citation mapping** — Exact span matching
- **Contradiction detection** — Flag when chunks disagree
- **Dynamic thresholds** — Adapt filters based on query type
- **Citation verification** — LLM checks if citations actually support claims

## Summary

Phase 9 completes the retrieval stack:

| Phase | Focus | Quality Layer |
|-------|-------|---------------|
| 7 | Selective reranking | Ordering |
| 8 | Query transformations | Recall |
| 9 | Evidence-aware retrieval | Trustworthiness |

**Together:** Better candidates → Better ordering → Better filtering → Better answers
