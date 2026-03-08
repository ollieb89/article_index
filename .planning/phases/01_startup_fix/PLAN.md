# Phase 1: Startup Fix — Plan

**Phase:** 1 / 5  
**Goal:** Fix the FastAPI `lifespan()` double-yield defect so pipeline components initialize at startup, not shutdown  
**Requirements:** PRE-01, PRE-02  
**Success Criteria:** 3  

---

## The Problem

The FastAPI `lifespan()` context manager in `api/app.py` has a critical double-yield defect:

```
Lines 175–335 (current structure):
  175: @asynccontextmanager
  176: async def lifespan(app: FastAPI):
  
  177–203:   Startup code (Phase 14 classifiers, ollama initialize)
  204:   YIELD (first)  ← Should be here after ALL startup
  205–334:   MORE startup code! (hybrid_retriever, query_transformer, context_filter, etc.)
  335:   YIELD (second)   ← Should NOT be here
  336–342:   Shutdown code
```

**Impact:** Everything initialized between the two yields (lines 205–334) runs during shutdown, not startup. This includes:
- `hybrid_retriever` — Never live during request handling → hybrid RAG fails silently
- `query_transformer` — Never live → query expansion disabled
- `context_filter` — Never live → evidence filtering broken
- `reranker` — Never live → selective reranking skipped
- `context_builder` — Never live → context building fails

Request handlers that expect `app.state.hybrid_retriever` find `None`, causing silent fallbacks or `AttributeError`.

---

## Solution Breakdown

### Step 1: Consolidate Startup Initialization

Move all initialization code to BEFORE the first yield. The corrected structure:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the FastAPI application."""
    # ============ STARTUP (all before yield) ============
    logger.info("Starting up article index API...")
    
    # 1. Ensure Ollama models available
    models_status = await embedding_manager.ensure_models_available()
    
    # 2. Load policy
    try:
        policy_data = await policy_repo.get_active_policy()
        # ... policy loading code ...
    except Exception as e:
        # ... error handling ...
    
    # 3. Initialize Phase 14 modules
    app.state.query_classifier = QueryClassifier()
    app.state.evidence_shape_extractor = EvidenceShapeExtractor()
    app.state.retrieval_state_labeler = RetrievalStateLabeler()
    app.state.contextual_router = ContextualRouter()
    
    # 4. Initialize Ollama client
    await ollama_client.initialize()
    
    # 5. Initialize hybrid retriever (MOVE HERE)
    app.state.hybrid_retriever = HybridRetriever(...)
    
    # 6. Initialize query transformer (MOVE HERE)
    app.state.query_transformer = QueryTransformer(...)
    
    # 7. Initialize context filter (MOVE HERE)
    app.state.context_filter = ContextFilter(...)
    
    # 8. Initialize reranker (MOVE HERE)
    app.state.reranker = Reranker(...)
    
    # 9. Initialize context builder (MOVE HERE)
    app.state.context_builder = ContextBuilder(...)
    
    # 10. Set feature flags
    app.state.use_hybrid_rag = os.getenv('USE_HYBRID_RAG', 'false').lower() == 'true'
    
    # 11. Initialize HNSW search params
    try:
        await db_manager.set_search_params(ef_search=hnsw_ef_search)
    except Exception as e:
        logger.warning(...)
    
    logger.info("Startup complete")
    
    # ============ YIELD (one time, after startup) ============
    yield
    
    # ============ SHUTDOWN (after yield) ============
    logger.info("Shutting down article index API...")
    await ollama_client.close()
```

### Step 2: Remove Second Yield

Delete the duplicate `yield` statement at line 335. There must be exactly one `yield` in a lifespan context manager.

### Step 3: Move Shutdown Code

Any cleanup that runs "after yield" stays after the yield. Currently there's only `await ollama_client.close()`, which is correct.

---

## Implementation Checklist

- [ ] **Read full lifespan function** (api/app.py, lines 174–342)  
  Identify all initialization blocks that need consolidation

- [ ] **Extract initialization blocks**  
  - Phase 14 classifier init (lines 200–203) — already before yield ✓
  - Policy loading (lines 183–196) — already before yield ✓
  - Ollama initialize (line 197) — already before yield ✓
  - Hybrid retriever init (lines 209–226) — MOVE BEFORE YIELD
  - Query transformer init (lines 228–243) — MOVE BEFORE YIELD
  - Context filter init (lines 245–260) — MOVE BEFORE YIELD
  - Reranker init (lines 262–296) — MOVE BEFORE YIELD
  - Context builder init (lines 298–303) — MOVE BEFORE YIELD
  - Feature flags (line 305–306) — MOVE BEFORE YIELD
  - HNSW params (lines 308–313) — MOVE BEFORE YIELD

- [ ] **Remove duplicate yield**  
  Delete line 335 (second yield statement)

- [ ] **Clean up shutdown block**  
  Keep only `await ollama_client.close()` after the single yield

- [ ] **Verify log messages**  
  Ensure all component initialization logs appear during startup, not shutdown

---

## Verification Steps

### 1. Static Inspection (code review)
- [ ] Lifespan function has exactly **1** yield statement
- [ ] All `app.state.*` assignments for pipeline components are **before** the yield
- [ ] Shutdown code (after yield) only contains cleanup, no initialization
- [ ] Log statement "Startup complete" comes **before** yield
- [ ] Log statement "Shutting down" comes **after** yield

### 2. Startup Log Inspection
Run the API and capture startup logs:
```bash
docker compose up api 2>&1 | grep -E "(Starting up|Hybrid ranking|Query transformer|Evidence-aware|Reranker|context_builder|Startup complete|Shutting down)"
```

Expected output (in startup phase):
```
Starting up article index API...
Model availability: {...}
Loaded active policy version: ...
Hybrid ranking mode: weighted
Query transformer initialized: mode=...
Evidence-aware retrieval initialized: mode=...
Reranker initialized: mode=...
Startup complete  ← MUST appear here
[API ready to accept requests]
```

### 3. Request Handler Inspection
Verify components are available in request context (write a test or use a curl request):
```python
# In any request handler, these should NOT be None
assert app.state.hybrid_retriever is not None
assert app.state.query_transformer is not None  # or None if disabled, but intentionally
assert app.state.context_filter is not None     # or None if disabled
assert app.state.reranker is not None           # or None if disabled
assert app.state.context_builder is not None
```

### 4. Integration Test Suite
Run all integration tests to confirm no regression:
```bash
make test
```

All tests in `tests/` must pass without modification. Specifically:
- `test_async_ingestion.py` — ensures hybrid RAG pipeline works end-to-end
- `test_async_failure.py` — ensures error handling is preserved
- Any other integration tests covering `/rag` or `/search/hybrid`

### 5. Smoke Test
```bash
./scripts/smoke_test.sh
```

Must pass all health checks and basic requests.

---

## Dependencies

**Blocks:** All control-loop work (Phases 2–5)  
**Depends On:** None — this is Phase 0 pre-condition

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Concurrent initialization issues | Pipeline components race during startup | Components already have async-aware init; verify no state mutation race conditions in test |
| Config misread | Wrong env vars → wrong setup | Log all env var reads during startup; CI must capture logs |
| Shutdown hanging | API gets stuck on shutdown | Ensure no blocking I/O in shutdown; timeout at 30s |
| Regression in hybrid RAG | Existing `/rag` requests fail | Run full test suite before commit; verify hybrid features still work |
| App startup failure | API never becomes ready | Watch for dependency loop (e.g., reranker needing hybrid_retriever before init) |

---

## Success Criteria Verification

**Criterion 1:** All hybrid pipeline components are set on `app.state` during startup phase, confirmed by startup logs
- ✓ Logs show "Hybrid ranking mode", "Query transformer initialized", etc. before "Startup complete"
- ✗ Any of these logs appear during shutdown or not at all

**Criterion 2:** No `AttributeError` or silent `None` fallback on any `/rag` or `/search/hybrid` request
- ✓ Requests succeed and use actual hybrid retrieval (not fallback)
- ✗ Errors like "app.state.hybrid_retriever is None" or silent fallback seen in logs

**Criterion 3:** All existing integration tests pass without modification
- ✓ `make test` exits 0; all tests PASSED
- ✗ Any test fails or requires code changes to pass

---

## Estimated Effort

| Task | Effort | Notes |
|------|--------|-------|
| Code audit + fix | 15 min | Straightforward move + delete |
| Startup log inspection | 10 min | Manual or grep |
| Integration test run | 15 min | Full test suite |
| Regression validation | 10 min | Spot-check a few requests |
| **Total** | **~50 min** | Low risk, high confidence |

---

## Implementation Notes

### Key Code Locations

- **Main function:** `api/app.py`, lines 174–342
- **Test coverage:** `tests/test_async_ingestion.py` (main hybrid RAG test)
- **Smoke test:** `scripts/smoke_test.sh`
- **Config reference:** `.env.example` (documents all env vars used during init)

### Environment Variables to Watch

All of these are read during startup initialization:
```
HYBRID_USE_RRF, HYBRID_RANKING_MODE, HYBRID_LEXICAL_WEIGHT, HYBRID_SEMANTIC_WEIGHT, 
HYBRID_LEXICAL_LIMIT, HYBRID_VECTOR_LIMIT, HYBRID_AUTO_TUNE_WEIGHTS, 
QUERY_TRANSFORM_MODE, QUERY_TRANSFORM_MAX_QUERIES, ... (many more)
```

Ensure CI / test environment sets `.env` correctly before running API.

### Backward Compatibility

**No breaking changes to API contract:**
- Request/response models unchanged
- Endpoint signatures unchanged
- Behavior should become MORE consistent (not less)

---

## Next Steps

After Phase 1 complete:
1. Commit fix to main branch (or feature branch if WIP)
2. Run full CI pipeline to confirm integration tests pass
3. Proceed to Phase 2: Confidence-Driven Control Loop

---

*Plan created: 2026-03-08 | Status: Ready for execution*
