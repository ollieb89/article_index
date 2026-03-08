# Article Index

## What This Is

Article Index is a self-regulating, evidence-aware RAG system for articles. It ingests, chunks, and embeds articles using local AI (Ollama), and answers natural language questions by combining hybrid lexical + vector retrieval with calibrated confidence scoring, query classification, and contextual routing to determine *how* to respond based on the quality and shape of available evidence. The system is designed to know when to trust itself, when to spend extra retrieval budget, and when to abstain rather than confabulate.

## Core Value

The system knows when to trust its own retrieval — routing high-confidence answers directly, applying extra effort for ambiguous queries, and abstaining rather than hallucinating when evidence is insufficient.

## Requirements

### Validated

<!-- Capabilities already shipped and confirmed in codebase -->

- ✓ Article ingestion (sync + async) with SHA-256 duplicate detection — Phase 1–2
- ✓ Token-based chunking and chunk-level embeddings (nomic-embed-text, 768-dim) — Phase 1–2
- ✓ RAG answer generation via local LLM (llama3.2) — Phase 3
- ✓ Hybrid retrieval: BM25 lexical + pgvector cosine with RRF / weighted merge — Phase 4–6
- ✓ Selective reranking (cross-encoder, policy-gated) — Phase 7
- ✓ Query transformations for recall expansion — Phase 8
- ✓ Evidence filtering, confidence scoring, and citation tracking — Phase 9
- ✓ Confidence calibration pipeline (CI-ready, threshold tuning) — Phase 10
- ✓ Query classification (exact_fact, comparison, summarization, ambiguous) — Phase 14 M1
- ✓ Evidence shape extraction (coverage, spread, density metrics) — Phase 14 M2
- ✓ Retrieval state labeling (SOLID / FRAGILE / CONFLICTED / SPARSE / ABSENT) — Phase 14 M3
- ✓ Contextual policy routing (ContextualRouter wired into RAG pipeline) — Phase 14 M4
- ✓ Policy trace telemetry logging to DB — Phase 14 M6
- ✓ RSS feed ingestion and background polling (Celery) — Platform
- ✓ HNSW vector index for ANN search — Platform

### Active

<!-- Current milestone: make the control loop close at runtime -->

- [ ] **CTRL-01**: High-confidence retrieval routes to fast answer path without additional processing
- [ ] **CTRL-02**: Medium-confidence retrieval triggers expanded retrieval or selective reranking
- [ ] **CTRL-03**: Low-confidence retrieval produces conservative phrasing and stronger citation requirements
- [ ] **CTRL-04**: Insufficient-confidence retrieval produces abstention or explicit weak-evidence signal
- [ ] **CTRL-05**: Control loop is verified in CI — calibration → threshold update → routing decision → reply-verified behavior change
- [ ] **CTRL-06**: FastAPI lifespan bug fixed — hybrid retriever, query transformer, context filter initialized at startup
- [ ] **PLCY-01**: Policy registry is production-reliable (versioned, queryable, no data loss)
- [ ] **PLCY-02**: Replay harness correctly recreates routing decisions from historical traces
- [ ] **PLCY-03**: Telemetry is complete and consistent — all routing decisions captured with sufficient context for audit and replay
- [ ] **CTX-01**: Routing decisions incorporate query type as a first-class input (not just confidence band)
- [ ] **CTX-02**: Evidence shape (coverage, spread, density) drives retrieval budget and prompting strategy
- [ ] **CTX-03**: Retrieval state (SOLID/FRAGILE/CONFLICTED/SPARSE) maps to distinct execution paths
- [ ] **CTX-04**: Effort budgets constrain latency-sensitive paths (fast-path vs. expanded-retrieval-path)

### Out of Scope

- External AI API calls (OpenAI, Anthropic, etc.) — Ollama-only for privacy and zero API cost
- User authentication / multi-tenant — single-operator tool, not a product
- Streaming answer generation — deferred; platform upgrade, not control architecture
- Multi-model routing at inference time — deferred; single-model assumption simplifies calibration
- Horizontal scaling / load balancing — single-node deployment for now
- UI / frontend — API-only system; no frontend planned

## Context

**Existing system:** A fully-wired retrieval pipeline exists: query_classifier → hybrid retrieval → reranker → context_filter → evidence_scorer → evidence_shape → retrieval_state → contextual_router → answer generation. The individual components are implemented and tested in isolation.

**The gap:** The pipeline's routing decisions (from ContextualRouter) do not yet *change runtime behavior*. High and low confidence paths produce the same prompt and retrieval depth — the routing signal exists but drives nothing. Confidence calibration (Phase 10) is solid enough to be used as a control signal, not just a metric.

**Critical bug:** The FastAPI `lifespan()` context manager has a double-yield defect. The hybrid retriever, query transformer, context filter, reranker, and context builder are initialized *after* the first yield — inside the shutdown block — meaning they are never live during request handling. This must be fixed before control-loop work proceeds.

**Trajectory:** Phase 7 (selective reranking) → Phase 8 (recall expansion) → Phase 9 (evidence scoring + citations) → Phase 10 (calibration) → Phase 11 (control loop) → Phase 12–13 (policy hardening) → Phase 14 (contextual routing)

**Phase 14 status:** Query classification, evidence shape, retrieval state labeling, and ContextualRouter are partially implemented in the codebase (from the phase_14.md planning document), but the routing rules and execution paths are not fully wired and verified.

## Constraints

- **AI stack**: Ollama only (nomic-embed-text + llama3.2) — no external API calls
- **Database**: PostgreSQL 16 + pgvector — no external vector DB
- **Language**: Python 3.13+ async-first — sync adapters for Celery tasks only
- **Replayability**: All routing decisions must be replayable via the policy replay harness
- **API compatibility**: Existing REST API contract must be preserved (no breaking changes to `/rag`, `/search`)
- **Control loop testability**: Phase 11 behavior changes must be verifiable in CI without a running Ollama instance

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Local AI only (Ollama) | Zero API cost, privacy-preserving, offline-capable | — Existing |
| Hybrid retrieval (BM25 + vector) | Recall improvement for exact-term and version queries | ✓ Good |
| Calibration as control signal | Phase 10 hardened calibration enough to trust at runtime | — Pending |
| Phase 11 before Phase 14 | Establish the control loop before extending routing dimensions | — Pending |
| Fix lifespan bug as Phase 0 | Pipeline components not initialized at startup; everything downstream is broken | — Pending |
| Policy decisions must be replayable | Telemetry + replay harness enables closed-loop optimization | — Pending |

---
*Last updated: 2026-03-08 after project initialization*
