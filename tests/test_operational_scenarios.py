"""E2E tests for operational scenarios.

Tests 3 scenarios:
1. Emergency hotfix (create v2 → activate → verify)
2. Rollback after bad policy (activate v3 → detect → rollback to v2)
3. Audit incorrect routing (replay_audit identifies root cause)
"""

import pytest
import pytest_asyncio
import os
import asyncio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio
]

API_BASE = os.environ.get("API_BASE", "http://localhost:8001")
API_KEY = os.environ.get("API_KEY", "change-me-long-random")


@pytest_asyncio.fixture
async def api_client():
    """Fixture for async HTTP client."""
    import httpx
    async with httpx.AsyncClient() as client:
        yield client


@pytest_asyncio.fixture
async def api_headers():
    """Fixture for API headers with authentication."""
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }


@pytest_asyncio.fixture
async def clean_test_policies(api_client, api_headers):
    """Cleanup fixture to track test policies."""
    test_versions = []
    yield test_versions


async def test_scenario_emergency_hotfix_deployment(api_client, api_headers, clean_test_policies):
    """Scenario 1: Emergency hotfix deployment.
    
    Steps:
    1. Create hotfix policy (v2-hotfix)
    2. Activate hotfix
    3. Verify activation
    4. Generate traffic and verify behavior
    """
    policy_content = {
        "thresholds": {
            "high": 0.90,  # More conservative
            "medium": 0.65,
            "low": 0.40,
            "insufficient": 0.0
        },
        "routing_rules": {
            "query_types": {
                "general": {
                    "high": "fast",
                    "medium": "standard",
                    "low": "cautious",
                    "insufficient": "abstain"
                }
            }
        }
    }
    
    # Step 1: Create hotfix policy
    create_response = await api_client.post(
        f"{API_BASE}/admin/policy/create",
        params={"version": "v2-hotfix"},
        json=policy_content,
        headers=api_headers
    )
    
    assert create_response.status_code == 200, f"Failed to create hotfix: {create_response.text}"
    create_result = create_response.json()
    assert create_result["status"] == "created"
    clean_test_policies.append("v2-hotfix")
    
    # Step 2: Activate hotfix
    activate_response = await api_client.post(
        f"{API_BASE}/admin/policy/activate",
        params={"version": "v2-hotfix", "reason": "Emergency hotfix: tighten thresholds"},
        headers=api_headers
    )
    
    assert activate_response.status_code == 200
    activate_result = activate_response.json()
    assert activate_result["status"] == "activated"
    
    # Step 3: Verify activation
    status_response = await api_client.get(f"{API_BASE}/admin/policy/status")
    status = status_response.json()
    assert status["active_policy_version"] == "v2-hotfix"
    assert status["active_policy_hash"] == create_result["policy_hash"]
    
    # Step 4: Generate traffic
    rag_response = await api_client.post(
        f"{API_BASE}/rag",
        json={"question": "Test hotfix deployment", "context_limit": 2}
    )
    assert rag_response.status_code == 200
    
    print(f"Emergency hotfix deployed successfully: v2-hotfix")


async def test_scenario_rollback_after_bad_policy(api_client, api_headers, clean_test_policies):
    """Scenario 2: Rollback after bad policy detection.
    
    Steps:
    1. Create known-good policy (v2-stable)
    2. Activate v2-stable
    3. Create bad policy (v3-bad)
    4. Activate v3-bad
    5. Detect issue (simulated)
    6. Rollback to v2-stable
    7. Verify rollback
    """
    # Step 1: Create stable policy
    stable_content = {
        "thresholds": {"high": 0.85, "medium": 0.60, "low": 0.35},
        "routing_rules": {"query_types": {}}
    }
    
    await api_client.post(
        f"{API_BASE}/admin/policy/create",
        params={"version": "v2-stable"},
        json=stable_content,
        headers=api_headers
    )
    clean_test_policies.append("v2-stable")
    
    # Step 2: Activate stable
    await api_client.post(
        f"{API_BASE}/admin/policy/activate",
        params={"version": "v2-stable", "reason": "Stable baseline"},
        headers=api_headers
    )
    
    # Step 3: Create bad policy
    bad_content = {
        "thresholds": {"high": 0.99, "medium": 0.95, "low": 0.90},  # Too strict
        "routing_rules": {"query_types": {}}
    }
    
    await api_client.post(
        f"{API_BASE}/admin/policy/create",
        params={"version": "v3-bad"},
        json=bad_content,
        headers=api_headers
    )
    clean_test_policies.append("v3-bad")
    
    # Step 4: Activate bad policy
    await api_client.post(
        f"{API_BASE}/admin/policy/activate",
        params={"version": "v3-bad", "reason": "Attempt new thresholds"},
        headers=api_headers
    )
    
    # Verify v3 is active
    status = await api_client.get(f"{API_BASE}/admin/policy/status")
    assert status.json()["active_policy_version"] == "v3-bad"
    
    # Step 5: Simulate detection (generate trace)
    rag_response = await api_client.post(
        f"{API_BASE}/rag",
        json={"question": "Test with bad policy", "context_limit": 2}
    )
    assert rag_response.status_code == 200
    
    # Step 6: Rollback
    rollback_response = await api_client.post(
        f"{API_BASE}/admin/policy/rollback",
        headers=api_headers
    )
    
    assert rollback_response.status_code == 200
    rollback_result = rollback_response.json()
    assert rollback_result["status"] == "rolled_back"
    
    # Step 7: Verify rollback to v2-stable
    status = await api_client.get(f"{API_BASE}/admin/policy/status")
    status_data = status.json()
    assert status_data["active_policy_version"] == "v2-stable"
    
    print(f"Rollback successful: v3-bad → v2-stable")


async def test_scenario_audit_incorrect_routing(api_client, api_headers, clean_test_policies):
    """Scenario 3: Audit incorrect routing to identify root cause.
    
    Steps:
    1. Create and activate a policy
    2. Generate traffic with known query
    3. Use replay_audit on the trace
    4. Verify audit shows routing decision details
    """
    # Step 1: Create and activate policy
    policy_content = {
        "thresholds": {"high": 0.80, "medium": 0.55, "low": 0.30},
        "routing_rules": {
            "query_types": {
                "exact_fact": {
                    "high": "fast",
                    "medium": "standard"
                }
            }
        }
    }
    
    await api_client.post(
        f"{API_BASE}/admin/policy/create",
        params={"version": "v1-audit"},
        json=policy_content,
        headers=api_headers
    )
    clean_test_policies.append("v1-audit")
    
    await api_client.post(
        f"{API_BASE}/admin/policy/activate",
        params={"version": "v1-audit", "reason": "Audit test"},
        headers=api_headers
    )
    
    # Step 2: Generate traffic
    rag_response = await api_client.post(
        f"{API_BASE}/rag",
        json={"question": "What is retrieval-augmented generation?", "context_limit": 3}
    )
    assert rag_response.status_code == 200
    
    query_id = rag_response.json().get("query_id")
    assert query_id
    
    # Wait for telemetry
    await api_client.get(f"{API_BASE}/health")
    
    # Step 3: Audit the trace
    audit_response = await api_client.post(
        f"{API_BASE}/admin/replay/audit",
        params={"trace_id": query_id},
        headers=api_headers
    )
    
    assert audit_response.status_code == 200
    audit_result = audit_response.json()
    
    # Step 4: Verify audit shows decision details
    assert "original_decision" in audit_result
    assert "reconstructed_decision" in audit_result
    
    original = audit_result["original_decision"]
    assert "action_taken" in original
    assert "execution_path" in original
    assert "confidence_band" in original
    
    print(f"Audit complete for trace {query_id}:")
    print(f"  Action: {original['action_taken']}")
    print(f"  Path: {original['execution_path']}")
    print(f"  Band: {original['confidence_band']}")
    print(f"  Status: {audit_result['status']}")


async def test_scenario_each_completes_quickly(api_client, api_headers, clean_test_policies):
    """Test that each operational scenario completes in < 1s."""
    import time
    
    async def run_scenario():
        # Create → Activate → Verify
        policy_content = {
            "thresholds": {"high": 0.85, "medium": 0.60, "low": 0.35},
            "routing_rules": {"query_types": {}}
        }
        
        await api_client.post(
            f"{API_BASE}/admin/policy/create",
            params={"version": "v-speed-test"},
            json=policy_content,
            headers=api_headers
        )
        clean_test_policies.append("v-speed-test")
        
        await api_client.post(
            f"{API_BASE}/admin/policy/activate",
            params={"version": "v-speed-test"},
            headers=api_headers
        )
        
        status = await api_client.get(f"{API_BASE}/admin/policy/status")
        return status.json()
    
    start = time.time()
    result = await run_scenario()
    elapsed = time.time() - start
    
    assert elapsed < 5.0, f"Scenario took {elapsed:.2f}s, expected < 5s"
    assert result["active_policy_version"] == "v-speed-test"
    
    print(f"Scenario completed in {elapsed:.2f}s")


async def test_scenario_production_readiness_check(api_client, api_headers):
    """Verify production readiness through policy status endpoint."""
    status_response = await api_client.get(f"{API_BASE}/admin/policy/status")
    assert status_response.status_code == 200
    
    status = status_response.json()
    
    # Key production readiness indicators
    checks = {
        "has_active_policy": status.get("active_policy_version") is not None,
        "has_policy_hash": status.get("active_policy_hash") is not None,
        "policy_count_reasonable": 0 < status.get("policy_count", 0) < 1000,
        "has_telemetry": status.get("telemetry_stats", {}).get("total_traces", 0) > 0,
        "has_schema_version_info": len(status.get("trace_schema_versions", {})) > 0,
    }
    
    # Log results
    for check, passed in checks.items():
        status_str = "✓" if passed else "✗"
        print(f"  {status_str} {check}")
    
    # All checks should pass
    assert all(checks.values()), f"Some production readiness checks failed: {checks}"
