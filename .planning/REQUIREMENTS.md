# Requirements: Article Index — Control Architecture Milestone

**Defined:** 2026-03-08
**Core Value:** The system knows when to trust its own retrieval — routing high-confidence answers directly, applying extra effort for ambiguous queries, and abstaining rather than hallucinating when evidence is insufficient.

## v1 Requirements

Requirements for this milestone. Builds toward self-regulating, evidence-aware retrieval and answer routing.

### Pre-Condition: Startup Fix

- [ ] **PRE-01**: FastAPI `lifespan()` double-yield bug is fixed — hybrid retriever, query transformer, context filter, reranker, and context builder are all initialized during startup (not shutdown)
- [ ] **PRE-02**: All existing hybrid RAG integration tests pass with startup-initialized components

### Control Loop: Runtime Behavior

- [ ] **CTRL-01**: High-confidence retrieval (calibrated band) routes to fast answer path with no additional processing overhead
- [ ] **CTRL-02**: Medium-confidence retrieval triggers selective reranking and/or expanded retrieval before answer generation
- [ ] **CTRL-03**: Low-confidence retrieval produces conservative answer phrasing and stronger citation requirements enforced at prompt level
- [ ] **CTRL-04**: Insufficient-confidence retrieval produces an explicit abstention or weak-evidence signal rather than a best-guess answer

### Control Loop: Verification

- [ ] **CTRL-05**: Confidence-to-behavior mapping is verified in CI — a test demonstrates that routing changes produce observably different response behavior (not just different confidence scores)
- [ ] **CTRL-06**: Calibration runs in CI produce threshold updates that are reflected in the next routing decision without manual intervention

### Policy Infrastructure

- [ ] **PLCY-01**: Policy registry supports versioned policies with no data loss on update or rollback
- [ ] **PLCY-02**: Replay harness correctly recreates routing decisions from stored policy traces with deterministic output
- [ ] **PLCY-03**: Telemetry captures all routing decisions with sufficient context (query type, confidence band, evidence shape, retrieval state, routing action) for audit and replay

### Contextual Routing

- [ ] **CTX-01**: Routing decisions incorporate query type (exact_fact, comparison, summarization, ambiguous) as a first-class routing dimension, not just confidence band
- [ ] **CTX-02**: Evidence shape (coverage, spread, density) drives retrieval budget decisions — dense evidence reduces expanded-retrieval spend
- [ ] **CTX-03**: Retrieval state (SOLID / FRAGILE / CONFLICTED / SPARSE / ABSENT) maps to distinct execution paths with different prompting and phrasing strategies
- [ ] **CTX-04**: Effort budgets are enforced — latency-sensitive paths (exact_fact + SOLID evidence) bypass expensive reranking; ambiguous paths are permitted to spend more

## v2 Requirements

Deferred to future milestones. Tracked but not in current roadmap.

### Platform Upgrades

- Streaming answer generation — useful for UX but doesn't affect control accuracy
- Multi-model routing at inference time — adds complexity, deferred until control loop is stable
- CORS origin configuration from environment — security hygiene, deferred (not user-facing)
- `cleanup_old_embeddings_task` implementation — the stub task should be completed or removed

### Observability

- Admin dashboard for routing decision distribution — Flower + DB telemetry cover monitoring for now
- Real-time policy performance metrics — deferred until replay harness is reliable

## Out of Scope

| Feature | Reason |
|---------|--------|
| External AI API integration | Ollama-only; no external API fees or data egress |
| User authentication / multi-tenant | Single-operator tool; not a product |
| Frontend / UI | API-only system |
| Horizontal scaling / load balancing | Single-node deployment; scaling deferred |
| Breaking changes to `/rag` or `/search` API | Backward compatibility required |
| Replacing pgvector with external vector DB | Stack constraint; PostgreSQL-only |

## Traceability

Requirement-to-phase mapping. Updated by roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PRE-01 | Phase 1 | Pending |
| PRE-02 | Phase 1 | Pending |
| CTRL-01 | Phase 2 | Pending |
| CTRL-02 | Phase 2 | Pending |
| CTRL-03 | Phase 2 | Pending |
| CTRL-04 | Phase 2 | Pending |
| CTRL-05 | Phase 3 | Pending |
| CTRL-06 | Phase 3 | Pending |
| PLCY-01 | Phase 4 | Pending |
| PLCY-02 | Phase 4 | Pending |
| PLCY-03 | Phase 4 | Pending |
| CTX-01 | Phase 5 | Pending |
| CTX-02 | Phase 5 | Pending |
| CTX-03 | Phase 5 | Pending |
| CTX-04 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 15 total
- Mapped to phases: 15
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-08*
*Last updated: 2026-03-08 after project initialization*
