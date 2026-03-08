"""Pytest configuration and fixtures."""
import os
import json
import pytest
import httpx
from typing import Dict, Any, Optional


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks test as integration test (requires running stack)",
    )


@pytest.fixture
def api_base():
    """API base URL for integration tests."""
    return os.getenv("API_BASE", "http://localhost:8001")


@pytest.fixture
def api_headers():
    """Headers including API key for write/admin endpoints."""
    key = os.getenv("API_KEY", "change-me-long-random")
    return {"X-API-Key": key}


@pytest.fixture
async def policy_seed(api_base, api_headers):
    """
    Seed test database with three policy versions using different confidence thresholds.
    Used by all CI tests to verify threshold-driven routing behavior.
    
    Returns: dict with keys 'lenient', 'baseline', 'strict'
    Each value is the policy version string (e.g., 'test-v1-lenient')
    """
    from shared.database import PolicyRepository, db_manager
    
    policy_repo = PolicyRepository(db_manager)
    
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
    from sqlalchemy import text
    async with db_manager.get_async_connection_context() as conn:
        await conn.execute(
            "DELETE FROM intelligence.policy_registry WHERE version IN ('test-v1-lenient', 'test-v2-baseline', 'test-v3-strict')"
        )


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


async def query_latest_trace(api_base: str, query_id: str, api_headers: dict):
    """
    Query the policy_telemetry table for the most recent trace with the given query_id.
    
    Args:
        api_base: Base URL of the API
        query_id: The query ID to look up
        api_headers: Headers including API key for auth
    
    Returns:
        dict with trace fields or None if not found
    """
    # Note: This would require an admin endpoint to query telemetry
    # For now, we'll extract query_id from RAG response and verify it exists
    # In a full implementation, this would call an admin endpoint like GET /admin/telemetry/{query_id}
    return None  # Placeholder - will be implemented with additional endpoint if needed


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
def trace_assertions():
    """Provide assertion helpers for trace validation."""
    return {
        'assert_path': assert_execution_path,
        'assert_band': assert_confidence_band,
        'assert_flags': assert_stage_flags,
    }


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
