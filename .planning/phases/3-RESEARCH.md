# Phase 3 Research Report: CI Verification Infrastructure Audit

**Date:** 2026-03-08  
**Status:** Complete  
**Next Steps:** Planning Phase

---

## Executive Summary

The Article Index codebase has **substantial existing infrastructure** for Phase 3 CI verification, but requires specific additions to fulfill the Phase 3 CONTEXT.md decisions:

| Component | Status | Phase 3 Impact |
|-----------|--------|---|
| Policy trace schema | ✅ Mostly complete | Add `routing_action`, `stage_flags` to JSONB metadata |
| Policy registry + reload | ⚠️ Partial | Add `update_active_policy()` method + `/admin/policy/reload` endpoint |
| Telemetry logging | ✅ Complete | Already writes execution_path, policy_version; no changes needed |
| Routing with execution paths | ✅ Complete | `route_with_confidence()` returns execution_path correctly |
| Calibration audit | ✅ Complete | `/admin/evaluation/calibration-audit` endpoint exists; output format verified |
| Admin endpoint patterns | ✅ Complete | Model patterns exist; can extend with `/admin/policy/reload` |

**Bottom line:** Phase 3 can be implemented with **minimal schema changes** (only JSONB metadata extensions) and two small additions: a policy update method + a reload endpoint.

---

## 1. Current Policy Trace Schema (Area 2)

### Current State: `PolicyTrace` Class
**Location:** [shared/telemetry.py](shared/telemetry.py#L1-L60)

**Fields Already Present:**
```python
@dataclass
class PolicyTrace:
    query_id: str            # ✅ For request correlation
    query_text: str
    query_type: str
    confidence_score: float
    confidence_band: str     # ✅ Band (high/medium/low/insufficient)
    action_taken: str        # ✅ Action (direct_generation, expanded_retrieval, etc.)
    execution_path: str      # ✅ Path (fast/standard/cautious/abstain/none)
    retrieval_state: str     # ✅ Evidence quality state
    policy_version: str      # ✅ Policy version tracking
    reranker_invoked: bool   # ✅ Stage flag
    reranker_reason: str
    tokens_generated: int
    tokens_total: int
    abstention_triggered: bool
    evidence_shape: Dict[...]  # ✅ Flexible JSONB storage
    metadata: Dict[...]      # ✅ Flexible JSONB storage
    created_at: str
```

### Current DB Schema: `policy_telemetry` Table
**Location:** [migrations/005_add_policy_optimization.sql](migrations/005_add_policy_optimization.sql)

**Existing columns:**
```sql
query_id UUID PRIMARY KEY
query_text TEXT
query_type TEXT
confidence_score FLOAT
confidence_band TEXT
action_taken TEXT              -- ✅ Already stored
execution_path TEXT            -- ✅ Already stored
policy_version TEXT (FK)
metadata JSONB                 -- ✅ Flexible for additional fields
created_at TIMESTAMPTZ
-- Plus outcome metrics: latency_ms, groundedness_score, etc.
```

**Stored fields in `log_telemetry()` method:**
**Location:** [shared/database.py](shared/database.py#L460-L490)

Currently inserts: `query_id`, `query_text`, `query_type`, `confidence_score`, `confidence_band`, `action_taken`, `execution_path`, `retrieval_state`, `policy_version`, `retrieval_mode`, `chunks_retrieved`, `latency_ms`, `evidence_shape`, `metadata`

### Phase 3 Additions Required

**Option 1 (Recommended): Extend via JSONB metadata**
```json
// Current metadata structure can hold:
{
  "stage_flags": {
    "retrieval_expanded": boolean,
    "reranker_invoked": boolean, 
    "generation_skipped": boolean
  },
  "routing_action_detail": "...",
  ...
}
```

**Option 2: Add first-class columns (if preference)**
```sql
ALTER TABLE intelligent.policy_telemetry 
ADD COLUMN retrieval_expanded BOOLEAN DEFAULT FALSE,
ADD COLUMN generation_skipped BOOLEAN DEFAULT FALSE;
```

**Recommendation:** Use Option 1 (JSONB metadata) — zero migration cost, already structured in PolicyTrace.

### ✅ Assessment for Area 2
- **Trace schema:** 95% ready. Only need to populate `stage_flags` in metadata during routing.
- **Request correlation:** Ready via `query_id` (UUID).
- **Retrieved execution path + confidence band:** Already logged in DB.
- **Reranker flag:** Already exists as `reranker_invoked` field.
- **Generation suppression flag:** Needs to be set during abstain path (minor code addition in app.py).

---

## 2. Policy Registry and Threshold Reload (Area 3)

### Current State: Policy Registry Table and Repository

**Location:** [migrations/005_add_policy_optimization.sql](migrations/005_add_policy_optimization.sql)

**Schema:**
```sql
CREATE TABLE intelligence.policy_registry (
    version TEXT PRIMARY KEY,
    is_active BOOLEAN DEFAULT FALSE,
    thresholds JSONB NOT NULL,
    routing_rules JSONB NOT NULL,
    contextual_thresholds JSONB,           -- Phase 14 addition
    latency_budgets JSONB,                 -- Phase 14 addition
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
```

**Initial policy `v13.0` inserted with `is_active = TRUE`**

### Current PolicyRepository Methods
**Location:** [shared/database.py](shared/database.py#L430-L530)

**Existing methods:**
- `get_active_policy()` ✅ Retrieves currently active policy from DB
- `list_policies()` ✅ Lists all policies
- `log_telemetry()` ✅ Writes trace data
- `get_telemetry_by_id()` ✅ Retrieves single trace
- `get_route_distribution()` ✅ Aggregates routing decisions
- `update_telemetry_outcome()` ✅ Updates outcome metrics

**Missing for Phase 3:**
- ❌ `update_active_policy()` or `set_active_policy()` — to switch which policy is active
- ❌ `create_policy()` or `insert_policy()` — to write calibrated thresholds to a new policy version

### Current RAGPolicy Class
**Location:** [shared/policy.py](shared/policy.py#L1-L100)

**Fields:**
```python
@dataclass
class RAGPolicy:
    version: str
    thresholds: Dict[str, float]           # {"high": 0.75, "medium": 0.50, ...}
    routing_rules: Dict[str, Any]
    contextual_thresholds: Dict[str, Dict[str, float]]
    latency_budgets: Dict[str, int]
    
    def get_threshold(self, band: str, query_type: str = "general") -> float
    def get_latency_budget(self, query_type: str = "general") -> int
    def get_action(self, band: str, query_type: str = "general") -> str
    def to_dict() -> Dict
    @classmethod
    def from_db_row(row: Dict) -> RAGPolicy
```

**Already supports versioning and contextual overrides.** ✅

### Current Policy Loading in FastAPI Startup
**Location:** [api/app.py](api/app.py#L210-L217)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load default/active policy at startup
    policy_data = await policy_repo.get_active_policy()
    if policy_data:
        app.state.active_policy = RAGPolicy.from_db_row(policy_data)
    else:
        app.state.active_policy = RAGPolicy(version="default-fallback")
```

**Current behavior:** Policy loaded once at startup, cached in `app.state.active_policy`. ✅

### Phase 3 Additions Required

#### 1. Add PolicyRepository Methods
```python
async def create_policy(self, version: str, thresholds: Dict, routing_rules: Dict = None) -> bool:
    """Insert a new policy version into the registry."""

async def set_active_policy(self, version: str) -> bool:
    """Mark a specific policy version as active (SET is_active=FALSE for all others, TRUE for this one)."""
```

#### 2. Add `/admin/policy/reload` Endpoint
**Location:** api/app.py (new endpoint section)

```python
@app.post("/admin/policy/reload")
async def reload_policy(_: None = Depends(require_api_key)):
    """Reload active policy from DB into app.state.
    
    Useful in CI to pick up calibrated thresholds without restart.
    """
    try:
        policy_data = await policy_repo.get_active_policy()
        if policy_data:
            app.state.active_policy = RAGPolicy.from_db_row(policy_data)
            return {
                "status": "success",
                "policy_version": app.state.active_policy.version,
                "thresholds": app.state.active_policy.thresholds
            }
        else:
            return {
                "status": "error",
                "message": "No active policy found"
            }
    except Exception as e:
        logger.error(f"Policy reload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Reload failed: {str(e)}")
```

#### 3. Calibration Output Integration (Optional for Phase 3, but expected for Phase 6)
**Current calibration audit endpoint:** [api/app.py](api/app.py#L2050-L2150)

Currently produces a `CalibrationAuditResponse` with `status`, `report`, `execution_time_seconds`.

**The report needs to be written to the policy registry after calibration completes.**

Not currently implemented — the calibration audit runs but doesn't auto-persist new thresholds. Phase 3 CI tests will manually insert policies for testing.

### ✅ Assessment for Area 3
- **Policy storage:** 90% ready. Schema exists, loading works, only needs update/insert methods + reload endpoint.
- **Versioning:** Already implemented (version field is primary key).
- **Active policy tracking:** Already implemented (is_active boolean + index).
- **In-process caching:** Already in place (`app.state.active_policy`).
- **Reload mechanism:** Simple to add (requires 1 new endpoint + 1-2 repository methods).

---

## 3. Execution Path Verification (Area 2 Implementation Details)

### Current Routing Implementation

**Location:** [api/routing.py](api/routing.py)

**ContextualRouter class:**
```python
class ContextualRouter:
    def route(context: RoutingContext) -> RouteDecision
    async def route_with_confidence(context, chunks, evidence_shape, uncertainty_detector) -> RouteDecision
```

**RouteDecision dataclass:**
```python
@dataclass
class RouteDecision:
    action: str              # e.g., "direct_generation", "expanded_retrieval", "abstain"
    execution_path: str      # ✅ "fast" | "standard" | "cautious" | "abstain"
    reason: str
```

**Current routing logic (Phase 2 implementation):**
- High confidence → `execution_path = "fast"`
- Medium confidence → `execution_path = "standard"` (with uncertainty gates check)
- Low confidence → `execution_path = "cautious"` (mandatory reranking)
- Insufficient confidence → `execution_path = "abstain"`

✅ **Execution paths already implemented correctly.**

### Current Routing Usage in RAG Pipeline

**Location:** [api/app.py](api/app.py#L1053-L1250)

The `_rag_hybrid()` function:
1. ✅ Classifies query type
2. ✅ Scores confidence and gets confidence band
3. ✅ Calls `router.route_with_confidence()` → gets `RouteDecision`
4. ✅ Sets `trace.execution_path = route.execution_path`
5. ✅ Routes based on execution_path:
   - **Fast:** skip reranking, skip expansion
   - **Standard:** conditional reranking via uncertainty gates
   - **Cautious:** mandatory reranking + expansion
   - **Abstain:** return `RAG_ABSTAIN_RESPONSE`

### Current Telemetry Logging

**Location:** [api/app.py](api/app.py#L1049 and `log_policy_telemetry` function)

Traces are logged via background task:
```python
if background_tasks:
    background_tasks.add_task(log_policy_telemetry, trace)
```

Which calls `policy_repo.log_telemetry(trace.to_dict())` — writes to DB.

✅ **Execution path + confidence band + policy version already logged correctly.**

### Phase 3 Additions for Area 2

**All core pieces exist. Only need:**

1. Populate `stage_flags` in trace metadata during execution
   - Set `metadata['stage_flags']['reranker_invoked'] = trace.reranker_invoked`
   - Set `metadata['stage_flags']['retrieval_expanded'] = True` when context expansion happens
   - Set `metadata['stage_flags']['generation_skipped'] = trace.abstention_triggered`

2. Populate `metadata['routing_action'] = route.routing_action` (minor alias for clarity)

3. Ensure abstention response includes machine-readable status field (already present in abstain response)

**Effort: Minimal** — mostly just setting flags that already exist in trace object into the metadata JSONB.

### ✅ Assessment for Area 2 Implementation
- **Routing logic:** 100% ready (fast/standard/cautious/abstain already work)
- **Trace telemetry:** 95% ready (just need stage_flags metadata population)
- **Execution path logging:** 100% ready (already logs to DB)
- **Request correlation:** 100% ready (query_id UUID for correlation)

---

## 4. Test Coverage and Fixtures (Area 4)

### Current Test Suite

**Location:** tests/

**Existing tests:**
- [test_async_ingestion.py](tests/test_async_ingestion.py) — tests article ingestion pipeline
- [test_async_failure.py](tests/test_async_failure.py) — tests error handling
- [test_calibration.py](tests/test_calibration.py) — tests confidence calibration
- [test_policy_routing.py](tests/test_policy_routing.py) — tests routing decisions
- [test_query_classifier.py](tests/test_query_classifier.py) — tests query classification
- [test_evidence_shape.py](tests/test_evidence_shape.py) — tests evidence extraction
- [test_retrieval_state.py](tests/test_retrieval_state.py) — tests retrieval state labeling
- [test_selective_reranking.py](tests/test_selective_reranking.py) — tests reranking policy
- [test_evidence_shape.py](tests/test_evidence_shape.py) — tests evidence metrics

**Pytest configuration:** [tests/conftest.py](tests/conftest.py)

**Test infrastructure:**
- Uses hypothesis for property testing
- Has fixtures for mock articles, queries, etc.
- Has database fixtures

✅ **Test infrastructure is mature. Phase 3 can extend it.**

---

## 5. Known Issues and Technical Debt

### Issue 1: Dead Code in PolicyRepository
**Location:** [shared/database.py](shared/database.py#L540)

```python
async def get_route_distribution(self, days: int = 7) -> List[Dict[str, Any]]:
    # ... query code ...
    return [dict(r) for r in rows]
    return str(result['query_id'])  # ❌ UNREACHABLE CODE
```

**Phase 3 Action:** Remove the dead return statement during refactor.

### Issue 2: Missing "generation_skipped" Tracking
**Location:** [shared/telemetry.py](shared/telemetry.py)

The `PolicyTrace` class has `abstention_triggered` field, but no explicit `generation_skipped` field.

**Phase 3 Action:** Add `generation_skipped: bool = False` field to PolicyTrace for clarity (or rely on `abstention_triggered`).

### Issue 3: No Fast-Path Verification in Routing
**Location:** [api/app.py](api/app.py#L1150-L1165)

The fast path is implemented but doesn't explicitly log that reranking was **skipped**.

**Phase 3 Action:** Add explicit logging:
```python
if execution_path == "fast":
    logger.debug("Fast path: skipping reranking and expansion")
    trace.metadata['stage_flags'] = {
        'reranker_invoked': False,
        'retrieval_expanded': False,
        'generation_skipped': False
    }
```

### Issue 4: Calibration Output Not Auto-Persisted
**Location:** [api/app.py](api/app.py#L2165)

Calibration audit runs but doesn't write results to policy_registry.

**Phase 3 Action:** Manual policy insertion will work for CI tests. Phase 6 will wire this up for production.

---

## 6. Admin Endpoint Patterns

### Existing Admin Endpoints (Model for `/admin/policy/reload`)

**Location:** [api/app.py](api/app.py#L1300-onwards)

Examples:
```python
@app.post("/admin/reindex/{article_id}")
@app.post("/admin/models/check")
@app.get("/admin/vector-index/status")
@app.post("/admin/vector-index/tune")
@app.post("/admin/rerank/test")
@app.post("/admin/rerank/tune")
@app.get("/admin/rerank/status")
@app.post("/admin/evidence/tune")
@app.get("/admin/evidence/status")
@app.post("/admin/evaluation/calibration-audit")
```

**Pattern:** `@app.post("/admin/{subsystem}/{action}")` with `Depends(require_api_key)` for auth.

**Requirements met in Phase 3 Reload Endpoint:**
- ✅ Auth via `require_api_key` dependency
- ✅ Admin namespace
- ✅ Simple POST method
- ✅ Error handling with HTTPException
- ✅ JSON response

✅ **Endpoint pattern is straightforward to extend.**

---

## 7. Calibration Audit Output Format

### Current Calibration Audit Endpoint

**Location:** [api/app.py](api/app.py#L2010-L2170)

**Endpoint:** `POST /admin/evaluation/calibration-audit`

**Response structure:**
```json
{
  "status": "success",
  "report": { /* detailed metrics */ },
  "raw_results": [ /* optional */ ],
  "execution_time_seconds": 3.45
}
```

**Report contains (from CalibrationAuditor):**
- Per-band accuracy (high/medium/low/insufficient)
- Calibration error (ECE)
- Confidence-quality correlation
- Citation metrics
- Recommendations

### How to Integrate with Phase 3

For Phase 3 CI tests:
1. **Option A:** Call calibration endpoint, extract thresholds from report, manually call `POST /admin/policy/create` or insert directly to DB
2. **Option B:** Direct DB insertion of test policies (faster for CI)

**Recommendation for Phase 3:** Use Option B (direct DB insertion). Option A can be wired in Phase 6.

---

## 8. Summary: What Needs to be Built for Phase 3

### Minimal Required Changes

| Component | Change | Effort |
|-----------|--------|--------|
| PolicyRepository | Add `create_policy()` method | ~20 lines |
| PolicyRepository | Add `set_active_policy()` method | ~15 lines |
| FastAPI app.py | Add `/admin/policy/reload` endpoint | ~25 lines |
| Policy trace logging | Populate `stage_flags` in metadata | ~10 lines |
| Policy trace logging | Set `generation_skipped` flag on abstain | ~3 lines |
| Policy trace logging | Remove dead code in `get_route_distribution()` | ~1 line |
| Telemetry.py | Add `generation_skipped: bool` field to PolicyTrace | ~1 line |
| App.py | Add explicit logging for fast-path skip | ~5 lines |

**Total estimated code changes: ~80 lines across 4 files**

### No Schema Migrations Needed
The existing JSONB metadata fields are sufficient for all Phase 3 observability requirements.

### Files to Modify
1. `shared/database.py` — Add 2 methods to PolicyRepository
2. `shared/telemetry.py` — Add 1 field to PolicyTrace  
3. `api/app.py` — Add 1 endpoint + minor logging enhancements
4. `migrations/007_phase3_ci_verification.sql` — Add initial test policies (optional; can use direct DB inserts in CI)

---

## 9. Verification Checkpoints

### Pre-Planning Validation

Before moving to the planning phase, verify:

1. ✅ **Policy registry table exists:** `SELECT * FROM intelligence.policy_registry;` should show v13.0
2. ✅ **Policy telemetry table exists:** `SELECT COUNT(*) FROM intelligence.policy_telemetry;`
3. ✅ **PolicyRepository.get_active_policy() works:** Can retrieve v13.0 from DB
4. ✅ **Routing returns execution_path:** Call `/rag` and check response includes execution_path in trace
5. ✅ **Traces are persisted:** Query policy_telemetry table and find recent records with execution_path set

### Post-Implementation Validation

After implementing Phase 3 changes:

1. **Trace schema:** Run query and verify `metadata::json->>'stage_flags'` contains expected flags
2. **Reload endpoint:** Call `POST /admin/policy/reload` and verify app.state.active_policy updates
3. **Threshold chaining:** Insert new policy → call reload → call `/rag` → verify trace uses new policy version
4. **Execution paths:** Call with forced-high, forced-low, forced-insufficient confidence → verify execution_path in trace differs

---

## 10. Dependency Tree for Implementation

```
Prerequisite: PolicyRepository methods (create/update policies)
         ↓
Add reload endpoint
         ↓
Enhance trace logging (stage_flags + generation_skipped)
         ↓
Test fixtures and matrix
         ↓
CI assertions and verification
```

All components can proceed in parallel after the PolicyRepository methods are ready.

---

## Recommendations for Planning Phase

1. **Start with PolicyRepository methods.** They're the foundation — everything else depends on them.
2. **Write the `/admin/policy/reload` endpoint.** It's small and unblocks all CI tests.
3. **Enhance trace logging.** Add stage_flags and generation_skipped — forces you to verify routing paths are working.
4. **Run smoke tests.** Manually call `/rag`, verify trace structure, verify reload endpoint works.
5. **Build test fixtures once verification passes.** Use direct DB insertion for test policies (no API dependency).

---

## Appendix: Key File Locations Summary

| Concern | File | Location |
|---------|------|----------|
| Policy schema | migrations/005_add_policy_optimization.sql | Lines 1-60 |
| Policy trace dataclass | shared/telemetry.py | Lines 1-80 |
| Policy repository | shared/database.py | Lines 430-530 |
| Policy loading | api/app.py | Lines 210-220 |
| Routing logic | api/routing.py | Lines 1-161 |
| RAG pipeline | api/app.py | Lines 1053-1250 |
| Admin endpoint patterns | api/app.py | Lines 1300-onwards |
| Calibration audit | api/app.py | Lines 2010-2170 |
| Test infrastructure | tests/conftest.py | General |

---

*Research complete: 2026-03-08*  
*Ready for Planning Phase*
