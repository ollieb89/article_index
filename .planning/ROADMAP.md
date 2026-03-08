# Roadmap: Article Index — Control Architecture Milestone

**Created:** 2026-03-08
**Depth:** Standard (5 phases)
**Core Value:** The system knows when to trust its own retrieval — routing high-confidence answers directly, applying extra effort for ambiguous queries, and abstaining rather than hallucinating when evidence is insufficient.

---

## Overview

| # | Phase | Goal | Requirements | Success Criteria | Status |
|---|-------|------|--------------|-----------------|--------|
| 1 | Startup Fix | ✓ Complete | 2026-03-08 | 3 | ✅ |
| 2 | Confidence-Driven Control | ✓ Make confidence bands produce different runtime behavior | CTRL-01 – CTRL-04 | 4 | ✅ 2026-03-08 |
| 3 | CI Verification | Prove behavior changes and calibration loop in automated tests | CTRL-05, CTRL-06 | 3 | ✅ 2026-03-08 |
| 4 | Policy Hardening | Make policy registry, replay harness, and telemetry production-reliable | PLCY-01 – PLCY-03 | 3 | ✅ 2026-03-08 |
| 5 | Contextual Routing | Route on query type + evidence shape + retrieval state + effort budget | CTX-01 – CTX-04 | 4 | ✅ 2026-03-08 |

**Progress:** 5/5 phases complete (100%) ✅

---

## Phase 1: Startup Fix

**Goal:** Fix the FastAPI `lifespan()` double-yield defect so that all pipeline components — hybrid retriever, query transformer, context filter, reranker, and context builder — are initialized during application startup, not during shutdown. This is the pre-condition for all control-loop work.

**Why first:** Every phase that follows depends on the RAG pipeline actually being live at request time. Building Phase 2 routing on top of broken initialization would produce silent degradation rather than control.

**Requirements:**
- PRE-01: lifespan double-yield fixed; components initialized at startup
- PRE-02: Existing hybrid RAG integration tests pass with fixed startup

**Plans:**
1/1 plans complete
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
- **[4-1-PLAN.md](4-1-PLAN.md)**: Foundation — Policy versioning, SHA-256 hashing, telemetry instrumentation (Waves 1-3)
- **[4-2-PLAN.md](4-2-PLAN.md)**: Replay & Admin — Deterministic replay harness, admin endpoints (Waves 4-5)
- **[4-3-PLAN.md](4-3-PLAN.md)**: Verification — Integration testing, PLCY-01/02/03 validation (Wave 6)

**Execution Order:** Plan 4-1 → 4-2 → 4-3 (sequential, each plan depends on prior)

**Success Criteria:**
1. Policy update and rollback round-trips produce identical schema state with no row loss, verified by test comparing before/after DB state
2. Replaying any stored policy trace produces the same routing decision; tested across at least 20 historical traces from different routing paths
3. Every `/rag` request produces a telemetry row with non-null values for: query_type, confidence_band, evidence_shape, retrieval_state, routing_action — asserted by post-request DB query in CI

---

## Phase 5: Contextual Policy Routing ✅ COMPLETE

**Goal:** Extend routing beyond confidence bands. Incorporate query type, evidence shape, retrieval state, and effort budgets as first-class routing dimensions. Different question types + evidence profiles follow different execution strategies.

**Status:** ✅ Complete (2026-03-08)  
**Test Count:** 107 tests passing  
**Implementation:** See [5-IMPLEMENTATION-SUMMARY.md](5-IMPLEMENTATION-SUMMARY.md)

**Requirements:**
- CTX-01: Query type is a first-class routing dimension ✅
- CTX-02: Evidence shape drives retrieval budget decisions ✅
- CTX-03: Retrieval state (SOLID/FRAGILE/CONFLICTED/EMPTY) maps to distinct execution paths ✅
- CTX-04: Effort budgets enforced — fast path for exact_fact + SOLID; expanded path for ambiguous ✅

**Implementation:**
- **[5-1-PLAN.md](5-1-PLAN.md)**: Core Rule Engine — RoutingContext, RoutingRule, RuleEngine ✅
- **[5-2-PLAN.md](5-2-PLAN.md)**: Query Classification & Evidence Shape — QueryType taxonomy, EvidenceShape bands ✅
- **[5-3-PLAN.md](5-3-PLAN.md)**: Integration & Budget Constraint — ContextualRouterV2, BudgetConstraint layer, E2E tests ✅

**Design Decisions (from [5-CONTEXT.md](5-CONTEXT.md)):**
- Declarative rule-table engine (not nested conditionals) ✅
- Precedence: Specificity → Priority → ID (stable tie-break) ✅
- Structured action objects (future-proof) ✅
- Simple condition semantics: equality + list membership only ✅
- Layered fallback: confidence-band defaults → hard safety ✅
- Query types: exact_fact, comparison, multi_hop, ambiguous, summarization, other ✅
- Evidence shape: coverage_band, agreement_band, spread_band ✅
- Effort budget: post-routing constraint (not rule condition) ✅

**Success Criteria:**
1. ✅ An `exact_fact` query with SOLID evidence completes without invoking the reranker — verified via `test_exact_fact_solid_high_to_fast`
2. ✅ A query with FRAGILE evidence triggers expanded retrieval — verified via `test_fragile_retrieval_to_cautious`
3. ✅ A CONFLICTED retrieval state produces conservative answer phrasing — verified via `test_conflicted_retrieval_to_cautious`
4. ✅ Routing decision tests cover all required combinations — verified via `test_contextual_routing_e2e.py`

---

## Execution Notes

- **Phase order is strict**: Phase 1 is a pre-condition for all others. Phases 2–3 must complete before Phase 4 is meaningful. Phase 5 is only viable after Phase 4.
- **No breaking API changes**: All phases must preserve the `/rag` and `/search` API contract.
- **Replayability invariant**: Every routing decision introduced in this milestone must be captured in telemetry and replayable.
- **Test-first for control paths**: Each confidence band and routing path should have a test before the implementation is finalized.

---
*Roadmap created: 2026-03-08*
*Last updated: 2026-03-08 after project initialization*
