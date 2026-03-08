# Phase 3 Context: CI Verification

**Created:** 2026-03-08  
**Discussed:** 2026-03-08 (deep-dive areas: 2→3→4→1)  
**Status:** Ready for research and planning

---

## Phase Objective

Automated tests demonstrate that confidence-to-behavior mapping works end-to-end: different confidence bands produce different response strategies, and the calibration loop produces threshold updates without manual steps. This phase locks in the control loop as a testable, verifiable system contract.

### Requirements Covered
- **CTRL-05**: Behavior changes verified in CI per confidence band
- **CTRL-06**: Calibration produces threshold updates consumed by routing without manual steps

---

## Locked Implementation Decisions

### Area 1: Test Data & Fixtures

**Strategy:** Deterministic synthetic fixtures with mocked/frozen retrieval; fixture-driven confidence injection.

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **Fixture source** | Synthetic fixtures with mocked/frozen retrieval (core suite); optional tiny frozen-data smoke layer | Phase 3 proves *routing* behavior, not retrieval quality. Retrieval stability is assumed and tested elsewhere. Synthetic fixtures keep CI deterministic and fast. |
| **Confidence injection** | Fixture-driven direct override (e.g., `confidence_score: 0.87`); no full calibration pipeline in routine CI runs | Real calibration audit is Phase 10's responsibility. Phase 3 assumes calibration is correct and uses fixtures to exercise routing logic under known confidence bands. The reload test can use calibration output, but routine tests use direct override. |
| **Test isolation model** | Shared test DB/harness with per-test logical isolation via `request_id` correlation; rollback only where async telemetry permits | Traces must be queryable after request to assert execution_path. Pure rollback may hide async telemetry commits. Use request_id as the isolation boundary. |
| **CI runtime target** | < 1 minute total; ideally 30–45 seconds | Avoid fresh ingest, embedding generation, or full pipeline startup inside test body. |
| **Out of scope** | Fresh article ingest, live embeddings, full calibration audit, retrieval-quality benchmarking | These are covered by earlier phases and later phases; Phase 3 focus is narrow: routing correctness under known inputs. |

**Fixture shape (candidate):**
```json
{
  "query": "When was X founded?",
  "query_type": "exact_fact",
  "retrieval_candidates": [
    {"text": "Founded in 1998", "similarity": 0.92, "source": "..."},
    {"text": "Established 1998", "similarity": 0.88, "source": "..."}
  ],
  "confidence_score": 0.87,
  "expected_confidence_band": "high",
  "expected_execution_path": "fast",
  "expected_route": "direct_answer"
}
```

**Fixture roster (minimum):**
- High-confidence `exact_fact` fixture (→ fast path)
- Medium-confidence `exact_fact` fixture (→ standard path)
- Low-confidence `exact_fact` fixture (→ cautious path)
- Empty retrieval fixture (→ abstain path)
- Policy reload fixture (thresholds A→B, same query, route changes)
- Boundary fixtures: just below/above each threshold transition

---

### Area 2: Execution Path Verification

**Strategy:** DB telemetry as primary proof of path taken; response structure as secondary contract proof.

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **Observability sources** | DB telemetry (primary) + response structure (secondary) | DB traces prove *which path was selected*; response structure proves *what contract was delivered*. Never rely on prose hedging or text parsing for CI assertion. |
| **Trace schema additions** | Add `execution_path`, `routing_action`, `stage_flags`, `confidence_band`, `policy_version` to policy trace in Phase 3 | These are core to control-loop verification. Not deferred to Phase 4. |
| **Trace field storage** | JSONB metadata object if migration-minimal; first-class columns otherwise | Recommended minimal shape: <br/>```json<br/>{\n  "execution_path": "fast\|standard\|cautious\|abstain",\n  "routing_action": "direct_answer\|rerank_and_expand\|conservative_prompt\|abstain",\n  "policy_version": "v42",\n  "confidence_band": "high\|medium\|low\|insufficient",\n  "stage_flags": {\n    "retrieval_expanded": boolean,\n    "reranker_invoked": boolean,\n    "generation_skipped": boolean\n  }\n}``` |
| **Response contract** | Machine-readable `status` field for abstention; debug metadata for normal paths | Example abstain response: `{"status": "insufficient_evidence", "execution_path": "abstain", "generation_skipped": true}`. Never emit a best-guess answer for insufficient-evidence cases. |
| **Test assertion pattern** | Call `/rag` → capture `request_id` → assert response contract → query policy trace → assert `execution_path` + stage flags | Primary assertion lives in the DB trace; response structure is secondary verification. |
| **Instrumentation timing** | Phase 3 responsibility, not deferred to Phase 4 | This *is* the verification layer. Deferring telemetry would break the phase objective. |

**Path-specific telemetry contract:**

| Path | execution_path | routing_action | stage_flags | response_status |
|------|---|---|---|---|
| **Fast** | "fast" | "direct_answer" | `{"retrieval_expanded": false, "reranker_invoked": false, "generation_skipped": false}` | "ok" |
| **Standard** | "standard" | "answer_with_retrieval" | `{"retrieval_expanded": false, "reranker_invoked": false, "generation_skipped": false}` | "ok" |
| **Cautious** | "cautious" | "expanded_and_reranked" | `{"retrieval_expanded": true, "reranker_invoked": true, "generation_skipped": false}` | "ok" (with hedging) |
| **Abstain** | "abstain" | "abstain" | `{"retrieval_expanded": false, "reranker_invoked": false, "generation_skipped": true}` | "insufficient_evidence" |

---

### Area 3: Calibration Reload Mechanism

**Strategy:** DB-backed policy registry with explicit admin reload endpoint. Thresholds are versioned policy data, not hardcoded constants.

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **Threshold source** | DB-backed policy/config record via PolicyRepository | Thresholds are versioned policy data, enabling replay, audit, and version tracking. Not hardcoded in Python. |
| **Router read model** | Load active policy at startup; cache in-process until reload endpoint is called | Deterministic for CI; no polling delays or TTL flakiness. |
| **Calibration write target** | DB policy row as primary; optional JSON/YAML audit artifact as secondary | Primary source of truth is the DB. File export is for human audit/debugging. |
| **Reload trigger for CI** | Explicit `POST /admin/policy/reload` endpoint | Deterministic and explicit. Avoids file watchers, TTL-based refresh, and cache-expiration surprises. |
| **Freshness guarantee** | After reload endpoint returns, next `/rag` request uses new thresholds | Tight freshness for CI verification; no eventual-consistency ambiguity. |
| **Replay contract** | Trace logs `policy_version` alongside `confidence_band` and `execution_path` | Enables reconstruction: "this request used policy v42, which mapped confidence 0.87 to band high and path fast". |

**Policy storage schema (candidate):**
```json
{
  "policy_id": "active",
  "policy_version": "v42",
  "created_at": "2026-03-08T12:00:00Z",
  "confidence_thresholds": {
    "high_min": 0.85,
    "medium_min": 0.60,
    "low_min": 0.35,
    "insufficient_max": 0.35
  },
  "calibration_metadata": {
    "source_run_id": "calibration_2026-03-08_audit",
    "calibration_status": "valid|degraded|insufficient_data"
  }
}
```

**CI reload test flow:**
1. Seed policy with thresholds A (`high_min: 0.80, medium_min: 0.55, low_min: 0.30`)
2. Call `/rag` → assert `policy_version: "vA"`, `execution_path: "cautious"` (due to test confidence 0.65)
3. Write new policy to DB with thresholds B (`high_min: 0.70, medium_min: 0.50, low_min: 0.25`)
4. Call `POST /admin/policy/reload`
5. Call `/rag` again with same query → assert `policy_version: "vB"`, `execution_path: "standard"` (due to test confidence 0.65 now mapping to medium)
6. Assert paths differ; assert policy_versions differ

---

### Area 4: Test Coverage Scope

**Strategy:** All 4 execution paths, boundary conditions at each threshold transition, single stable query type, edge cases that directly exercise the control contract.

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **Execution paths** | All 4: fast, standard, cautious, abstain | Phase 3 must prove full band-to-path mapping. Anything less leaves a hole in the control-loop contract. Priority: abstain (safety), cautious (safeguards), fast (efficiency), standard (control). |
| **Boundary tests** | One deterministic below/above test per band transition (high↔medium, medium↔low, low↔insufficient) | Proves routing flips at the correct threshold. Avoids floating-point torture suite; defer exhaustive precision to later phases. |
| **Query type scope** | Single stable type (`exact_fact`); multi-type routing deferred to Phase 5 contextual routing | Phase 3 proves confidence-band routing. Query-type routing is a separate dimension added later. Keeps test meaning clear: behavior changed because of confidence, not query classification. |
| **Edge cases in scope** | (1) Empty retrieval → abstain contract, (2) Weak evidence → cautious behavior, (3) Threshold reload behavior, (4) Optional latency check (fast completes faster than cautious). | These directly exercise the Phase 3 control contract. Include them. |
| **Edge cases deferred** | Conflicting evidence (Phase 5 retrieval_state), evidence-shape richness (Phase 5), combinatorial precision sweeps, single weak vs many weak chunks | These belong to later phases where retrieval_state and evidence_shape become routing dimensions. |

**Minimum Phase 3 test matrix (11 core + optional fixtures):**

| # | Name | Query | Confidence | Expected Band | Expected Path | Assertion |
|---|------|-------|-----------|---|---|---|
| 1 | High-confidence exact_fact | "When was X founded?" | 0.87 | high | fast | `execution_path=="fast"`, `reranker_invoked==false`, `context_expanded==false` |
| 2 | Medium-confidence exact_fact | "When was X founded?" | 0.65 | medium | standard | `execution_path=="standard"`, normal retrieval |
| 3 | Low-confidence exact_fact | "When was X founded?" | 0.45 | low | cautious | `execution_path=="cautious"`, `reranker_invoked==true` OR `context_expanded==true` |
| 4 | Empty retrieval | "When was X founded?" | N/A | insufficient | abstain | `execution_path=="abstain"`, `status=="insufficient_evidence"`, `generation_skipped==true` |
| 5 | Boundary: high↔medium below | "When was X founded?" | 0.8399 | medium | standard | Threshold assumed 0.85; confidence below → medium band |
| 6 | Boundary: high↔medium above | "When was X founded?" | 0.8501 | high | fast | Threshold assumed 0.85; confidence above → high band |
| 7 | Boundary: medium↔low below | "When was X founded?" | 0.5999 | low | cautious | Threshold assumed 0.60; confidence below → low band |
| 8 | Boundary: medium↔low above | "When was X founded?" | 0.6001 | medium | standard | Threshold assumed 0.60; confidence above → medium band |
| 9 | Boundary: low↔insufficient below | "When was X founded?" | 0.3499 | insufficient | abstain | Threshold assumed 0.35; confidence below → insufficient band |
| 10 | Boundary: low↔insufficient above | "When was X founded?" | 0.3501 | low | cautious | Threshold assumed 0.35; confidence above → low band |
| 11 | Policy reload: A→B | Same as medium-confidence, run 2x with reload | 0.65 → 0.65 | medium→low (due to threshold shift) | standard→cautious | `policy_version` changes; `execution_path` changes; same query routed differently |
| 12 (opt) | Latency check | High-confidence vs low-confidence, same query | See rows 1,3 | high vs low | fast vs cautious | fast latency < cautious latency (relative timing acceptable if CI stability strong) |

**Query type**: All fixtures use `exact_fact` unless explicitly required otherwise (e.g., abstain via empty retrieval is type-agnostic).

---

## Deferred Decisions

None at this stage. All four major areas (Execution Path Verification, Calibration Reload, Test Coverage, Fixtures) are fully decided.

**Minor clarifications for research/planning:**
- Exact threshold values (0.85, 0.60, 0.35) should be validated against current calibration; may differ slightly.
- Latency check (row 12) is optional; include only if CI harness is stable enough to avoid timing flakiness.
- `stage_flags` field names may need to align with actual implementation terminology (e.g., `rerank_applied` vs `reranker_invoked`).

---

## Next Steps

### For Research Phase
1. **Validate current state of policy trace schema** — confirm which telemetry fields already exist, which must be added
2. **Audit ContextualRouter** — how are thresholds currently stored and loaded?
3. **Check PolicyRepository interface** — what methods exist for policy loading/updating?
4. **Locate calibration output format** — where does calibration script write results? Can it write to DB?
5. **Verify async telemetry behavior** — do traces commit reliably inside request transaction, or outside?
6. **Confirm admin endpoint patterns** — are there existing `/admin/*` endpoints that can serve as model for reload endpoint?

### For Planning Phase
1. **Define trace schema migration** — add `execution_path`, `routing_action`, `stage_flags`, `policy_version` fields
2. **Implement `/admin/policy/reload` endpoint** — refresh in-process policy cache from DB
3. **Define fixture harness** — synthetic fixtures, mocked retrieval, confidence override mechanism
4. **Write test fixtures** — 11 core + optional latency check
5. **Implement assertion helpers** — request_id correlation, trace query, response contract validation
6. **Create CI test suite** — pytest structure, fixtures, parametrization
7. **Document telemetry contract** — path → trace shape mapping for all 4 execution paths

---

## Success Criteria (from ROADMAP)

- [ ] A pytest test with forced-high confidence score demonstrates fast-path execution; the same question with forced-low confidence demonstrates cautious or abstain path — asserted via routing telemetry
- [ ] Running the calibration script regenerates thresholds; the router picks up new thresholds within one request cycle
- [ ] All four execution paths (fast / standard / cautious / abstain) have at least one CI test covering expected behavior

---

*Context locked: 2026-03-08*  
*Ready for phase research and planning*
