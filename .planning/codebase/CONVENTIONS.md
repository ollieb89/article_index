# Code Conventions

## Python Style

- **Python version**: 3.13+ (specified in `pyproject.toml`)
- **Async-first**: Nearly all I/O operations use `async`/`await` via `asyncpg` and `httpx`
- **Type hints**: Used throughout all function signatures with `Optional`, `Dict`, `List`, `Any`, `Tuple` from `typing`
- **Docstrings**: Google-style docstrings used for classes and methods with `Args:`, `Returns:`, and `Example:` sections
- **Dataclasses**: `@dataclass` with `field(default_factory=...)` used for config/policy objects (e.g., `RAGPolicy`)
- **Enums**: `str, Enum` subclassing pattern for typed string enums (e.g., `QueryType`, `RerankMode`, `FilterMode`)

### Example — type hints and docstring style:
```python
async def create_document(
    self,
    title: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    embedding: Optional[List[float]] = None,
    content_hash: Optional[str] = None,
) -> int:
    """Create a new document."""
    ...
```

### Example — dataclass with field defaults:
```python
@dataclass
class RAGPolicy:
    """Control parameters for RAG behavior."""
    version: str
    thresholds: Dict[str, float] = field(default_factory=lambda: {
        "high": 0.75, "medium": 0.50
    })
```

---

## Naming Conventions

| Entity | Convention | Example |
|---|---|---|
| Files | `snake_case.py` | `hybrid_retriever.py`, `query_classifier.py` |
| Classes | `PascalCase` | `HybridRetriever`, `DocumentRepository`, `OllamaClient` |
| Functions/Methods | `snake_case` | `process_article()`, `generate_embedding()` |
| Variables | `snake_case` | `document_id`, `embedding_str`, `lexical_weight` |
| Constants | `UPPER_CASE` | `POLL_INTERVAL`, `POLL_TIMEOUT`, `API_KEY` |
| Private/internal | `_leading_underscore` | `_normalize_database_url()` |
| Celery tasks | `{verb}_{noun}_task` | `process_article_task`, `update_embeddings_task` |
| Pydantic models | `{Entity}Create`, `{Entity}Query` | `ArticleCreate`, `RAGQuery`, `SearchQuery` |

---

## Error Handling

- **Raise with message**: Exceptions are raised with descriptive strings including context:
  ```python
  raise Exception(f"Embedding generation failed: {response.status_code} - {response.text}")
  ```
- **Try/except with logging**: Errors are caught, logged with context, then re-raised or handled:
  ```python
  except Exception as exc:
      logger.error(f"Failed to process article '{title}': {str(exc)}")
      raise self.retry(countdown=countdown, exc=exc)
  ```
- **Celery retry pattern**: Celery tasks use `self.retry(countdown=2 ** self.request.retries, exc=exc)` for exponential backoff (max 3 retries)
- **FastAPI HTTPExceptions**: Auth and validation failures raise typed `HTTPException` with explicit status codes:
  ```python
  raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid API key",
  )
  ```
- **Graceful fallback**: Some errors return `False`/default values instead of raising (e.g., `check_model_available()` catches all exceptions and returns `False`)
- **HTTP response status checks**: Checked inline before parsing:
  ```python
  if response.status_code != 200:
      raise Exception(f"Text generation failed: {response.status_code} - {response.text}")
  ```

---

## Logging

- **Setup pattern**: Every module declares a module-level logger immediately after imports:
  ```python
  logger = logging.getLogger(__name__)
  ```
- **Root config**: The API entrypoint sets the root log level:
  ```python
  logging.basicConfig(level=logging.INFO)
  ```
- **Log levels used**:
  - `logger.info(...)` — successful operations, task completion, retry announcements
  - `logger.error(...)` — caught exceptions and failures, always includes context string
  - `logger.debug(...)` — used in retrieval and search modules for verbose diagnostics
- **F-string messages with context**:
  ```python
  logger.info(f"Successfully processed article: {title}")
  logger.error(f"Failed to process article '{title}': {str(exc)}")
  logger.info(f"Retrying article processing in {countdown} seconds...")
  ```

---

## Async Patterns

### Standard async/await with `asyncpg`
All database operations open a connection via an `asynccontextmanager` context manager:
```python
@asynccontextmanager
async def get_async_connection_context(self):
    conn = await self.get_async_connection()
    try:
        yield conn
    finally:
        await conn.close()

async def create_document(self, ...) -> int:
    async with self.db.get_async_connection_context() as conn:
        row = await conn.fetchrow(query, ...)
        return row["id"]
```

### HTTP calls with `httpx.AsyncClient`
All outbound HTTP calls use `httpx.AsyncClient` as a context manager with explicit timeouts:
```python
async with httpx.AsyncClient(timeout=60.0) as client:
    response = await client.post(url, json={...})
```

### Celery tasks bridging sync→async
Celery workers are sync, so they spawn a new event loop to call async code:
```python
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    result = loop.run_until_complete(async_function(...))
finally:
    loop.close()
```

### FastAPI lifecycle
The application uses `@asynccontextmanager` on a lifespan function for startup/shutdown:
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown
```

---

## Code Organization

### Import ordering (PEP 8 + project convention)
```python
# 1. Standard library
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

# 2. Third-party
import httpx
from fastapi import Depends, FastAPI, HTTPException

# 3. Local / shared
from shared.database import document_repo
from shared.ollama_client import OllamaClient

# 4. Package-relative (API internal)
from .query_classifier import QueryClassifier, QueryType
from auth import require_api_key

# 5. Module-level logger
logger = logging.getLogger(__name__)
```

### Module structure patterns
- `shared/` — reusable business logic, repositories, clients mounted into both `api/` and `worker/`
- `api/` — FastAPI application, Pydantic request/response models, auth, routing logic
- `worker/` — Celery app configuration, task definitions
- `tests/` — all tests; no sub-packages, flat layout
- `migrations/` — numbered SQL migration files (`001_`, `003_`, etc.)
- `scripts/` — standalone utility and benchmark scripts

### Pydantic model placement
All request/response Pydantic models are defined at the top of `api/app.py` before endpoint functions, using `Field(...)` with validation constraints:
```python
class ArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    chunk_size: Optional[int] = Field(default=500, ge=100, le=2000)
```

### Singleton repository pattern
Repositories are instantiated once at module level and imported as singletons:
```python
# In shared/database.py
db_manager = DatabaseManager()
document_repo = DocumentRepository(db_manager)

# In api/app.py
from shared.database import document_repo
```

### Class attribute documentation
Complex classes document all attributes in the class docstring using `Attributes:` block:
```python
class HybridRetriever:
    """Two-stage hybrid retriever: lexical + vector → merge → rerank.
    
    Attributes:
        document_repo: Repository for database operations
        lexical_weight: Weight for lexical scores in final blend (default 0.35)
        ...
    """
```
