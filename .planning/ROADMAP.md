# Roadmap: Article Index — Control Architecture Milestone

**Created:** 2026-03-08
**Depth:** Standard (5 phases)
**Core Value:** The system knows when to trust its own retrieval — routing high-confidence answers directly, applying extra effort for ambiguous queries, and abstaining rather than hallucinating when evidence is insufficient.

---

## Overview

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|-----------------|
| 1 | Startup Fix | Fix lifespan double-yield so pipeline components initialize at startup | PRE-01, PRE-02 | 3 |
| 2 | Confidence-Driven Control | Make confidence bands produce different runtime behavior | CTRL-01 – CTRL-04 | 4 |
| 3 | CI Verification | Prove behavior changes and calibration loop in automated tests | CTRL-05, CTRL-06 | 3 |
| 4 | Policy Hardening | Make policy registry, replay harness, and telemetry production-reliable | PLCY-01 – PLCY-03 | 3 |
| 5 | Contextual Routing | Route on query type + evidence shape + retrieval state + effort budget | CTX-01 – CTX-04 | 4 |

**15 requirements mapped across 5 phases. Full v1 coverage ✓**

---

## Phase 1: Startup Fix

**Goal:** Fix the FastAPI `lifespan()` double-yield defect so that all pipeline components — hybrid retriever, query transformer, context filter, reranker, and context builder — are initialized during application startup, not during shutdown. This is the pre-condition for all control-loop work.

**Why first:** Every phase that follows depends on the RAG pipeline actually being live at request time. Building Phase 2 routing on top of broken initialization would produce silent degradation rather than control.

**Requirements:**
- PRE-01: lifespan double-yield fixed; components initialized at startup
- PRE-02: Existing hybrid RAG integration tests pass with fixed startup

**Plans:**
1. Audit and fix `lifespan()` in `api/app.py` — remove second yield, move all initialization before first yield
2. Verify `app.state` attributes are set and reachable in request handlers
3. Run full integration test suite to confirm no regression

**Success Criteria:**
1. All hybrid pipeline components (`hybrid_retriever`, `query_transformer`, `context_filter`, `reranker`, `context_builder`) are set on `app.state` during the startup phase, confirmed by startup logs
2. No `AttributeError` or silent `None` fallback on any `/rag` or `/search/hybrid` request under normal load
3. All existing integration tests in `tests/` pass without modification

---

## Phase 2: Confidence-Driven Control Loop

**Goal:** Make confidence bands actively change runtime retrieval and answer behavior. High confidence → direct answer path. Medium → expanded retrieval or reranking. Low → conservative phrasing + stronger citations. Insufficient → abstention.

**Why second:** The routing signal exists in ContextualRouter but drives nothing. The pipeline is instrumented; it just doesn't act on its own conclusions.

**Requirements:**
- CTRL-01: High-confidence → fast path with no additional processing
- CTRL-02: Medium-confidence → selective reranking and/or expanded retrieval
- CTRL-03: Low-confidence → conservative phrasing + citation enforcement in prompt
- CTRL-04: Insufficient-confidence → explicit abstention response

**Plans:**
1. Define confidence band thresholds and map each band to a named execution path (fast / standard / cautious / abstain)
2. Wire ContextualRouter's routing decision into the RAG pipeline as execution-path selector
3. Implement per-band prompt templates (direct / hedged / cited / abstention)
4. Add expanded retrieval logic for medium-confidence path (increase context limit, trigger reranking)

**Success Criteria:**
1. A request with calibrated high-confidence score completes without invoking the reranker or expanding context — measurable via policy trace
2. A medium-confidence request invokes reranking or fetches additional chunks before answer generation
3. A low-confidence answer begins with a hedge phrase ("Based on limited evidence..." or similar) or explicitly lists source citations
4. A request where evidence is absent (ABSENT retrieval state or confidence below abstain threshold) returns a structured abstention response, not a best-guess answer

---

## Phase 3: CI Verification

**Goal:** Automated tests demonstrate that confidence-to-behavior mapping works end-to-end: different confidence bands produce different response strategies, and the calibration loop produces threshold updates without manual steps.

**Why third:** Without verifiable CI coverage, the control loop is a runtime claim, not a guarantee. Phase 3 locks in the behavior contract.

**Requirements:**
- CTRL-05: Behavior changes verified in CI per confidence band
- CTRL-06: Calibration produces threshold updates consumed by routing without manual steps

**Plans:**
1. Write pytest fixtures that inject forced confidence scores to exercise each execution path
2. Assert execution path taken (via policy trace fields) matches expected path for each confidence band
3. Wire calibration output into router config reload — no manual restart required for threshold changes

**Success Criteria:**
1. A pytest test with forced-high confidence score demonstrates fast-path execution; the same question with forced-low confidence demonstrates cautious or abstain path — asserted via routing telemetry
2. Running the calibration script regenerates thresholds; the router picks up new thresholds within one request cycle
3. All four execution paths (fast / standard / cautious / abstain) have at least one CI test covering expected behavior

---

## Phase 4: Policy Infrastructure Hardening

**Goal:** Make the policy registry, replay harness, and telemetry pipeline production-reliable. Versioned policies without data loss, deterministic replay from traces, and complete telemetry records for every routing decision.

**Why fourth:** Before contextual routing extends the routing decision space (Phase 5), the infrastructure that tracks and replays decisions must be trustworthy. A broken replay harness would mean Phase 5 routing extensions can't be audited.

**Requirements:**
- PLCY-01: Policy registry versioned, queryable, no data loss on update/rollback
- PLCY-02: Replay harness recreates routing decisions deterministically from stored traces
- PLCY-03: Telemetry captures all routing decisions with full context (query type, confidence band, evidence shape, retrieval state, routing action)

**Plans:**
1. Audit `PolicyRepository` — fix dead code (unreachable `return str(result['query_id'])`) and verify update/rollback semantics
2. Harden policy trace schema — ensure all required fields (query_type, confidence_band, evidence_shape, retrieval_state, routing_action, execution_path) are captured on every request
3. Test replay harness against real traces — deterministic output assertion

**Success Criteria:**
1. Policy update and rollback round-trips produce identical schema state with no row loss, verified by test comparing before/after DB state
2. Replaying any stored policy trace produces the same routing decision; tested across at least 20 historical traces from different routing paths
3. Every `/rag` request produces a telemetry row with non-null values for: query_type, confidence_band, evidence_shape, retrieval_state, routing_action — asserted by post-request DB query in CI

---

## Phase 5: Contextual Policy Routing

**Goal:** Extend routing beyond confidence bands. Incorporate query type, evidence shape, retrieval state, and effort budgets as first-class routing dimensions. Different question types + evidence profiles follow different execution strategies.

**Why fifth:** Only viable after the control loop (Phase 2–3) and infrastructure (Phase 4) are solid. Phase 5 extends the decision surface; if the base routing is unreliable, extending it adds complexity without control.

**Requirements:**
- CTX-01: Query type is a first-class routing dimension
- CTX-02: Evidence shape drives retrieval budget decisions
- CTX-03: Retrieval state (SOLID/FRAGILE/CONFLICTED/SPARSE/ABSENT) maps to distinct execution paths
- CTX-04: Effort budgets enforced — fast path for exact_fact + SOLID; expanded path for ambiguous

**Plans:**
1. Extend ContextualRouter routing table to incorporate query_type × retrieval_state matrix (not just confidence band)
2. Implement effort budget enforcement — skip expensive steps (reranking, expanded retrieval) on fast-path routing; permit on budget-heavy paths
3. Add CONFLICTED retrieval state handling — conservative prompting and citation enforcement regardless of confidence band
4. Write routing decision tests for each significant query_type × evidence profile combination

**Success Criteria:**
1. An `exact_fact` query with SOLID evidence completes without invoking the reranker — measurable via policy trace
2. An `ambiguous` query with FRAGILE evidence triggers expanded retrieval (larger context window or additional retrieval pass)
3. A CONFLICTED retrieval state produces conservative answer phrasing regardless of numerical confidence score — asserted via response content pattern or policy trace action field
4. Routing decision tests cover at least: `{exact_fact + SOLID}`, `{ambiguous + FRAGILE}`, `{comparison + CONFLICTED}`, `{summarization + SPARSE}`, `{any + ABSENT}` combinations

---

## Execution Notes

- **Phase order is strict**: Phase 1 is a pre-condition for all others. Phases 2–3 must complete before Phase 4 is meaningful. Phase 5 is only viable after Phase 4.
- **No breaking API changes**: All phases must preserve the `/rag` and `/search` API contract.
- **Replayability invariant**: Every routing decision introduced in this milestone must be captured in telemetry and replayable.
- **Test-first for control paths**: Each confidence band and routing path should have a test before the implementation is finalized.

---
*Roadmap created: 2026-03-08*
*Last updated: 2026-03-08 after project initialization*
