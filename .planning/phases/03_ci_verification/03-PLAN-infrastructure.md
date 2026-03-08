---
wave: 1
depends_on: []
files_modified:
  - shared/database.py
  - shared/telemetry.py
  - api/app.py
autonomous: false
---

# Phase 3 Plan 1: Policy Infrastructure

**Objective:** Establish the policy management and trace instrumentation foundation for CI verification. Add repository methods for policy management, implement the policy reload endpoint, and enhance telemetry logging with stage flags and confidence override tracking for verifiable routing decisions.

**Requirements Covered:**
- CTRL-06: Calibration produces threshold updates consumed by routing without manual steps

**Success Criteria:**
- [ ] PolicyRepository has working `create_policy()` and `set_active_policy()` methods
- [ ] `/admin/policy/reload` endpoint can reload active policy from DB into app.state
- [ ] Policy traces include `stage_flags` metadata with routing decision details
- [ ] CI test-mode header handling (X-CI-Test-Mode, X-CI-Override-Confidence) is implemented
- [ ] Confidence override is applied after scoring but before routing
- [ ] Telemetry logs confidence_override information for audit trail
- [ ] All routing paths (fast/standard/cautious/abstain) populate stage flags correctly
- [ ] Abstention responses include machine-readable `generation_skipped` flag

**Effort:** ~120 lines of code across 3 files. Sequential dependency chain: PolicyRepository → reload endpoint → header handling → trace logging.

---

## Tasks

### Task 1: Add PolicyRepository Methods for Policy Management

**Action:**
Add two methods to the `PolicyRepository` class in [shared/database.py](shared/database.py#L430). These methods enable creation and activation of new policies in the registry without manual SQL or server restart.

**Steps:**

1. Open [shared/database.py](shared/database.py) and locate the PolicyRepository class definition (around line 430)

2. Add the `create_policy()` method after the existing `get_active_policy()` method:
   ```python
   async def create_policy(
       self,
       version: str,
       thresholds: Dict[str, float],
       routing_rules: Dict[str, Any] = None,
       contextual_thresholds: Dict[str, Any] = None,
       latency_budgets: Dict[str, int] = None
   ) -> bool:
       """
       Insert a new policy version into the policy_registry.
       
       Args:
           version: Semantic version identifier (e.g., "v42", "v14.0-calibrated")
           thresholds: Confidence thresholds dict (e.g., {"high_min": 0.85, "medium_min": 0.60, ...})
           routing_rules: Optional routing rules JSONB
           contextual_thresholds: Optional contextual overrides
           latency_budgets: Optional per-query-type latency budgets
       
       Returns:
           True if insert succeeded, False if version already exists
       """
       try:
           query = """
               INSERT INTO intelligence.policy_registry 
               (version, is_active, thresholds, routing_rules, contextual_thresholds, latency_budgets, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
               ON CONFLICT (version) DO NOTHING
           """
           result = await self.pool.execute(
               query,
               version,
               False,  # New policies default to inactive
               json.dumps(thresholds),
               json.dumps(routing_rules or {}),
               json.dumps(contextual_thresholds or {}),
               json.dumps(latency_budgets or {})
           )
           return result == "INSERT 0 1"
       except Exception as e:
           logger.error(f"Failed to create policy {version}: {e}")
           return False
   ```

3. Add the `set_active_policy()` method immediately after `create_policy()`:
   ```python
   async def set_active_policy(self, version: str) -> bool:
       """
       Mark a specific policy version as the active policy.
       Deactivates all other versions.
       
       Args:
           version: Policy version to activate
       
       Returns:
           True if activation succeeded, False otherwise
       """
       try:
           # Use transaction to ensure atomic swap
           async with self.pool.acquire() as conn:
               async with conn.transaction():
                   # Deactivate all others
                   await conn.execute(
                       "UPDATE intelligence.policy_registry SET is_active = FALSE"
                   )
                   # Activate the specified version
                   result = await conn.execute(
                       "UPDATE intelligence.policy_registry SET is_active = TRUE, updated_at = NOW() WHERE version = $1",
                       version
                   )
                   # Result is like "UPDATE 1" if succeeded
                   return "1" in str(result)
       except Exception as e:
           logger.error(f"Failed to set active policy to {version}: {e}")
           return False
   ```

4. Verify the imports at the top of [shared/database.py](shared/database.py#L1-L30) include `json`:
   ```python
   import json  # Should already be present
   ```

**Verification:**
- [ ] Both methods are syntactically correct (no indentation errors)
- [ ] Methods use `self.pool` to access the async connection pool (matches existing patterns)
- [ ] `create_policy()` returns False if version already exists (no exception thrown)
- [ ] `set_active_policy()` uses a transaction to avoid race conditions

---

### Task 2: Implement `/admin/policy/reload` FastAPI Endpoint

**Action:**
Add a new endpoint to [api/app.py](api/app.py) that reloads the active policy from the database into the FastAPI app state. This enables CI tests to change thresholds mid-test without server restart.

**Steps:**

1. Open [api/app.py](api/app.py) and find the admin endpoint section (search for `@app.post("/admin/..."`). Typical location is around line 1300–1400.

2. Add the new endpoint at the end of the admin section, before any other route or after the last admin endpoint:
   ```python
   @app.post("/admin/policy/reload")
   async def reload_policy(_: None = Depends(require_api_key)):
       """
       Reload the active policy from the database into app.state.
       
       Triggers an immediate refresh of the policy cache without server restart.
       Useful for CI tests that need to verify threshold changes are picked up by the router.
       
       Returns:
           {"status": "success", "policy_version": "v42", "thresholds": {...}}
           or
           {"status": "error", "message": "..."}
       """
       try:
           logger.info("Reloading active policy from database...")
           policy_data = await policy_repo.get_active_policy()
           
           if not policy_data:
               logger.warning("No active policy found in database")
               return {
                   "status": "error",
                   "message": "No active policy found in database"
               }, 404
           
           # Instantiate RAGPolicy from DB row
           new_policy = RAGPolicy.from_db_row(policy_data)
           
           # Update app state
           app.state.active_policy = new_policy
           
           logger.info(f"Policy reloaded: {new_policy.version}")
           
           return {
               "status": "success",
               "policy_version": new_policy.version,
               "thresholds": new_policy.thresholds,
               "timestamp": datetime.utcnow().isoformat()
           }
       
       except Exception as e:
           logger.error(f"Policy reload failed: {e}", exc_info=True)
           return {
               "status": "error",
               "message": str(e)
           }, 500
   ```

3. Verify the necessary imports exist at the top of [api/app.py](api/app.py#L1-L50):
   ```python
   from shared.policy import RAGPolicy  # Should already be present
   from datetime import datetime  # Add if missing
   ```

4. Verify `require_api_key` dependency is imported (should already be present from api/auth.py)

**Verification:**
- [ ] Endpoint has correct decorator: `@app.post("/admin/policy/reload")`
- [ ] Requires API key via `Depends(require_api_key)`
- [ ] Returns JSON with `status` field (either "success" or "error")
- [ ] Success response includes `policy_version` and `thresholds`
- [ ] Error cases return appropriate HTTP status codes (404 for not found, 500 for exception)

---

### Task 3: Enhance PolicyTrace to Track Generation Suppression

**Action:**
Add a `generation_skipped` field to the `PolicyTrace` dataclass in [shared/telemetry.py](shared/telemetry.py). This explicitly tracks when the abstain path bypasses answer generation.

**Steps:**

1. Open [shared/telemetry.py](shared/telemetry.py) and locate the `PolicyTrace` dataclass (typically near the top, after imports)

2. Find the line with `abstention_triggered: bool` (should be around line 50–60)

3. Add a new field immediately after it:
   ```python
   abstention_triggered: bool  # Existing field
   generation_skipped: bool = False  # NEW: True when answer generation was bypassed (abstain path)
   ```

4. Verify the dataclass is correctly formatted with all fields having defaults (e.g., `field_name: type = default_value`)

**Verification:**
- [ ] Field is added to the dataclass with a boolean type
- [ ] Default value is `False` (most requests do generate answers)
- [ ] Field is positioned logically near other routing/execution fields

---

### Task 4: Implement CI Test-Mode Header Handling for Confidence Override

**Action:**
Add middleware or validation logic in [api/app.py](api/app.py) in the RAG handler to recognize and apply confidence score overrides via HTTP headers. This enables deterministic testing without live calibration.

**Locked Decision Contract:**
- Header `X-CI-Test-Mode: true` enables override recognition
- Header `X-CI-Override-Confidence: <float>` (e.g., "0.87") specifies the override value
- Optional: `X-CI-Override-Band: high|medium|low|insufficient` for convenience (implemented only if worth it)
- Override is applied **after** confidence scoring but **before** routing decision
- In normal mode (no test header or test mode disabled), ignore override headers silently
- Telemetry captures override information for audit trail

**Steps:**

1. Open [api/app.py](api/app.py) and find the `_rag_hybrid()` function (around line 1053)

2. Locate the section where confidence score is computed (typically after retrieval and before routing). Add this code block right after confidence is calculated but before routing:

   ```python
   # Extract test-mode headers for CI verification
   test_mode_enabled = request.headers.get('X-CI-Test-Mode', '').lower() == 'true'
   override_confidence_str = request.headers.get('X-CI-Override-Confidence')
   
   confidence_override_applied = False
   override_value = None
   
   if test_mode_enabled and override_confidence_str:
       try:
           override_value = float(override_confidence_str)
           # Override the computed confidence with test value
           confidence_score = override_value
           confidence_override_applied = True
           logger.debug(
               f"Confidence override applied for query {trace.query_id}: "
               f"{confidence_score} (test mode)"
           )
       except ValueError:
           logger.warning(
               f"Invalid X-CI-Override-Confidence header value: {override_confidence_str}"
           )
   elif test_mode_enabled and override_confidence_str is None:
       # Test mode is enabled but no override provided - just log it
       logger.debug(f"Test mode enabled for query {trace.query_id}, no override provided")
   elif override_confidence_str and not test_mode_enabled:
       # Override header provided but test mode not enabled - ignore silently
       logger.debug(f"Override header ignored (test mode not enabled)")
   
   # Always add telemetry for audit trail
   if trace.metadata is None:
       trace.metadata = {}
   
   trace.metadata['confidence_override'] = {
       'applied': confidence_override_applied,
       'source': 'header' if confidence_override_applied else 'computed',
       'override_value': override_value,  # What was requested (null if not applied)
       'final_value': confidence_score   # What was actually used
   }
   ```

3. (Optional) If implementing `X-CI-Override-Band` for convenience, add this helper after the above block:

   ```python
   # Optional: Support band-based override for cleaner test code
   override_band = request.headers.get('X-CI-Override-Band', '').lower()
   if test_mode_enabled and override_band and not override_confidence_str:
       # Map band to a representative confidence value
       band_confidence_map = {
           'high': 0.87,
           'medium': 0.65,
           'low': 0.40,
           'insufficient': 0.15
       }
       if override_band in band_confidence_map:
           confidence_score = band_confidence_map[override_band]
           confidence_override_applied = True
           override_value = confidence_score
           trace.metadata['confidence_override']['applied'] = True
           trace.metadata['confidence_override']['override_value'] = override_value
           trace.metadata['confidence_override']['final_value'] = confidence_score
           logger.debug(f"Band-based override: {override_band} -> {confidence_score}")
   ```

4. Verify the imports at the top of the function:
   ```python
   from starlette.requests import Request  # Should already be present
   ```

**Verification:**
- [ ] Test-mode check recognizes `X-CI-Test-Mode: true` header
- [ ] Override confidence is converted from string to float and validated
- [ ] Override is applied after confidence scoring
- [ ] Telemetry includes `confidence_override` metadata with all required fields
- [ ] Invalid override values are logged but don't crash
- [ ] Override headers are ignored if test mode is not enabled
- [ ] Normal requests (no test headers) log `confidence_override_applied: false`

---

### Task 5: Populate Stage Flags in Policy Trace Metadata

**Action:**
Modify the trace logging logic in [api/app.py](api/app.py) to populate the `stage_flags` metadata with information about which routing stages (retrieval expansion, reranking, generation) were executed or skipped. This is the primary assertion source for CI tests.

**Steps:**

1. Open [api/app.py](api/app.py) and find the RAG handler function `_rag_hybrid()` (around line 1053)

2. Locate the section where the trace is populated after routing decisions are made (typically after calling `router.route_with_confidence()`)

3. After the line that sets `trace.execution_path`, add code to populate stage_flags. Find the `RouteDecision` object (named something like `route_decision` or `path_decision`) and after it's processed, add:

   ```python
   # Populate stage flags for trace audit trail
   if trace.metadata is None:
       trace.metadata = {}
   
   trace.metadata['stage_flags'] = {
       'retrieval_expanded': trace.metadata.get('retrieval_expanded', False),
       'reranker_invoked': trace.reranker_invoked,
       'generation_skipped': trace.generation_skipped
   }
   trace.metadata['routing_action'] = route_decision.action
   ```

4. Locate the abstain path code (where the response is `RAG_ABSTAIN_RESPONSE` or similar). At that point, set:
   ```python
   trace.generation_skipped = True
   trace.metadata['stage_flags']['generation_skipped'] = True
   ```

5. Locate the fast path code (where reranking is skipped). Add logging:
   ```python
   # Fast path: skip reranking and depth expansion
   logger.debug(f"Fast path execution for query {trace.query_id}: skipping rerank and expansion")
   trace.metadata['stage_flags']['reranker_invoked'] = False
   trace.metadata['stage_flags']['retrieval_expanded'] = False
   ```

6. Locate the cautious path code. Ensure it sets:
   ```python
   trace.metadata['stage_flags']['reranker_invoked'] = True
   trace.metadata['stage_flags']['retrieval_expanded'] = True
   ```

**Verification:**
- [ ] Fast path traces include `stage_flags` with both expansion and reranking set to False
- [ ] Cautious path traces include `stage_flags` with both expansion and reranking set to True
- [ ] Abstain path traces include `generation_skipped: True` and appropriate stage_flags
- [ ] Stage flags are always present in `trace.metadata` (not missing/None)

---

### Task 6: Clean Up Dead Code in PolicyRepository

**Action:**
Remove unreachable code in [shared/database.py](shared/database.py#L540). The `get_route_distribution()` method has a dead return statement after the main return.

**Steps:**

1. Open [shared/database.py](shared/database.py) and find the `get_route_distribution()` method (around line 530)

2. Locate the two return statements:
   ```python
   return [dict(r) for r in rows]
   return str(result['query_id'])  # ❌ This line is unreachable
   ```

3. Delete the second (unreachable) return line. The method should end with:
   ```python
   return [dict(r) for r in rows]
   ```

4. Verify the indentation is correct

**Verification:**
- [ ] Only one return statement remains in `get_route_distribution()`
- [ ] No syntax errors introduced

---

## Verification & Sign-Off

After all tasks complete, run these verification steps:

### Manual Verification

1. **PolicyRepository methods work:**
   ```bash
   # Start Python REPL in project venv
   python
   >>> import asyncio
   >>> from shared.database import PolicyRepository
   >>> repo = PolicyRepository(...)
   >>> asyncio.run(repo.create_policy("v99-test", {"high_min": 0.80}))
   True
   >>> asyncio.run(repo.set_active_policy("v99-test"))
   True
   ```

2. **Reload endpoint accessible:**
   ```bash
   curl -X POST http://localhost:8001/admin/policy/reload \
     -H "X-API-Key: change-me-long-random" \
     -H "Content-Type: application/json"
   # Should return: {"status": "success", "policy_version": "...", ...}
   ```

3. **Header-based override works:**
   ```bash
   curl -X POST http://localhost:8001/rag \
     -H "Content-Type: application/json" \
     -H "X-CI-Test-Mode: true" \
     -H "X-CI-Override-Confidence: 0.87" \
     -d '{"question": "What is X?"}'
   
   # Query the trace table
   psql $DATABASE_URL -c "SELECT metadata FROM policy_telemetry ORDER BY created_at DESC LIMIT 1; \`
   # Should include: "confidence_override": {"applied": true, "source": "header", "override_value": 0.87, "final_value": 0.87}
   ```

4. **Trace logging includes stage_flags:**
   ```bash
   # Call /rag endpoint
   curl -X POST http://localhost:8001/rag \
     -H "Content-Type: application/json" \
     -d '{"question": "What is X?"}'
   
   # Query the trace table
   psql $DATABASE_URL -c "SELECT metadata FROM policy_telemetry ORDER BY created_at DESC LIMIT 1; \`
   # Should include: "stage_flags": {"retrieval_expanded": ..., "reranker_invoked": ..., "generation_skipped": ...}
   # Should also include: "confidence_override": {"applied": boolean, "source": "header" | "computed"}
   ```

### Automated Verification (if running CI)

```bash
make test -- -k "test_policy" -v
```

Expected: No new failures; existing policy tests still pass.

---

## Must-Haves Delivered by This Plan

1. ✅ Policy versions can be created and activated programmatically
2. ✅ Active policy can be reloaded mid-test without server restart
3. ✅ Header-based confidence override is recognized and applied correctly
4. ✅ Confidence override telemetry is logged for audit trail
5. ✅ All routing decisions populate stage flags for audit trail
6. ✅ Abstention path explicitly signals generation was skipped
7. ✅ Fast path explicitly signals reranking and expansion were skipped
8. ✅ Override headers are ignored unless test mode is enabled

---

*Plan 1 of 3 for Phase 3 CI Verification*
