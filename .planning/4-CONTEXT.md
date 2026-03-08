# Phase 4: Policy Infrastructure Hardening - Context

**Phase:** 4  
**Name:** Policy Infrastructure Hardening  
**Status:** Ready to plan  
**Created:** 2026-03-08  

---

## Goals

Make the policy registry, replay harness, and telemetry pipeline production-reliable:
- Versioned policies without data loss on update/rollback
- Deterministic replay from traces
- Complete telemetry records for every routing decision

---

## Implementation Decisions

### 1. Policy Versioning Strategy

**Decision:** Use sequential integers with optional descriptive suffixes

- Primary format: `v1`, `v2`, `v42`
- Optional descriptive suffix: `v43-calibrated`, `v44-hotfix`
- Database stores both `version` (unique) and `display_name` (optional)
- Active policy is marked via `is_active` boolean

**Rationale:** Simple ordering, easy to understand, supports quick rollback by version number.

---

### 2. Policy Rollback Retention

**Decision:** Retain last 5 versions minimum

- Soft-delete approach: Never physically delete policy rows
- Active flag controls which policy is live
- Historical policies queryable via `list_policies()`
- Future enhancement: Add `archived_at` timestamp for true cleanup

**Schema consideration:** Add index on `created_at` for efficient historical queries.

---

### 3. Update/Rollback Atomicity

**Decision:** Full transactional rollback

- `set_active_policy()` already uses transaction wrapper
- Verify and enhance error handling for partial failures
- Add `rollback_policy()` method for explicit reversion
- Return detailed status (success/failure/reason) to caller

**Transaction boundary:**
```sql
BEGIN;
  -- Deactivate all policies
  UPDATE policy_registry SET is_active = false;
  
  -- Activate target policy
  UPDATE policy_registry SET is_active = true WHERE version = $1;
  
  -- Verify exactly one policy is active
  -- (assertion check)
COMMIT;
```

---

### 4. Replay Harness Scope

**Decision:** Full deterministic replay

**Replay capabilities:**
1. **Decision Verification** - Confirm same routing decision on identical inputs
2. **Regression Testing** - Compare old vs new policy behavior
3. **Debugging** - Reconstruct exact execution path from trace

**Implementation approach:**
- Create `ReplayHarness` class in `shared/replay_harness.py`
- Load policy trace from `policy_telemetry` table
- Reconstruct `RoutingContext` from stored evidence
- Execute `ContextualRouter.route()` with original inputs
- Compare output (action, execution_path) to stored values

**Determinism requirements:**
- Fixed random seeds where applicable
- Same policy version (or explicit override)
- Same component configurations

---

### 5. Telemetry Retention

**Decision:** 30 days full detail, then aggregate

**Implementation:**
- Partition `policy_telemetry` table by `created_at` (monthly)
- After 30 days, compress to daily aggregates:
  - Count per (query_type, confidence_band, execution_path)
  - Average latency, quality scores
  - No individual query_text retention
- Maintain full detail for 20 most recent traces (for replay tests)

**Aggregate table schema:**
```sql
CREATE TABLE intelligence.policy_telemetry_daily (
    date DATE PRIMARY KEY,
    query_type VARCHAR(50),
    confidence_band VARCHAR(20),
    execution_path VARCHAR(50),
    count INTEGER,
    avg_latency_ms FLOAT,
    avg_quality_score FLOAT
);
```

---

### 6. Dead Code Fix

**Decision:** Remove unreachable return statement

**Location:** `shared/database.py`, line 529

**Current code (bug):**
```python
except Exception as e:
    logger.error(f"Failed to get route distribution: {e}")
    return []
    return str(result['query_id'])  # <-- DEAD CODE
```

**Fix:** Delete line 529 (the unreachable return)

**Additional audit:** Review entire `PolicyRepository` class for:
- Other unreachable code
- Missing error handling
- Inconsistent return types

---

### 7. Telemetry Schema Hardening

**Decision:** Ensure all required fields are non-nullable in traces

**Required fields (must be present in every trace):**
- `query_type` - Classification of query
- `confidence_band` - HIGH/MEDIUM/LOW/INSUFFICIENT
- `evidence_shape` - JSON structure describing evidence profile
- `retrieval_state` - SOLID/FRAGILE/CONFLICTED/SPARSE/ABSENT
- `routing_action` - What decision was made
- `execution_path` - Which path was taken (fast/standard/cautious/abstain)

**Validation:**
- Add Pydantic validation in `PolicyTrace.to_dict()`
- Database constraints (NOT NULL where appropriate)
- CI test asserting all fields present in logged traces

---

### 8. Replay Test Coverage

**Decision:** Minimum 20 historical traces for replay validation

**Test strategy:**
- Store 20 diverse traces in test fixtures
- Cover all 4 execution paths (fast/standard/cautious/abstain)
- Include edge cases (boundary confidence scores)
- Test with both same-policy and cross-policy replay

**Replay harness tests:**
1. `test_replay_produces_same_decision` - Determinism check
2. `test_replay_different_policy_detects_change` - Regression detection
3. `test_replay_missing_trace_raises` - Error handling

---

## Technical Constraints

1. **No breaking API changes** - All changes internal to policy system
2. **Backward compatible schema** - Add columns, don't modify existing
3. **Async-first** - All repository methods remain async
4. **Fail-closed** - Errors in policy operations log and return safe defaults

---

## Files to Modify

| File | Changes |
|------|---------|
| `shared/database.py` | Remove dead code (line 529), enhance error handling |
| `shared/telemetry.py` | Add validation for required fields |
| `shared/replay_harness.py` | **NEW** - Replay implementation |
| `shared/policy.py` | Add version comparison utilities |
| `api/app.py` | Wire replay harness endpoints (admin) |
| `tests/test_policy_hardening.py` | **NEW** - Comprehensive test suite |

---

## Success Criteria (from ROADMAP)

1. **PLCY-01:** Policy update and rollback round-trips produce identical schema state with no row loss
2. **PLCY-02:** Replaying any stored policy trace produces the same routing decision (tested across 20+ traces)
3. **PLCY-03:** Every `/rag` request produces telemetry with non-null: query_type, confidence_band, evidence_shape, retrieval_state, routing_action

---

## Open Questions (None)

All key decisions captured. Ready to proceed to planning phase.

---

*Context captured: 2026-03-08*  
*Next step: GSD plan phase 4*
