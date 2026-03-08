---
wave: 1
depends_on:
  - 03-PLAN-infrastructure.md
files_modified:
  - tests/conftest.py
  - tests/test_control_loop_ci.py
autonomous: false
---

# Phase 3 Plan 2: Test Fixtures & Harness

**Objective:** Build the test infrastructure that enables deterministic CI verification using header-based confidence overrides. Create pytest fixtures for synthetic retrieval data, seed test policies into the database, provide helper functions for trace assertion, and define helper function for making CI test requests with confidence overrides.

**Requirements Covered:**
- Supporting infrastructure for CTRL-05: CI tests can inject confidence scores via headers and verify routing behavior
- Supporting infrastructure for CTRL-06: CI can reload policies and verify threshold changes

**Success Criteria:**
- [ ] `policy_seed` pytest fixture defines 3 test policies (lenient, baseline, strict)
- [ ] `make_ci_headers()` helper function generates headers for confidence override requests
- [ ] `routing_fixture_data` provides deterministic synthetic retrieval data
- [ ] `assert_execution_path()` helper queries DB trace and validates execution path matches expected
- [ ] `assert_stage_flags()` helper validates stage_flags metadata in trace
- [ ] All fixtures are reusable across multiple test functions (no per-test mutation)
- [ ] No monkeypatch or mock-retrieval code in test utilities
- [ ] Header-based override is the only mechanism for forcing confidence scores

**Effort:** ~120 lines of test code and fixtures. No changes to production code.

---

## Tasks

### Task 1: Create Test Policy Seed Fixture

**Action:**
Add a pytest fixture to [tests/conftest.py](tests/conftest.py) that seeds three test policies with different confidence thresholds. This fixture provides clean policy data for all CI tests.

**Steps:**

1. Open [tests/conftest.py](tests/conftest.py)

2. Locate or add the import section at the top:
   ```python
   import pytest
   import json
   from datetime import datetime
   from sqlalchemy import text
   from shared.database import PolicyRepository
   from shared.policy import RAGPolicy
   ```

3. Add the following fixture at the end of conftest.py:

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

**Verification:**
- [ ] Fixture is defined as async and accepts db_session parameter
- [ ] All three policies created with clear threshold ordering (lenient > baseline > strict min thresholds)
- [ ] Cleanup removes all test policies after test completes
- [ ] Fixture returns dict with keys matching actual policy versions strings

---

### Task 2: Create CI Header Helper Function

**Action:**
Add a helper function to [tests/conftest.py](tests/conftest.py) that generates HTTP headers for CI test requests with optional confidence override. This replaces all monkeypatch approaches with header-based overrides.

**Steps:**

1. Open [tests/conftest.py](tests/conftest.py) and add:

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
           - (other CI metadata headers as needed)
       
       Example usage in test:
           headers = make_ci_headers(confidence=0.87)
           response = await client.post("/rag", json=query, headers=headers)
       """
       headers = {'X-CI-Test-Mode': 'true'}
       
       if confidence is not None:
           # Validate confidence score range
           if not (0.0 <= confidence <= 1.0):
               raise ValueError(f"Confidence must be between 0.0 and 1.0, got {confidence}")
           headers['X-CI-Override-Confidence'] = str(confidence)
       
       elif band:
           # Map band to representative confidence value
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

**Verification:**
- [ ] Function is a pure utility (no async, no fixtures)
- [ ] Validates confidence range [0.0-1.0]
- [ ] Generates valid header dict with X-CI-Test-Mode and optional X-CI-Override-Confidence
- [ ] Can be imported and called directly in tests
- [ ] Documentation includes usage example

---

### Task 3: Create Trace Query and Assertion Helpers

**Action:**
Add helper functions to [tests/conftest.py](tests/conftest.py) that query the policy telemetry table and assert expected routing decisions. Tests will use these to verify execution paths match expected confidence bands.

**Steps:**

1. Open [tests/conftest.py](tests/conftest.py) and add:

   ```python
   async def query_latest_trace(db_session, query_id: str):
       """
       Query the policy_telemetry table for the most recent trace with the given query_id.
       
       Args:
           db_session: SQLAlchemy async session
           query_id: UUID from the RAG request
       
       Returns:
           dict with trace fields (execution_path, confidence_band, stage_flags, etc.)
           or None if not found
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
       
       # Convert to dict
       trace = dict(row._mapping)
       
       # Parse metadata JSONB - handle both string and dict formats
       if trace['metadata']:
           trace['metadata'] = json.loads(trace['metadata']) if isinstance(trace['metadata'], str) else trace['metadata']
       else:
           trace['metadata'] = {}
       
       return trace
   
   
   def assert_execution_path(trace: dict, expected_path: str):
       """
       Assert that the trace execution_path matches the expected path.
       
       Args:
           trace: dict returned by query_latest_trace()
           expected_path: 'fast', 'standard', 'cautious', or 'abstain'
       
       Raises:
           AssertionError if path does not match
       """
       actual_path = trace.get('execution_path')
       assert actual_path == expected_path, \
           f"Expected execution_path={expected_path}, got {actual_path}. " \
           f"Query: {trace.get('query_text')} | Confidence: {trace.get('confidence_score')} | Band: {trace.get('confidence_band')}"
   
   
   def assert_confidence_band(trace: dict, expected_band: str):
       """
       Assert that the trace confidence_band matches the expected band.
       
       Args:
           trace: dict returned by query_latest_trace()
           expected_band: 'high', 'medium', 'low', or 'insufficient'
       
       Raises:
           AssertionError if band does not match
       """
       actual_band = trace.get('confidence_band')
       assert actual_band == expected_band, \
           f"Expected confidence_band={expected_band}, got {actual_band}. " \
           f"Confidence score: {trace.get('confidence_score')} | Path: {trace.get('execution_path')}"
   
   
   def assert_stage_flags(trace: dict, expected_flags: dict):
       """
       Assert that the trace stage_flags match expected values.
       
       Args:
           trace: dict returned by query_latest_trace()
           expected_flags: dict like {
               'reranker_invoked': False,
               'retrieval_expanded': False,
               'generation_skipped': False
           }
       
       Raises:
           AssertionError if any flag does not match
       """
       metadata = trace.get('metadata', {})
       actual_flags = metadata.get('stage_flags', {})
       
       for flag_name, expected_value in expected_flags.items():
           actual_value = actual_flags.get(flag_name)
           assert actual_value == expected_value, \
               f"Stage flag mismatch: {flag_name}. " \
               f"Expected {expected_value}, got {actual_value}. " \
               f"Trace metadata: {metadata}"
   
   
   def assert_response_status(response: dict, expected_status: str):
       """
       Assert that the RAG response status matches expected status.
       
       Args:
           response: dict from /rag or /rag/hybrid endpoint
           expected_status: 'ok', 'insufficient_evidence', etc.
       
       Raises:
           AssertionError if status does not match
       """
       actual_status = response.get('status', 'unknown')
       assert actual_status == expected_status, \
           f"Expected response status={expected_status}, got {actual_status}. Response: {response}"
   ```

2. Add a pytest fixture that wraps these helpers for easy use in tests:

   ```python
   @pytest.fixture
   def trace_assertions(db_session):
       """
       Provide assertion helpers for trace validation.
       Bundles query_latest_trace() and assertion functions.
       
       Returns:
           dict with keys 'query', 'assert_path', 'assert_band', 'assert_flags', 'assert_status'
       """
       return {
           'query': lambda qid: query_latest_trace(db_session, qid),
           'assert_path': assert_execution_path,
           'assert_band': assert_confidence_band,
           'assert_flags': assert_stage_flags,
           'assert_status': assert_response_status
       }
   ```

**Verification:**
- [ ] Helper functions use proper async/await for DB queries
- [ ] Assertion functions provide clear error messages showing context (confidence, band, path)
- [ ] JSONB metadata parsing handles both string and dict types
- [ ] All helpers work with the existing policy_telemetry schema
- [ ] No mocking or monkeypatch used in assertion code
       actual_path = trace.get('execution_path')
       assert actual_path == expected_path, \
           f"Expected execution_path={expected_path}, got {actual_path}. " \
           f"Query: {trace.get('query_text')} | Confidence: {trace.get('confidence_score')} | Band: {trace.get('confidence_band')}"
   
   
   def assert_confidence_band(trace: dict, expected_band: str):
       """
       Assert that the trace confidence_band matches the expected band.
       
       Args:
           trace: dict returned by query_latest_trace()
           expected_band: 'high', 'medium', 'low', or 'insufficient'
       
       Raises:
           AssertionError if band does not match
       """
       actual_band = trace.get('confidence_band')
       assert actual_band == expected_band, \
           f"Expected confidence_band={expected_band}, got {actual_band}. " \
           f"Confidence score: {trace.get('confidence_score')} | Path: {trace.get('execution_path')}"
   
   
   def assert_stage_flags(trace: dict, expected_flags: dict):
       """
       Assert that the trace stage_flags match expected values.
       
       Args:
           trace: dict returned by query_latest_trace()
           expected_flags: dict like {
               'reranker_invoked': False,
               'retrieval_expanded': False,
               'generation_skipped': False
           }
       
       Raises:
           AssertionError if any flag does not match
       """
       metadata = trace.get('metadata', {})
       actual_flags = metadata.get('stage_flags', {})
       
       for flag_name, expected_value in expected_flags.items():
           actual_value = actual_flags.get(flag_name)
           assert actual_value == expected_value, \
               f"Stage flag mismatch: {flag_name}. " \
               f"Expected {expected_value}, got {actual_value}. " \
               f"Trace metadata: {metadata}"
   
   
   def assert_response_status(response: dict, expected_status: str):
       """
       Assert that the RAG response status matches expected status.
       
       Args:
           response: dict from /rag or /rag/hybrid endpoint
           expected_status: 'ok', 'insufficient_evidence', etc.
       
       Raises:
           AssertionError if status does not match
       """
       actual_status = response.get('status', 'unknown')
       assert actual_status == expected_status, \
           f"Expected response status={expected_status}, got {actual_status}. Response: {response}"
   ```

2. Add a pytest fixture that wraps these helpers for easy use in tests:

   ```python
   @pytest.fixture
   def trace_assertions(db_session):
       """
       Provide assertion helpers for trace validation.
       Bundles query_latest_trace() and assertion functions.
       
       Returns:
           dict with keys 'query', 'assert_path', 'assert_band', 'assert_flags', 'assert_status'
       """
       return {
           'query': lambda qid: query_latest_trace(db_session, qid),
           'assert_path': assert_execution_path,
           'assert_band': assert_confidence_band,
           'assert_flags': assert_stage_flags,
           'assert_status': assert_response_status
       }
   ```

**Verification:**
- [ ] Helper functions use proper async/await for DB queries
- [ ] Assertion functions provide clear error messages on failure
- [ ] JSONB metadata parsing handles both string and dict types
- [ ] All helpers work with the existing policy_telemetry schema

---

---

### Task 4: Create Synthetic Retrieval Fixture for Testing

**Action:**
Add a fixture to [tests/conftest.py](tests/conftest.py) that provides deterministic synthetic retrieval data for testing. This fixture creates query and retrieval chunk combinations without requiring live retrieval or OpenAI access.

**Steps:**

1. Open [tests/conftest.py](tests/conftest.py) and add:

   ```python
   @pytest.fixture
   async def routing_fixture_data():
       """
       Provide deterministic synthetic retrieval data for RAG CI tests.
       This simulates retrieval results without hitting live services.
       
       Returns:
           dict with keys for different test scenarios:
           - high_confidence_query: Query + chunks that would score high confidence
           - medium_confidence_query: Query + chunks that would score medium confidence
           - low_confidence_query: Query + chunks that would score low confidence
           - no_retrieval_query: Query with empty retrieval (for abstain path)
       
       Each entry contains:
           - query_text: The original question
           - query_type: "exact_fact", "opinion", etc.
           - retrieval_chunks: [{text, similarity, source}, ...]
           - expected_confidence_band: "high" | "medium" | "low" | "insufficient" (for reference)
       """
       return {
           'high_confidence_query': {
               'query_text': 'When was the Python programming language first released?',
               'query_type': 'exact_fact',
               'retrieval_chunks': [
                   {
                       'text': 'Python was first released in 1991 by Guido van Rossum.',
                       'similarity': 0.96,
                       'source': 'official_history.md'
                   },
                   {
                       'text': 'Python 0.9.0 was published on February 20, 1991.',
                       'similarity': 0.93,
                       'source': 'release_timeline.md'
                   },
                   {
                       'text': 'The first stable release of Python was 1.0 in January 1994.',
                       'similarity': 0.89,
                       'source': 'version_history.md'
                   }
               ],
               'expected_confidence_band': 'high'
           },
           'medium_confidence_query': {
               'query_text': 'What are the main benefits of using Python?',
               'query_type': 'explanation',
               'retrieval_chunks': [
                   {
                       'text': 'Python is known for its readability and simplicity.',
                       'similarity': 0.72,
                       'source': 'tutorial.md'
                   },
                   {
                       'text': 'Python has extensive built-in libraries for many use cases.',
                       'similarity': 0.68,
                       'source': 'overview.md'
                   },
                   {
                       'text': 'Python is interpreted and dynamically typed, making development faster.',
                       'similarity': 0.65,
                       'source': 'features.md'
                   }
               ],
               'expected_confidence_band': 'medium'
           },
           'low_confidence_query': {
               'query_text': 'What was the weather in Tokyo on March 15, 1850?',
               'query_type': 'exact_fact',
               'retrieval_chunks': [
                   {
                       'text': 'Historical weather records from the 1800s are scarce and incomplete.',
                       'similarity': 0.42,
                       'source': 'historical_notes.md'
                   },
                   {
                       'text': 'Japan in 1850 did not have systematic weather documentation.',
                       'similarity': 0.38,
                       'source': 'japan_history.md'
                   }
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

**Verification:**
- [ ] Fixture is async and uses yield pattern
- [ ] Data includes 4+ query scenarios (high, medium, low, empty retrieval)
- [ ] Each scenario has realistic similarity scores and source attributions
- [ ] Query and chunk text are deterministic (same each run)
- [ ] Fixture can be imported and used directly in tests

---

---

### Task 5: Verify All Fixtures Can Be Imported

**Action:**
Verify that all fixtures defined in conftest.py can be imported and used by tests. This ensures the test foundation is solid before Plan 3 tests are written.

**Steps:**

1. Open [tests/conftest.py](tests/conftest.py) and verify the fixtures are properly defined:
   - `policy_seed` (async fixture, creates 3 policies)
   - `make_ci_headers()` (helper function, not a fixture)
   - `trace_assertions` (pytest fixture, provides assertion helpers)
   - `routing_fixture_data` (async fixture, provides synthetic retrieval data)

2. Create a simple verification test [tests/test_fixtures_available.py](tests/test_fixtures_available.py):

   ```python
   """Verify that all CI test fixtures are available."""
   
   import pytest
   from tests.conftest import make_ci_headers, query_latest_trace
   
   
   def test_make_ci_headers_basic():
       """Verify make_ci_headers helper works."""
       headers = make_ci_headers(confidence=0.87)
       assert headers['X-CI-Test-Mode'] == 'true'
       assert headers['X-CI-Override-Confidence'] == '0.87'
   
   
   def test_make_ci_headers_band():
       """Verify make_ci_headers band mapping works."""
       headers = make_ci_headers(band='high')
       assert headers['X-CI-Test-Mode'] == 'true'
       assert headers['X-CI-Override-Confidence'] == '0.87'
   
   
   def test_make_ci_headers_validation():
       """Verify make_ci_headers validates input."""
       with pytest.raises(ValueError):
           make_ci_headers(confidence=1.5)  # Out of range
       
       with pytest.raises(ValueError):
           make_ci_headers(band='invalid')  # Unknown band
   
   
   @pytest.mark.asyncio
   async def test_policy_seed_fixture(policy_seed):
       """Verify policy_seed fixture creates policies."""
       assert 'lenient' in policy_seed
       assert 'baseline' in policy_seed
       assert 'strict' in policy_seed
       assert len(policy_seed) == 3
   
   
   @pytest.mark.asyncio
   async def test_routing_fixture_data(routing_fixture_data):
       """Verify routing_fixture_data provides all scenarios."""
       assert 'high_confidence_query' in routing_fixture_data
       assert 'medium_confidence_query' in routing_fixture_data
       assert 'low_confidence_query' in routing_fixture_data
       assert 'no_retrieval_query' in routing_fixture_data
       
       # Each scenario should have required keys
       for scenario_key, scenario in routing_fixture_data.items():
           assert 'query_text' in scenario, f"{scenario_key} missing query_text"
           assert 'query_type' in scenario, f"{scenario_key} missing query_type"
           assert 'retrieval_chunks' in scenario, f"{scenario_key} missing retrieval_chunks"
   
   
   def test_trace_assertions_fixture(trace_assertions):
       """Verify trace_assertions fixture provides all utilities."""
       assert 'query' in trace_assertions
       assert 'assert_path' in trace_assertions
       assert 'assert_band' in trace_assertions
       assert 'assert_flags' in trace_assertions
       assert 'assert_status' in trace_assertions
       
       # All should be callable
       for key, value in trace_assertions.items():
           assert callable(value), f"{key} is not callable"
   ```

3. Run the verification test:
   ```bash
   pytest tests/test_fixtures_available.py -v
   ```

**Verification:**
- [ ] All 4 fixtures can be imported without errors
- [ ] `make_ci_headers()` generates valid header dicts
- [ ] Helper function validation works (rejects invalid inputs)
- [ ] `policy_seed` creates exactly 3 polices with expected keys
- [ ] `routing_fixture_data` provides all 4 query scenarios with consistent structure
- [ ] `trace_assertions` provides all 5 helper functions that are callable

---

## Verification & Sign-Off

After all tasks complete, run:

### Manual Verification

1. **Test policies seeded:**
   ```bash
   psql $DATABASE_URL -c "SELECT version, is_active, thresholds FROM intelligence.policy_registry WHERE version LIKE 'test-v%';"
   # Should show: test-v1-lenient | f | {"high_min": 0.60, ...}
   #              test-v2-moderate | f | {"high_min": 0.75, ...}
   #              test-v3-strict | f | {"high_min": 0.90, ...}
   ```

2. **Fixtures load without error:**
   ```bash
   pytest tests/conftest.py -v --co -q
   # Should list all fixtures without errors
   ```

3. **Trace queries work:**
   ```bash
   python -c "
   import asyncio
   from tests.conftest import query_latest_trace
   # (Requires test DB setup; defer to actual test execution)
   "
   ```

### Automated Verification

```bash
# Run fixture verification tests
pytest tests/test_fixtures_available.py -v

# Should show:
# test_make_ci_headers_basic PASSED
# test_make_ci_headers_band PASSED
# test_make_ci_headers_validation PASSED
# test_policy_seed_fixture PASSED
# test_routing_fixture_data PASSED
# test_trace_assertions_fixture PASSED
```

---

## Must-Haves Delivered by This Plan

1. ✅ `policy_seed` fixture creates 3 test policies (lenient, baseline, strict) with clear thresholds
2. ✅ `make_ci_headers()` helper generates X-CI-Test-Mode and X-CI-Override-Confidence headers
3. ✅ `routing_fixture_data` provides deterministic synthetic retrieval for 4 test scenarios
4. ✅ `trace_assertions` provides reusable trace query and assertion helpers
5. ✅ All fixtures use header-based approach (no monkeypatch or mock-retrieval code)
6. ✅ Fixture naming is consistent and clear (policy_seed, routing_fixture_data)
7. ✅ No unused fixtures cluttering conftest.py
8. ✅ Fixtures can be imported and verified without running full test suite

---

*Plan 2 of 3 for Phase 3 CI Verification*
