"""Pytest configuration and fixtures."""
import os

import pytest


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
