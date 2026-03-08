# Testing

## Framework & Setup

- **Framework**: `pytest` with `pytest-asyncio`
- **Configuration**: Minimal — no `pytest.ini` or `setup.cfg`; markers registered in `conftest.py`
- **Python path manipulation**: Some unit test files add the project root to `sys.path` directly:
  ```python
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
  ```
- **Custom markers**: One marker is defined and registered in `conftest.py`:
  ```python
  def pytest_configure(config):
      config.addinivalue_line(
          "markers",
          "integration: marks test as integration test (requires running stack)",
      )
  ```
- **Environment-based skip**: Integration test files can skip the entire module via:
  ```python
  pytestmark = [
      pytest.mark.integration,
      pytest.mark.skipif(
          os.getenv("SKIP_API_TESTS") == "1",
          reason="API tests disabled via SKIP_API_TESTS"
      )
  ]
  ```

---

## Test Structure

### Layout
All tests live in a flat `tests/` directory — no sub-packages. Files named `test_{feature}.py`:

```
tests/
  conftest.py               # Global fixtures and marker registration
  test_async_ingestion.py   # Integration: full async article pipeline
  test_async_failure.py     # Integration: error handling / task failure paths
  test_query_classifier.py  # Unit: QueryClassifier (Phase 14 routing)
  test_policy_routing.py    # Unit: RAGPolicy and EvidenceScorer
  test_retrieval_state.py   # Unit: RetrievalStateLabeler
  test_evidence_shape.py    # Unit: EvidenceShapeExtractor
  test_selective_reranking.py  # Integration: reranking modes via live API
  test_query_transformations.py  # Unit: QueryTransformer
  test_hnsw_plan.py         # Unit: HNSW index planning
  test_contextual_router.py # Unit: ContextualRouter
  test_control_loop.py      # Unit: policy control loop
  test_calibration.py       # Unit: calibration pipeline
```

### Test class grouping
Unit tests are grouped into classes by feature or scenario:
```python
class TestQueryClassifierExactFact:
    def test_who_question(self, classifier): ...
    def test_when_question(self, classifier): ...

class TestQueryClassifierComparison:
    def test_versus_keyword(self, classifier): ...
```

### Standalone test functions
Integration tests and policy unit tests use standalone functions (not classes):
```python
def test_rag_policy_contextual_thresholds():
    policy = RAGPolicy(...)
    assert policy.get_threshold("high", "exact_fact") == 0.90

@pytest.mark.integration
def test_async_ingestion_full_flow(api_base: str, api_headers: dict):
    ...
```

---

## What's Tested

### Integration tests (require running Docker stack + Ollama)
- **`test_async_ingestion.py`**: Full end-to-end async pipeline — POST to `/articles/async`, poll `/tasks/{id}` until `SUCCESS`, verify article via `/articles/{id}`, verify search returns it via `/search`
- **`test_async_failure.py`**: Task failure handling — error propagation, status reporting
- **`test_selective_reranking.py`**: Reranking modes (off/always/selective) via live `/search/hybrid` endpoint; verifies response metadata structure

### Unit tests (no external dependencies)
- **`test_query_classifier.py`**: Pattern-matching classification for 7 `QueryType` values; covers happy path, edge cases (empty string, single word, whitespace), and fallback behaviour
- **`test_policy_routing.py`**: `RAGPolicy.get_threshold()` with contextual overrides; `RAGPolicy.get_latency_budget()` with fallback; `EvidenceScorer.score_evidence()` contextual band assignment; evidence shape metadata
- **`test_retrieval_state.py`**: `RetrievalStateLabeler` state transitions
- **`test_evidence_shape.py`**: `EvidenceShapeExtractor` metadata extraction
- **`test_query_transformations.py`**: `QueryTransformer` modes and output
- **`test_contextual_router.py`**: `ContextualRouter` routing decisions
- **`test_control_loop.py`**: Policy control loop logic
- **`test_hnsw_plan.py`**: HNSW index configuration planning
- **`test_calibration.py`**: Calibration pipeline components

### Coverage areas
- API authentication flow (implicitly via integration tests)
- Vector embedding pipeline (via integration tests)
- Query classification patterns and edge cases
- Policy threshold lookup with contextual overrides
- Evidence scoring and confidence band assignment
- Celery task retry logic (via failure tests)

---

## Mocking Approach

### No mock framework for unit tests
Unit tests instantiate real objects with injected test data — no `unittest.mock`, `pytest-mock`, or `MagicMock` is used in the unit test files. Tests construct objects directly:
```python
@pytest.fixture
def classifier():
    return QueryClassifier()

def test_who_question(self, classifier):
    assert classifier.classify("Who founded Apple?") == QueryType.EXACT_FACT
```

### Fixture-based test data injection
Dataclass-based objects (`RAGPolicy`, `EvidenceScorer`) are constructed inline within tests with explicit parameters:
```python
def test_rag_policy_contextual_thresholds():
    policy = RAGPolicy(
        version="test-14",
        thresholds={"high": 0.75, "medium": 0.50},
        contextual_thresholds={
            "exact_fact": {"high": 0.90, "medium": 0.70},
        }
    )
    assert policy.get_threshold("high", "exact_fact") == 0.90
```

### Integration tests use live services
Integration tests communicate with the actual running stack over HTTP (no mocking of API, DB, or Ollama):
```python
@pytest.fixture(scope="module")
async def http_client():
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client
```

### Environment variable configuration
External service URLs (Ollama, API, DB) are injected via environment variables, allowing tests to target different environments without code changes:
- `API_BASE` — defaults to `http://localhost:8001`
- `API_KEY` — defaults to `change-me-long-random`
- `SKIP_API_TESTS=1` — skips all API-touching tests

---

## Running Tests

### Makefile commands
```bash
# Run integration tests only (requires docker compose up + Ollama)
make test

# Same as above, explicit alias
make test-integration

# Override API base URL
API_BASE=http://localhost:8001 make test

# Run smoke test script
make smoke
```

### Manual pytest commands
```bash
# Run all integration tests
API_BASE=http://localhost:8001 API_KEY=change-me-long-random \
  pytest tests/test_async_ingestion.py tests/test_async_failure.py -v -m integration

# Run a specific integration test file
API_BASE=http://localhost:8001 pytest tests/test_async_ingestion.py -v

# Run all unit tests (no stack required)
pytest tests/test_query_classifier.py tests/test_policy_routing.py -v

# Run unit tests excluding integration marker
pytest tests/ -v -m "not integration"

# Run a specific test class
pytest tests/test_query_classifier.py::TestQueryClassifierExactFact -v

# Install dev dependencies first
pip install -r requirements-dev.txt
```

### Test environment requirements
| Test type | Requirements |
|---|---|
| Unit tests | Local Python environment with project dependencies |
| Integration tests | `docker compose up`, Ollama running with `nomic-embed-text` and `llama3.2` pulled |

### Polling pattern for async integration tests
Since Celery tasks are asynchronous, integration tests use a polling loop with a configurable timeout:
```python
POLL_INTERVAL = 1.0
POLL_TIMEOUT = 120.0  # Ollama embedding can be slow on first run

start = time.monotonic()
while True:
    if time.monotonic() - start > POLL_TIMEOUT:
        pytest.fail(f"Task {task_id} did not complete within {POLL_TIMEOUT}s")
    status = client.get(f"{api_base}/tasks/{task_id}").json()
    if status["status"] == "SUCCESS":
        break
    time.sleep(POLL_INTERVAL)
```
