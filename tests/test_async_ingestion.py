"""Integration test for async article ingestion path.

Enqueues a task, polls until completion, asserts article exists,
and asserts search returns it. Requires running stack (API, worker, Ollama).

Run: API_BASE=http://localhost:8001 pytest tests/test_async_ingestion.py -v
"""
import time

import httpx
import pytest


POLL_INTERVAL = 1.0
POLL_TIMEOUT = 120.0  # Ollama embedding can be slow on first run


@pytest.mark.integration
def test_async_ingestion_full_flow(api_base: str, api_headers: dict):
    """Enqueue article, poll until done, verify article exists and search returns it."""
    client = httpx.Client(timeout=30.0)

    # 1. Enqueue async ingestion
    enqueue_resp = client.post(
        f"{api_base}/articles/async",
        headers=api_headers,
        json={
            "title": "Async integration test article",
            "content": "Celery processes documents in the background. "
            "This article is used to verify the async ingestion path end to end.",
            "metadata": {"source": "integration-test"},
        },
    )
    enqueue_resp.raise_for_status()
    data = enqueue_resp.json()
    assert data["status"] == "accepted"
    task_id = data["task_id"]
    assert task_id

    # 2. Poll until SUCCESS or FAILURE
    start = time.monotonic()
    while True:
        if time.monotonic() - start > POLL_TIMEOUT:
            pytest.fail(f"Task {task_id} did not complete within {POLL_TIMEOUT}s")

        status_resp = client.get(f"{api_base}/tasks/{task_id}")
        status_resp.raise_for_status()
        status_data = status_resp.json()

        if status_data["status"] == "SUCCESS":
            result = status_data.get("result")
            assert result is not None, "Task succeeded but no result"
            assert status_data.get("error") is None, "SUCCESS should have error=null"
            document_id = result.get("document_id")
            assert document_id, "Result missing document_id"
            break
        elif status_data["status"] == "FAILURE":
            error = status_data.get("error", "Unknown error")
            pytest.fail(f"Task failed: {error}")

        time.sleep(POLL_INTERVAL)

    # 3. Assert article exists
    article_resp = client.get(f"{api_base}/articles/{document_id}")
    article_resp.raise_for_status()
    article = article_resp.json()
    assert article["id"] == document_id
    assert "Async integration test article" in article["title"]
    assert "Celery" in article["content"]

    # 4. Assert search returns it
    search_resp = client.post(
        f"{api_base}/search",
        json={"query": "Celery background processing", "limit": 5},
    )
    search_resp.raise_for_status()
    search_data = search_resp.json()
    results = search_data.get("results", [])
    doc_ids = {r.get("document_id") for r in results}
    assert document_id in doc_ids, (
        f"Search did not return document {document_id}. Results: {results}"
    )
