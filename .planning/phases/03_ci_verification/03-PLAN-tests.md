---
wave: 2
depends_on:
  - 03-PLAN-infrastructure.md
  - 03-PLAN-fixtures.md
files_modified:
  - tests/test_control_loop_ci.py
autonomous: false
---

# Phase 3 Plan 3: CI Test Suite

**Objective:** Implement the full test matrix that verifies all four execution paths (fast, standard, cautious, abstain) using header-based confidence overrides. Prove confidence band routing correctness under different policy configurations. This plan delivers CTRL-05 and CTRL-06 requirements as executable, verifiable CI tests.

**Requirements Covered:**
- CTRL-05: Behavior changes verified in CI per confidence band (via header-based overrides)
- CTRL-06: Calibration produces threshold updates consumed by routing without manual steps

**Success Criteria:**
- [ ] All 4 execution paths (fast/standard/cautious/abstain) have dedicated test cases using X-CI-Override-Confidence headers
- [ ] All 6 confidence band boundary transitions have test coverage (high↔medium, medium↔low, low↔insufficient)
- [ ] Policy reload test verifies threshold changes are picked up by router
- [ ] Execution path and confidence band assertions pass for all 11 core tests
- [ ] Stage flags are correctly populated for each path
- [ ] Abstain path returns `insufficient_evidence` status (machine-readable)
- [ ] All tests use header-based overrides exclusively (no monkeypatch or mock-retrieval code)
- [ ] Tests run in < 1 minute total
- [ ] All tests pass on CI without live Ollama (use mocked retrieval)

**Effort:** ~250 lines of test code. 11–12 core test cases + boundary parametrization.

---

## Strategy

Tests will use the fixtures from Plan 2 to:
1. Make `/rag` requests with CI headers for confidence override (X-CI-Test-Mode + X-CI-Override-Confidence)
2. Query the policy telemetry table for the resulting trace
3. Assert execution_path, confidence_band, and stage_flags match expected values

The test matrix covers:
- **Main paths (4):** high→fast, medium→standard, low→cautious, insufficient→abstain (using headers)
- **Boundaries (6):** just-above and just-below each threshold transition (exact float overrides)
- **Reload (1):** policy switch changes routing decision for same query
- **Optional latency (1):** fast path completes faster than cautious path

---

## Tasks

### Task 1: Create Test File Structure with Header-Based Path Tests

**Action:**
Create a new test file [tests/test_control_loop_ci.py](tests/test_control_loop_ci.py) with the test class and base tests for the 4 execution paths using header-based confidence overrides.

**Steps:**

1. Create [tests/test_control_loop_ci.py](tests/test_control_loop_ci.py) with the following content:

   ```python
   """
   Phase 3 CI Verification: Control Loop End-to-End Tests
   
   This test suite verifies that:
   1. Different confidence bands produce different execution paths
   2. Execution paths are logged correctly in policy telemetry
   3. Policy reload changes router behavior without server restart
   4. Header-based confidence overrides work correctly in all scenarios
   
   Requirements:
   - CTRL-05: Behavior changes verified in CI per confidence band
   - CTRL-06: Calibration produces threshold updates consumed by routing without manual steps
   """

   import pytest
   import json
   import uuid
   from datetime import datetime
   from sqlalchemy import text
   
   import logging
   logger = logging.getLogger(__name__)
   
   from tests.conftest import make_ci_headers, query_latest_trace
   
   
   class TestExecutionPaths:
       """Test all 4 execution paths using header-based confidence overrides."""
       
       @pytest.mark.asyncio
       async def test_high_confidence_executes_fast_path(
           self,
           client,
           trace_assertions,
           policy_seed,
           routing_fixture_data
       ):
           """
           GIVEN: Headers with X-CI-Override-Confidence=0.87 (high band)
           WHEN: We call /rag with test-mode headers
           THEN: execution_path should be 'fast', with no reranking or expansion
           
           Requirement: CTRL-05 - fast path execution confirmed
           """
           query = routing_fixture_data['high_confidence_query']['query_text']
           headers = make_ci_headers(confidence=0.87)
           
           response = await client.post(
               "/rag",
               json={"question": query},
               headers=headers
           )
           
           assert response.status_code in [200, 202], f"Request failed: {response.text}"
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           # ASSERTION: Query trace and verify execution path
           trace = await trace_assertions['query'](query_id)
           assert trace is not None, f"No trace found for query_id {query_id}"
           
           trace_assertions['assert_path'](trace, 'fast')
           trace_assertions['assert_band'](trace, 'high')
           trace_assertions['assert_flags'](trace, {
               'reranker_invoked': False,
               'retrieval_expanded': False,
               'generation_skipped': False
           })
           
           # Verify confidence override was applied and logged
           metadata = trace.get('metadata', {})
           assert metadata.get('confidence_override', {}).get('applied') == True
           assert metadata.get('confidence_override', {}).get('source') == 'header'
           
           logger.info(f"✓ Fast path test passed: confidence_override=[applied={metadata.get('confidence_override', {}).get('applied')}], path={trace['execution_path']}")
       
       
       @pytest.mark.asyncio
       async def test_medium_confidence_executes_standard_path(
           self,
           client,
           trace_assertions,
           policy_seed,
           routing_fixture_data
       ):
           """
           GIVEN: Headers with X-CI-Override-Confidence=0.65 (medium band)
           WHEN: We call /rag with test-mode headers
           THEN: execution_path should be 'standard', with normal retrieval
           
           Requirement: CTRL-05 - standard path execution confirmed
           """
           query = routing_fixture_data['medium_confidence_query']['query_text']
           headers = make_ci_headers(confidence=0.65)
           
           response = await client.post(
               "/rag",
               json={"question": query},
               headers=headers
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_band'](trace, 'medium')
           trace_assertions['assert_path'](trace, 'standard')
           
           logger.info(f"✓ Standard path test passed: confidence_score={trace['confidence_score']}, path={trace['execution_path']}")
       
       
       @pytest.mark.asyncio
       async def test_low_confidence_executes_cautious_path(
           self,
           client,
           trace_assertions,
           policy_seed,
           routing_fixture_data
       ):
           """
           GIVEN: Headers with X-CI-Override-Confidence=0.40 (low band)
           WHEN: We call /rag with test-mode headers
           THEN: execution_path should be 'cautious', with reranking and expansion
           
           Requirement: CTRL-05 - cautious path execution with extra effort
           """
           query = routing_fixture_data['low_confidence_query']['query_text']
           headers = make_ci_headers(confidence=0.40)
           
           response = await client.post(
               "/rag",
               json={"question": query},
               headers=headers
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_band'](trace, 'low')
           trace_assertions['assert_path'](trace, 'cautious')
           trace_assertions['assert_flags'](trace, {
               'reranker_invoked': True,
               'retrieval_expanded': True,
               'generation_skipped': False
           })
           
           logger.info(f"✓ Cautious path test passed: confidence={trace['confidence_score']}, path={trace['execution_path']}")
       
       
       @pytest.mark.asyncio
       async def test_insufficient_confidence_executes_abstain_path(
           self,
           client,
           trace_assertions,
           policy_seed,
           routing_fixture_data
       ):
           """
           GIVEN: Headers with X-CI-Override-Confidence=0.15 (insufficient band)
           WHEN: We call /rag with test-mode headers
           THEN: execution_path should be 'abstain', generation_skipped=true, status='insufficient_evidence'
           
           Requirement: CTRL-05 - abstain path safety mechanism
           """
           query = routing_fixture_data['no_retrieval_query']['query_text']
           headers = make_ci_headers(confidence=0.15)
           
           response = await client.post(
               "/rag",
               json={"question": query},
               headers=headers
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           
           trace_assertions['assert_status'](response_data, 'insufficient_evidence')
           
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_path'](trace, 'abstain')
           trace_assertions['assert_flags'](trace, {
               'generation_skipped': True
           })
           
           logger.info(f"✓ Abstain path test passed: path={trace['execution_path']}")
   ```

**Verification:**
- [ ] Test file is syntactically valid Python
- [ ] All test methods use `@pytest.mark.asyncio` decorator
- [ ] All tests use `make_ci_headers()` to generate test headers
- [ ] Fixtures policy_seed and routing_fixture_data are imported and used
- [ ] Tests verify both execution_path and confidence override metadata

   ```python
   """
   Phase 3 CI Verification: Control Loop End-to-End Tests
   
   This test suite verifies that:
   1. Different confidence bands produce different execution paths
   2. Execution paths are logged correctly in policy telemetry
   3. Policy reload changes router behavior without server restart
   
   Requirements:
   - CTRL-05: Behavior changes verified in CI per confidence band
   - CTRL-06: Calibration produces threshold updates consumed by routing without manual steps
   """

   import pytest
   import json
   import uuid
   from datetime import datetime, timedelta
   from sqlalchemy import text
   
   import logging
   logger = logging.getLogger(__name__)
   
   
   class TestExecutionPaths:
       """Test all 4 execution paths with exact_fact queries."""
       
       @pytest.mark.asyncio
       async def test_high_confidence_executes_fast_path(
           self,
           client,
           db_session,
           trace_assertions,
           ci_test_data,
           synthetic_fixture
       ):
           """
           GIVEN: A high-confidence exact_fact query (confidence >= 0.75)
           WHEN: We call /rag with this query
           THEN: execution_path should be 'fast', with no reranking or expansion
           
           Requirement: CTRL-05 - fast path execution confirmed
           """
           # Setup
           policy_version = ci_test_data['baseline']
           query = synthetic_fixture['query_text']
           
           # For this test, we need confidence to be >= 0.75 (high threshold in baseline policy)
           # This requires either:
           # A) Live retrieval that naturally produces high confidence
           # B) Mocked retrieval with known confidence
           # C) Direct confidence override (requires app instrumentation)
           
           # For Phase 3 CI, we'll use approach B (mocked/synthetic fixtures)
           # This would require injecting test doubles or parametrizing the /rag endpoint
           
           # ACTION: Make /rag request with synthetic query
           response = await client.post(
               "/rag",
               json={"question": query}
           )
           
           # Parse response
           assert response.status_code in [200, 202], f"Request failed: {response.text}"
           response_data = response.json()
           query_id = response_data.get('query_id') or response_data.get('execution_id')
           
           # Wait briefly for trace to be written (async background task)
           # In real CI, traces are written to DB via background task
           import asyncio
           await asyncio.sleep(0.5)
           
           # ASSERTION: Query trace and verify execution path
           trace = await trace_assertions['query'](query_id)
           assert trace is not None, f"No trace found for query_id {query_id}"
           
           # Verify execution path
           trace_assertions['assert_path'](trace, 'fast')
           
           # Verify confidence band
           trace_assertions['assert_band'](trace, 'high')
           
           # Verify stage flags: fast path should skip reranking and expansion
           trace_assertions['assert_flags'](trace, {
               'reranker_invoked': False,
               'retrieval_expanded': False,
               'generation_skipped': False
           })
           
           logger.info(f"✓ Fast path test passed: confidence={trace['confidence_score']}, path={trace['execution_path']}")
       
       
       @pytest.mark.asyncio
       async def test_medium_confidence_executes_standard_path(
           self,
           client,
           db_session,
           trace_assertions,
           ci_test_data,
           synthetic_fixture
       ):
           """
           GIVEN: A medium-confidence exact_fact query (0.50 <= confidence < 0.75)
           WHEN: We call /rag with this query
           THEN: execution_path should be 'standard', with normal retrieval
           
           Requirement: CTRL-05 - standard path execution confirmed
           """
           # Similar structure to test_high_confidence_executes_fast_path
           # but expects confidence band 'medium' and execution_path 'standard'
           
           policy_version = ci_test_data['baseline']
           query = synthetic_fixture['query_text']
           
           response = await client.post(
               "/rag",
               json={"question": query}
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_band'](trace, 'medium')
           trace_assertions['assert_path'](trace, 'standard')
           
           # Standard path may or may not use reranking depending on uncertainty gates
           # But should not expand context
           assert trace['metadata'].get('stage_flags', {}).get('retrieval_expanded', False) == False
           
           logger.info(f"✓ Standard path test passed: confidence={trace['confidence_score']}, path={trace['execution_path']}")
       
       
       @pytest.mark.asyncio
       async def test_low_confidence_executes_cautious_path(
           self,
           client,
           db_session,
           trace_assertions,
           ci_test_data,
           synthetic_fixture
       ):
           """
           GIVEN: A low-confidence exact_fact query (0.30 <= confidence < 0.50)
           WHEN: We call /rag with this query
           THEN: execution_path should be 'cautious', with reranking and expansion
           
           Requirement: CTRL-05 - cautious path execution with extra effort
           """
           policy_version = ci_test_data['baseline']
           query = synthetic_fixture['query_text']
           
           response = await client.post(
               "/rag",
               json={"question": query}
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_band'](trace, 'low')
           trace_assertions['assert_path'](trace, 'cautious')
           
           # Cautious path must invoke reranking and expansion
           trace_assertions['assert_flags'](trace, {
               'reranker_invoked': True,
               'retrieval_expanded': True,
               'generation_skipped': False
           })
           
           logger.info(f"✓ Cautious path test passed: confidence={trace['confidence_score']}, path={trace['execution_path']}")
       
       
       @pytest.mark.asyncio
       async def test_insufficient_confidence_executes_abstain_path(
           self,
           client,
           db_session,
           trace_assertions,
           ci_test_data,
           synthetic_empty_retrieval
       ):
           """
           GIVEN: A query with no retrieval results (or confidence < 0.30)
           WHEN: We call /rag with this query
           THEN: execution_path should be 'abstain', generation_skipped=true, status='insufficient_evidence'
           
           Requirement: CTRL-05 - abstain path safety mechanism
           """
           query = synthetic_empty_retrieval['query_text']
           
           response = await client.post(
               "/rag",
               json={"question": query}
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           
           # Abstain response should have status field
           trace_assertions['assert_status'](response_data, 'insufficient_evidence')
           
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_path'](trace, 'abstain')
           
           # Abstain path always skips generation
           trace_assertions['assert_flags'](trace, {
               'generation_skipped': True
           })
           
           logger.info(f"✓ Abstain path test passed: path={trace['execution_path']}")
   
   
   class TestConfidenceBoundaryTransitions:
       """Test routing changes at threshold boundaries using exact header-based overrides."""
       
       @pytest.mark.parametrize("confidence,expected_band,expected_path", [
           # High ↔ Medium boundary (threshold=0.85 in baseline)
           (0.8399, 'medium', 'standard'),  # Just below high threshold
           (0.8501, 'high', 'fast'),        # Just above high threshold
           
           # Medium ↔ Low boundary (threshold=0.60)
           (0.5999, 'low', 'cautious'),     # Just below medium threshold
           (0.6001, 'medium', 'standard'),  # Just above medium threshold
           
           # Low ↔ Insufficient boundary (threshold=0.35)
           (0.3499, 'insufficient', 'abstain'),  # Just below low threshold
           (0.3501, 'low', 'cautious'),           # Just above low threshold
       ])
       @pytest.mark.asyncio
       async def test_boundary_transitions(
           self,
           confidence,
           expected_band,
           expected_path,
           client,
           trace_assertions,
           policy_seed,
           routing_fixture_data
       ):
           """
           GIVEN: Headers with exact confidence value at threshold boundary
           WHEN: We call /rag with test-mode headers
           THEN: The band and path should match the expected value on the correct side of the threshold
           
           Requirement: CTRL-05 - routing flips at correct threshold values
           """
           query = routing_fixture_data['high_confidence_query']['query_text']
           headers = make_ci_headers(confidence=confidence)
           
           response = await client.post(
               "/rag",
               json={"question": query},
               headers=headers
           )
           
           assert response.status_code in [200, 202]
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_band'](trace, expected_band)
           trace_assertions['assert_path'](trace, expected_path)
           
           logger.info(f"✓ Boundary test passed: confidence={confidence} -> {expected_band}/{expected_path}")
   
   
   class TestPolicyReload:
       """Test that policy reload changes routing decisions."""
       
       @pytest.mark.asyncio
       async def test_policy_reload_changes_routing_decision(
           self,
           client,
           db_session,
           trace_assertions,
           policy_seed,
           routing_fixture_data
       ):
           """
           GIVEN: Lenient policy active (high_min: 0.70), confidence=0.75
           WHEN: We call /rag with header-based override 
           THEN: path should be 'fast' (band=high due to lenient thresholds)
           
           THEN: We switch to strict policy (high_min: 0.95)
           THEN: We call /admin/policy/reload
           THEN: We call /rag with same confidence=0.75
           THEN: path should be 'standard' (band=medium due to strict thresholds)
           THEN: traces should show different policy_version
           
           Requirement: CTRL-06 - policy reload changes routing behavior
           """
           lenient_policy = policy_seed['lenient']
           strict_policy = policy_seed['strict']
           
           query = routing_fixture_data['high_confidence_query']['query_text']
           headers_75 = make_ci_headers(confidence=0.75)
           
           # Step 1: Activate lenient policy
           policy_repo = PolicyRepository(db_session.pool)
           await policy_repo.set_active_policy(lenient_policy)
           
           # Step 2: Call /rag with confidence=0.75 (high in lenient, medium in strict)
           response1 = await client.post(
               "/rag",
               json={"question": query},
               headers=headers_75
           )
           assert response1.status_code in [200, 202]
           query_id_1 = response1.json().get('query_id')
           
           import asyncio
           await asyncio.sleep(0.5)
           
           trace1 = await trace_assertions['query'](query_id_1)
           assert trace1 is not None
           assert trace1['policy_version'] == lenient_policy
           path1 = trace1['execution_path']
           band1 = trace1['confidence_band']
           
           # Step 3: Switch to strict policy and reload
           await policy_repo.set_active_policy(strict_policy)
           
           reload_response = await client.post(
               "/admin/policy/reload",
               headers={"X-API-Key": "change-me-long-random"}
           )
           assert reload_response.status_code == 200
           assert reload_response.json()['status'] == 'success'
           
           # Step 4: Call /rag again with same confidence=0.75
           response2 = await client.post(
               "/rag",
               json={"question": query},
               headers=headers_75
           )
           assert response2.status_code in [200, 202]
           query_id_2 = response2.json().get('query_id')
           
           await asyncio.sleep(0.5)
           
           trace2 = await trace_assertions['query'](query_id_2)
           assert trace2 is not None
           assert trace2['policy_version'] == strict_policy
           path2 = trace2['execution_path']
           band2 = trace2['confidence_band']
           
           # Step 5: Assert that policy version and execution path changed
           assert trace1['policy_version'] != trace2['policy_version'], \
               f"Policy reload should change policy version"
           assert path1 != path2 or band1 != band2, \
               f"Same confidence (0.75) should route differently under different policies. " \
               f"Lenient/{path1}/{band1} -> Strict/{path2}/{band2}"
           
           logger.info(f"✓ Policy reload test passed: {path1}/{band1} -> {path2}/{band2} (policy {lenient_policy} -> {strict_policy})")
   
   
   class TestStageFlags:
       """Test that stage_flags are correctly populated for each execution path."""
       
       @pytest.mark.asyncio 
       async def test_stage_flags_all_paths(
           self,
           client,
           trace_assertions,
           routing_fixture_data
       ):
           """
           GIVEN: Query routed to fast path (high confidence=0.88)
           WHEN: We query the trace
           THEN: stage_flags should show: reranker_invoked=False, retrieval_expanded=False
           
           GIVEN: Query routed to standard path (medium confidence=0.68)
           WHEN: We query the trace
           THEN: stage_flags should show: reranker_invoked=False, retrieval_expanded=True
           
           GIVEN: Query routed to cautious path (low confidence=0.42)
           WHEN: We query the trace
           THEN: stage_flags should show: reranker_invoked=True, retrieval_expanded=True
           
           Requirement: TRACE-04 - Stage flags populated correctly per execution path
           """
           test_cases = [
               (0.88, 'fast', {'reranker_invoked': False, 'retrieval_expanded': False}),
               (0.68, 'standard', {'reranker_invoked': False, 'retrieval_expanded': True}),
               (0.42, 'cautious', {'reranker_invoked': True, 'retrieval_expanded': True}),
           ]
           
           query = routing_fixture_data['high_confidence_query']['query_text']
           
           for confidence, expected_path, expected_flags in test_cases:
               headers = make_ci_headers(confidence=confidence)
               
               response = await client.post(
                   "/rag",
                   json={"question": query},
                   headers=headers
               )
               assert response.status_code in [200, 202], \
                   f"Expected 200/202, got {response.status_code}"
               query_id = response.json().get('query_id')
               
               import asyncio
               await asyncio.sleep(0.5)
               
               trace = await trace_assertions['query'](query_id)
               assert trace is not None, f"No trace found for query_id {query_id}"
               
               # Verify execution path
               trace_assertions['assert_path'](trace, expected_path)
               
               # Verify stage flags
               trace_assertions['assert_flags'](trace, expected_flags)
               
               logger.info(f"✓ Stage flags correct for {expected_path} path: {expected_flags}")

   
   
   class TestAbstainResponse:
       """Test that abstain path returns machine-readable status."""
       
       @pytest.mark.asyncio
       async def test_abstain_path_returns_insufficient_evidence(
           self,
           client,
           trace_assertions,
           routing_fixture_data
       ):
           """
           GIVEN: A query with insufficient confidence (confidence=0.15)
           WHEN: We call /rag with header-based override
           THEN: response should have status='insufficient_evidence' (machine-readable)
           THEN: response should NOT contain an answer (generation_skipped=true)
           THEN: execution_path should be 'abstain'
           THEN: confidence_band should be 'insufficient'
           
           Requirement: CTRL-05 - abstain safety mechanism prevents hallucinations
           """
           query = routing_fixture_data['no_retrieval_query']['query_text']
           headers = make_ci_headers(confidence=0.15)
           
           response = await client.post(
               "/rag",
               json={"question": query},
               headers=headers
           )
           assert response.status_code in [200, 202]
           response_data = response.json()
           query_id = response_data.get('query_id')
           
           # Check immediate response status
           assert response_data.get('status') == 'insufficient_evidence', \
               f"Expected status='insufficient_evidence', got {response_data.get('status')}"
           assert not response_data.get('answer') or response_data.get('answer') == '', \
               f"Abstain path should not generate answer"
           
           # Check trace for execution details
           import asyncio
           await asyncio.sleep(0.5)
           
           trace = await trace_assertions['query'](query_id)
           assert trace is not None
           
           trace_assertions['assert_path'](trace, 'abstain')
           trace_assertions['assert_confidence_band'](trace, 'insufficient')
           
           # Verify generation was skipped
           assert trace['generation_skipped'] == True, \
               "Generation should be skipped in abstain path"
           
           logger.info(f"✓ Abstain response test passed: status=insufficient_evidence, generation_skipped=true")
   ```

2. Verify the file structure is correct (indentation, class/method definitions)

**Verification:**
- [ ] Test file is syntactically valid Python
- [ ] All test methods use `@pytest.mark.asyncio` decorator
- [ ] Fixtures are imported and used correctly
- [ ] Parametrized boundary test has correct parameter names and values

---

### Task 2: Implement Test Utilities for Confidence Injection

**Action:**
Add utility functions to the test file or conftest.py that enable direct injection of forced confidence scores into requests. This is needed for boundary testing (Task 1, TestConfidenceBoundaryTransitions).

**Steps:**

1. Add to [tests/test_control_loop_ci.py](tests/test_control_loop_ci.py) or [tests/conftest.py](tests/conftest.py):

   ```python
   import os
   from unittest import mock
   
   
   @pytest.fixture
   def mock_confidence_scorer(monkeypatch):
       """
       Provide a fixture that allows test to override confidence score calculation.
       
       Usage in test:
           with mock_confidence_scorer(confidence=0.87):
               response = await client.post("/rag", json={"question": "..."})
       """
       def scorer_override(forced_value):
           """Context manager for forcing a specific confidence score."""
           class ConfidenceOverride:
               def __enter__(self):
                   # Patch the confidence scorer to return forced_value
                   # This requires identifying the actual scorer function in app.py
                   # Example (depends on actual implementation):
                   def mock_scorer(*args, **kwargs):
                       return forced_value
                   
                   # Apply patch to the actual scorer location
                   # This is project-specific; adjust based on actual code
                   # Example: monkeypatch.setattr("api.app.score_confidence", mock_scorer)
                   return self
               
               def __exit__(self, *args):
                   pass  # Cleanup happens automatically when context exits
           
           return ConfidenceOverride()
       
       return scorer_override
   ```

2. (Alternative) If direct monkeypatching is complex, use direct HTTP header injection:

   ```python
   @pytest.fixture
   async def rag_with_confidence_override(client):
       """
       Make a /rag call that signals a confidence override via headers.
       
       Requires app.py to respect the X-CI-Override-Confidence header.
       """
       async def call(question: str, override_confidence: float = None):
           headers = {}
           if override_confidence is not None:
               headers['X-CI-Override-Confidence'] = str(override_confidence)
               headers['X-CI-Test-Mode'] = 'true'
           
           response = await client.post(
               "/rag",
               json={"question": question},
               headers=headers
           )
           
           return response.json()
       
       return call
   ```

3. If using header-based injection, modify [api/app.py](api/app.py) to recognize test headers and override confidence:

   ```python
   # In the _rag_hybrid() function, after computing confidence:
   
   from starlette.requests import Request
   
   # Inside _rag_hybrid:
   request = request  # from function signature
   override_confidence = request.headers.get('X-CI-Override-Confidence')
   if override_confidence and request.headers.get('X-CI-Test-Mode') == 'true':
       try:
           confidence = float(override_confidence)
           logger.debug(f"Overriding confidence for test: {confidence}")
       except ValueError:
           pass
   ```

**Verification:**
- [ ] Override fixture/utility is syntactically valid
- [ ] Can be used in test with clear API (e.g., `mock_confidence_scorer(0.87)`)
- [ ] Does not affect production code (test-only imports and functions)

---

### Task 3: Run and Validate CI Tests

**Action:**
Execute the test suite and verify all tests pass, providing clear output for debugging if failures occur.

**Steps:**

1. Ensure all fixtures and dependencies are available:
   ```bash
   cd /home/ollie/Development/Tools/db/article_index
   pytest tests/test_control_loop_ci.py --collect-only -q
   # Should list all test cases without errors
   ```

2. Run a subset of tests first (fast sanity check):
   ```bash
   pytest tests/test_control_loop_ci.py::TestExecutionPaths::test_high_confidence_executes_fast_path -v
   ```

3. If that passes, run all tests:
   ```bash
   pytest tests/test_control_loop_ci.py -v --tb=short
   # Should show all tests passing
   ```

4. Check total runtime:
   ```bash
   pytest tests/test_control_loop_ci.py -v --durations=0
   # Should complete in < 1 minute total
   ```

5. Verify trace assertions are working:
   ```bash
   pytest tests/test_control_loop_ci.py::TestStageFlags -v
   # Should pass tests that verify stage_flags are populated
   ```

**Verification:**
- [ ] All 11 core tests pass (4 main paths + 6 boundaries + 1 reload)
- [ ] No trace query failures (all traces are found in DB)
- [ ] No stage_flags assertion failures (all flags populated correctly)
- [ ] Execution completes in < 1 minute
- [ ] Clear failure messages if any test fails (for debugging)

---

### Task 4: Add Tests for Edge Cases and Multiple Concurrent Requests

**Action:**
Verify error handling and test isolation when processing multiple requests concurrently.

**Specification:**

```python
class TestEdgeCases:
    """Test edge cases, error handling, and concurrent request isolation."""
    
    @pytest.mark.asyncio
    async def test_multiple_concurrent_requests_isolated(
        self,
        client,
        trace_assertions,
        routing_fixture_data
    ):
        """
        GIVEN: Multiple concurrent /rag requests with different confidence overrides
        WHEN: We execute them in parallel using header-based overrides
        THEN: Each should produce separate traces with correct query_id correlation
        THEN: No traces should be mixed up or interfere with each other
        
        Requirement: CI-ISOLATION - Test isolation and concurrent request handling
        """
        import asyncio
        
        query = routing_fixture_data['high_confidence_query']['query_text']
        
        # Create 3 concurrent requests with different confidence overrides
        tasks = []
        confidence_values = [0.88, 0.68, 0.42]  # fast, standard, cautious
        expected_paths = ['fast', 'standard', 'cautious']
        
        for confidence, expected_path in zip(confidence_values, expected_paths):
            headers = make_ci_headers(confidence=confidence)
            tasks.append(
                client.post(
                    "/rag",
                    json={"question": query},
                    headers=headers
                )
            )
        
        # Execute concurrently
        responses = await asyncio.gather(*tasks)
        assert all(r.status_code in [200, 202] for r in responses), \
            f"Some requests failed: {[r.status_code for r in responses]}"
        
        # Extract query_ids
        query_ids = [r.json().get('query_id') for r in responses]
        assert len(set(query_ids)) == 3, \
            f"Each request should have unique query_id, got duplicates: {query_ids}"
        
        # Query traces
        await asyncio.sleep(0.5)
        traces = await asyncio.gather(
            *[trace_assertions['query'](qid) for qid in query_ids]
        )
        
        # All traces should be found
        assert all(t is not None for t in traces), \
            f"Not all traces found: {[t is not None for t in traces]}"
        
        # Verify traces match their respective execution paths
        for i, (qid, trace, expected_path) in enumerate(zip(query_ids, traces, expected_paths)):
            trace_assertions['assert_path'](trace, expected_path)
            logger.info(f"✓ Request {i+1}: query_id={qid}, path={expected_path}")
        
        logger.info(f"✓ Concurrent requests isolated correctly (3 requests, 3 unique query_ids, 3 correct paths)")
    
    
    @pytest.mark.asyncio
    async def test_confidence_override_header_validation(
        self,
        client,
        trace_assertions,
        routing_fixture_data
    ):
        """
        GIVEN: Invalid confidence override values (out of range, malformed)
        WHEN: We send requests with these headers
        THEN: Should reject invalid values and fall back to computed confidence
        
        Requirement: CI-SAFETY - Invalid overrides don't break system
        """
        query = routing_fixture_data['high_confidence_query']['query_text']
        
        # Test case 1: Confidence out of range (> 1.0)
        headers_invalid_high = make_ci_headers(confidence=1.5)  # Should clamp or reject
        response = await client.post(
            "/rag",
            json={"question": query},
            headers=headers_invalid_high
        )
        # Should either reject or normalize
        assert response.status_code in [200, 202, 400], \
            f"Should handle out-of-range confidence, got {response.status_code}"
        
        # Test case 2: Confidence below range (< 0.0)
        headers_invalid_low = make_ci_headers(confidence=-0.5)  # Should clamp or reject
        response = await client.post(
            "/rag",
            json={"question": query},
            headers=headers_invalid_low
        )
        assert response.status_code in [200, 202, 400], \
            f"Should handle negative confidence, got {response.status_code}"
        
        # Test case 3: Valid boundary values should work
        for boundary_value in [0.0, 1.0]:
            headers_valid = make_ci_headers(confidence=boundary_value)
            response = await client.post(
                "/rag",
                json={"question": query},
                headers=headers_valid
            )
            assert response.status_code in [200, 202], \
                f"Should accept boundary value {boundary_value}, got {response.status_code}"
        
        logger.info("✓ Confidence override validation tests passed")
```

**Verification:**
- [ ] Multiple concurrent requests produce unique query_ids and don't interfere
- [ ] Each concurrent request produces correct execution path trace
- [ ] Invalid confidence overrides are handled gracefully (reject or normalize)
- [ ] Boundary values (0.0, 1.0) are accepted or validated

---

## Task 5: Add Optional Performance Verification

**Action:**
Optionally add tests that verify performance characteristics when switching between execution paths.

**Specification:**

```python
class TestOptionalPerformance:
    """Optional performance verification tests."""
    
    @pytest.mark.asyncio
    async def test_execution_path_routing_correctness(
        self,
        client,
        trace_assertions,
        routing_fixture_data
    ):
        """
        GIVEN: Three different confidence levels
        WHEN: We execute with header-based confidence overrides
        THEN: Verify execution_path and routing decisions are correct
        THEN: (Opportunistically) Check latency makes semantic sense
        
        This test verifies correctness first; performance is secondary.
        Requirement: CTRL-05 - Routing behavior consistent with confidence
        """
        import asyncio
        import time
        
        query = routing_fixture_data['high_confidence_query']['query_text']
        
        test_cases = [
            (0.88, 'fast'),
            (0.68, 'standard'),
            (0.42, 'cautious'),
        ]
        
        traces_data = []
        
        for confidence, expected_path in test_cases:
            headers = make_ci_headers(confidence=confidence)
            
            request_start = time.time()
            response = await client.post(
                "/rag",
                json={"question": query},
                headers=headers
            )
            request_elapsed = time.time() - request_start
            
            assert response.status_code in [200, 202]
            query_id = response.json().get('query_id')
            
            await asyncio.sleep(0.5)
            
            trace = await trace_assertions['query'](query_id)
            assert trace is not None
            
            # Verify execution path matches expectation
            trace_assertions['assert_path'](trace, expected_path)
            
            traces_data.append({
                'confidence': confidence,
                'expected_path': expected_path,
                'execution_path': trace['execution_path'],
                'request_elapsed_ms': request_elapsed * 1000,
                'trace_latency_ms': trace.get('latency_ms', 0),
                'query_id': query_id
            })
            
            logger.info(f"✓ {expected_path} path verified: confidence={confidence}, latency={trace.get('latency_ms', 'N/A')}ms")
        
        # Optional performance check: fast should generally be faster than cautious
        # But don't fail if not (CI timing is variable)
        fast_latency = traces_data[0]['trace_latency_ms']
        cautious_latency = traces_data[2]['trace_latency_ms']
        
        if fast_latency > 0 and cautious_latency > 0 and fast_latency >= 5 and cautious_latency >= 5:
            # Only check if both latencies are measurable (> 5ms)
            if fast_latency > cautious_latency:
                logger.warning(f"⊘ Latency unexpectedly high for fast path: {fast_latency}ms vs cautious {cautious_latency}ms")
            else:
                logger.info(f"✓ Latency trend correct: fast {fast_latency}ms < cautious {cautious_latency}ms")
        
        logger.info("✓ Execution path routing correctness verified")
    
    
    @pytest.mark.skip(reason="Optional determinism test - may be flaky on variable CI hardware")
    @pytest.mark.asyncio
    async def test_confidence_override_deterministic(
        self,
        client,
        trace_assertions,
        routing_fixture_data
    ):
        """
        GIVEN: Same query with same confidence override, run multiple times
        WHEN: We execute the requests
        THEN: All should produce the same execution_path
        THEN: All should produce the same confidence_band
        
        This is a determinism check; skipped by default due to CI variability.
        """
        query = routing_fixture_data['high_confidence_query']['query_text']
        headers = make_ci_headers(confidence=0.75)
        
        import asyncio
        
        # Run 3 times with same override
        responses = await asyncio.gather(
            *[
                client.post(
                    "/rag",
                    json={"question": query},
                    headers=headers
                )
                for _ in range(3)
            ]
        )
        
        # Extract query_ids and wait for traces
        query_ids = [r.json().get('query_id') for r in responses]
        await asyncio.sleep(0.5)
        
        traces = await asyncio.gather(
            *[trace_assertions['query'](qid) for qid in query_ids]
        )
        
        # All should produce same execution path and band
        paths = [t['execution_path'] for t in traces]
        bands = [t['confidence_band'] for t in traces]
        
        assert len(set(paths)) == 1, f"Paths should be deterministic: {paths}"
        assert len(set(bands)) == 1, f"Bands should be deterministic: {bands}"
        
        logger.info(f"✓ Determinism verified: 3 runs all produced {paths[0]}/{bands[0]}")
```

**Verification:**
- [ ] Routing correctness verified for all three execution paths
- [ ] Latency measurement doesn't fail the test (informational only)
- [ ] Determinism test skipped by default (can be enabled locally)

---

## Verification & Sign-Off

After all tasks complete:

### Pre-Run Checklist

1. ✅ All fixtures from Plan 2 are available:
   ```bash
   python -c "from tests.conftest import synthetic_fixture, test_policies, trace_assertions; print('Fixtures OK')"
   ```

2. ✅ Test file is syntactically valid:
   ```bash
   python -m py_compile tests/test_control_loop_ci.py
   ```

3. ✅ All test methods are discovered:
   ```bash
   pytest tests/test_control_loop_ci.py --collect-only | grep "test_" | wc -l
   # Should show 11+ tests
   ```

### Run Test Suite

```bash
# Full test suite with verbose output
make test -- tests/test_control_loop_ci.py -v

# Or manually:
pytest tests/test_control_loop_ci.py -v --tb=short --durations=5
```

### Expected Output

```
tests/test_control_loop_ci.py::TestExecutionPaths::test_high_confidence_executes_fast_path PASSED
tests/test_control_loop_ci.py::TestExecutionPaths::test_medium_confidence_executes_standard_path PASSED
tests/test_control_loop_ci.py::TestExecutionPaths::test_low_confidence_executes_cautious_path PASSED
tests/test_control_loop_ci.py::TestExecutionPaths::test_insufficient_confidence_executes_abstain_path PASSED
tests/test_control_loop_ci.py::TestConfidenceBoundaryTransitions::test_boundary_transitions[...] PASSED (x6)
tests/test_control_loop_ci.py::TestPolicyReload::test_policy_reload_changes_routing_decision PASSED
tests/test_control_loop_ci.py::TestStageFlags::test_fast_path_stage_flags PASSED
tests/test_control_loop_ci.py::TestStageFlags::test_cautious_path_stage_flags PASSED
tests/test_control_loop_ci.py::TestAbstainResponse::test_abstain_returns_insufficient_evidence_status PASSED
tests/test_control_loop_ci.py::TestEdgeCases::test_trace_query_with_malformed_metadata PASSED
tests/test_control_loop_ci.py::TestEdgeCases::test_multiple_requests_do_not_interfere PASSED
tests/test_control_loop_ci.py::TestEdgeCases::test_policy_reload_order_independence PASSED

====== 15 passed in 42.5s ======
```

### Post-Run Verification

1. **All 4 execution paths covered:**
   ```bash
   pytest tests/test_control_loop_ci.py -v | grep "PASSED" | grep -E "fast|standard|cautious|abstain" | wc -l
   # Should show >= 4
   ```

2. **All 6 boundaries covered:**
   ```bash
   pytest tests/test_control_loop_ci.py::TestConfidenceBoundaryTransitions -v
   # Should show 6 parametrized tests passing
   ```

3. **Policy reload works:**
   ```bash
   pytest tests/test_control_loop_ci.py::TestPolicyReload -v
   # Should show successful reload and routing change
   ```

4. **Traces captured correctly:**
   ```bash
   psql $DATABASE_URL -c "SELECT COUNT(DISTINCT query_id) FROM policy_telemetry WHERE created_at > NOW() - INTERVAL '5 minutes';"
   # Should show >= 15 (one per test)
   ```

---

## Must-Haves Delivered by This Plan

1. ✅ All 4 execution paths (fast/standard/cautious/abstain) are tested and verified
2. ✅ All 6 confidence band boundary transitions are covered
3. ✅ Policy reload behavior is demonstrated and asserted
4. ✅ Stage flags are correctly populated and verified for each path
5. ✅ Abstain response contract (machine-readable status, generation_skipped) is verified
6. ✅ Edge cases and error conditions are tested
7. ✅ Full test suite completes in < 1 minute
8. ✅ CTRL-05 requirement: Behavior changes verified in CI per confidence band
9. ✅ CTRL-06 requirement: Calibration produces threshold updates consumed by routing without manual steps

---

*Plan 3 of 3 for Phase 3 CI Verification*
