# Phase 4 Context: Policy Infrastructure Hardening

**Created:** 2026-03-08  
**Discussed:** 2026-03-08 (deep-dive areas: 1→2→3)  
**Status:** Ready for research and planning

---

## Phase Objective

Make the policy registry, replay harness, and telemetry pipeline production-reliable with immutable versioning, deterministic auditability, and forward-compatible schema evolution. This phase locks down the infrastructure that enables Phase 5 contextual routing to be trustworthy and replayable.

### Requirements Covered
- **PLCY-01**: Policy registry versioned, queryable, no data loss on update/rollback
- **PLCY-02**: Replay harness recreates routing decisions deterministically from stored traces
- **PLCY-03**: Telemetry captures all routing decisions with full context for audit and replay

---

## Locked Implementation Decisions

### Area 1: Policy Versioning & Rollback Semantics

**Core Runtime Contract**

A policy version is an **immutable content snapshot**. Only one policy version is **active** at any time. A request binds to the active policy **at request start** and uses that policy for the entire lifecycle. Reload/rollback affects only **subsequent requests**.

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **In-flight request behavior** | Requests started under v41 finish under v41; requests started after reload use v42. Do not switch policy mid-request. | Ensures deterministic replay (one request = one policy); telemetry is unambiguous; avoids split-brain behavior (retrieval under old policy, routing/generation under new). |
| **Cutover mechanism** | Request captures immutable `policy_snapshot_ref` at request start. | Makes policy binding explicit, replayable, and auditable. |
| **Rollback semantics** | Instant for new requests only; no staged drain. Supports rollback to any retained prior version, not just immediate predecessor. | Clean operational boundary; same semantics as reload; enables recovery from multiple bad versions without manual intermediate steps. |
| **Rollback targets** | Simple rollback: immediate predecessor. Explicit rollback: any named retained policy. | Common case is easy; full history is preserved for audit. |
| **Trace reference** | Store **both** `policy_version` (human-readable) **and** `policy_hash` (immutable fingerprint). | Version labels aid operator dashboards; hashes enable deterministic, tamper-proof replay. Replay uses hash as primary identity. |
| **Version immutability** | Activated policy versions are immutable and retained indefinitely. | Replay and audit lose credibility if old traces point to deleted policies. Retention bias toward preservation. |
| **Version naming** | Timestamp-based labels (e.g., `2026-03-08T15:30:01Z`) plus stable internal DB IDs. Optionally operator tags (e.g., `calibration-run-17`). | Timestamps provide readability; IDs provide stable ordering. Tags add context without sacrificing ordering. |
| **New version creation** | Every material policy-content change creates a new version. Includes: threshold changes, routing map changes, any behavior-altering update. Calibration absolutely creates a new version if thresholds change. | Version = immutable content snapshot. Activation record = metadata about when it became active. Clean separation enables audit trail without constant version churn. |
| **Version retention** | Activated policy versions and activation history are retained and not deleted. Draft/unactivated versions can be garbage-collected. | Enables replay and audit durability. Archival to cold storage is a future ops feature, not deletion. |
| **Activation validation** | Required before activation: schema validation + semantic validation. Optional advisory: replay smoke validation against sample traces. | Prevents obviously-broken policies from becoming active. Replay validation can be added later as ops sophistication. |
| **Concurrent activation** | Transaction-serialized. Only one activation transaction may succeed at a time; losers get `409 Conflict` error. | Prevents surprising cutovers; makes admin behavior explicit; preserves clean activation history. |
| **Immutability enforcement** | SHA-256 content hashes on policy body. Traces record hash. Replay verifies stored policy still hashes to same value. Cryptographic signatures deferred unless cross-system provenance or regulated audit is needed. | Strong integrity checking without key management overhead. Sufficient for Phase 4. |

**Data Model: Policy Registry**

```json
{
  "policy_id": 42,
  "policy_version": "2026-03-08T15:30:01Z",
  "policy_hash": "sha256:abcd1234...",
  "content": {
    "confidence_thresholds": {
      "high_min": 0.85,
      "medium_min": 0.60,
      "low_min": 0.35,
      "abstain_max": 0.35
    },
    "routing_map": {
      "high": "fast",
      "medium": "standard", 
      "low": "cautious",
      "insufficient": "abstain"
    },
    "execution_paths": {
      "fast": {
        "reranker_enabled": false,
        "retrieval_expanded": false,
        "answer_generation": true
      },
      "standard": {
        "reranker_enabled": false,
        "retrieval_expanded": false,
        "answer_generation": true
      },
      "cautious": {
        "reranker_enabled": true,
        "retrieval_expanded": true,
        "answer_generation": true,
        "prompt_hedging": "conservative"
      },
      "abstain": {
        "reranker_enabled": false,
        "retrieval_expanded": false,
        "answer_generation": false,
        "response_status": "insufficient_evidence"
      }
    }
  },
  "created_at": "2026-03-08T15:30:01Z"
}
```

**Data Model: Activation History**

```json
{
  "activation_id": 1234,
  "activated_policy_id": 42,
  "activated_at": "2026-03-08T15:30:05Z",
  "activated_by": "admin|calibration",
  "reason": "calibration update|manual hotfix",
  "deactivated_at": "2026-03-09T10:15:00Z"
}
```

---

### Area 2: Replay Determinism Boundary

**Core Replay Contract**

Deterministic replay reconstructs the **routing decision** from immutable trace data and frozen inputs. It reproduces the original `confidence_band`, `routing_action`, `execution_path`, and `stage_flags` without querying live retrieval systems or invoking the LLM.

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **Replay scope** | Reconstruct routing decision (confidence/evidence-scoring inputs + stage selection) only. Do not require full answer generation for determinism. | Phase 4 audit question is "was the policy path correct given the inputs?" not "was the entire response perfect?" Full lifecycle is fragile due to drift in retrieval, embeddings, models. Determinism = control plane, not data plane. |
| **Retrieval input freezing** | Replay uses original retrieval results captured in trace. Does not query live DB. | Live query is not deterministic; article edits, index rebuilds, embedding changes contaminate audit. Replay validates "what the router saw then," not "what retrieval would return now." |
| **Retrieval snapshot content** | Traces freeze: document/chunk IDs, scores/similarities, rank order, retrieval parameters, optional small content snapshots/hashes if evidence-shape logic depends on text characteristics. | Sufficient to recompute routing from frozen inputs. Optional content aids evidence-shape reconstruction but not required. |
| **LLM / Ollama in replay** | Skip entirely for deterministic core. Traces may store model name/version for reference only. Generation replay is optional diagnostic, not pass/fail. | LLM outputs are not core Phase 4 audit concern. Even with frozen model, deterministic regeneration is operationally brittle. Routing correctness is upstream from phrasing. |
| **Replay failure modes** | Replay produces different routing decision = log discrepancy for investigation. Regression mode **can** fail CI on mismatches. Mismatch = either code broke or schema/precision evolved (both need investigation). | Explicit alerting; not silently ignoring. Failure mode depends on context (audit vs. regression). |
| **Trace incompleteness** | Backfill if derivable from other trace fields. Otherwise return explicit `partial_replay` status. Strict-fail mode available if user requests it. | Avoids brittle "fail on every old trace" behavior. Backfill where safe; explicit gaps where not. |
| **Replay modes (explicit)** | Three named modes: `deterministic_audit`, `regression_sample`, `counterfactual_policy`. | Clear boundaries for different use cases. Counterfactual is valuable follow-on but not core for Phase 4. |
| **deterministic_audit mode** | "Was request X routed correctly given its original inputs?" Frozen trace inputs only, no live DB/Ollama, pass/fail on route reproduction. | Primary Phase 4 audit use case. |
| **regression_sample mode** | "Does replay harness still work after code/schema changes?" Same as audit but run in batch over sample traces in CI. | Core Phase 4 regression insurance. |
| **counterfactual_policy mode** | "What would historical routes look like under a candidate policy?" Same frozen inputs, substitute new policy, report route deltas. | Valuable for policy testing before activation; not required for Phase 4 but high-value feature to implement if time. |

**Minimal Trace Fields for Replay**

```json
{
  "request_id": "req-2026-03-08-15-30-47-xyz",
  "query": "When was PostgreSQL first released?",
  "policy_version": "2026-03-08T15:30:01Z",
  "policy_hash": "sha256:abcd1234...",
  "telemetry_schema_version": "1.0",
  
  "retrieval": {
    "parameters": {
      "limit": 5,
      "similarity_threshold": 0.7
    },
    "items": [
      {
        "item_id": "chunk-12345",
        "rank": 1,
        "similarity_score": 0.92,
        "source_doc": "doc-6789"
      },
      {
        "item_id": "chunk-12346",
        "rank": 2,
        "similarity_score": 0.88,
        "source_doc": "doc-6790"
      }
    ]
  },
  
  "scoring": {
    "confidence_score": 0.87,
    "confidence_band": "high",
    "evidence_shape_inputs": {
      "coverage": 0.89,
      "spread": 0.76,
      "density": 0.92
    }
  },
  
  "routing": {
    "routing_action": "direct_answer",
    "execution_path": "fast",
    "stage_flags": {
      "retrieval_expanded": false,
      "reranker_invoked": false,
      "generation_skipped": false
    }
  },
  
  "trace_timestamp": "2026-03-08T15:30:47Z"
}
```

---

### Area 3: Telemetry Coverage & Schema Versioning

**Phase 4 Required vs. Optional vs. Future-Dependent Fields**

| Field | Status | Reason |
|---|---|---|
| `request_id` | **Required** | Uniqueness, correlation, audit trail |
| `query` | **Required** | Replay input |
| `policy_version` | **Required** | Audit trail, human-readable policy identity |
| `policy_hash` | **Required** | Deterministic policy identity for replay |
| `telemetry_schema_version` | **Required** | Version-aware replay and schema evolution |
| `confidence_score` | **Required** | Routing decision input |
| `confidence_band` | **Required** | Routing decision output |
| `routing_action` | **Required** | Routing decision output |
| `execution_path` | **Required** | Routing decision output |
| `retrieval_items` | **Required** | Frozen replay input |
| `stage_flags` | **Required** | Observable proof of execution path (retrieval_expanded, reranker_invoked, generation_skipped, etc.) |
| `trace_timestamp` | **Required** | Chronological audit trail |
| `response_status` | **Optional** | Observability (e.g., "ok", "insufficient_evidence", truncated) |
| `latency_ms` | **Optional** | Performance diagnostics |
| `model_metadata` | **Optional** | Reference only; Ollama version, generation settings |
| `response_metadata` | **Optional** | Response structure, word count, citation count |
| `override_metadata` | **Optional** | CI/test-mode flags (X-CI-Test-Mode, confidence override, etc.) |
| `evidence_shape` | **Phase 5 Required** | Accepted now, but becomes required when Phase 5 makes it a routing input |
| `retrieval_state` | **Phase 5 Required** | Accepted now, but becomes required when Phase 5 makes it a routing input |
| `query_type` | **Phase 5 Required** | Accepted now, but becomes required when Phase 5 makes it a routing input |

**Schema Evolution Rules**

| Rule | Consequence |
|---|---|
| New field added to schema | Phase 4+1 traces include it; older traces return NULL. Backward-compatible; no migration required yet. |
| Field becomes routing-required | Flag it as such; new schema version; traces created under new policy include it in "required" set. Old traces marked as "partial replay" if missing. |
| Telemetry schema version increments | Replay harness branches on version; each version has its own validation and backfill logic. |
| Old traces remain queryable | All queries support version-aware filtering and nullable fields. No data deletion. |

**Schema Versioning Implementation**

| Decision | Outcome | Rationale |
|----------|---------|-----------|
| **Schema versioning mechanism** | Explicit `telemetry_schema_version` on every trace (e.g., "1.0", "1.1", "2.0"). Stored alongside data. | Clear compatibility contract. Replay can branch on version number. Ambiguity eliminated (NULL = old schema, not bug). |
| **Schema storage model** | Stable top-level columns for high-value fields + extensible JSONB metadata for evolving details. `telemetry_schema_version` is a stable column. | Structure where it matters; flexibility where it's needed. Avoids over-rigidity and over-flexibility at once. |
| **Missing field handling (replay)** | **Backfill if derivable** from other trace fields (e.g., `execution_path` from `confidence_band + policy_hash + routing_map`). **If not derivable**, return explicit `partial_replay` status with reasons. **Strict-fail mode** available if user explicitly requests it. | Default behavior is pragmatic (try to recover what you can). Strict mode available for audit requirements. Explicit gaps prevent silent incorrectness. |
| **Future extensibility** | Keep stable **core schema** (listed above). Extensible **JSONB metadata** layer for Phase 5+ fields not yet understood. **Do not pre-bake** every Phase 5 field as first-class columns today. Promote to stable columns only when stable and frequently queried. | Avoids over-engineering while keeping forward compatibility. Clean migration path as routing evolves. |
| **Core vs. extensions structure** | Recommend nested JSON shape: `core` (immutable, Phase 4 contract), `retrieval` (frozen inputs), `stages` (execution flags), `extensions` (future routing fields, Phase 5+). | Clear intent; easy to extend. Easier to migrate fields as they stabilize. |

**Telemetry Schema Example (Phase 4)**

```json
{
  "telemetry_schema_version": "1.0",
  "core": {
    "request_id": "req-2026-03-08-15-30-47-xyz",
    "query": "When was PostgreSQL first released?",
    "policy_version": "2026-03-08T15:30:01Z",
    "policy_hash": "sha256:abcd1234...",
    "confidence_score": 0.87,
    "confidence_band": "high",
    "routing_action": "direct_answer",
    "execution_path": "fast"
  },
  "retrieval": {
    "parameters": {
      "limit": 5,
      "threshold": 0.7
    },
    "items": [
      {"item_id": "chunk-12345", "rank": 1, "score": 0.92}
    ]
  },
  "stages": {
    "retrieval_expanded": false,
    "reranker_invoked": false,
    "generation_skipped": false
  },
  "diagnostics": {
    "latency_ms": 285,
    "response_status": "ok"
  },
  "extensions": {
    "query_type": "exact_fact",
    "retrieval_state": "SOLID",
    "evidence_shape": {
      "coverage": 0.89,
      "spread": 0.76,
      "density": 0.92
    }
  },
  "trace_timestamp": "2026-03-08T15:30:47Z"
}
```

---

## Success Criteria

### Registry & Versioning
1. Policy create/update/activate/rollback endpoints all idempotent and transaction-safe. No data loss on concurrent calls.
2. Every activated policy has immutable content + SHA-256 hash + timestamp + version label. Retained indefinitely.
3. Activation history preserves complete audit trail (who, when, reason, predecessor version).

### Replay Determinism
1. Audit mode: call `POST /admin/replay/audit` with trace_id; system reproduces original routing decision from frozen inputs. Response includes `status: success|partial_replay` and reasoning.
2. Regression mode: run `POST /admin/replay/batch` with mode=regression_sample over 50 random traces. All pass with deterministic reproduction (or explicit `partial_replay` if schema version too old).
3. Counterfactual mode (stretch): `POST /admin/replay/counterfactual` with candidate policy; returns route deltas vs. original for all traces.
4. Zero live Ollama calls during replay. Zero live DB retrieval queries during replay unless explicitly (diagnostic mode).

### Telemetry Coverage
1. Every `/rag` request produces a trace with all Phase 4 required fields non-null: policy_version, policy_hash, confidence_score, confidence_band, routing_action, execution_path, stage_flags, telemetry_schema_version.
2. Traces with different schema versions coexist and are queryable.
3. Missing optional fields do not crash replay or queries; treated as NULL/unknown.
4. Migration test: backfill logic handles old schema traces; `partial_replay` returned explicitly.

### Operational Verification
1. Reload endpoint (`POST /admin/policy/reload`) switches active policy; next request uses new policy. Previous in-flight requests continue on prior policy.
2. Rollback endpoint (`POST /admin/policy/rollback`) reverts to previous active version; next request uses rolled-back version. Not a schema/content change, just activation pointer flip.
3. Schema validation on policy activation (valid thresholds, complete routing map, etc.). Invalid policy rejected with `400 Bad Request` + reasons.
4. Concurrent activation attempts: second writer gets `409 Conflict`; activation history reflects only successful commit.

---

## Out of Scope (Deferred)

- **Cryptographic signing** of policies (unless cross-system provenance needed)
- **Graceful drain / staged cutover** (instant cutover sufficient for Phase 4)
- **Dry-run replay as hard activation gate** (schema/semantic validation sufficient; replay validation can be advisory or Phase 4.5 feature)
- **Multiple admins / role-based activation** (assume single-operator for Phase 4; RBAC is ops infrastructure feature)
- **Archival to cold storage** (retain indefinitely for now; export/archive process for later)
- **Full response regeneration validation** (answer may differ; routing must not)
- **Field-level schema migration** (versioning handles backward compatibility; no column-level migrations required for Phase 4)

---

## Data Integrity & Audit Requirements

### Activation Safety
- All activations are **logged** (who, when, reason, predecessor, success/failure).
- Rollback is **logged** (same audit trail as activation).
- No activation without validation (schema + semantic).
- Activation history is **queryable** — operators can audit "what was active at time T?"

### Trace Integrity
- Every trace has a `telemetry_schema_version` for version-aware queries.
- Traces are **immutable after creation** (no updates, only inserts).
- Traces reference immutable policy hash, not mutable version label (replay uses hash as primary key).
- Old traces remain **queryable and replayable** indefinitely.

### Replay Trustworthiness
- Replay uses frozen inputs from trace; no live system state is consulted.
- Replay explicitly states when it cannot reproduce a decision (`partial_replay` + reasons).
- Mismatches between replay and stored trace are **logged and surfaced** to operators.
- Strict-fail mode available for compliance/audit use.

---

## Recommended Implementation Approach

1. **Phase 4.1: Registry Hardening**
   - Implement policy table schema: (id, version, hash, content JSONB, created_at, deleted_at nullable)
   - Implement activation table: (id, policy_id, activated_at, activated_by, reason, deactivated_at nullable)
   - Add activation endpoints: `POST /admin/policy/create`, `POST /admin/policy/activate`, `POST /admin/policy/rollback`
   - Schema + semantic validation before activation
   - Transaction-serialized activation (critical for safety)

2. **Phase 4.2: Replay Harness**
   - Extract frozen retrieval/evidence inputs from existing Phase 3 traces
   - Implement `POST /admin/replay/audit` — reproduce routing from trace + frozen inputs
   - Implement `POST /admin/replay/batch` — regression mode over trace sample
   - Handle schema version branching and backfill logic
   - Test against all Phase 3 traces

3. **Phase 4.3: Telemetry Instrumentation**
   - Update trace schema to include `telemetry_schema_version`, stage_flags, policy_hash
   - Ensure all Phase 4 required fields are populated on every request
   - Add `partial_replay` field for backfill scenarios
   - Migrate existing Phase 3 traces to Phase 4 schema (backfill policy_hash from content, add schema version)

4. **Phase 4.4: Operational Verification**
   - Test reload/rollback semantics: verify in-flight vs. new-request behavior
   - Test activation validation: reject invalid policies, accept valid ones
   - Test concurrent activations: verify transaction serialization
   - End-to-end: modify policy → activate → verify next request uses it → replay old requests under old policy → confirm determinism

---

## Next Steps

**After Phase 4 Discussion**
- Move to **research phase** (4-RESEARCH.md) — map existing codebase against Phase 4 contract, identify gaps
- Then **planning phase** (4-PLAN.md) — detailed implementation plan with wave-based tasks
- Then **execution phase** — implement and verify

**Dependencies on Phase 5**
- Phase 5 will add `query_type`, `retrieval_state`, `evidence_shape` as required routing inputs
- These fields are already "accepted" in Phase 4 telemetry (not required yet)
- Phase 5 will promote them to required; Phase 4 ensures the infrastructure to do so cleanly

---

*Phase 4 context locked: 2026-03-08 | Ready for research and planning*
