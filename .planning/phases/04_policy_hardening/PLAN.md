# Phase 4 Implementation Plan: Policy Infrastructure Hardening

**Created:** 2026-03-08  
**Status:** Ready for Execution  
**Duration Estimate:** 8–10 days (1.5–2 weeks with testing and integration)  
**Execution Strategy:** Wave-based parallel tasks with integration checkpoints

---

## Phase 4 Planning Overview

### Phase Objective

Make the policy registry, replay harness, and telemetry pipeline production-reliable with:
- **Immutable policy versioning** protected by SHA-256 hashing
- **Deterministic audit & replay** of routing decisions from frozen inputs
- **Forward-compatible telemetry schema** that survives migrations and Phase 5 extensions
- **Operational safety** through transaction-serialized activation, activation history, and admin oversight

### What We're Building

**Three interconnected pillars:**

1. **Policy Versioning & Rollback** (`PLCY-01`)
   - Immutable policy content snapshots with cryptographic fingerprints
   - Atomic activation with transaction semantics (no race conditions)
   - Instant rollback to prior versions for operational recovery
   - Full audit trail (who activated when, why, from what prior state)

2. **Replay Determinism Boundary** (`PLCY-02`)
   - Frozen retrieval snapshots captured at request time (no live queries during replay)
   - Pure-function routing reconstruction using policy + frozen inputs
   - Three replay modes: `deterministic_audit` (single trace), `regression_sample` (batch CI), `counterfactual_policy` (stretch goal)
   - Explicit failure modes: `success`, `partial_replay`, `mismatch`, `policy_deleted`, `not_found`

3. **Telemetry & Schema Versioning** (`PLCY-03`)
   - All traces include `policy_hash` for deterministic identity
   - Explicit `telemetry_schema_version` field enables safe schema evolution
   - Core + extension fields: core is stable (Phase 4 locked), extensions grow Phase 5+
   - Backfill logic for old traces (no data loss on schema upgrades)

### Scope: Locked vs. Deferred

**Locked for Phase 4:**
- Policy hashing (SHA-256, canonical JSON)
- Activation history table + transactional semantics
- Frozen retrieval snapshots in traces
- Admin endpoints for policy management and replay audit
- Schema versioning (explicit version field + backfill)
- Replay determinism harness (audit + batch modes)
- CI regression test for replay reproducibility

**Deferred to Phase 5+ (documented, not implemented):**
- Counterfactual policy replay (high value, not critical for audit)
- Cryptographic signatures (X.509/RSA — only if regulated audit needed)
- Schema archival to cold storage (operational concern, not Phase 4)
- Policy distribution to edge caches (deployment concern, not Phase 4)

---

## Key Decisions from Context (4-CONTEXT.md)

### Area 1: Policy Versioning & Rollback

**Decision Tree:**
- **In-flight request binding:** Request captures `policy_snapshot_ref` at entry point; all I/O operations use that snapshot
  - **Why:** Ensures deterministic replay; avoids split-brain (retrieval under v1, routing under v2)
  - **Implementation:** FastAPI captures `policy_version` + `policy_hash` in request state
- **Rollback semantics:** Instant for new requests only; no staged drain
  - **Why:** Simple, safe, operationally clear; same semantics as reload
  - **Implementation:** Activate prior policy in DB; new requests use prior, in-flight requests complete on original
- **Version immutability:** Once activated (and used in production), policy is never modified
  - **Why:** Replay and audit lose credibility if old policies change; retention bias toward preservation
  - **Implementation:** `policy_registry` table is INSERT-only after activation; triggers prevent UPDATEs
- **Trace reference:** Store **both** `policy_version` (human-readable) **and** `policy_hash` (immutable fingerprint)
  - **Why:** Version helps operators; hash enables tamper-proof replay
  - **Implementation:** Traces include both fields; replay uses hash as primary identity

### Area 2: Replay Determinism Boundary

**Decision Tree:**
- **Replay scope:** Reconstruct routing decision (control plane) only; skip LLM/Ollama (data plane)
  - **Why:** Phase 4 audit question is "was policy applied correctly?" not "was entire response perfect?"
  - **Implementation:** Routing harness takes frozen inputs, returns `RouteDecision`
- **Retrieval input freezing:** Traces capture exact retrieval results (IDs, scores, rank order, parameters)
  - **Why:** Live query is not deterministic; embedding/index changes break audit
  - **Implementation:** `RetrievalSnapshot` dataclass stores immutable snapshot at request time
- **LLM in replay:** Skip entirely for deterministic core
  - **Why:** Not core audit concern; even with frozen model, regeneration is operationally fragile
  - **Implementation:** `DeterministicReplayer` only traces retrieval → routing → traces; skips generation
- **Replay failure modes:** Explicit status: `success` | `partial_replay` | `mismatch` | `policy_deleted` | `not_found`
  - **Why:** Clear signal for operations; enables version-aware backfill without silent failures
  - **Implementation:** Backfill logic handles old schema; returns partial status instead of failing

### Area 3: Telemetry & Schema Versioning

**Decision Tree:**
- **Schema versioning field:** Explicit `telemetry_schema_version` on every trace (e.g., "1.0", "2.0")
  - **Why:** Enables version-aware queries and replay; forward-compatible migrations
  - **Implementation:** Increment major on breaking changes (routing-required fields); minor on additions
- **Core vs. extension fields:** Stable top-level JSON + extensible JSONB
  - **Why:** Phase 4 fields are locked; Phase 5+ fields grow without breaking existing logic
  - **Implementation:** Traces have fixed `core` columns + `extensions` JSONB for future
- **Backward compatibility:** Backfill derivable fields for old traces
  - **Why:** No data loss; no forced schema backfill runs; gradual migration
  - **Implementation:** Read-side backfill: `confidence_band` → `retrieval_state`, `execution_path` → `stage_flags`

---

## Implementation Waves

### Wave 1: Database Schema Foundation (Non-Blocking, 1–2 days)

**Goal:** Extend schema with Phase 4 requirements (policy hashing, activation history, frozen inputs). No code changes yet; preparation for Waves 2–4.

#### Task 1.1: Add Policy Hash & Content Immutability

**Title:** Add `policy_hash` column and immutability constraints to `policy_registry`

**Goal:** Enable immutable policy content verification via SHA-256 fingerprints.

**Acceptance Criteria:**
- ✅ Migration `007_phase4_hardening.sql` created with `ALTER TABLE policy_registry ADD COLUMN policy_hash`
- ✅ Existing policies backfilled with hashes (deterministically computed from content)
- ✅ Unique constraint `UNIQUE (policy_hash)` prevents duplicate content
- ✅ `policy_hash` column is non-nullable after backfill; migration is idempotent
- ✅ Query `SELECT COUNT(*) FROM intelligence.policy_registry WHERE policy_hash LIKE 'sha256:%'` shows all rows hashed

**Dependencies:** None

**Estimated Effort:** XS (2–4 hours)

**Code/Test References:**
- Create: `migrations/007_phase4_hardening.sql`
- Test: Manual SQL verification in test DB, confirm existing policies hashed
- Patterns: See `migrations/005_add_policy_optimization.sql`, `migrations/006_contextual_policy_routing.sql`

**Rationale:** 
Policy hashing is foundational for replay determinism (allows verifying policies haven't been tampered with). Must complete before replay harness can work. Non-blocking design (schema-only, no code dependencies) enables Waves 2–4 to proceed in parallel.

#### Task 1.2: Create Policy Activation History Table

**Title:** Create `policy_activations` table for audit trail

**Goal:** Track every policy activation with timestamp, actor, reason, and prior policy context.

**Acceptance Criteria:**
- ✅ Migration creates `intelligence.policy_activations(activation_id, policy_version, activated_at, activated_by, reason, deactivated_at, prior_policy_version)`
- ✅ Columns: `policy_version` FK to `policy_registry.version`, `activated_by` (text: "admin"/"calibration"/"ci"), `deactivated_at` (nullable, NULL while active)
- ✅ Indexes created: `(policy_version)` and `(activated_at DESC)` for audit queries
- ✅ Not NULL constraints on `policy_version, activated_at, activated_by`
- ✅ Query `SELECT * FROM intelligence.policy_activations ORDER BY activated_at DESC LIMIT 10` returns readable audit trail

**Dependencies:** Task 1.1 (needs policy_registry stable)

**Estimated Effort:** XS (2–3 hours)

**Code/Test References:**
- Create: `migrations/007_phase4_hardening.sql` (add to same migration as Task 1.1)
- Test: Manual SQL: insert activation record, verify deactivated_at updates on subsequent activation
- Pattern: Append-only table pattern; similar to feed_entries in RSS support

**Rationale:**
Activation history is the operational ledger for rollback decisions. Append-only design ensures no accidents; recording `deactivated_at` provides complete lifecycle visibility.

#### Task 1.3: Add Telemetry Schema Version & Policy Hash to Traces

**Title:** Extend `policy_telemetry` table with `telemetry_schema_version` and `policy_hash` columns

**Goal:** Enable forward-compatible telemetry schema evolution and deterministic replay identity.

**Acceptance Criteria:**
- ✅ Migration adds `policy_hash TEXT` and `telemetry_schema_version TEXT DEFAULT '1.0'` columns
- ✅ No backfill required for `policy_hash` (NULL for old traces is acceptable; replay harness handles `partial_replay` status)
- ✅ Index `(telemetry_schema_version)` for version-aware queries
- ✅ Query `SELECT DISTINCT telemetry_schema_version FROM intelligence.policy_telemetry` distinguishes old vs. new schema
- ✅ Old traces (before Phase 4) readable without error

**Dependencies:** Task 1.1 (needs policy_registry with hashes)

**Estimated Effort:** XS (1–2 hours)

**Code/Test References:**
- Update: `migrations/007_phase4_hardening.sql`
- Test: Manual query old traces; verify schema version is visible
- Pattern: Same as `content_hash` column addition in migration `001_add_content_hash.sql`

**Rationale:**
Schema versioning is the key to forward compatibility. By marking every trace with its schema version, read-side logic can branch and backfill old schema intelligently. Prevents future migrations from breaking old traces.

#### Task 1.4: Create Frozen Retrieval Items Storage

**Title:** Add `retrieval_items` JSONB column to `policy_telemetry` for frozen input snapshots

**Goal:** Store immutable retrieval results (item IDs, scores, rank order) for deterministic replay.

**Acceptance Criteria:**
- ✅ Migration adds `retrieval_items JSONB DEFAULT '{}'::jsonb` column
- ✅ Column nullable/empty for old traces (not backfilled)
- ✅ Index `USING gin (retrieval_items)` for potential search
- ✅ Sample trace structure validates: `retrieval_items` contains array of `{item_id, rank, score, source_doc}`
- ✅ Query `SELECT COUNT(*) FROM intelligence.policy_telemetry WHERE retrieval_items != '{}' ORDER BY created_at DESC LIMIT 10` shows recent traces with items

**Dependencies:** Task 1.3 (telemetry table must exist)

**Estimated Effort:** XS (1–2 hours)

**Code/Test References:**
- Update: `migrations/007_phase4_hardening.sql`
- Test: Manual JSON validation; sample retrieval items structure
- Pattern: Same as `metadata` JSONB column usage; reference `shared/telemetry.py` for trace structure

**Rationale:**
Frozen retrieval items are the primary determinism input. Storing as JSONB allows:
- Schema-free evolution (add fields without schema changes)
- Efficient queries (GIN index for filtering by item type)
- No changes to retrieval pipeline (frozen at request time, logged to trace)

---

### Wave 2: Policy Versioning & Registry Hardening (2–3 days)

**Goal:** Implement policy hashing, versioning, transactional activation, and rollback semantics. Enables safe policy updates in production.

**Blockers:** Wave 1 (schema must exist first)

#### Task 2.1: Implement Policy Hashing Function

**Title:** Add `compute_policy_hash()` function to `shared/policy.py`

**Goal:** Compute immutable SHA-256 fingerprints of policy content for integrity verification.

**Acceptance Criteria:**
- ✅ Function `compute_policy_hash(policy_content: Dict[str, Any]) -> str` exists in `shared/policy.py`
- ✅ Uses `hashlib.sha256()` on canonical JSON (sorted keys, tight formatting): `json.dumps(..., sort_keys=True, separators=(',', ':'))`
- ✅ Returns format `"sha256:<hexdigest>"` (e.g., `"sha256:abc123..."`)
- ✅ Same content always produces same hash; different content produces different hash
- ✅ Test: `compute_policy_hash({"a": 1}) == compute_policy_hash({"a": 1})` ✓
- ✅ Test: `compute_policy_hash({"a": 1, "b": 2}) != compute_policy_hash({"a": 1, "b": 3})` ✓

**Dependencies:** None

**Estimated Effort:** XS (1 hour)

**Code/Test References:**
- Modify: `shared/policy.py`
- Test: Add unit tests to `tests/test_policy_routing.py` (hash determinism, format validation)
- Pattern: Reuse existing pattern from `shared/processor.py` duplicate detection hashing

**Rationale:**
Policy hashing is the cryptographic foundation for immutability. Low complexity, high impact; unblocks all replay logic.

#### Task 2.2: Extend PolicyRepository with Policy Versioning Methods

**Title:** Add policy creation, activation, rollback, and immutability enforcement to `PolicyRepository`

**Goal:** Provide transactional policy management with audit trail and rollback support.

**Acceptance Criteria:**
- ✅ Method `create_policy(version: str, content: Dict, created_by: str = "system") -> Tuple[bool, str]` exists
  - Validates schema before insert
  - Computes and stores policy_hash
  - Returns `(True, policy_hash)` on success or `(False, error_reason)` on failure
  - Prevents duplicate version IDs with `ON CONFLICT DO NOTHING`
- ✅ Method `activate_policy(version: str, activated_by: str, reason: str) -> Tuple[bool, str]` exists
  - Uses `BEGIN ISOLATION LEVEL SERIALIZABLE` for atomic activation
  - Deactivates current policy, activates target, records history in single transaction
  - Returns `409 Conflict` error if concurrent activation detected
  - Updates `policy_activations` with activation + prior policy context
- ✅ Method `rollback_to_previous(activated_by: str) -> Tuple[bool, str]` exists
  - Looks up immediate prior policy from `policy_activations` history
  - Calls `activate_policy()` with reason="Rollback"
- ✅ Method `get_activation_history(limit: int = 10) -> List[Dict]` exists
  - Returns last N activations: `{policy_version, activated_at, activated_by, reason, deactivated_at, prior_policy_version}`
- ✅ All methods log operations with context (version, actor, timestamp)

**Dependencies:** Task 1.1–1.2 (schema must exist)

**Estimated Effort:** M (6–8 hours)

**Code/Test References:**
- Modify: `shared/database.py`, class `PolicyRepository`
- Create: `tests/test_policy_versioning.py` (new test file)
  - Test policy creation with hash computation
  - Test transactional activation (verify `BEGIN SERIALIZABLE`)
  - Test concurrent activation conflict detection
  - Test rollback to prior version
  - Test activation history audit trail
- Pattern: Extend existing `set_active_policy()` method; reference `create_policy()` and `set_active_policy()` in research

**Rationale:**
PolicyRepository is the central control plane for policy management. Implementing transactional activation ensures no race conditions in production. Rollback support provides operational safety.

#### Task 2.3: Implement Policy Schema Validation

**Title:** Add `validate_policy_schema()` function to `shared/policy.py` or `shared/database.py`

**Goal:** Catch obviously-broken policies before they become active.

**Acceptance Criteria:**
- ✅ Function `validate_policy_schema(content: Dict) -> List[str]` exists
- ✅ Checks: required sections (`thresholds`, `routing_rules`), threshold ranges (0–1), routing map completeness
- ✅ Returns list of validation errors; empty list = valid
- ✅ Called in `create_policy()` before insert; policy rejected if errors present
- ✅ Test: `validate_policy_schema({"thresholds": {"high": 1.5}})` returns error (out of range)
- ✅ Test: `validate_policy_schema({"thresholds": {...}, "routing_rules": {...}})` returns `[]` (valid)

**Dependencies:** Task 2.1 (needs policy function infrastructure)

**Estimated Effort:** S (3–4 hours)

**Code/Test References:**
- Create: `shared/policy.py` function or reference existing schema in `shared/policy.py::RAGPolicy`
- Test: `tests/test_policy_routing.py` or new `tests/test_policy_validation.py`
- Pattern: Similar to event/payload validation patterns in FastAPI

**Rationale:**
Schema validation prevents silent failures where bad policies activate. Catches errors early; reduces production incidents.

#### Task 2.4: Add Policy Management Admin Endpoints

**Title:** Create `/admin/policy/*` endpoints for policy creation, activation, rollback, history

**Goal:** Provide operational interface for policy management with audit trail.

**Acceptance Criteria:**
- ✅ Endpoint `POST /admin/policy/create` accepts `{version, content}`, requires API key
  - Calls `policy_repo.create_policy()`
  - Returns `{success, policy_hash, message}` or error
- ✅ Endpoint `POST /admin/policy/activate` accepts `{version, reason}`, requires API key
  - Calls `policy_repo.activate_policy()`
  - Returns `{success, message}` or `409 Conflict` if concurrent activation
- ✅ Endpoint `POST /admin/policy/rollback` requires API key
  - Calls `policy_repo.rollback_to_previous()`
  - Returns `{success, message}` or error if no prior policy
- ✅ Endpoint `GET /admin/policy/history` accepts `limit` (default 10), returns JSON list
  - Returns activation history: `[{policy_version, activated_at, activated_by, reason, prior_policy_version}, ...]`
- ✅ Endpoint `GET /admin/policy/list` returns all policies with versions, hashes, timestamps
- ✅ All endpoints require `X-API-Key` header; return `401 Unauthorized` if missing or wrong

**Dependencies:** Task 2.2 (PolicyRepository methods must exist), Task 2.3 (validation required)

**Estimated Effort:** M (6–8 hours)

**Code/Test References:**
- Modify: `api/app.py`
- Test: `tests/test_policy_routing.py` or new `tests/test_policy_admin.py`
  - Mock policy_repo, test endpoint auth, test response structures
  - Reference `test_control_loop.py` for async/mock patterns
- Pattern: Follow existing admin endpoint pattern in `api/app.py` (e.g., `/admin/models/check`)

**Rationale:**
Admin endpoints expose policy management to operators. Audit trail (history endpoint) is key for operational confidence in rollback decisions.

---

### Wave 3: Telemetry Instrumentation & Schema Evolution (2–3 days)

**Goal:** Instrument policy lifecycle events into traces; implement schema versioning + backfill logic for forward compatibility.

**Blockers:** Wave 1 (schema must exist), Wave 2.1 (hashing needed for traces)

#### Task 3.1: Extend PolicyTrace with Phase 4 Fields

**Title:** Update `PolicyTrace` dataclass in `shared/telemetry.py` with new required fields

**Goal:** Capture policy hash, schema version, and frozen retrieval snapshot in every trace.

**Acceptance Criteria:**
- ✅ `PolicyTrace` class updated in `shared/telemetry.py`
- ✅ New fields:
  - `policy_hash: str = "unknown"` — SHA-256 hash of active policy
  - `telemetry_schema_version: str = "1.0"` — Schema version for migration
  - `retrieval_items: Optional[List[Dict]] = None` — Frozen retrieval results
  - `retrieval_parameters: Optional[Dict] = None` — Query parameters (limit, threshold, mode)
- ✅ `to_dict()` method includes all new fields
- ✅ Backward compatibility: old traces (missing fields) readable without error
- ✅ Test: Create trace, call `to_dict()`, verify all fields present in output

**Dependencies:** Task 1.4 (schema must exist), Task 2.1 (hashing)

**Estimated Effort:** S (2–3 hours)

**Code/Test References:**
- Modify: `shared/telemetry.py` class `PolicyTrace`
- Test: `tests/test_policy_routing.py` or extend existing telemetry tests
- Pattern: Reference dataclass pattern; similar to adding fields in `shared/evidence_scorer.py`

**Rationale:**
Extending PolicyTrace is low-risk foundation work that unblocks replay harness (Wave 4). Dataclass changes are additive; no breaking changes to existing code.

#### Task 3.2: Implement Frozen Retrieval Snapshot Capture

**Title:** Capture frozen retrieval snapshot at request time in `api/app.py` (endpoint where retrieval occurs)

**Goal:** Store immutable retrieval results in trace for deterministic replay.

**Acceptance Criteria:**
- ✅ At retrieval time (after `hybrid_retriever.retrieve()`), create snapshot:
  ```python
  retrieval_snapshot = {
      "items": [
          {
              "item_id": item["id"],
              "rank": idx + 1,
              "score": item.get("similarity_score"),
              "source_doc": item.get("document_id"),
              "chunk_index": item.get("chunk_index")
          }
          for idx, item in enumerate(retrieved_items)
      ],
      "parameters": {
          "limit": retrieval_params["limit"],
          "threshold": retrieval_params.get("similarity_threshold")
      },
      "total_candidates": len(retrieved_items)
  }
  ```
- ✅ Add snapshot to trace before logging
- ✅ Test: Retrieve articles, inspect trace in telemetry table; `retrieval_items` populated with correct structure
- ✅ Verify: Rank order matches original retrieval order

**Dependencies:** Task 3.1 (PolicyTrace schema must include retrieval_items)

**Estimated Effort:** S (3–4 hours)

**Code/Test References:**
- Modify: `api/app.py` (search for `hybrid_retriever.retrieve()` calls, add snapshot after)
- Test: `tests/test_retrieval_state.py` or create new `tests/test_frozen_retrieval.py`
  - Mock retrieval, capture snapshot, verify structure in test
- Pattern: Similar to evidence scoring capture pattern (`api/routing.py`)

**Rationale:**
Frozen retrieval is the determinism input. Capturing at request time (not replay time) ensures we freeze the exact state the original routing saw.

#### Task 3.3: Populate policy_hash and Schema Version in Trace Logging

**Title:** Update `log_telemetry()` in `PolicyRepository` to include policy_hash and schema_version

**Goal:** Ensure every new trace includes policy hash for replay identity.

**Acceptance Criteria:**
- ✅ Method `log_telemetry()` in `PolicyRepository` updated
- ✅ Reads active policy from database (or passed via argument)
- ✅ Inserts `policy_hash` (from active policy) and `telemetry_schema_version='1.0'` into DB
- ✅ Old code path (no policy_hash available): accepts `policy_hash=None`, stores `NULL` in DB
- ✅ Test: Log telemetry, query DB, verify `policy_hash` and `telemetry_schema_version` populated

**Dependencies:** Task 1.3 (schema must have columns), Task 2.1 (hashing)

**Estimated Effort:** S (2–3 hours)

**Code/Test References:**
- Modify: `shared/database.py`, `PolicyRepository.log_telemetry()`
- Test: `tests/test_policy_routing.py`
  - Mock policy_repo, call log_telemetry, verify insert
- Pattern: Similar to existing `log_telemetry()` insert logic

**Rationale:**
Populating policy_hash at insert time is atomic and safe. Simplifies replay logic (all traces from Phase 4 onward are guaranteed to have policy hash).

#### Task 3.4: Implement Telemetry Backfill Logic

**Title:** Create `backfill_trace_fields()` function in `shared/telemetry.py`

**Goal:** Enable backward-compatible reads of old traces by deriving missing Phase 4 fields.

**Acceptance Criteria:**
- ✅ Function `backfill_trace_fields(trace: Dict, source_version: str = "0.9") -> Dict` exists
- ✅ Backfill logic:
  - Derive `retrieval_state` from `confidence_band` (high→SOLID, medium→MIXED, low→WEAK)
  - Derive `stage_flags` from `execution_path` (cautious→{retrieval_expanded, reranker_invoked})
  - Set `telemetry_schema_version` if missing
  - Leave `policy_hash` as `None` if not in trace (replay harness handles `partial_replay` status)
- ✅ Test: Load old trace from DB (before Phase 4), call backfill, verify all fields present
- ✅ Test: New trace (after Phase 4) backfilled idempotently (no change)

**Dependencies:** Task 3.1 (PolicyTrace schema must exist)

**Estimated Effort:** S (3–4 hours)

**Code/Test References:**
- Create: `shared/telemetry.py` function
- Test: `tests/test_policy_routing.py` or new `tests/test_telemetry_backfill.py`
  - Create old trace structure, backfill, verify fields populated
- Pattern: Defensive read logic; similar to optional field handling in existing code

**Rationale:**
Backfill logic ensures no data loss on schema upgrades. Old traces remain readable and replayable even after Phase 4 deployment.

---

### Wave 4: Replay Harness Implementation (3–4 days)

**Goal:** Build deterministic routing reconstruction from frozen inputs. Core Phase 4 feature enabling audit & regression testing.

**Blockers:** Wave 2 (policy hashing), Wave 3 (telemetry instrumentation)

#### Task 4.1: Implement Replay Determinism Harness

**Title:** Create `DeterministicReplayer` class in new file `shared/replay.py`

**Goal:** Reconstruct routing decisions from frozen trace inputs using stored policy.

**Acceptance Criteria:**
- ✅ Class `DeterministicReplayer` created in `shared/replay.py`
- ✅ Constructor: `__init__(self, policy_repo: PolicyRepository, router: ContextualRouter)`
- ✅ Method `replay_audit(self, trace_id: str) -> Dict[str, Any]` exists:
  - Load trace from DB by ID
  - Backfill old schema fields
  - Reconstruct policy from policy_hash
  - Verify hash integrity (tamper check)
  - Check frozen retrieval items present
  - Reconstruct routing context from frozen inputs (pure data, no I/O)
  - Run routing (stateless, pure function)
  - Compare original vs. reconstructed decision
  - Return: `{"status": "success|partial_replay|mismatch|...", "original_decision": {...}, "reconstructed_decision": {...}, "reason": "..."}`
- ✅ Method `replay_batch(self, limit: int = 50) -> Dict[str, Any]` exists:
  - Load recent traces (sample)
  - Run `replay_audit()` on each
  - Aggregate results: `{"total_replayed": N, "passed": P, "failed": F, "partial": PA, "failures": [...]}`
- ✅ Handles all replay failure modes: `success`, `partial_replay`, `mismatch`, `policy_deleted`, `not_found`, `error`
- ✅ Test: Single trace audit (success case), trace with missing hash (partial_replay), trace with wrong hash (policy_deleted)

**Dependencies:** Wave 2.1 (hashing), Wave 3 (telemetry fields)

**Estimated Effort:** L (10–12 hours)

**Code/Test References:**
- Create: `shared/replay.py` (new file)
- Test: `tests/test_replay_determinism.py` (new test file)
  - Mock DB traces with known inputs
  - Test audit success case
  - Test partial_replay (missing fields)
  - Test mismatch (routing divergence)
  - Test policy_deleted (hash not found)
  - Reference mock patterns from `test_control_loop.py`
- Pattern: Refer to 4-RESEARCH.md code example `replay_audit_deterministic()`

**Rationale:**
Replay harness is the core Phase 4 audit engine. Enables operator confidence in policy changes (can always audit why a routing decision was made).

#### Task 4.2: Add `/admin/replay/audit` Endpoint

**Title:** Create POST endpoint for single-trace deterministic audit

**Goal:** Enable operator to audit a single trace: "Was this request routed correctly?"

**Acceptance Criteria:**
- ✅ Endpoint `POST /admin/replay/audit?trace_id=<id>` exists
- ✅ Requires API key (`X-API-Key` header)
- ✅ Calls `replayer.replay_audit(trace_id)`
- ✅ Returns JSON: `{status, original_decision, reconstructed_decision, reason, trace_timestamp}`
- ✅ Status codes: `200 OK` (replay succeeded); `404 Not Found` (no trace); `500` (internal error)
- ✅ Test: Audit a trace, verify response structure

**Dependencies:** Task 4.1 (replayer class must exist)

**Estimated Effort:** S (2–3 hours)

**Code/Test References:**
- Modify: `api/app.py`
- Test: `tests/test_replay_audit.py` or extend `tests/test_policy_admin.py`
  - Mock replayer, test endpoint
  - Verify auth (no API key = 401)
  - Verify response structure
- Pattern: Follow existing admin endpoint pattern

**Rationale:**
Audit endpoint is the operational interface to the replayer. Enables ad-hoc investigation of individual requests.

#### Task 4.3: Add `/admin/replay/batch` Endpoint for CI Regression Test

**Title:** Create POST endpoint for batch replay regression testing

**Goal:** Enable CI to verify replay harness reproducibility after code changes.

**Acceptance Criteria:**
- ✅ Endpoint `POST /admin/replay/batch?limit=<N>` exists (default limit=50)
- ✅ Requires API key
- ✅ Calls `replayer.replay_batch(limit=limit)`
- ✅ Returns JSON: `{mode, total_replayed, passed, failed, partial, failures: [{trace_id, reason}, ...]}`
- ✅ Status: `200 OK` if all passed or partial; `400 Bad Request` if any failed (CI should fail on bad)
- ✅ Test: Batch replay 10 traces, verify counts and failure list

**Dependencies:** Task 4.1 (replayer class)

**Estimated Effort:** S (2–3 hours)

**Code/Test References:**
- Modify: `api/app.py`
- Test: `tests/test_replay_batch.py` or extension of `tests/test_replay_audit.py`
  - Mock replayer with mixed results (pass, fail, partial)
  - Test aggregation logic
  - Verify `400` returned if failures present
- Pattern: Reference `/admin/replay/batch` design in 4-RESEARCH.md

**Rationale:**
Batch endpoint enables CI regression testing. Ensures replay harness doesn't break after code/schema changes.

#### Task 4.4: Create CI Regression Test Hook

**Title:** Add CI test step to run batch replay; fail CI if mismatches detected

**Goal:** Catch replay regressions in CI before merge.

**Acceptance Criteria:**
- ✅ Script `scripts/test_replay_ci.py` (or addition to existing CI step) created
- ✅ Script calls `POST /admin/replay/batch` with limit=50
- ✅ Parses response JSON; fails if `failed > 0`
- ✅ Logs results: `Replay CI: <passed>/<total> passed`
- ✅ Integrates into CI pipeline (Makefile or GitHub Actions)
- ✅ Test: Run locally, verify script calls endpoint and interprets response

**Dependencies:** Task 4.3 (batch endpoint must exist)

**Estimated Effort:** S (2–3 hours)

**Code/Test References:**
- Create: `scripts/test_replay_ci.py` or extend `scripts/smoke_test.sh`
- Update: `Makefile` (add `make test-replay` target)
- Test: Manual run against live API

**Rationale:**
CI regression test is the insurance policy for replay determinism. Prevents subtle replay breakage from shipping.

---

### Wave 5: Admin Operational Safety & Verification (1–2 days)

**Goal:** Add endpoints for operational oversight (policy status, schema health checks). Enables safe Phase 4 operations.

**Blockers:** Wave 2 (versioning), Wave 4 (replay harness)

#### Task 5.1: Add `/admin/policy/status` Endpoint

**Title:** Create GET endpoint for current policy status and schema health

**Goal:** Enable operators to verify policy state and telemetry health.

**Acceptance Criteria:**
- ✅ Endpoint `GET /admin/policy/status` returns JSON:
  ```json
  {
    "active_policy_version": "2026-03-08T15:30:01Z",
    "active_policy_hash": "sha256:abc123...",
    "policy_count": 42,
    "recent_telemetry_count": 1000,
    "recent_traces_with_policy_hash": 980,
    "recent_traces_with_schema_version": 980,
    "trace_schema_versions": {"1.0": 980, "0.9": 20},
    "last_activation": "2026-03-08T15:30:05Z",
    "activation_history_count": 15
  }
  ```
- ✅ Accepts query params: `telemetry_lookback_hours` (default 24) for recent trace stats
- ✅ No auth required (read-only, operational dashboard)
- ✅ Test: Query endpoint, verify response structure and counts increase

**Dependencies:** Wave 2 (policy management), Wave 3 (telemetry)

**Estimated Effort:** M (4–6 hours)

**Code/Test References:**
- Add: `api/app.py` endpoint
- Add: Method to `PolicyRepository` to compute stats
- Test: `tests/test_policy_status.py` or extension
  - Mock DB queries, verify response format

**Rationale:**
Status endpoint provides operational visibility. Enables monitoring of migration progress during Phase 4 rollout; confirms all telemetry flowing correctly.

#### Task 5.2: Add Telemetry Validation Checks

**Title:** Create function to validate telemetry schema health (all required fields present)

**Goal:** Verify no traces are missing Phase 4 required fields (early detection of bugs).

**Acceptance Criteria:**
- ✅ Function `validate_telemetry_health(trace: Dict) -> Tuple[bool, List[str]]` exists in `shared/telemetry.py`
- ✅ Checks:
  - `request_id` present (required)
  - `query` present (required)
  - `confidence_score` is number (required)
  - `confidence_band` in [high, medium, low, insufficient] (required)
  - `routing_action` present (required)
  - `execution_path` present (required)
  - `policy_version` present (for audit)
  - `retrieval_items` present if Phase 4 trace, acceptable as NULL for old traces
- ✅ Returns `(pass: bool, errors: List[str])` where errors list is empty if valid
- ✅ Test: Valid trace passes; invalid trace fails with specific error messages

**Dependencies:** Wave 3 (telemetry schema)

**Estimated Effort:** S (2–3 hours)

**Code/Test References:**
- Create: `shared/telemetry.py` function
- Test: `tests/test_telemetry_validation.py` or extend existing tests
  - Create valid and invalid traces, verify validation

**Rationale:**
Telemetry validation catches data quality issues early. Prevents silent failures in telemetry pipeline.

---

### Wave 6: Integration Testing & Phase 4 Verification (3–4 days)

**Goal:** End-to-end testing of all Phase 4 features; verify PLCY-01, PLCY-02, PLCY-03 requirements are met.

**Blockers:** All prior waves (must complete before integration testing)

#### Task 6.1: Integration Test Suite for Policy Versioning

**Title:** Create comprehensive test suite for policy lifecycle (create → validate → activate → audit → rollback)

**Goal:** Verify policy versioning works correctly end-to-end.

**Acceptance Criteria:**
- ✅ Test file `tests/test_policy_versioning_e2e.py` created
- ✅ Tests cover:
  - Create policy with schema validation
  - Activate policy (verify in DB and request.state)
  - Verify in-flight requests use snapshot
  - Activate second policy (verify prior still in history)
  - Rollback to prior (verify activation history updated)
  - Query activation history (verify audit trail)
  - Hash verification (reproduced hash matches stored)
  - Concurrent activation conflict (second request gets error)
- ✅ All tests pass; no flakes
- ✅ CI integration: test runs on every commit

**Dependencies:** Wave 2, Wave 5

**Estimated Effort:** L (8–10 hours)

**Code/Test References:**
- Create: `tests/test_policy_versioning_e2e.py`
- Pattern: Follow existing async test patterns from `test_control_loop.py`, `test_async_ingestion.py`
- Mock: DB, FastAPI request context
- Fixtures: Sample policies, DB connection

**Rationale:**
E2E tests catch integration issues between schema, PolicyRepository, and API. Essential to verify PLCY-01 complete.

#### Task 6.2: Integration Test Suite for Replay Determinism

**Title:** Create test suite for deterministic routing reconstruction

**Goal:** Verify replay harness correctly reconstructs decisions from frozen inputs.

**Acceptance Criteria:**
- ✅ Test file `tests/test_replay_determinism_e2e.py` created
- ✅ Tests cover:
  - Replay audit (single trace, success case)
  - Replay audit (trace with policy deleted, partial_replay status)
  - Replay audit (divergent routing, mismatch status)
  - Replay batch (10 traces, aggregate results)
  - Replay failure mode handling (explicit status, not exceptions)
  - Frozen retrieval prevents divergence (same ranking produces same routing)
- ✅ All tests pass; no flakes
- ✅ CI integration: test runs on every commit

**Dependencies:** Wave 4, Wave 6.1

**Estimated Effort:** L (8–10 hours)

**Code/Test References:**
- Create: `tests/test_replay_determinism_e2e.py`
- Pattern: Mock policy_repo, router, generate sample traces with frozen inputs
- Fixtures: Sample traces (success, partial, mismatch scenarios)

**Rationale:**
Replay E2E tests verify the core Phase 4 auditing capability. Ensures determinism actually works (not just theoretically sound).

#### Task 6.3: Telemetry Schema Migration Test

**Title:** Test backward compatibility: Phase 1-3 traces queryable after Phase 4 deployment

**Goal:** Verify no data loss on schema upgrades; old traces remain readable.

**Acceptance Criteria:**
- ✅ Test scenario: Load pre-Phase4 trace from fixture
- ✅ Backfill fields using `backfill_trace_fields()`
- ✅ Verify all required fields present after backfill
- ✅ Replay audit on backfilled trace returns `partial_replay` (not error)
- ✅ Query old traces by schema version: `SELECT COUNT(*) FROM policy_telemetry WHERE telemetry_schema_version != '1.0'` shows pre-Phase4 traces
- ✅ Test passes; no data loss

**Dependencies:** Wave 3, Wave 6.1–6.2

**Estimated Effort:** S (4–6 hours)

**Code/Test References:**
- Create: `tests/test_schema_migration_e2e.py` or extend `tests/test_replay_determinism_e2e.py`
- Fixtures: Pre-Phase4 trace JSON structure (from Phase 1-3 schema)
- Test logic: Load fixture, backfill, verify, query

**Rationale:**
Schema compatibility test ensures zero downtime on Phase 4 rollout. Operations can deploy with confidence that old data won't break.

#### Task 6.4: Operational Scenarios & Runbooks

**Title:** Document and test operational scenarios (policy hotfix, rollback, incident response)

**Goal:** Verify Phase 4 enables safe operational procedures.

**Acceptance Criteria:**
- ✅ Scenario 1: Emergency hotfix
  - Create new policy (v2)
  - Activate v2 (verify in prod)
  - Verify in-flight requests complete on v1 (snapshot)
  - Test passes: no partial responses
- ✅ Scenario 2: Rollback after bad policy
  - Activate bad policy (v3)
  - Detect issue (via `/admin/policy/status` or monitoring)
  - Rollback to v2 (verify history)
  - Test passes: rollback completes in < 1s
- ✅ Scenario 3: Audit why a request routed incorrectly
  - Use `/admin/replay/audit` to reconstruct decision
  - Compare original vs. reconstructed
  - Replay identifies root cause (policy changed, or code bug)
  - Test passes: audit result is actionable
- ✅ Runbook created: `docs/OPERATIONAL_RUNBOOK_PHASE4.md` with step-by-step procedures

**Dependencies:** Wave 4, Wave 5

**Estimated Effort:** M (6–8 hours, including doc writing)

**Code/Test References:**
- Create: `tests/test_operational_scenarios.py`
  - Mock scenarios as test cases
- Create: `docs/OPERATIONAL_RUNBOOK_PHASE4.md`
  - Step-by-step hotfix, rollback, audit procedures
  - Decision tree: when to rollback vs. push hotfix
- Manual testing: Walk through scenarios in staging/test DB

**Rationale:**
Operational scenarios test the "happy path" for production use. Runbook ensures operations team has clear procedures on day 1.

#### Task 6.5: Phase 4 Success Criteria Verification

**Title:** Systematic verification against PLCY-01, PLCY-02, PLCY-03 requirements

**Goal:** Confirm all Phase 4 objectives met before marking complete.

**Acceptance Criteria:**

**PLCY-01: Policy Registry Versioned, Queryable, No Data Loss**
- ✅ Policy versioning: SHA-256 hashes on all policies
- ✅ Immutability: policies cannot be updated after creation (schema constraints + app code)
- ✅ Queryability: list policies by version, hash, timestamps
- ✅ No data loss: all prior policies retained (not deleted)
- ✅ Rollback semantics: instant for new requests, safe for in-flight requests
- ✅ Audit trail: activation history table with {policy_version, activated_at, activated_by, reason, prior_version}

**PLCY-02: Replay Harness Recreates Routing Deterministically**
- ✅ Frozen retrieval inputs: traces store exact retrieval results (IDs, scores, rank order)
- ✅ Deterministic routing: router is pure function (no side effects, deterministic)
- ✅ Replay reconstruction: `/admin/replay/audit` reproduces original routing given frozen inputs
- ✅ Explicit failure modes: success|partial_replay|mismatch|policy_deleted|not_found (no silent failures)
- ✅ Batch regression test: `/admin/replay/batch` for CI verification
- ✅ LLM skipped: replay focuses on control plane (routing), not data plane (generation)

**PLCY-03: Telemetry Captures All Routing Decisions with Full Context**
- ✅ Required fields present: request_id, query, policy_version, policy_hash, confidence_score, confidence_band, routing_action, execution_path, retrieval_items, trace_timestamp
- ✅ Schema versioning: explicit telemetry_schema_version on every trace
- ✅ Backfill for old traces: derivable fields reconstructed (retrieval_state from confidence_band, stage_flags from execution_path)
- ✅ Forward compatibility: telemetry_schema_version enables Phase 5+ fields without breaking existing traces
- ✅ No data loss: pre-Phase4 traces readable and replayable

**Verification:**
- ✅ Run all E2E test suites; all pass
- ✅ Run CI regression test; all pass
- ✅ Query telemetry: `SELECT COUNT(DISTINCT policy_hash) FROM policy_telemetry` > 0 (policies tracked)
- ✅ Query activation history: `SELECT * FROM policy_activations ORDER BY activated_at DESC LIMIT 5` shows audit trail
- ✅ Replay sample trace: `POST /admin/replay/audit?trace_id=...` returns success and reproducible routing
- ✅ Status endpoint shows health: `GET /admin/policy/status` shows no missing required fields in recent traces

**Dependencies:** All prior waves

**Estimated Effort:** S (4–6 hours)

**Code/Test References:**
- Create: `tests/test_phase4_verification.py` (comprehensive checklist)
  - Functional tests for each PLCY-0X requirement
  - Queries and assertions for each criterion

**Rationale:**
Final verification ensures Phase 4 actually delivered on requirements. Gating task before archiving phase.

---

## Critical Path Analysis

### Must Complete Before Execution Starts
1. **Wave 1** (schema): All 4 tasks — no code work possible without schema
2. **Wave 2.1** (policy hashing): Foundational for all replay logic
3. **Wave 2.2** (PolicyRepository methods): Foundation for admin endpoints

### Blocking Dependencies (Sequential)
```
Wave 1 (schema) 
    ↓
Wave 2.1 (hashing) + Wave 3.1 (PolicyTrace)
    ↓
Wave 2.2 (PolicyRepository) + Wave 3.2 (frozen retrieval capture)
    ↓
Wave 3.3 (populate policy_hash) + Wave 3.4 (backfill)
    ↓
Wave 4.1 (replayer harness)
    ↓
Wave 4.2–4.4 (endpoints + CI)
    ↓
Wave 5 (admin endpoints)
    ↓
Wave 6 (E2E tests + verification)
```

### Parallelizable Work (Can Start Simultaneously)
- **Wave 1 Tasks 1.1–1.4:** Schema changes can proceed in parallel
- **Wave 2 Tasks 2.1 & 2.3:** Hashing and validation are independent
- **Wave 3 Tasks 3.1–3.2:** DataClass update and snapshot capture independent
- **Wave 4 Tasks 4.2–4.4:** Endpoints can be built after replayer done
- **Wave 5 Tasks 5.1–5.2:** Status endpoint and validation independent
- **Wave 6 Tasks 6.1–6.3:** Test suite files can be written in parallel, CI together

---

## Parallelization Opportunities

### Recommended Wave Scheduling

**Timeline: 8–10 days total**

| Week | Wave | Tasks | Duration | Notes |
|------|------|-------|----------|-------|
| Week 1 Mon–Tue | 1 | Schema 1.1–1.4 | 2 days | **Critical path.** Can start immediately. |
| Week 1 Wed–Fri | 2 | Versioning 2.1–2.4 | 2–3 days | **Parallel with Wave 3.** Starts after Wave 1 completes. |
| | _Parallel start_ | 3 | Telemetry 3.1–3.4 | 2–3 days | Starts after Wave 1; independent of Wave 2 until Task 3.3. |
| Week 2 Mon–Tue | 4 | Replay 4.1–4.4 | 3–4 days | **Blockers:** Wave 2.2, 3.3. |
| Week 2 Wed | 5 | Operational 5.1–5.2 | 1–2 days | **Parallel with Wave 4.** Low dependencies. |
| Week 2 Thu–Fri + | 6 | E2E Tests 6.1–6.5 | 3–4 days | **Final gate.** All prior waves must complete. |

### Optimal Team Structure

**Team A (Database & Core):** Wave 1 + Wave 2.1–2.2 (2–3 people)
- Schema expert handles migrations
- BackEnd expert implements PolicyRepository

**Team B (Telemetry & Replay):** Wave 3 + Wave 4.1 (1–2 people)
- Can start Wave 3 in parallel with Team A
- Coordinates with Team A on policy_hash integration

**Team C (Admin & APIs):** Wave 2.3–2.4 + Wave 4.2–4.4 + Wave 5 (1 person)
- Builds admin endpoints; can interleave with Teams A/B

**Team D (Testing & Verification):** Wave 6 + spot-check work (1–2 people)
- Write E2E tests as features complete
- Can start writing test scaffolding during prior waves

---

## Success Criteria Checklist

### PLCY-01: Policy Registry Versioned, Queryable, No Data Loss

- [ ] **Policy Versioning:**
  - [ ] SHA-256 hashes computed on all policy content
  - [ ] Hashes stored in `policy_registry.policy_hash` column
  - [ ] Hashes are deterministic (same content = same hash)
  - [ ] `UNIQUE (policy_hash)` constraint prevents duplicates

- [ ] **Immutability:**
  - [ ] **No** UPDATE operations on `policy_registry` after creation
  - [ ] App code never calls UPDATE on policy_registry for existing policies
  - [ ] Only INSERT (new versions) or SELECT (read operations) allowed
  - [ ] Test: Attempt UPDATE fails with explicit error

- [ ] **Queryability:**
  - [ ] Query by version: `SELECT * FROM policy_registry WHERE version = '2026-03-08T15:30:01Z'` returns 1 row
  - [ ] Query by hash: `SELECT * FROM policy_registry WHERE policy_hash = 'sha256:abc...'` returns 1 row
  - [ ] List policies: `SELECT * FROM policy_registry ORDER BY created_at DESC` returns all versions
  - [ ] Activation history queryable: `SELECT * FROM policy_activations WHERE policy_version = '2026-03-08T15:30:01Z'` returns all activations

- [ ] **No Data Loss:**
  - [ ] All prior policies retained (not deleted)
  - [ ] Rollback targets are queryable: `SELECT prior_policy_version FROM policy_activations WHERE ... ORDER BY activated_at DESC`
  - [ ] Test: Activate v1, then v2, then rollback → v1 is still in DB and queryable

- [ ] **Rollback Semantics:**
  - [ ] Rollback affects only new requests (not in-flight)
  - [ ] Request captures policy snapshot at entry point
  - [ ] In-flight request on v1 completes on v1 even if v2 activated mid-request
  - [ ] New request after rollback uses v1

- [ ] **Audit Trail:**
  - [ ] `policy_activations` table records every activation
  - [ ] Fields: policy_version, activated_by, reason, activated_at, deactivated_at, prior_policy_version
  - [ ] Query: `SELECT * FROM policy_activations ORDER BY activated_at DESC LIMIT 10` readable and ordered

### PLCY-02: Replay Harness Recreates Routing Deterministically

- [ ] **Frozen Retrieval Inputs:**
  - [ ] Retrieval results captured at request time (before routing)
  - [ ] Snapshot includes: item IDs, scores, rank order, query parameters
  - [ ] Stored in `policy_telemetry.retrieval_items` JSONB
  - [ ] Test trace query: `SELECT retrieval_items FROM policy_telemetry WHERE created_at > NOW() - INTERVAL '1 hour' LIMIT 1` returns valid JSON

- [ ] **Deterministic Routing:**
  - [ ] `ContextualRouter.route()` is pure function (no side effects)
  - [ ] Takes frozen context (confidence_band, query_type, retrieval_state) + policy
  - [ ] Returns consistent RouteDecision given same inputs
  - [ ] Test: `route(context1) == route(context1)` ✓

- [ ] **Replay Reconstruction:**
  - [ ] `/admin/replay/audit?trace_id=<id>` endpoint exists
  - [ ] Returns: `{status, original_decision, reconstructed_decision, reason, trace_timestamp}`
  - [ ] Status: `success` if decisions match, `mismatch` if diverge
  - [ ] Test: Audit recent trace, get `success` status

- [ ] **Explicit Failure Modes:**
  - [ ] `success`: routing reproduced exactly
  - [ ] `partial_replay`: frozen inputs incomplete (old trace); derivable fields backfilled
  - [ ] `mismatch`: routing diverged (code change or schema evolution); investigate
  - [ ] `policy_deleted`: policy hash not found in registry (archived)
  - [ ] `not_found`: trace not found in DB (wrong ID)
  - [ ] **No** silent failures; all statuses explicit

- [ ] **Batch Regression Test:**
  - [ ] `/admin/replay/batch?limit=50` endpoint exists
  - [ ] Returns: `{mode, total_replayed, passed, failed, partial, failures: [...]}`
  - [ ] CI fails if `failed > 0` (drift detected)
  - [ ] Test: Run batch, inspect counts

- [ ] **No LLM in Replay Core:**
  - [ ] DeterministicReplayer does not call Ollama/LLM
  - [ ] Focus: retrieval → routing decision (control plane)
  - [ ] Skip: generation (data plane / future work)
  - [ ] Code audit: no `ollama_client.generate()` calls in replay path

### PLCY-03: Telemetry Captures All Routing Decisions with Full Context

- [ ] **Required Fields Present:**
  - [ ] `request_id`: UUIDs or unique identifiers for correlation
  - [ ] `query`: original query text
  - [ ] `policy_version`: e.g., "2026-03-08T15:30:01Z"
  - [ ] `policy_hash`: e.g., "sha256:abc123..."
  - [ ] `confidence_score`: numeric score (0–1)
  - [ ] `confidence_band`: high|medium|low|insufficient
  - [ ] `routing_action`: direct_answer|abstain|expanded_retrieval
  - [ ] `execution_path`: fast|standard|cautious|abstain
  - [ ] `retrieval_items`: frozen snapshot
  - [ ] `trace_timestamp`: ISO-8601 creation time
  - [ ] Test query: `SELECT COUNT(*) FROM policy_telemetry WHERE policy_hash IS NOT NULL AND telemetry_schema_version = '1.0' AND created_at > NOW() - INTERVAL '1 hour'` > 0

- [ ] **Schema Versioning:**
  - [ ] `telemetry_schema_version` column exists in DB
  - [ ] Every trace has explicit version (default '1.0' for new)
  - [ ] Query: `SELECT DISTINCT telemetry_schema_version FROM policy_telemetry ORDER BY telemetry_schema_version` shows versions

- [ ] **Backfill for Old Traces:**
  - [ ] Pre-Phase4 traces remain queryable (no errors)
  - [ ] `backfill_trace_fields()` derives missing fields:
    - [ ] `retrieval_state` from `confidence_band`
    - [ ] `stage_flags` from `execution_path`
  - [ ] Test: Load Phase 1–3 trace (old schema), backfill, verify fields present

- [ ] **Forward Compatibility:**
  - [ ] Phase 5+ can add fields to schema without breaking Phase 4 traces
  - [ ] Example: Phase 5 adds `query_type_inference` field; Phase 4 traces still queryable
  - [ ] Versioning strategy enables gradual migration

- [ ] **No Data Loss:**
  - [ ] All user request traces retained
  - [ ] No purges or archival during Phase 4
  - [ ] Trace count increases (not resets) across phases
  - [ ] Test: `SELECT COUNT(*) FROM policy_telemetry` shows monotonically increasing count

---

## Risk Register

### Risk 1: Policy Hash Collisions Break Immutability Verification

**Severity:** HIGH  
**Probability:** LOW (SHA-256 is cryptographically sound)

**Mitigation:**
- Use canonical JSON (sorted keys, tight formatting) to ensure determinism
- Include ALL content fields in hash (not just thresholds)
- Test: Verify same policy hashed twice = same result

**Acceptance:**
- Accept if comprehensive unit test coverage exists
- Accept if hash format is documented

---

### Risk 2: Concurrent Activation Race Conditions (Second Activation Wins)

**Severity:** HIGH  
**Probability:** MEDIUM (without SERIALIZABLE isolation)

**Mitigation:**
- Use `BEGIN ISOLATION LEVEL SERIALIZABLE` for activation transactions
- Test concurrent activation requests; verify second gets conflict error (409)
- Unit test: mock DB transaction conflict, verify error handling

**Acceptance:**
- Accept if SERIALIZABLE isolation enforced in code
- Accept if unit tests verify conflict detection

---

### Risk 3: In-Flight Requests Use Multiple Policies (Split-Brain Routing)

**Severity:** HIGH  
**Probability:** MEDIUM (if policy snapshot captured too late)

**Mitigation:**
- Capture policy reference at FastAPI middleware/entry (earliest possible)
- Propagate same policy through entire request lifecycle (no reloads)
- Unit test: inject policy into request.state, verify used throughout
- Operational test: activate policy mid-request; verify in-flight request completes on prior policy

**Acceptance:**
- Accept if middleware-level capture documented
- Accept if request lifecycle test passes

---

### Risk 4: Replay Fails Because Frozen Retrieval Missing (Old Traces)

**Severity:** MEDIUM  
**Probability:** HIGH (Phase 1–3 traces won't have frozen items)

**Mitigation:**
- Implement backfill logic for old traces
- Return explicit `partial_replay` status (not error)
- Document limitation: old traces can be audited but with partial information

**Acceptance:**
- Accept if backfill logic implemented and tested
- Accept if partial_replay status is explicit and handled in CI

---

### Risk 5: Schema Migration Breaks Old Queries

**Severity:** MEDIUM  
**Probability:** MEDIUM (if schema versioning not explicit)

**Mitigation:**
- Explicit `telemetry_schema_version` field on every trace
- Backfill logic on read (not in-place migration)
- Test: Load Phase 1–3 schema trace, verify queryable in Phase 4 code

**Acceptance:**
- Accept if schema versioning explicit
- Accept if E2E test validates old traces queryable

---

### Risk 6: Policy Deletion Breaks Historical Replays

**Severity:** MEDIUM  
**Probability:** MEDIUM (if policies accidentally deleted)

**Mitigation:**
- Schema constraint: no DELETE from policy_registry
- Operational procedure: never delete policies (only mark archived)
- Audit: query policy_activations to verify retention

**Acceptance:**
- Accept if schema prevents deletes (e.g., `REVOKE DELETE` permission)
- Accept if operational runbook documents "never delete" policy

---

### Risk 7: Deterministic Routing Diverges After Schema Changes

**Severity:** MEDIUM  
**Probability:** LOW (if replay harness is pure function)

**Mitigation:**
- Ensure `ContextualRouter.route()` is pure function (no side effects)
- CI regression test: batch replay recent traces after code changes
- Test should FAIL if divergence detected

**Acceptance:**
- Accept if CI regression test fails on divergence
- Accept if code review verifies pure function contract

---

### Risk 8: Backfill Logic Produces Incorrect Derived Fields

**Severity:** MEDIUM  
**Probability:** LOW (if backfill mapping simple: confidence_band → retrieval_state)

**Mitigation:**
- Keep backfill logic simple (deterministic mappings)
- Document assumptions (e.g., "high confidence → SOLID")
- Unit test: backfill function with all scenarios

**Acceptance:**
- Accept if backfill maps are documented
- Accept if unit tests cover all scenarios

---

## Risk Mitigation Strategy by Wave

| Wave | Risk | Mitigation | Owner |
|------|------|-----------|-------|
| 1 | Schema correctness | SQL review, manual testing | DBA |
| 2 | Concurrent activation | Unit test with transaction conflict, code review | Backend |
| 3 | Missing fields in old traces | Backfill logic + unit tests | Backend |
| 4 | Replay divergence | CI regression test, pure function verification | Backend |
| 5 | Operational procedures unclear | Runbook + manual scenario testing | Ops/Backend |
| 6 | Integration failures | E2E tests + staging verification | QA |

---

## Timeline Estimate

### Total Duration: 8–10 Days (1.5–2 Weeks)

**With 3–4 Person Team (Parallel Waves):**

| Phase | Duration | Effort | Notes |
|-------|----------|--------|-------|
| **Wave 1** (schema) | 2 days | 8–12 hours | Sequential; critical path. Starts immediately. |
| **Wave 2 + 3** (versioning + telemetry) | **3–4 days** | **40–50 hours** | **Parallel.** Team A: Wave 2; Team B: Wave 3. After Wave 1. |
| **Wave 4** (replay) | **3–4 days** | **40–50 hours** | **After Wave 2.2 + 3.3.** Depends on hashining & frozen inputs. |
| **Wave 5** (admin) | **1–2 days** | **12–16 hours** | **Parallel with Wave 4.** Low dependencies. |
| **Wave 6** (E2E tests) | **3–4 days** | **40–50 hours** | **Final gate.** All prior waves must complete. |
| **Buffer/Integration** | **1–2 days** | **8–16 hours** | For surprises, code review, CI fixes. |
| | | | |
| **TOTAL** | **10–14 days** | **160–200 hours** | Assuming 3–4 person team, 8 hours/day. |

### With Smaller Team (2 People, Sequential Waves):

| Scenario | Duration | Notes |
|----------|----------|-------|
| 2 people, strict sequential | **15–20 days** | One person per wave; no parallelization. |
| 2 people, some overlap (Schema + Versioning) | **12–14 days** | Start Wave 3 mid-Wave 2; reinterpret work. |

### With Fully Parallel Team (4+ People):

| Scenario | Duration | Notes |
|----------|----------|-------|
| 4 people, optimized waves | **8–10 days** | Team A: Wave 1; Team B: Wave 2; Team C: Wave 3; Team D: Wave 4. |
| 5 people, max parallelization | **7–9 days** | Add Wave 5 team; reduce critical path. |

### Recommended Approach

**Assumption: 3–4 person team, 1.5 sprints (10–12 business days)**

1. **Week 1 (Mon–Fri):** Wave 1 (2 days) + Wave 2 & 3 in parallel (3 days)
2. **Week 2 (Mon–Tue):** Wave 4 (2–3 days)
3. **Week 2 (Wed–Fri):** Wave 5 + Wave 6 (2–3 days if overlap; buffer day)

**Contingency:** Add 1–2 days for:
- Unexpected schema issues
- CI/test environment setup
- Code review cycles

---

## Out-of-Scope (Deferred to Future Phases)

These are **intentionally deferred** — not limitations, but deliberate sequencing:

1. **Counterfactual Policy Replay** (`counterfactual_policy` mode)
   - High value for policy experimentation
   - Not required for Phase 4 audit
   - Deferred to Phase 5+

2. **Digital Signature Verification (RSA/X.509)**
   - Not required for Phase 4 (hash is sufficient)
   - Needed only if cross-system provenance required
   - Deferred to Phase 5+ (compliance phase)

3. **Policy Archival/Cold Storage**
   - Retention bias towards preservation for Phase 4
   - Archival is operational/cost concern, not functional
   - Deferred to Phase 6+ (ops optimization)

4. **Edge Cache Distribution**
   - Policy rollout to distributed caches
   - Deferred to Phase 7+ (scaling phase)

5. **Full-Lifecycle Replay (Generation Included)**
   - Phase 4 focuses on routing (control plane)
   - Full response regeneration is Phase 5+ (after evidence-aware retrieval)

---

## Success Criteria Summary

Phase 4 is **complete** when:

1. ✅ **Policy versioning works**: Policies created, activated, rolled back; audit trail recorded; immutability enforced
2. ✅ **Replay harness works**: Traces reconstructible from frozen inputs; determinism verified in CI; explicit failure modes handled
3. ✅ **Telemetry captures context**: All required fields present; schema versioning explicit; old traces remain queryable
4. ✅ **All E2E tests pass**: Policy lifecycle, replay determinism, schema migration tests green
5. ✅ **Operations ready**: Admin endpoints working; runbook documented; team trained

**Gating Criteria:**
- CI regression test passes (replay batch succeeds on recent traces)
- E2E test suite passes (PLCY-01, PLCY-02, PLCY-03 verified)
- Code review approved (critical path items: hashing, transactions, backfill logic)
- Staging verification complete (hotfix, rollback, replay scenarios tested manually)

---

## Appendix: Test Fixtures & Example Data

### Sample Policy Fixture

```json
{
  "version": "2026-03-08T15:30:01Z",
  "thresholds": {
    "high": 0.85,
    "medium": 0.60,
    "low": 0.35,
    "insufficient": 0.0
  },
  "routing_rules": {
    "default": "standard",
    "query_types": {
      "exact_fact": {"high": "direct_answer", "medium": "expanded_retrieval", "low": "abstain"},
      "ambiguous": {"high": "standard", "medium": "cautious", "low": "abstain"}
    }
  },
  "latency_budgets": {
    "general": 2000,
    "exact_fact": 1000,
    "ambiguous": 3000
  }
}
```

### Sample Trace Fixture (Phase 4 Schema)

```json
{
  "request_id": "req-2026-03-08-abc123",
  "query": "When was PostgreSQL first released?",
  "policy_version": "2026-03-08T15:30:01Z",
  "policy_hash": "sha256:abcd1234...",
  "telemetry_schema_version": "1.0",
  "confidence_score": 0.87,
  "confidence_band": "high",
  "routing_action": "direct_answer",
  "execution_path": "fast",
  "trace_timestamp": "2026-03-08T15:30:47Z",
  "retrieval_items": [
    {"item_id": "chunk-1", "rank": 1, "score": 0.92, "source_doc": "doc-1"},
    {"item_id": "chunk-2", "rank": 2, "score": 0.88, "source_doc": "doc-2"}
  ],
  "retrieval_parameters": {"limit": 5, "threshold": 0.7}
}
```

### Sample Activation History Record

```json
{
  "activation_id": 42,
  "policy_version": "2026-03-08T15:30:01Z",
  "activated_at": "2026-03-08T15:30:05Z",
  "activated_by": "calibration_worker_3",
  "reason": "calibration update: Phase 4 pilot",
  "prior_policy_version": "2026-03-08T14:00:00Z",
  "deactivated_at": null
}
```

---

## End of Plan

**Status:** ✅ Ready for Execution  
**Next Step:** Begin Wave 1 (Schema Foundation)  
**Verification:** Run through Success Criteria Checklist at end of Phase 4

