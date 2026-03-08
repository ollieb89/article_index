---
phase: 03
title: CI Verification
---

# Phase 3 Plan: CI Verification

**Created:** 2026-03-08  
**Status:** Ready for execution  

---

## Overview

Phase 3 converts the control loop confidence bands and routing logic from code proof-of-concept into **verified, automated CI tests**. The goal is to demonstrate that different confidence scores reliably produce different execution paths, and that policy updates are consumed by the routing layer without manual intervention.

**Inputs:**
- Phase 2 confidence-driven control loop (fast/standard/cautious/abstain paths)
- ContextualRouter with confidence band calculation
- Policy registry database table
- Telemetry instrumentation

**Outputs:**
- Pytest fixtures for deterministic CI testing (header-based confidence overrides)
- Test matrix covering all 4 execution paths and boundary transitions
- Policy reload mechanism and end-to-end verification
- CTRL-05 and CTRL-06 requirements fully satisfied by automated tests

---

## Task Breakdown

### Wave 1: Infrastructure & Fixtures

**Goal:** Build the testing foundation that enables deterministic CI verification.

#### Plan 1: Policy Infrastructure

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

**Files Modified:**
- shared/database.py
- shared/telemetry.py
- api/app.py

**Tasks for Plan 1:**

##### Task 1.1: Add PolicyRepository Methods for Policy Management

Add to `PolicyRepository` class in [shared/database.py](shared/database.py#L430):

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
        async with self.pool.acquire() as conn:
            await conn.execute("BEGIN")
            
            # Deactivate all policies
            await conn.execute(
                "UPDATE intelligence.policy_registry SET is_active = false, updated_at = NOW()"
            )
            
            # Activate target policy
            result = await conn.execute(
                text("UPDATE intelligence.policy_registry SET is_active = true, updated_at = NOW() WHERE version = :version"),
                {"version": version}
            )
            
            await conn.execute("COMMIT")
            
        logger.info(f"Activated policy version: {version}")
        return True
    except Exception as e:
        logger.error(f"Failed to activate policy {version}: {e}")
        return False
```

**Verification:**
- [ ] Both methods are async and return bool
- [ ] `create_policy()` serializes thresholds dict to JSON for storage
- [ ] `set_active_policy()` atomically deactivates all, then activates one
- [ ] Error handling with logging

---

##### Task 1.2: Add `/admin/policy/reload` Endpoint

Add to [api/app.py](api/app.py):

```python
@app.post("/admin/policy/reload", tags=["admin"])
async def reload_policy_from_db():
    """
    Reload the active policy from database into app.state.
    
    Called after calibration script updates the policy registry.
    Allows new thresholds to take effect without server restart.
    
    Returns:
        {
            "status": "ok",
            "reloaded_version": "v42-calibrated",
            "thresholds": {...}
        }
    """
    try:
        policy_repo = PolicyRepository(app.state.db_pool)
        active_policy = await policy_repo.get_active_policy()
        
        if not active_policy:
            raise ValueError("No active policy found in registry")
        
        # Load policy into app.state
        app.state.active_policy = active_policy
        
        logger.info(f"Policy reloaded: version={active_policy.get('version')}")
        
        return {
            "status": "ok",
            "reloaded_version": active_policy.get("version"),
            "thresholds": active_policy.get("thresholds", {})
        }
    except Exception as e:
        logger.error(f"Policy reload failed: {e}")
        return {"status": "error", "message": str(e)}, 500
```

**Verification:**
- [ ] Endpoint queries active policy from DB
- [ ] Loads policy into app.state for immediate use
- [ ] Returns version + thresholds in response
- [ ] Error handling with logging

---

##### Task 1.3: Add CI Test-Mode Header Handling

Update [api/app.py](api/app.py) in the `/rag` endpoint to detect and apply confidence overrides from CI headers:

Before the confidence scoring step, add:

```python
# CI override handling (for deterministic testing)
ci_confidence_override = None
if request.headers.get("X-CI-Test-Mode") == "true":
    override_header = request.headers.get("X-CI-Override-Confidence")
    if override_header:
        try:
            ci_confidence_override = float(override_header)
            logger.debug(f"CI confidence override applied: {ci_confidence_override}")
        except ValueError:
            logger.warning(f"Invalid CI confidence override header: {override_header}")

# Score confidence (or use override)
if ci_confidence_override is not None:
    confidence_score = ci_confidence_override
    confidence_band = score_confidence_band(confidence_score)
else:
    confidence_score = ev_scorer.score_evidence(context_chunks)
    confidence_band = confidence_score.get("band", "medium")
    confidence_score = confidence_score.get("score", 0.5)
```

**Verification:**
- [ ] Reads X-CI-Test-Mode and X-CI-Override-Confidence headers
- [ ] Override applied after retrieval, before routing
- [ ] Logs override for audit trail
- [ ] Falls back to normal scoring if override not present

---

##### Task 1.4: Update Telemetry to Capture stage_flags and confidence_override

Update [shared/telemetry.py](shared/telemetry.py) to log additional metadata:

```python
async def log_routing_decision(
    query_id: str,
    query_text: str,
    query_type: str,
    confidence_score: float,
    confidence_band: str,
    execution_path: str,
    action_taken: str,
    policy_version: str,
    metadata: Dict[str, Any] = None,
    stage_flags: Dict[str, bool] = None,
    ci_confidence_override: float = None
):
    """
    Log a routing decision to policy telemetry table.
    
    Args:
        query_id: UUID for the request
        query_text: The original question
        query_type: "exact_fact", "opinion", etc.
        confidence_score: Computed or overridden confidence [0.0-1.0]
        confidence_band: "high", "medium", "low", or "insufficient"
        execution_path: "fast", "standard", "cautious", or "abstain"
        action_taken: What the router did (e.g., "expanded_retrieval", "reranked", "abstained")
        policy_version: The active policy version
        metadata: Optional JSONB metadata dict
        stage_flags: Dict with keys like {"reranker_invoked": True, "retrieval_expanded": False, ...}
        ci_confidence_override: If confidence was overridden by CI header, store it for audit
    """
    if metadata is None:
        metadata = {}
    
    # Add stage flags to metadata
    if stage_flags:
        metadata["stage_flags"] = stage_flags
    
    # Add CI override flag if present
    if ci_confidence_override is not None:
        metadata["ci_confidence_override"] = ci_confidence_override
    
    try:
        query = """
            INSERT INTO intelligence.policy_telemetry
            (query_id, query_text, query_type, confidence_score, confidence_band, 
             execution_path, action_taken, policy_version, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
        """
        
        await db_pool.execute(
            query,
            query_id,
            query_text,
            query_type,
            confidence_score,
            confidence_band,
            execution_path,
            action_taken,
            policy_version,
            json.dumps(metadata)
        )
    except Exception as e:
        logger.error(f"Failed to log routing decision for query {query_id}: {e}")
```

**Verification:**
- [ ] Accepts stage_flags dict and stores in metadata JSONB
- [ ] Captures ci_confidence_override for audit trail
- [ ] All 8+ fields logged for each routing decision
- [ ] Error handling with logging

---

#### Plan 2: Test Fixtures & Harness

**Objective:** Build the test infrastructure that enables deterministic CI verification using header-based confidence overrides. Create pytest fixtures for synthetic retrieval data, seed test policies into the database, provide helper functions for trace assertion, and define helper function for making CI test requests with confidence overrides.

**Requirements Covered:**
- Supporting infrastructure for CTRL-05: CI tests can inject confidence scores via headers and verify routing behavior
- Supporting infrastructure for CTRL-06: CI can reload policies and verify threshold changes

**Files Modified:**
- tests/conftest.py
- tests/test_control_loop_ci.py (will create)

**Tasks for Plan 2:**

##### Task 2.1: Create Test Policy Seed Fixture

Add to [tests/conftest.py](tests/conftest.py):

```python
@pytest.fixture
async def policy_seed(db_session):
    """
    Seed test database with three policy versions using different confidence thresholds.
    Used by all CI tests to verify threshold-driven routing behavior.
    
    Returns: dict with keys 'lenient', 'baseline', 'strict'
    Each value is the policy version string (e.g., 'test-v1-lenient')
    """
    policy_repo = PolicyRepository(db_session.pool)
    
    policies = {}
    
    # Policy 1: Lenient thresholds (favor fast path)
    policies['lenient'] = 'test-v1-lenient'
    await policy_repo.create_policy(
        version='test-v1-lenient',
        thresholds={
            'high_min': 0.70,
            'medium_min': 0.45,
            'low_min': 0.25,
            'insufficient_max': 0.25
        }
    )
    
    # Policy 2: Baseline thresholds (default operational mode)
    policies['baseline'] = 'test-v2-baseline'
    await policy_repo.create_policy(
        version='test-v2-baseline',
        thresholds={
            'high_min': 0.85,
            'medium_min': 0.60,
            'low_min': 0.35,
            'insufficient_max': 0.35
        }
    )
    
    # Policy 3: Strict thresholds (favor cautious path)
    policies['strict'] = 'test-v3-strict'
    await policy_repo.create_policy(
        version='test-v3-strict',
        thresholds={
            'high_min': 0.95,
            'medium_min': 0.75,
            'low_min': 0.50,
            'insufficient_max': 0.50
        }
    )
    
    yield policies
    
    # Cleanup: Delete test policies after test
    await db_session.execute(
        text("DELETE FROM intelligence.policy_registry WHERE version IN ('test-v1-lenient', 'test-v2-baseline', 'test-v3-strict')")
    )
```

---

##### Task 2.2: Create CI Header Helper Function

Add to [tests/conftest.py](tests/conftest.py):

```python
def make_ci_headers(confidence: float = None, band: str = None) -> dict:
    """
    Generate HTTP headers for CI test requests with optional confidence override.
    
    Use this to make /rag calls that override confidence scores for testing different routing paths.
    
    Args:
        confidence: Float value [0.0-1.0] to override computed confidence.
                   Example: 0.87 for high confidence, 0.35 for low confidence
        band: Optional band name 'high'|'medium'|'low'|'insufficient' as convenience.
              If provided, overrides confidence with representative value.
    
    Returns:
        dict with headers:
        - X-CI-Test-Mode: 'true' (enables override recognition)
        - X-CI-Override-Confidence: '<float>' (if confidence provided)
    
    Example:
        headers = make_ci_headers(confidence=0.87)
        response = await client.post("/rag", json=query, headers=headers)
    """
    headers = {'X-CI-Test-Mode': 'true'}
    
    if confidence is not None:
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {confidence}")
        headers['X-CI-Override-Confidence'] = str(confidence)
    elif band:
        band_map = {
            'high': '0.87',
            'medium': '0.65',
            'low': '0.40',
            'insufficient': '0.15'
        }
        if band not in band_map:
            raise ValueError(f"Band must be one of {list(band_map.keys())}, got {band}")
        headers['X-CI-Override-Confidence'] = band_map[band]
    
    return headers
```

---

##### Task 2.3: Create Trace Query and Assertion Helpers

Add to [tests/conftest.py](tests/conftest.py):

```python
async def query_latest_trace(db_session, query_id: str):
    """
    Query the policy_telemetry table for the most recent trace with the given query_id.
    
    Returns:
        dict with trace fields or None if not found
    """
    result = await db_session.execute(
        text("""
            SELECT 
                query_id, 
                query_text, 
                query_type, 
                confidence_score, 
                confidence_band,
                execution_path,
                action_taken,
                policy_version,
                metadata,
                created_at
            FROM intelligence.policy_telemetry
            WHERE query_id = :query_id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"query_id": query_id}
    )
    row = result.first()
    if not row:
        return None
    
    trace = dict(row._mapping)
    if trace['metadata']:
        trace['metadata'] = json.loads(trace['metadata']) if isinstance(trace['metadata'], str) else trace['metadata']
    else:
        trace['metadata'] = {}
    
    return trace

def assert_execution_path(trace: dict, expected_path: str):
    """Assert that the trace execution_path matches expected path."""
    actual_path = trace.get('execution_path')
    assert actual_path == expected_path, \
        f"Expected execution_path={expected_path}, got {actual_path}. " \
        f"Confidence: {trace.get('confidence_score')} | Band: {trace.get('confidence_band')}"

def assert_confidence_band(trace: dict, expected_band: str):
    """Assert that the trace confidence_band matches expected band."""
    actual_band = trace.get('confidence_band')
    assert actual_band == expected_band, \
        f"Expected confidence_band={expected_band}, got {actual_band}. " \
        f"Confidence score: {trace.get('confidence_score')}"

def assert_stage_flags(trace: dict, expected_flags: dict):
    """Assert that the trace stage_flags match expected values."""
    metadata = trace.get('metadata', {})
    actual_flags = metadata.get('stage_flags', {})
    
    for flag_name, expected_value in expected_flags.items():
        actual_value = actual_flags.get(flag_name)
        assert actual_value == expected_value, \
            f"Stage flag mismatch: {flag_name}. Expected {expected_value}, got {actual_value}."

@pytest.fixture
def trace_assertions(db_session):
    """Provide assertion helpers for trace validation."""
    return {
        'query': lambda qid: query_latest_trace(db_session, qid),
        'assert_path': assert_execution_path,
        'assert_band': assert_confidence_band,
        'assert_flags': assert_stage_flags,
    }
```

---

##### Task 2.4: Create Synthetic Retrieval Fixture

Add to [tests/conftest.py](tests/conftest.py):

```python
@pytest.fixture
async def routing_fixture_data():
    """
    Provide deterministic synthetic retrieval data for RAG CI tests.
    
    Returns:
        dict with scenarios: high_confidence_query, medium_confidence_query, 
        low_confidence_query, no_retrieval_query
    """
    return {
        'high_confidence_query': {
            'query_text': 'When was the Python programming language first released?',
            'query_type': 'exact_fact',
            'retrieval_chunks': [
                {'text': 'Python was first released in 1991 by Guido van Rossum.', 'similarity': 0.96, 'source': 'official_history.md'},
                {'text': 'Python 0.9.0 was published on February 20, 1991.', 'similarity': 0.93, 'source': 'release_timeline.md'},
            ],
            'expected_confidence_band': 'high'
        },
        'medium_confidence_query': {
            'query_text': 'What are the main benefits of using Python?',
            'query_type': 'explanation',
            'retrieval_chunks': [
                {'text': 'Python is known for its readability and simplicity.', 'similarity': 0.72, 'source': 'tutorial.md'},
                {'text': 'Python has extensive built-in libraries for many use cases.', 'similarity': 0.68, 'source': 'overview.md'},
            ],
            'expected_confidence_band': 'medium'
        },
        'low_confidence_query': {
            'query_text': 'What was the weather in Tokyo on March 15, 1850?',
            'query_type': 'exact_fact',
            'retrieval_chunks': [
                {'text': 'Historical weather records from the 1800s are scarce and incomplete.', 'similarity': 0.42, 'source': 'historical_notes.md'},
            ],
            'expected_confidence_band': 'low'
        },
        'no_retrieval_query': {
            'query_text': 'What is the airspeed velocity of an unladen swallow?',
            'query_type': 'nonsense',
            'retrieval_chunks': [],
            'expected_confidence_band': 'insufficient'
        }
    }
```

---

### Wave 2: Tests

**Goal:** Implement the full test matrix that verifies all four execution paths.

#### Plan 3: CI Test Suite

**Objective:** Implement the full test matrix that verifies all four execution paths (fast, standard, cautious, abstain) using header-based confidence overrides. Prove confidence band routing correctness under different policy configurations. This plan delivers CTRL-05 and CTRL-06 requirements as executable, verifiable CI tests.

**Requirements Covered:**
- CTRL-05: Behavior changes verified in CI per confidence band (via header-based overrides)
- CTRL-06: Calibration produces threshold updates consumed by routing without manual steps

**Files Modified:**
- tests/test_control_loop_ci.py (create new)

**Tasks for Plan 3:**

Tests will verify:
1. High confidence (0.87) → fast path execution
2. Medium confidence (0.65) → standard path with selective reranking
3. Low confidence (0.40) → cautious path with hedged output
4. Insufficient confidence (0.15) → abstain path with explicit abstention
5. Boundary conditions (just above/below threshold transitions)
6. Policy reload picks up new thresholds without restart

Detailed test implementations will be provided in the full task breakdown once infrastructure (Plans 1-2) are complete.

---

## Wave Summary

| Wave | Plans | Dependency | Deliverables |
|------|-------|-----------|--------------|
| 1 | Infrastructure + Fixtures | None | PolicyRepository methods, reload endpoint, CI headers, telemetry logging, test fixtures, assertion helpers |
| 2 | Tests | Plans 1-2 | Full test matrix (4 paths × 6 boundaries × policy reload) |

---

## Verification & Sign-Off

**Phase Complete When:**
1. All PolicyRepository methods work (create_policy, set_active_policy)
2. `/admin/policy/reload` endpoint successfully reloads policy from DB
3. CI header overrides (X-CI-Test-Mode, X-CI-Override-Confidence) applied correctly
4. Telemetry captures stage_flags and ci_confidence_override
5. All 4 test fixtures provide data without errors
6. All 11+ test cases pass (execution paths, boundaries, reload)
7. CTRL-05 and CTRL-06 requirements fully verified by CI

---

*Phase 3 Consolidated Plan: CI Verification*
*Created: 2026-03-08*

