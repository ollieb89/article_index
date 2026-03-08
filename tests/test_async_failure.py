"""Failure-case integration tests for async ingestion and task status.

Covers invalid payloads, unknown tasks, and response shape consistency.
Requires running API (worker optional for payload tests).

Run: API_BASE=http://localhost:8001 pytest tests/test_async_failure.py -v
"""
import uuid

import httpx
import pytest


@pytest.mark.integration
def test_async_ingestion_requires_api_key(api_base: str):
    """Write endpoints return 401 without valid API key."""
    client = httpx.Client(timeout=10.0)

    resp = client.post(
        f"{api_base}/articles/async",
        json={"title": "Test", "content": "Content"},
    )
    assert resp.status_code == 401

    resp = client.post(
        f"{api_base}/articles/async",
        headers={"X-API-Key": "wrong-key"},
        json={"title": "Test", "content": "Content"},
    )
    assert resp.status_code == 401


@pytest.mark.integration
def test_async_ingestion_invalid_payload(api_base: str, api_headers: dict):
    """Invalid payload returns 422 with validation errors."""
    client = httpx.Client(timeout=10.0)

    # Missing required fields (with valid API key)
    resp = client.post(
        f"{api_base}/articles/async",
        headers=api_headers,
        json={"title": "No content"},  # content required
    )
    assert resp.status_code == 422
    detail = resp.json()
    assert "detail" in detail
    # FastAPI returns list of validation errors
    errors = detail["detail"]
    assert any("content" in str(e).lower() for e in errors)

    # Empty title
    resp = client.post(
        f"{api_base}/articles/async",
        headers=api_headers,
        json={"title": "", "content": "Some content"},
    )
    assert resp.status_code == 422

    # Invalid JSON
    resp = client.post(
        f"{api_base}/articles/async",
        headers={**api_headers, "Content-Type": "application/json"},
        content="not json",
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_task_status_unknown_task(api_base: str):
    """Unknown task ID returns PENDING with stable schema."""
    client = httpx.Client(timeout=10.0)
    fake_id = str(uuid.uuid4())

    resp = client.get(f"{api_base}/tasks/{fake_id}")
    resp.raise_for_status()
    data = resp.json()

    # Celery returns PENDING for unknown IDs
    assert data["status"] in ("PENDING", "STARTED")
    assert data["task_id"] == fake_id
    assert "result" in data
    assert "error" in data
    assert data["result"] is None
    assert data["error"] is None


@pytest.mark.integration
def test_task_status_schema_has_required_fields(api_base: str):
    """Task status response always has task_id, status, result, error."""
    client = httpx.Client(timeout=10.0)
    fake_id = str(uuid.uuid4())

    resp = client.get(f"{api_base}/tasks/{fake_id}")
    resp.raise_for_status()
    data = resp.json()

    required = {"task_id", "status", "result", "error"}
    assert required.issubset(data.keys()), f"Missing keys: {required - data.keys()}"
    assert data["task_id"] == fake_id
    assert data["status"] in ("PENDING", "STARTED", "SUCCESS", "FAILURE")
    # result and error are mutually exclusive for terminal states
    if data["status"] == "SUCCESS":
        assert data["error"] is None
    elif data["status"] == "FAILURE":
        assert data["result"] is None
        assert data["error"] is not None
