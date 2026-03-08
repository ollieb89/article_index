# Directory Structure

## Layout

```
article_index/
│
├── api/                          # FastAPI REST service
│   ├── app.py                    # Main application: all endpoints, lifespan, Pydantic models
│   ├── auth.py                   # require_api_key() dependency
│   ├── query_classifier.py       # QueryClassifier + QueryType enum (Phase 14)
│   ├── evidence_shape.py         # EvidenceShapeExtractor (Phase 14)
│   ├── retrieval_state.py        # RetrievalStateLabeler + RetrievalState enum (Phase 14)
│   ├── routing.py                # ContextualRouter, RoutingContext, RouteDecision (Phase 14)
│   ├── requirements.txt          # API-specific pip deps
│   ├── Dockerfile                # Multi-stage build; copies shared/ + api/
│   └── __init__.py
│
├── worker/                       # Celery worker service
│   ├── tasks.py                  # All @celery_app.task definitions
│   ├── celery_app.py             # Celery instance + config (broker, backend, task discovery)
│   ├── app.py                    # Worker entry point (imports celery_app for startup)
│   ├── requirements.txt          # Worker-specific pip deps
│   ├── Dockerfile                # Builds worker image; shared/ is volume-mounted in dev
│   └── shared/                   # Symlink or bind-mount of shared/ (dev only)
│
├── shared/                       # Shared library – mounted into both api and worker
│   ├── database.py               # DatabaseManager, DocumentRepository, PolicyRepository
│   ├── ollama_client.py          # OllamaClient (async httpx) + TextProcessor
│   ├── processor.py              # ArticleProcessor, EmbeddingManager
│   ├── hybrid_retriever.py       # HybridRetriever (lexical + vector merge)
│   ├── reranker.py               # Reranker (optional third stage)
│   ├── rerank_policy.py          # RerankPolicy, RerankDecision, RerankMode
│   ├── context_builder.py        # ContextBuilder (formats chunk context for prompts)
│   ├── context_filter.py         # ContextFilter, FilterMode (drop low-quality chunks)
│   ├── evidence_scorer.py        # EvidenceScorer, ConfidenceScore, ConfidenceBand
│   ├── citation_tracker.py       # CitationTracker (maps [N] citations to source chunks)
│   ├── query_transformer.py      # QueryTransformer, TransformMode (query expansion/reform)
│   ├── policy.py                 # RAGPolicy dataclass + PolicyRegistry
│   ├── telemetry.py              # PolicyTrace (per-request telemetry struct)
│   ├── rss_parser.py             # RSSFeedParser (feedparser wrapper)
│   ├── url_ingestion.py          # fetch_url_text() with SSRF protection
│   ├── celery_client.py          # Lightweight Celery client used by API to dispatch tasks
│   ├── __init__.py
│   │
│   └── evaluation/               # Offline evaluation & calibration sub-package
│       ├── calibration.py        # Threshold calibration logic
│       ├── policy_evaluator.py   # Evaluates policy versions against labelled data
│       ├── threshold_tuner.py    # Auto-tunes similarity thresholds
│       └── __init__.py
│
├── tests/                        # Pytest test suite
│   ├── conftest.py               # Fixtures (API base URL, headers, etc.)
│   ├── test_async_ingestion.py   # Integration: async article ingest pipeline
│   ├── test_async_failure.py     # Integration: error and retry behaviour
│   ├── test_calibration.py       # Unit: calibration logic
│   ├── test_contextual_router.py # Unit: ContextualRouter decisions
│   ├── test_control_loop.py      # Unit: policy control loop
│   ├── test_evidence_shape.py    # Unit: EvidenceShapeExtractor
│   ├── test_hnsw_plan.py         # Unit: HNSW index planning
│   ├── test_policy_routing.py    # Unit: RAGPolicy + routing rules
│   ├── test_query_classifier.py  # Unit: QueryClassifier patterns
│   ├── test_query_transformations.py  # Unit: QueryTransformer modes
│   ├── test_retrieval_state.py   # Unit: RetrievalStateLabeler
│   └── test_selective_reranking.py    # Unit: RerankPolicy selective mode
│
├── migrations/                   # SQL migration scripts (applied manually)
│   ├── 001_add_content_hash.sql
│   ├── 003_add_rss_support.sql
│   ├── 004_add_hybrid_search.sql
│   ├── 005_add_policy_optimization.sql
│   └── 006_contextual_policy_routing.sql
│
├── scripts/                      # Utility and evaluation scripts
│   ├── smoke_test.sh             # Basic end-to-end smoke test (curl)
│   ├── benchmark_hnsw.py         # HNSW performance benchmarking
│   ├── benchmark_queries.json    # Query set for benchmarks
│   ├── tune_thresholds.py        # CLI: run threshold tuning
│   ├── calibration_report.py     # Prints calibration results
│   ├── run_calibration_audit.py  # Runs full calibration audit
│   ├── policy_regret_analysis.py # Compares policy versions
│   ├── replay_policy.py          # Replays historical traces under new policy
│   ├── verify_phase14.py         # Smoke-tests Phase 14 modules
│   └── test_rss_ingestion.py     # Manual RSS ingestion test
│
├── evaluation/                   # Evaluation data
│   └── default_test_suite.json   # Labelled Q&A pairs for offline evaluation
│
├── docs/                         # Development documentation
│   ├── BENCHMARK_GUIDE.md
│   ├── HNSW_TUNING_GUIDE.md
│   └── plans/                    # Phase planning documents (HNSW, hybrid search, phases 7–14)
│
├── schema.sql                    # Full database schema (auto-applied by DB container)
├── indexes.sql                   # Vector + FTS performance indexes (auto-applied)
├── docker-compose.yml            # Five-service orchestration
├── Makefile                      # Developer workflow targets (up, down, test, smoke, logs…)
├── pyproject.toml                # Project metadata + shared tool config
├── requirements-shared.txt       # Dependencies for the shared/ library
├── requirements-dev.txt          # Development-only deps (pytest, httpx, etc.)
├── main.py                       # Local dev entry point
├── test_api.sh                   # Manual API test script (curl)
└── AGENTS.md                     # AI coding-agent guide for this repo
```

---

## Key Locations

| What you're looking for | Where to find it |
|------------------------|-----------------|
| All HTTP endpoints | `api/app.py` – one file, ~2310 lines |
| API key auth | `api/auth.py` → `require_api_key()` FastAPI dependency |
| Pydantic request/response models | `api/app.py` (top ~200 lines) |
| Article processing pipeline | `shared/processor.py` → `ArticleProcessor` |
| Database queries + SQL | `shared/database.py` → `DocumentRepository` |
| Hybrid search logic | `shared/hybrid_retriever.py` → `HybridRetriever` |
| Reranking pipeline | `shared/reranker.py` + `shared/rerank_policy.py` |
| RAG prompt templates | `api/app.py` – `RAG_PROMPT_TEMPLATE`, `RAG_CONSERVATIVE_PROMPT_TEMPLATE` |
| Evidence confidence scoring | `shared/evidence_scorer.py` → `EvidenceScorer` |
| Query type classification | `api/query_classifier.py` → `QueryClassifier` |
| Contextual routing decisions | `api/routing.py` → `ContextualRouter` |
| Policy (thresholds, routing rules) | `shared/policy.py` → `RAGPolicy` |
| Request telemetry struct | `shared/telemetry.py` → `PolicyTrace` |
| Background task definitions | `worker/tasks.py` |
| Celery configuration | `worker/celery_app.py` |
| RSS parsing | `shared/rss_parser.py` → `RSSFeedParser` |
| URL fetch + SSRF protection | `shared/url_ingestion.py` → `fetch_url_text()` |
| Ollama AI client | `shared/ollama_client.py` → `OllamaClient` |
| Text chunking (tiktoken) | `shared/ollama_client.py` → `TextProcessor.chunk_text()` |
| DB schema | `schema.sql` |
| Vector + FTS indexes | `indexes.sql` |
| Migration history | `migrations/` (numbered, sequential) |
| Integration tests | `tests/test_async_*.py` |
| Unit tests | `tests/test_*.py` (non-async) |
| Offline evaluation/calibration | `shared/evaluation/` + `scripts/` |

---

## Naming Conventions

### Files

- **`snake_case.py`** for all Python modules  
- **Migration files**: `NNN_description.sql` (three-digit prefix, sequential)
- **Test files**: `test_<feature>.py`
- **Plan documents**: `docs/plans/<feature>.md`

### Python Symbols

| Kind | Convention | Examples |
|------|-----------|---------|
| Classes | `PascalCase` | `ArticleProcessor`, `HybridRetriever`, `RAGPolicy` |
| Functions / methods | `snake_case` | `process_article()`, `generate_embedding()` |
| Constants | `UPPER_CASE` | `RAG_PROMPT_TEMPLATE`, `RAG_ABSTAIN_RESPONSE` |
| Private members | `_leading_underscore` | `_normalize_database_url()` |
| Module-level logger | `logger = logging.getLogger(__name__)` | (standard across all files) |
| Async functions | prefixed semantically, not syntactically | `async def process_article()` |

### Docker / Services

- Container names: `article_index-<service>` (e.g. `article_index-api`, `article_index-db`)
- Environment variable names: `UPPER_SNAKE_CASE` matching Python `os.getenv()` names

---

## Module Organization

### `shared/` – Shared Library

Organised by **concern layer**, not by feature:

```
Layer 1 – Infrastructure
  database.py          connection management + SQL repositories
  ollama_client.py     Ollama API client + text utilities

Layer 2 – Processing
  processor.py         ingest pipeline (clean → hash → embed → chunk → store)
  rss_parser.py        feed parsing
  url_ingestion.py     SSRF-safe URL fetching

Layer 3 – Retrieval
  hybrid_retriever.py  lexical + vector merge
  reranker.py          optional reranking stage
  rerank_policy.py     rerank decision logic
  context_filter.py    context quality gate

Layer 4 – RAG Intelligence
  query_transformer.py query expansion / reformulation
  evidence_scorer.py   confidence scoring
  context_builder.py   prompt context assembly
  citation_tracker.py  [N] citation mapping

Layer 5 – Policy / Telemetry
  policy.py            versioned RAGPolicy dataclass
  telemetry.py         PolicyTrace struct

Layer 6 – Evaluation (sub-package: shared/evaluation/)
  calibration.py
  policy_evaluator.py
  threshold_tuner.py
```

### `api/` – FastAPI Service

Thin orchestration layer. Imports from `shared/` for all business logic. Phase-14 modules (`query_classifier`, `evidence_shape`, `retrieval_state`, `routing`) live here because they are specific to the request-handling path and have no worker or script consumers:

```
app.py          all endpoints + lifespan + Pydantic models (~2300 lines)
auth.py         API key gate
query_classifier.py   QueryType + pattern rules
evidence_shape.py     structural chunk analysis
retrieval_state.py    RetrievalState labelling
routing.py            ContextualRouter (combines all signals)
```

### `worker/` – Celery Worker

Minimal: only task definitions and configuration. Business logic stays in `shared/`.

```
celery_app.py   create_app(), broker/backend config
tasks.py        @celery_app.task decorated functions (bridge async→sync)
app.py          entry point (imports celery_app to trigger task registration)
```

### `tests/` – Test Suite

One test file per conceptual feature/phase. Integration tests are annotated `@pytest.mark.integration` and require a running stack. Unit tests mock or stub all external I/O.
