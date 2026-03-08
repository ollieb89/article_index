import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared.ollama_client import OllamaClient
from shared.database import document_repo, get_db_connection, db_manager, policy_repo
from shared.processor import article_processor, embedding_manager
from shared.celery_client import celery_app
from shared.hybrid_retriever import HybridRetriever
from shared.context_builder import ContextBuilder
from shared.reranker import Reranker
from shared.rerank_policy import RerankPolicy, RerankMode
from shared.query_transformer import QueryTransformer, TransformMode
from shared.context_filter import ContextFilter, FilterMode
from shared.evidence_scorer import EvidenceScorer, ConfidenceBand
from shared.citation_tracker import CitationTracker
from shared.policy import RAGPolicy
from shared.telemetry import PolicyTrace
from celery.result import AsyncResult

# Phase 14 Imports
from .query_classifier import QueryClassifier, QueryType
from .evidence_shape import EvidenceShapeExtractor
from .retrieval_state import RetrievalStateLabeler, RetrievalState
from .routing import ContextualRouter, RoutingContext
from .uncertainty_gates import UncertaintyDetector

from auth import require_api_key
from shared.url_ingestion import fetch_url_text

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Pydantic models
class ArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    chunk_size: Optional[int] = Field(default=500, ge=100, le=2000)
    chunk_overlap: Optional[int] = Field(default=50, ge=0, le=200)


class HTMLArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    html_content: str = Field(..., min_length=1)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class SearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: Optional[int] = Field(default=5, ge=1, le=20)
    similarity_threshold: Optional[float] = Field(default=0.7, ge=0.0, le=1.0)
    search_type: Optional[str] = Field(default="chunks", pattern="^(chunks|documents)$")


class RAGQuery(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    context_limit: Optional[int] = Field(default=5, ge=1, le=10)
    similarity_threshold: Optional[float] = Field(default=0.7, ge=0.0, le=1.0)
    model: Optional[str] = Field(default=None)
    query_type: Optional[str] = Field(default="general")


class BatchArticles(BaseModel):
    articles: List[ArticleCreate] = Field(..., min_items=1, max_items=10)
    max_concurrent: Optional[int] = Field(default=3, ge=1, le=5)


class URLArticleCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    title: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class FeedCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    title: Optional[str] = None
    max_entries: Optional[int] = Field(default=50, ge=1, le=200)
    auto_process: Optional[bool] = Field(default=True)
    fetch_interval_minutes: Optional[int] = Field(default=60, ge=15, le=1440)


class FeedStats(BaseModel):
    id: int
    url: str
    title: Optional[str]
    is_active: bool
    last_fetched_at: Optional[str]
    last_entry_at: Optional[str]
    total_entries_fetched: int
    total_entries_processed: int
    pending_entries: int
    processed_entries: int
    error_entries: int
    success_rate: Optional[float]


# Hybrid search models
class HybridSearchQuery(BaseModel):
    """Query for hybrid search endpoint."""
    query: str = Field(..., min_length=1, max_length=1000)
    limit: Optional[int] = Field(default=10, ge=1, le=20)
    lexical_limit: Optional[int] = Field(default=30, ge=1, le=100)
    vector_limit: Optional[int] = Field(default=40, ge=1, le=100)
    lexical_weight: Optional[float] = Field(default=0.35, ge=0.0, le=1.0)
    semantic_weight: Optional[float] = Field(default=0.65, ge=0.0, le=1.0)


class SourceCitation(BaseModel):
    """Citation metadata for RAG answers."""
    citation_number: int
    chunk_id: int
    document_id: int
    title: str
    score: float
    from_lexical: bool
    from_vector: bool
    collapsed_from: int = 1


class HybridRAGResponse(BaseModel):
    """Enhanced RAG response with citations and hybrid search metadata."""
    question: str
    answer: str
    context: Optional[str] = None  # For backward compatibility
    sources: List[int] = []  # Backward compatible: list of document IDs
    source_citations: List[SourceCitation] = []  # New detailed format
    chunks_used: int
    chunks_dropped: int
    token_count: int
    documents_used: List[int]
    hybrid_search: bool = True  # Flag to indicate enhanced response


# Prompt templates for hybrid RAG
RAG_PROMPT_TEMPLATE = """You are a helpful assistant. Answer the question based ONLY on the provided context.
If the context doesn't contain relevant information, say "I don't have enough information."

Context:
{context}

Question: {question}

Provide a clear, accurate answer based on the context above.
When referencing information, cite the source using [number] format.

Answer:"""

# Phase 2: Medium confidence prompt with light hedging
RAG_MEDIUM_CONFIDENCE_PROMPT = """You are a helpful assistant. Answer the question based primarily on the provided context.

**Guidelines:**
- Base your answer on the retrieved sources
- Acknowledge when evidence is from multiple sources or comes from different perspectives
- Use phrases like "Based on the available sources..." or "The material suggests..."
- When evidence is limited, indicate the constraint
- Cite sources where appropriate

Context:
{context}

Question: {question}

Answer:"""

# Phase 11: Conservative prompt for LOW confidence
RAG_CONSERVATIVE_PROMPT_TEMPLATE = """You are a highly cautious assistant. 
Answer the question using ONLY the provided context. 
The evidence for this answer is not very strong, so you MUST be extremely literal and avoid any inference.
If the context does not explicitly state the answer, say "I cannot confirm this with high certainty from the available sources."
DO NOT speculate.

Context:
{context}

Question: {question}

Answer (Strictly from context):"""

# Phase 11: Abstention response for INSUFFICIENT confidence
RAG_ABSTAIN_RESPONSE = "I'm sorry, but I don't have enough reliable information in my database to answer your question accurately. Please try rephrasing your query or asking about a different topic."


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the FastAPI application."""
    # ============ STARTUP (all before yield) ============
    logger.info("Starting up article index API...")
    
    # Ensure Ollama models are available
    models_status = await embedding_manager.ensure_models_available()
    logger.info(f"Model availability: {models_status}")
    
    # Load default/active policy
    try:
        policy_data = await policy_repo.get_active_policy()
        if policy_data:
            app.state.active_policy = RAGPolicy.from_db_row(policy_data)
            logger.info(f"Loaded active policy version: {app.state.active_policy.version}")
        else:
            app.state.active_policy = RAGPolicy(version="default-fallback")
            logger.warning("No active policy found, using default fallback.")
    except Exception as e:
        logger.error(f"Failed to load policy: {e}")
        app.state.active_policy = RAGPolicy(version="error-fallback")

    # Phase 14 Module Initialization
    app.state.query_classifier = QueryClassifier()
    app.state.evidence_shape_extractor = EvidenceShapeExtractor()
    app.state.retrieval_state_labeler = RetrievalStateLabeler()
    app.state.contextual_router = ContextualRouter()
    
    # Phase 2: Uncertainty detector for confidence routing
    app.state.uncertainty_detector = UncertaintyDetector()
    logger.info("Uncertainty detector initialized for Phase 2 confidence routing")

    await ollama_client.initialize()
    
    # Support both old HYBRID_USE_RRF and new HYBRID_RANKING_MODE
    ranking_mode = os.getenv('HYBRID_RANKING_MODE', '').lower()
    if not ranking_mode:
        # Fall back to legacy HYBRID_USE_RRF
        use_rrf = os.getenv('HYBRID_USE_RRF', 'false').lower() == 'true'
        ranking_mode = 'rrf' if use_rrf else 'weighted'
    
    app.state.hybrid_retriever = HybridRetriever(
        document_repo=document_repo,
        lexical_weight=float(os.getenv('HYBRID_LEXICAL_WEIGHT', '0.35')),
        semantic_weight=float(os.getenv('HYBRID_SEMANTIC_WEIGHT', '0.65')),
        lexical_limit=int(os.getenv('HYBRID_LEXICAL_LIMIT', '30')),
        vector_limit=int(os.getenv('HYBRID_VECTOR_LIMIT', '40')),
        use_rrf=(ranking_mode == 'rrf'),
        auto_tune_weights=os.getenv('HYBRID_AUTO_TUNE_WEIGHTS', 'true').lower() == 'true'
    )
    logger.info(f"Hybrid ranking mode: {ranking_mode}")
    
    # Initialize query transformer
    transform_mode = os.getenv('QUERY_TRANSFORM_MODE', 'off').lower()
    if transform_mode in ('always', 'selective'):
        app.state.query_transformer = QueryTransformer(
            mode=transform_mode,
            max_expanded_queries=int(os.getenv('QUERY_TRANSFORM_MAX_QUERIES', '3')),
            enable_multi_query=os.getenv('QUERY_TRANSFORM_MULTIQUERY', 'true').lower() == 'true',
            enable_step_back=os.getenv('QUERY_TRANSFORM_STEPBACK', 'true').lower() == 'true',
            min_query_words=int(os.getenv('QUERY_TRANSFORM_MIN_WORDS', '4')),
            ambiguity_threshold=int(os.getenv('QUERY_TRANSFORM_AMBIGUITY', '1'))
        )
        logger.info(
            f"Query transformer initialized: mode={transform_mode}, "
            f"max_queries={os.getenv('QUERY_TRANSFORM_MAX_QUERIES', '3')}"
        )
    else:
        app.state.query_transformer = None
        logger.info("Query transformer disabled")
    
    # Initialize context filter (Phase 9)
    evidence_mode = os.getenv('EVIDENCE_AWARE_MODE', 'off').lower()
    if evidence_mode in ('always', 'selective'):
        app.state.context_filter = ContextFilter(
            mode=evidence_mode,
            dedup_threshold=float(os.getenv('CONTEXT_DEDUP_THRESHOLD', '0.85')),
            max_chunks_per_doc=int(os.getenv('CONTEXT_MAX_PER_DOC', '2')),
            min_score_threshold=float(os.getenv('CONTEXT_MIN_SCORE', '0.3')),
            max_total_chunks=int(os.getenv('CONTEXT_MAX_CHUNKS', '8')),
            remove_boilerplate=os.getenv('CONTEXT_FILTER_BOILERPLATE', 'true').lower() == 'true'
        )
        app.state.evidence_scorer = EvidenceScorer()
        app.state.citation_tracker = CitationTracker()
        logger.info(
            f"Evidence-aware retrieval initialized: mode={evidence_mode}, "
            f"max_per_doc={os.getenv('CONTEXT_MAX_PER_DOC', '2')}"
        )
    else:
        app.state.context_filter = None
        app.state.evidence_scorer = None
        app.state.citation_tracker = None
        logger.info("Evidence-aware retrieval disabled")
    
    # Initialize reranker with new selective mode support
    rerank_mode = os.getenv('RERANK_MODE', 'off').lower()
    
    # Legacy fallback: RERANK_ENABLED=true maps to mode='always'
    if rerank_mode == 'off' and os.getenv('RERANK_ENABLED', 'false').lower() == 'true':
        rerank_mode = 'always'
    
    if rerank_mode in ('always', 'selective'):
        # Create policy based on mode
        policy = RerankPolicy(
            mode=rerank_mode,
            score_gap_threshold=float(os.getenv('RERANK_SCORE_GAP', '0.03')),
            disagreement_threshold=float(os.getenv('RERANK_DISAGREEMENT', '0.40')),
            min_top_score=float(os.getenv('RERANK_MIN_TOP_SCORE', '0.55')),
            complex_query_words=int(os.getenv('RERANK_COMPLEX_QUERY_WORDS', '12'))
        )
        
        app.state.reranker = Reranker(
            hybrid_retriever=app.state.hybrid_retriever,
            policy=policy,
            top_n=int(os.getenv('RERANK_TOP_N', '30')),
            final_k=int(os.getenv('RERANK_FINAL_K', '10')),
            model=os.getenv('RERANK_MODEL', 'cross_encoder'),
            use_cross_encoder=os.getenv('RERANK_USE_CROSS_ENCODER', 'true').lower() == 'true'
        )
        
        logger.info(
            f"Reranker initialized: mode={rerank_mode}, "
            f"top_n={os.getenv('RERANK_TOP_N', '30')}, "
            f"model={os.getenv('RERANK_MODEL', 'cross_encoder')}"
        )
        
        if rerank_mode == 'selective':
            config = policy.get_config()
            logger.info(
                f"Selective reranking triggers: "
                f"score_gap<{config['score_gap_threshold']}, "
                f"disagreement>{config['disagreement_threshold']}, "
                f"min_score<{config['min_top_score']}, "
                f"complex_words>={config['complex_query_words']}"
            )
    else:
        app.state.reranker = None
        logger.info("Reranker disabled (mode=off)")
    
    app.state.context_builder = ContextBuilder(
        max_context_tokens=int(os.getenv('CONTEXT_MAX_TOKENS', '3000')),
        max_per_document=int(os.getenv('CONTEXT_MAX_PER_DOCUMENT', '2')),
        collapse_adjacent=os.getenv('CONTEXT_COLLAPSE_ADJACENT', 'true').lower() == 'true',
        include_citations=os.getenv('CONTEXT_INCLUDE_CITATIONS', 'true').lower() == 'true'
    )
    
    # Feature flag: make hybrid the default for /rag
    app.state.use_hybrid_rag = os.getenv('USE_HYBRID_RAG', 'false').lower() == 'true'
    logger.info(f"Hybrid search default: {app.state.use_hybrid_rag}")
    
    # Initialize HNSW search parameters
    hnsw_ef_search = int(os.getenv('HNSW_EF_SEARCH', '40'))
    try:
        await db_manager.set_search_params(ef_search=hnsw_ef_search)
        logger.info(f"HNSW ef_search set to {hnsw_ef_search}")
    except Exception as e:
        logger.warning(f"Could not set HNSW ef_search: {e}")
    
    logger.info("Startup complete")
    
    # ============ YIELD (one time, after startup) ============
    yield
    
    # ============ SHUTDOWN (after yield) ============
    logger.info("Shutting down article index API...")
    await ollama_client.close()


# Create FastAPI app
app = FastAPI(
    title="Article Index API",
    description="Semantic search and RAG for articles using pgvector and Ollama",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Endpoints

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Article Index API",
        "version": "1.0.0",
        "endpoints": [
            "/health",
            "/stats",
            "/articles/",
            "/articles/async",
            "/articles/url/async",
            "/tasks/{task_id}",
            "/feeds/",
            "/feeds/async",
            "/feeds/{feed_id}/stats",
            "/search",
            "/search/hybrid",
            "/rag",
            "/admin"
        ]
    }


@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint."""
    try:
        # Test database connection
        stats = await document_repo.get_stats()
        
        # Test Ollama connection
        ollama = OllamaClient()
        embedding_test = await embedding_manager.test_embedding_generation()
        generation_test = await embedding_manager.test_text_generation()
        
        # Hybrid search status
        hybrid_status = {
            "available": hasattr(request.app.state, 'hybrid_retriever'),
            "default_mode": "hybrid" if getattr(request.app.state, 'use_hybrid_rag', False) else "vector"
        }
        
        return {
            "status": "healthy",
            "database": "connected",
            "ollama_embeddings": "working" if embedding_test else "failed",
            "ollama_generation": "working" if generation_test else "failed",
            "hybrid_search": hybrid_status,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@app.get("/stats")
async def get_stats():
    """Get database statistics including vector index info."""
    try:
        stats = await document_repo.get_stats()
        
        # Add HNSW index statistics
        try:
            index_stats = await document_repo.get_index_stats()
            stats['vector_indexes'] = index_stats
        except Exception as e:
            logger.warning(f"Could not retrieve index stats: {e}")
            stats['vector_indexes'] = {'error': str(e)}
        
        return stats
    except Exception as e:
        logger.error(f"Failed to get stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve statistics: {str(e)}")


@app.post("/articles/")
async def create_article(
    article: ArticleCreate,
    _: None = Depends(require_api_key),
):
    """Create a new article with embeddings (synchronous - blocks until complete)."""
    try:
        result = await article_processor.process_article(
            title=article.title,
            content=article.content,
            metadata=article.metadata,
            chunk_size=article.chunk_size,
            chunk_overlap=article.chunk_overlap
        )
        return result
    except Exception as e:
        logger.error(f"Failed to create article: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create article: {str(e)}")


@app.post("/articles/async")
async def create_article_async(
    article: ArticleCreate,
    _: None = Depends(require_api_key),
):
    """Enqueue article for background processing. Returns task ID for status polling."""
    try:
        task = celery_app.send_task(
            "tasks.process_article_task",
            args=[
                article.title,
                article.content,
            ],
            kwargs={
                "metadata": article.metadata or {},
                "chunk_size": article.chunk_size,
                "chunk_overlap": article.chunk_overlap,
            },
        )
        return {
            "task_id": task.id,
            "status": "accepted",
            "message": "Article queued for processing",
        }
    except Exception as e:
        logger.error(f"Failed to enqueue article: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to enqueue article: {str(e)}"
        )


@app.post("/articles/html")
async def create_html_article(
    article: HTMLArticleCreate,
    _: None = Depends(require_api_key),
):
    """Create a new article from HTML content (synchronous)."""
    try:
        result = await article_processor.process_html_article(
            title=article.title,
            html_content=article.html_content,
            metadata=article.metadata
        )
        return result
    except Exception as e:
        logger.error(f"Failed to create HTML article: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create HTML article: {str(e)}")


@app.post("/articles/html/async")
async def create_html_article_async(
    article: HTMLArticleCreate,
    _: None = Depends(require_api_key),
):
    """Enqueue HTML article for background processing. Returns task ID for status polling."""
    try:
        task = celery_app.send_task(
            "tasks.process_html_article_task",
            args=[article.title, article.html_content],
            kwargs={"metadata": article.metadata or {}},
        )
        return {
            "task_id": task.id,
            "status": "accepted",
            "message": "HTML article queued for processing",
        }
    except Exception as e:
        logger.error(f"Failed to enqueue HTML article: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to enqueue HTML article: {str(e)}"
        )


@app.post("/articles/url/async")
async def create_url_article_async(
    article: URLArticleCreate,
    _: None = Depends(require_api_key),
):
    """Fetch URL, extract text, enqueue for background processing."""
    try:
        title, content = await fetch_url_text(article.url)
        effective_title = article.title or title
        metadata = {**(article.metadata or {}), "source_url": article.url}

        task = celery_app.send_task(
            "tasks.process_article_task",
            args=[effective_title, content],
            kwargs={
                "metadata": metadata,
                "chunk_size": 500,
                "chunk_overlap": 50,
            },
        )
        return {
            "task_id": task.id,
            "status": "accepted",
            "source_url": article.url,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch URL: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to enqueue URL article: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to enqueue URL article: {str(e)}"
        )


@app.post("/articles/batch")
async def create_batch_articles(
    batch: BatchArticles,
    _: None = Depends(require_api_key),
):
    """Create multiple articles in batch."""
    try:
        articles_data = [
            {
                "title": article.title,
                "content": article.content,
                "metadata": article.metadata,
                "chunk_size": article.chunk_size,
                "chunk_overlap": article.chunk_overlap
            }
            for article in batch.articles
        ]
        
        results = await article_processor.batch_process_articles(
            articles_data,
            max_concurrent=batch.max_concurrent
        )
        
        return {
            "processed_count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"Failed to create batch articles: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create batch articles: {str(e)}")


@app.get("/articles/")
async def list_articles(limit: int = 50, offset: int = 0):
    """List articles with pagination."""
    try:
        articles = await document_repo.list_documents(limit=limit, offset=offset)
        return articles
    except Exception as e:
        logger.error(f"Failed to list articles: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list articles: {str(e)}")


@app.get("/articles/{article_id}")
async def get_article(article_id: int):
    """Get a specific article."""
    try:
        article = await document_repo.get_document(article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        
        # Get chunks for this article
        chunks = await document_repo.get_document_chunks(article_id)
        
        return {
            **article,
            "chunks": chunks
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get article {article_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve article: {str(e)}")


# RSS Feed endpoints
@app.post("/feeds/async")
async def create_feed_async(
    feed: FeedCreate,
    _: None = Depends(require_api_key),
):
    """Create RSS feed and start processing entries asynchronously."""
    try:
        # Validate feed URL format
        if not (feed.url.startswith("http://") or feed.url.startswith("https://")):
            raise HTTPException(status_code=400, detail="Invalid feed URL")
        
        # Dispatch feed processing task
        task = celery_app.send_task(
            "tasks.process_feed_task",
            args=[feed.url],
            kwargs={
                "max_entries": feed.max_entries,
                "auto_process_entries": feed.auto_process,
            },
        )
        
        return {
            "task_id": task.id,
            "status": "accepted",
            "message": "Feed processing started",
            "feed_url": feed.url
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start feed processing: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start feed processing: {str(e)}")


@app.get("/feeds/")
async def list_feeds(limit: int = 50, offset: int = 0):
    """List RSS feeds with pagination."""
    try:
        conn = await get_db_connection()
        
        query = """
        SELECT 
            id, url, title, is_active, last_fetched_at, last_entry_at,
            total_entries_fetched, total_entries_processed,
            fetch_interval_minutes, created_at, updated_at
        FROM intelligence.feeds 
        ORDER BY created_at DESC
        LIMIT $1 OFFSET $2
        """
        
        feeds = await conn.fetch(query, limit, offset)
        
        # Convert to dict format
        result = []
        for feed in feeds:
            result.append({
                "id": feed["id"],
                "url": feed["url"],
                "title": feed["title"],
                "is_active": feed["is_active"],
                "last_fetched_at": feed["last_fetched_at"].isoformat() if feed["last_fetched_at"] else None,
                "last_entry_at": feed["last_entry_at"].isoformat() if feed["last_entry_at"] else None,
                "total_entries_fetched": feed["total_entries_fetched"],
                "total_entries_processed": feed["total_entries_processed"],
                "fetch_interval_minutes": feed["fetch_interval_minutes"],
                "created_at": feed["created_at"].isoformat() if feed["created_at"] else None,
                "updated_at": feed["updated_at"].isoformat() if feed["updated_at"] else None,
            })
        
        return {"feeds": result, "total": len(result)}
        
    except Exception as e:
        logger.error(f"Failed to list feeds: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list feeds: {str(e)}")
    finally:
        await conn.close()


@app.get("/feeds/{feed_id}/stats")
async def get_feed_stats(feed_id: int):
    """Get detailed statistics for a specific feed."""
    try:
        conn = await get_db_connection()
        
        query = """
        SELECT * FROM intelligence.feed_stats 
        WHERE id = $1
        """
        
        stats = await conn.fetchrow(query, feed_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="Feed not found")
        
        return {
            "id": stats["id"],
            "url": stats["url"],
            "title": stats["title"],
            "is_active": stats["is_active"],
            "last_fetched_at": stats["last_fetched_at"].isoformat() if stats["last_fetched_at"] else None,
            "last_entry_at": stats["last_entry_at"].isoformat() if stats["last_entry_at"] else None,
            "total_entries_fetched": stats["total_entries_fetched"],
            "total_entries_processed": stats["total_entries_processed"],
            "pending_entries": stats["pending_entries"],
            "processed_entries": stats["processed_entries"],
            "error_entries": stats["error_entries"],
            "success_rate": stats["success_rate"],
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get feed stats {feed_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get feed statistics: {str(e)}")
    finally:
        await conn.close()


@app.post("/search")
async def search_articles(query: SearchQuery):
    """Search articles using semantic similarity."""
    try:
        # Generate embedding for the query
        ollama = OllamaClient()
        query_embedding = await ollama.generate_embedding(query.query)
        
        # Search based on type
        if query.search_type == "documents":
            results = await document_repo.find_similar_documents(
                embedding=query_embedding,
                limit=query.limit,
                similarity_threshold=query.similarity_threshold
            )
        else:  # chunks
            results = await document_repo.find_similar_chunks(
                embedding=query_embedding,
                limit=query.limit,
                similarity_threshold=query.similarity_threshold
            )
        
        return {
            "query": query.query,
            "search_type": query.search_type,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/search/hybrid")
async def hybrid_search(
    query: HybridSearchQuery,
    request: Request
):
    """
    Search using hybrid retrieval (lexical + semantic).
    
    Combines PostgreSQL full-text search with pgvector similarity
    for better retrieval of exact terms and semantic concepts.
    """
    try:
        retriever: HybridRetriever = request.app.state.hybrid_retriever
        
        # Update retriever weights if provided
        if query.lexical_weight is not None:
            retriever.lexical_weight = query.lexical_weight
            retriever.semantic_weight = query.semantic_weight
        
        # Update limits
        retriever.lexical_limit = query.lexical_limit
        retriever.vector_limit = query.vector_limit
        
        # Generate embedding for vector search
        ollama = OllamaClient()
        try:
            embedding = await ollama.generate_embedding(query.query)
        except Exception as e:
            logger.warning(f"Embedding failed: {e}, using lexical-only")
            embedding = None
        
        # Check if query transformer is configured
        query_transformer = getattr(request.app.state, 'query_transformer', None)
        transform_info = {"query_transform_enabled": False, "query_transform_mode": "off"}
        
        # Check if reranker is configured
        reranker = getattr(request.app.state, 'reranker', None)
        rerank_info = {"rerank_enabled": False, "rerank_mode": "off"}
        
        # Stage 1: Query transformation (if enabled)
        if query_transformer and query_transformer.mode != TransformMode.OFF:
            logger.debug(f"Using query transformer (mode={query_transformer.mode.value}) for query: {query.query[:50]}...")
            
            # Get latency budget from config (default 150ms)
            latency_budget = float(os.getenv('QUERY_TRANSFORM_LATENCY_BUDGET_MS', '150'))
            
            # Retrieve with transformation
            chunks, transform_decision, merge_metadata = await retriever.retrieve_with_transform(
                query=query.query,
                query_transformer=query_transformer,
                query_embedding=embedding,
                k=query.limit,
                latency_budget_ms=latency_budget
            )
            
            transform_info = {
                "query_transform_enabled": True,
                "query_transform_mode": query_transformer.mode.value,
                "query_transform_applied": transform_decision.should_transform if transform_decision else False,
                "query_transform_types": transform_decision.transform_types if transform_decision else [],
                "query_transform_reasons": transform_decision.trigger_reasons if transform_decision else [],
                "generated_queries": transform_decision.transformed_queries if transform_decision and transform_decision.should_transform else [],
                "query_count": len(transform_decision.transformed_queries) if transform_decision else 1
            }
            
            # Add debug info if transformation was applied
            if transform_decision and transform_decision.should_transform:
                transform_info["query_transform_confidence"] = round(transform_decision.confidence, 3)
                
                # Add merge metadata
                if merge_metadata:
                    transform_info["merge_metadata"] = {
                        "queries_used": merge_metadata.get('queries_used', 1),
                        "queries_planned": merge_metadata.get('queries_planned', 1),
                        "unique_chunks": merge_metadata.get('unique_chunks', 0),
                        "result_overlap": merge_metadata.get('result_overlap', 0.0),
                        "latency_ms": merge_metadata.get('latency_ms', 0),
                        "budget_exceeded": merge_metadata.get('budget_exceeded', False)
                    }
                    if 'budget_utilization' in merge_metadata:
                        transform_info["merge_metadata"]["budget_utilization"] = merge_metadata['budget_utilization']
        
        # Stage 2: Reranking (if enabled and not already done by transformer path)
        elif reranker and reranker.policy.mode.value != 'off':
            logger.debug(f"Using reranker (mode={reranker.policy.mode.value}) for query: {query.query[:50]}...")
            chunks, rerank_decision = await reranker.rerank_with_decision(
                query=query.query,
                query_embedding=embedding
            )
            rerank_info = {
                "rerank_enabled": True,
                "rerank_mode": reranker.policy.mode.value,
                "rerank_applied": rerank_decision.should_rerank if rerank_decision else False,
                "rerank_triggers": rerank_decision.triggers if rerank_decision else [],
                "rerank_confidence": round(rerank_decision.confidence, 3) if rerank_decision else 0.0,
                "rerank_top_n": reranker.top_n,
                "rerank_model": reranker.model
            }
            # Add debug info if reranking was triggered
            if rerank_decision and rerank_decision.should_rerank:
                rerank_info["rerank_explanation"] = rerank_decision.explanation
                rerank_info["rerank_trigger_details"] = rerank_decision.trigger_details
        
        # Stage 3: Standard hybrid retrieval (no transformation or reranking)
        else:
            chunks = await retriever.retrieve(
                query.query,
                embedding,
                k=query.limit * 2  # Get extra for filtering
            )
        
        # Stage 4: Context filtering (Phase 9)
        evidence_info = {"evidence_aware_enabled": False}
        context_filter = getattr(request.app.state, 'context_filter', None)
        
        if context_filter and context_filter.mode != FilterMode.OFF:
            logger.debug(f"Applying context filtering to {len(chunks)} chunks")
            
            filter_result = context_filter.filter_chunks(chunks, query=query.query)
            chunks = filter_result.chunks
            
            evidence_info = {
                "evidence_aware_enabled": True,
                "evidence_mode": context_filter.mode.value,
                "filters_applied": filter_result.filters_applied,
                "chunks_filtered": filter_result.removed_count,
                "compression_ratio": round(filter_result.compression_ratio, 2)
            }
            
            if filter_result.filter_metadata:
                evidence_info["filter_stages"] = filter_result.filter_metadata.get('stages', [])
        
        # Stage 5: Evidence confidence scoring (Phase 9)
        evidence_scorer = getattr(request.app.state, 'evidence_scorer', None)
        
        if evidence_scorer and chunks:
            # Get rerank/transform decisions for scoring
            rerank_decision = None
            if reranker and reranker.policy.mode.value != 'off' and 'rerank_decision' in locals():
                rerank_decision = locals().get('rerank_decision')
            
            transform_metadata = None
            if query_transformer and query_transformer.mode != TransformMode.OFF:
                transform_metadata = transform_info.get('merge_metadata')
            
            confidence = evidence_scorer.score_evidence(
                chunks=chunks,
                query=query.query,
                rerank_decision=rerank_decision,
                transform_metadata=transform_metadata
            )
            
            evidence_info["retrieval_confidence"] = confidence.to_dict()
        
        return {
            "query": query.query,
            "results": chunks,
            "count": len(chunks),
            "config": {
                "lexical_weight": retriever.lexical_weight,
                "semantic_weight": retriever.semantic_weight,
                "lexical_candidates": retriever.lexical_limit,
                "vector_candidates": retriever.vector_limit,
                **transform_info,
                **rerank_info,
                **evidence_info
            }
        }
        
    except Exception as e:
        logger.error(f"Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


async def _rag_vector_only(query: RAGQuery) -> Dict[str, Any]:
    """Original vector-only RAG (backward compatibility)."""
    ollama = OllamaClient()
    question_embedding = await ollama.generate_embedding(query.question)
    
    context_result = await document_repo.get_rag_context(
        embedding=question_embedding,
        limit=query.context_limit,
        similarity_threshold=query.similarity_threshold
    )
    
    if not context_result or not context_result.get('context'):
        return {
            "question": query.question,
            "answer": "I couldn't find relevant information.",
            "context": None,
            "sources": [],
            "similarities": []
        }
    
    answer = await ollama.generate_response(
        prompt=query.question,
        context=context_result['context'],
        model=query.model
    )
    
    return {
        "question": query.question,
        "answer": answer,
        "context": context_result['context'],
        "sources": context_result.get('document_ids', []),
        "similarities": context_result.get('similarities', [])
    }


def build_abstention_response(
    confidence_score: float,
    confidence_band: str = "insufficient",
    retrieval_attempted: bool = True,
    suggestion: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build Phase 2-compliant abstention response.
    
    Returns structured response with status field for reliable client detection.
    
    Args:
        confidence_score: Raw confidence score (0-1)
        confidence_band: Which band triggered abstention
        retrieval_attempted: Whether retrieval was performed
        suggestion: Optional suggestion for query refinement
        
    Returns:
        Dict with status, message, and metadata fields
    """
    return {
        "status": "insufficient_evidence",
        "confidence_band": confidence_band,
        "message": "I don't have enough reliable evidence in the retrieved material to answer that confidently.",
        "metadata": {
            "confidence_score": round(confidence_score, 3),
            "retrieval_attempted": retrieval_attempted,
            "suggestion": suggestion or "Try rephrasing your question or providing more context."
        }
    }


async def log_policy_telemetry(trace: PolicyTrace):
    """Background task to log policy telemetry to database."""
    try:
        await policy_repo.log_telemetry(trace.to_dict())
    except Exception as e:
        logger.error(f"Telemetry logging failed: {e}")


async def _rag_hybrid(query: RAGQuery, request: Request, background_tasks: Optional[BackgroundTasks] = None) -> Dict[str, Any]:
    """Hybrid RAG implementation with Phase 13 adaptive policy control."""
    import time
    start_time = time.time()
    
    retriever: HybridRetriever = request.app.state.hybrid_retriever
    builder: ContextBuilder = request.app.state.context_builder
    evidence_scorer: EvidenceScorer = request.app.state.evidence_scorer
    policy: RAGPolicy = getattr(request.app.state, 'active_policy', RAGPolicy(version="inline-default"))
    ollama = OllamaClient()
    
    # Milestone 1: Query Classification
    classifier = getattr(request.app.state, 'query_classifier', None)
    qtype = classifier.classify(query.question) if classifier else (query.query_type or "general")
    # Coerce QueryType enum to plain string for consistent dict lookups and serialisation
    qtype_str = qtype.value if hasattr(qtype, 'value') else str(qtype)
    logger.info(f"Query classified as: {qtype_str}")

    # Accumulated control actions for telemetry
    control_actions: list = []

    # Initialize Trace
    trace = PolicyTrace(
        query_text=query.question,
        query_type=qtype_str,
        policy_version=policy.version
    )
    
    # Stage 1: Initial Retrieval
    embedding = None
    try:
        embedding = await ollama.generate_embedding(query.question)
    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}, using lexical-only")
    
    try:
        chunks = await retriever.retrieve(
            query.question,
            embedding,
            k=query.context_limit
        )
    except Exception as e:
        logger.error(f"Retrieval failed: {e}")
        raise HTTPException(status_code=500, detail="Search failed")
    
    if not chunks:
        trace.confidence_band = "insufficient"
        trace.action_taken = "abstain"
        trace.execution_path = "no_match"
        if background_tasks:
            background_tasks.add_task(log_policy_telemetry, trace)
            
        return {
            "question": query.question,
            "answer": RAG_ABSTAIN_RESPONSE,
            "sources": [],
            "source_citations": [],
            "confidence_band": "insufficient",
            "policy_version": policy.version,
            "hybrid_search": True
        }
    
    # Stage 2: Contextual Analysis (Phase 14)
    shape_extractor = getattr(request.app.state, 'evidence_shape_extractor', None)
    state_labeler = getattr(request.app.state, 'retrieval_state_labeler', None)
    router = getattr(request.app.state, 'contextual_router', None)
    
    evidence_shape = shape_extractor.extract(chunks, query.question) if shape_extractor else None
    retrieval_state = state_labeler.label(evidence_shape) if state_labeler else RetrievalState.RECOVERABLE
    
    # Stage 2.1: Confidence Check with Policy
    confidence = evidence_scorer.score_evidence(
        chunks, 
        query.question, 
        query_type=qtype_str,
        policy=policy
    )
    band = confidence.band
    trace.confidence_score = confidence.score
    trace.confidence_band = band
    trace.retrieval_state = retrieval_state.value if hasattr(retrieval_state, 'value') else str(retrieval_state)
    trace.evidence_shape = evidence_shape.to_dict() if evidence_shape else {}
    
    # Stage 3: Confidence-Driven Routing (Phase 2)
    
    # Phase 2: Use route_with_confidence() for confidence-based execution paths
    latency_budget = policy.get_latency_budget(qtype_str)
    uncertainty_detector = getattr(request.app.state, 'uncertainty_detector', None)
    
    routing_ctx = RoutingContext(
        query_type=qtype_str,
        confidence_band=band,
        retrieval_state=retrieval_state,
        latency_budget=latency_budget,
        policy=policy
    )
    
    # Use Phase 2 confidence-aware routing with uncertainty gates
    route = None
    if router and hasattr(router, 'route_with_confidence'):
        # Use new Phase 2 routing with uncertainty gates
        route = await router.route_with_confidence(
            routing_ctx,
            chunks,
            evidence_shape,
            uncertainty_detector
        )
    elif router:
        # Fallback to existing router
        route = router.route(routing_ctx)
    
    action = route.action if route else policy.get_action(band, qtype_str)
    execution_path = route.execution_path if route else "standard"
    
    logger.info(f"Phase 2 routing: {band} → {execution_path} ({action})")
    
    trace.retrieval_depth = len(chunks)
    trace.action_taken = action
    trace.execution_path = execution_path
    
    # Stage 4: Phase 2 Execution Path Logic
    
    if execution_path == "abstain":
        # Immediate abstention (band == insufficient)
        trace.abstention_triggered = True
        trace.latency_ms = int((time.time() - start_time) * 1000)
        if background_tasks:
            background_tasks.add_task(log_policy_telemetry, trace)
        return build_abstention_response(confidence.score, band)
    
    elif execution_path == "fast":
        # Fast path: no reranking, no expansion - use base retrieval only
        logger.debug("Fast path: using base retrieval only, skipping reranking/expansion")
        # Continue to generation (no additional processing)
        pass
    
    elif execution_path == "standard":
        # Standard path: conditional reranking based on uncertainty gates
        if action == "conditional_reranking":
            logger.info("Standard path: invoking reranker due to uncertainty gates")
            reranker = getattr(request.app.state, 'reranker', None)
            if reranker and reranker.policy.mode != RerankMode.OFF:
                chunks, _ = await reranker.rerank_with_decision(query.question, embedding)
                trace.reranker_invoked = True
                trace.reranker_reason = "uncertainty_gates_triggered"
                control_actions.append("reranking")
                
                # Re-score after reranking
                confidence = evidence_scorer.score_evidence(
                    chunks, query.question, query_type=qtype_str, policy=policy
                )
                band = confidence.band
                trace.confidence_band = band
        else:
            logger.debug("Standard path: uncertainty gates passed, using base evidence")
    
    elif execution_path == "cautious":
        # Cautious path: mandatory reranking + expanded retrieval
        logger.info("Cautious path: expanded retrieval + mandatory reranking")
        
        # Query expansion
        query_transformer = getattr(request.app.state, 'query_transformer', None)
        if query_transformer and query_transformer.mode != TransformMode.OFF:
            chunks, _, _ = await retriever.retrieve_with_transform(
                query=query.question,
                query_transformer=query_transformer,
                query_embedding=embedding,
                k=query.context_limit,
                latency_budget_ms=latency_budget
            )
            control_actions.append("query_expansion")
            logger.debug("Cautious path: query expansion completed")
        
        # Mandatory reranking
        reranker = getattr(request.app.state, 'reranker', None)
        if reranker and reranker.policy.mode != RerankMode.OFF:
            chunks, _ = await reranker.rerank_with_decision(query.question, embedding)
            trace.reranker_invoked = True
            trace.reranker_reason = "cautious_path_mandatory"
            control_actions.append("reranking")
            logger.debug("Cautious path: mandatory reranking completed")
        
        # Re-score after all processing
        confidence = evidence_scorer.score_evidence(
            chunks, query.question, query_type=qtype_str, policy=policy
        )
        band = confidence.band
        trace.confidence_band = band
    
    # Stage 5: Generation with band-specific prompt
    try:
        context_result = builder.build_context(chunks, query.question)
        
        # Select prompt template based on execution path and confidence band
        if execution_path == "fast" or band == "high":
            logger.info("High confidence: using direct generation prompt")
            prompt_template = RAG_PROMPT_TEMPLATE
            execution_path = "fast_generation"
        elif execution_path == "standard" or band == "medium":
            logger.info("Medium confidence: using hedged generation prompt")
            prompt_template = RAG_MEDIUM_CONFIDENCE_PROMPT
            execution_path = "standard_generation"
        elif execution_path == "cautious" or band == "low":
            logger.info("Low confidence: using conservative generation prompt")
            prompt_template = RAG_CONSERVATIVE_PROMPT_TEMPLATE
            execution_path = "cautious_generation"
        else:
            logger.debug(f"Unknown path {execution_path}, using default template")
            prompt_template = RAG_PROMPT_TEMPLATE
            
        prompt = prompt_template.format(
            context=context_result['context'],
            question=query.question
        )
        
        answer = await ollama.generate_response(
            prompt=prompt,
            model=query.model
        )
    except Exception as e:
        logger.error(f"Answer generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate answer")
    
    # Finalize Trace
    trace.execution_path = execution_path
    trace.chunks_retrieved = len(chunks)
    trace.latency_ms = int((time.time() - start_time) * 1000)
    
    # Log telemetry in background
    if background_tasks:
        background_tasks.add_task(log_policy_telemetry, trace)
    
    # Build response
    sources = context_result.get('sources', [])
    
    return {
        "question": query.question,
        "answer": answer,
        "context": context_result['context'][:1000] if context_result['context'] else None,
        "sources": [s['document_id'] for s in sources],
        "similarities": [s['score'] for s in sources],
        "source_citations": sources,
        "confidence": confidence.to_dict(),
        "control_actions": control_actions,
        "execution_path": execution_path,
        "policy_version": policy.version,
        "hybrid_search": True,
        "query_id": trace.query_id
    }


@app.post("/rag")
async def rag_query(
    query: RAGQuery,
    background_tasks: BackgroundTasks,
    mode: Optional[str] = Query(default=None, pattern="^(vector|hybrid)$"),
    request: Request = None
):
    """
    Answer a question using RAG (Retrieval-Augmented Generation).
    
    Query parameter `mode`:
    - vector: Use traditional vector-only search (default unless USE_HYBRID_RAG=true)
    - hybrid: Use hybrid search (lexical + vector) with citations
    """
    use_hybrid = (
        mode == "hybrid" or 
        (mode is None and request.app.state.use_hybrid_rag)
    )
    
    if use_hybrid:
        return await _rag_hybrid(query, request, background_tasks)
    else:
        return await _rag_vector_only(query)


# Admin endpoints
@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get status and result of an async task.

    Returns a stable schema:
    - SUCCESS: task_id, status, result, error=null
    - FAILURE: task_id, status, result=null, error
    - PENDING/STARTED: task_id, status, result=null, error=null
    """
    try:
        result = AsyncResult(task_id, app=celery_app)
        status = result.status

        if status == "SUCCESS":
            return {
                "task_id": task_id,
                "status": status,
                "result": result.result,
                "error": None,
            }
        if status == "FAILURE":
            return {
                "task_id": task_id,
                "status": status,
                "result": None,
                "error": str(result.result) if result.result else "Unknown error",
            }
        # PENDING or STARTED
        return {
            "task_id": task_id,
            "status": status,
            "result": None,
            "error": None,
        }
    except Exception as e:
        logger.error(f"Failed to get task status: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get task status: {str(e)}"
        )


@app.post("/admin/reindex/{article_id}")
async def reindex_article(
    article_id: int,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_key),
):
    """Reindex embeddings for a specific article."""
    try:
        # Verify article exists
        article = await document_repo.get_document(article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        
        # Schedule background task
        background_tasks.add_task(
            article_processor.update_embeddings_for_document,
            article_id
        )
        
        return {
            "message": f"Reindexing scheduled for article {article_id}",
            "article_title": article['title']
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to schedule reindex for article {article_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to schedule reindex: {str(e)}")


@app.post("/admin/models/check")
async def check_models(
    _: None = Depends(require_api_key),
):
    """Check availability of required models."""
    try:
        models_status = await embedding_manager.ensure_models_available()
        return models_status
    except Exception as e:
        logger.error(f"Failed to check models: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to check models: {str(e)}")


@app.get("/admin/vector-index/status")
async def get_vector_index_status(
    _: None = Depends(require_api_key),
):
    """Get detailed vector index (HNSW) status and statistics."""
    try:
        index_stats = await document_repo.get_index_stats()
        return {
            "status": "healthy" if index_stats['hnsw_enabled'] else "warning",
            "hnsw_enabled": index_stats['hnsw_enabled'],
            "ef_search": index_stats['ef_search'],
            "estimated_memory_mb": round(index_stats['estimated_memory_mb'], 2),
            "indexes": index_stats['indexes'],
            "tuning_recommendations": {
                "high_recall": {"ef_search": 100, "note": "Better recall, slower queries"},
                "balanced": {"ef_search": 40, "note": "Default balance"},
                "fast": {"ef_search": 20, "note": "Faster queries, may miss results"}
            }
        }
    except Exception as e:
        logger.error(f"Failed to get vector index status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get vector index status: {str(e)}")


@app.post("/admin/vector-index/tune")
async def tune_vector_index(
    ef_search: int,
    _: None = Depends(require_api_key),
):
    """Tune HNSW search parameters at runtime.
    
    Args:
        ef_search: HNSW exploration factor (1-1000). 
                  Higher = better recall, slower queries.
                  Recommended: 20 (fast), 40 (balanced), 100 (high recall)
    """
    try:
        if not 1 <= ef_search <= 1000:
            raise HTTPException(
                status_code=400, 
                detail="ef_search must be between 1 and 1000"
            )
        
        await db_manager.set_search_params(ef_search=ef_search)
        
        return {
            "message": f"HNSW ef_search set to {ef_search}",
            "ef_search": ef_search,
            "effect": "higher = better recall, slower queries" if ef_search > 40 else "lower = faster queries, may miss results" if ef_search < 40 else "balanced setting"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to tune vector index: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to tune vector index: {str(e)}")


@app.get("/admin/rerank/status")
async def get_rerank_status(
    _: None = Depends(require_api_key),
):
    """Get current reranking configuration and status."""
    reranker = getattr(app.state, 'reranker', None)
    
    if reranker is None:
        return {
            "status": "disabled",
            "mode": "off",
            "message": "Reranking is disabled. Set RERANK_MODE=selective or always to enable."
        }
    
    policy_config = reranker.policy.get_config()
    stats = reranker.policy.get_stats()
    
    return {
        "status": "enabled",
        "mode": reranker.policy.mode.value,
        "configuration": policy_config,
        "operational_params": {
            "top_n": reranker.top_n,
            "final_k": reranker.final_k,
            "model": reranker.model,
            "use_cross_encoder": reranker.use_cross_encoder
        },
        "triggers": {
            "score_gap": f"Rerank when top-5 score gap < {policy_config['score_gap_threshold']}",
            "ranking_disagreement": f"Rerank when lexical/vector disagreement > {policy_config['disagreement_threshold']}",
            "query_complexity": f"Rerank when query has >= {policy_config['complex_query_words']} words or complex patterns",
            "low_evidence": f"Rerank when top score < {policy_config['min_top_score']}"
        },
        "stats": stats
    }


@app.post("/admin/rerank/test")
async def test_rerank_decision(
    query: str,
    _: None = Depends(require_api_key),
):
    """Test selective reranking decision for a query without executing reranking.
    
    This endpoint shows what the selective reranker would decide for a given query,
    including which triggers would fire and why.
    
    Args:
        query: The query string to test
        
    Returns:
        Decision details including triggers, confidence, and explanation
    """
    reranker = getattr(app.state, 'reranker', None)
    
    if reranker is None:
        raise HTTPException(
            status_code=400,
            detail="Reranking is not enabled. Set RERANK_MODE=selective to test decisions."
        )
    
    try:
        # Get retrieval candidates for trigger evaluation
        retriever = app.state.hybrid_retriever
        
        # Generate embedding
        ollama = OllamaClient()
        try:
            embedding = await ollama.generate_embedding(query)
        except Exception as e:
            embedding = None
        
        # Retrieve candidates
        candidates = await retriever.retrieve(query, embedding, k=reranker.top_n)
        
        # Also get raw candidates for disagreement calculation
        lexical_candidates = await retriever.fetch_lexical(query)
        vector_candidates = await retriever.fetch_vector(embedding) if embedding else []
        
        # Evaluate policy decision
        decision = reranker.policy.should_rerank(
            query=query,
            candidates=candidates,
            lexical_candidates=lexical_candidates,
            vector_candidates=vector_candidates
        )
        
        return {
            "query": query,
            "mode": reranker.policy.mode.value,
            "decision": decision.to_dict(),
            "retrieval_stats": {
                "candidates_retrieved": len(candidates),
                "lexical_candidates": len(lexical_candidates),
                "vector_candidates": len(vector_candidates),
                "overlap": len(set(c['id'] for c in lexical_candidates) & 
                              set(c['id'] for c in vector_candidates))
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to test rerank decision: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to test rerank decision: {str(e)}")


@app.post("/admin/rerank/tune")
async def tune_rerank_policy(
    score_gap: Optional[float] = None,
    disagreement: Optional[float] = None,
    min_top_score: Optional[float] = None,
    complex_query_words: Optional[int] = None,
    _: None = Depends(require_api_key),
):
    """Tune selective reranking thresholds at runtime.
    
    Args:
        score_gap: Score gap threshold (0.0-1.0). Lower = more reranking.
        disagreement: Ranking disagreement threshold (0.0-1.0). Lower = more reranking.
        min_top_score: Minimum top score threshold (0.0-1.0). Higher = more reranking.
        complex_query_words: Word count for complexity trigger. Lower = more reranking.
    """
    reranker = getattr(app.state, 'reranker', None)
    
    if reranker is None:
        raise HTTPException(
            status_code=400,
            detail="Reranking is not enabled. Set RERANK_MODE=selective first."
        )
    
    if reranker.policy.mode != RerankMode.SELECTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Tuning only available in selective mode. Current mode: {reranker.policy.mode.value}"
        )
    
    try:
        # Update thresholds
        if score_gap is not None:
            if not 0.0 <= score_gap <= 1.0:
                raise HTTPException(status_code=400, detail="score_gap must be between 0.0 and 1.0")
            reranker.policy.score_gap_threshold = score_gap
        
        if disagreement is not None:
            if not 0.0 <= disagreement <= 1.0:
                raise HTTPException(status_code=400, detail="disagreement must be between 0.0 and 1.0")
            reranker.policy.disagreement_threshold = disagreement
        
        if min_top_score is not None:
            if not 0.0 <= min_top_score <= 1.0:
                raise HTTPException(status_code=400, detail="min_top_score must be between 0.0 and 1.0")
            reranker.policy.min_top_score = min_top_score
        
        if complex_query_words is not None:
            if complex_query_words < 1:
                raise HTTPException(status_code=400, detail="complex_query_words must be >= 1")
            reranker.policy.complex_query_words = complex_query_words
        
        return {
            "message": "Selective reranking thresholds updated",
            "configuration": reranker.policy.get_config()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to tune rerank policy: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to tune rerank policy: {str(e)}")


@app.post("/admin/rerank/reset-stats")
async def reset_rerank_stats(
    _: None = Depends(require_api_key),
):
    """Reset reranking statistics counters to zero.
    
    Useful for getting clean measurements after tuning thresholds.
    """
    reranker = getattr(app.state, 'reranker', None)
    
    if reranker is None:
        raise HTTPException(
            status_code=400,
            detail="Reranking is not enabled."
        )
    
    reranker.policy.reset_stats()
    
    return {
        "message": "Reranking statistics reset",
        "stats": reranker.policy.get_stats()
    }


# Query Transformer Admin Endpoints

@app.get("/admin/query-transform/status")
async def get_query_transform_status(
    _: None = Depends(require_api_key),
):
    """Get current query transformer configuration and statistics."""
    transformer = getattr(app.state, 'query_transformer', None)
    
    if transformer is None:
        return {
            "status": "disabled",
            "mode": "off",
            "message": "Query transformation is disabled. Set QUERY_TRANSFORM_MODE=selective or always to enable."
        }
    
    config = transformer.get_config()
    stats = transformer.get_stats()
    
    return {
        "status": "enabled",
        "mode": transformer.mode.value,
        "configuration": config,
        "stats": stats,
        "transform_types": {
            "multi_query": "Generate alternate phrasings for ambiguous queries",
            "step_back": "Create broader conceptual versions of specific questions"
        }
    }


@app.post("/admin/query-transform/test")
async def test_query_transform(
    query: str,
    _: None = Depends(require_api_key),
):
    """Test query transformation for a query without executing retrieval.
    
    This endpoint shows what transformations would be applied to a given query,
    including which transform types would fire and why.
    
    Args:
        query: The query string to test
        
    Returns:
        Transform decision details including generated queries and reasons
    """
    transformer = getattr(app.state, 'query_transformer', None)
    
    if transformer is None:
        raise HTTPException(
            status_code=400,
            detail="Query transformation is not enabled. Set QUERY_TRANSFORM_MODE=selective to test."
        )
    
    try:
        # Evaluate transformation decision
        decision = transformer.transform(query)
        
        return {
            "query": query,
            "mode": transformer.mode.value,
            "decision": decision.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Failed to test query transform: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to test query transform: {str(e)}")


@app.post("/admin/query-transform/tune")
async def tune_query_transform(
    max_queries: Optional[int] = None,
    min_words: Optional[int] = None,
    ambiguity: Optional[int] = None,
    enable_multi_query: Optional[bool] = None,
    enable_step_back: Optional[bool] = None,
    _: None = Depends(require_api_key),
):
    """Tune query transformation parameters at runtime.
    
    Args:
        max_queries: Maximum expanded queries (2-5)
        min_words: Minimum query words before transformation considered
        ambiguity: Number of ambiguity indicators to trigger transformation
        enable_multi_query: Enable/disable multi-query expansion
        enable_step_back: Enable/disable step-back reformulation
    """
    transformer = getattr(app.state, 'query_transformer', None)
    
    if transformer is None:
        raise HTTPException(
            status_code=400,
            detail="Query transformation is not enabled. Set QUERY_TRANSFORM_MODE=selective first."
        )
    
    if transformer.mode != TransformMode.SELECTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Tuning only available in selective mode. Current mode: {transformer.mode.value}"
        )
    
    try:
        # Update parameters
        if max_queries is not None:
            if not 2 <= max_queries <= 5:
                raise HTTPException(status_code=400, detail="max_queries must be between 2 and 5")
            transformer.max_expanded_queries = max_queries
        
        if min_words is not None:
            if min_words < 1:
                raise HTTPException(status_code=400, detail="min_words must be >= 1")
            transformer.min_query_words = min_words
        
        if ambiguity is not None:
            if ambiguity < 0:
                raise HTTPException(status_code=400, detail="ambiguity must be >= 0")
            transformer.ambiguity_threshold = ambiguity
        
        if enable_multi_query is not None:
            transformer.enable_multi_query = enable_multi_query
        
        if enable_step_back is not None:
            transformer.enable_step_back = enable_step_back
        
        return {
            "message": "Query transformation parameters updated",
            "configuration": transformer.get_config()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to tune query transform: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to tune query transform: {str(e)}")


@app.post("/admin/query-transform/reset-stats")
async def reset_query_transform_stats(
    _: None = Depends(require_api_key),
):
    """Reset query transformation statistics counters to zero.
    
    Useful for getting clean measurements after tuning parameters.
    """
    transformer = getattr(app.state, 'query_transformer', None)
    
    if transformer is None:
        raise HTTPException(
            status_code=400,
            detail="Query transformation is not enabled."
        )
    
    transformer.reset_stats()
    
    return {
        "message": "Query transformation statistics reset",
        "stats": transformer.get_stats()
    }


# Phase 9: Evidence-Aware Retrieval Admin Endpoints

@app.get("/admin/evidence/status")
async def get_evidence_status(
    _: None = Depends(require_api_key),
):
    """Get current evidence-aware retrieval configuration and statistics."""
    context_filter = getattr(app.state, 'context_filter', None)
    evidence_scorer = getattr(app.state, 'evidence_scorer', None)
    
    if context_filter is None or evidence_scorer is None:
        return {
            "status": "disabled",
            "mode": "off",
            "message": "Evidence-aware retrieval is disabled. Set EVIDENCE_AWARE_MODE=selective or always to enable."
        }
    
    filter_config = context_filter.get_config()
    scorer_config = evidence_scorer.get_config()
    filter_stats = context_filter.get_stats()
    
    return {
        "status": "enabled",
        "mode": context_filter.mode.value,
        "context_filter": filter_config,
        "evidence_scorer": scorer_config,
        "stats": filter_stats
    }


@app.post("/admin/evidence/tune")
async def tune_evidence_filter(
    dedup_threshold: Optional[float] = None,
    max_per_doc: Optional[int] = None,
    min_score: Optional[float] = None,
    max_chunks: Optional[int] = None,
    remove_boilerplate: Optional[bool] = None,
    _: None = Depends(require_api_key),
):
    """Tune evidence filtering parameters at runtime.
    
    Args:
        dedup_threshold: Similarity threshold for deduplication (0.0-1.0)
        max_per_doc: Maximum chunks per document
        min_score: Minimum score threshold
        max_chunks: Maximum total chunks
        remove_boilerplate: Enable/disable boilerplate filtering
    """
    context_filter = getattr(app.state, 'context_filter', None)
    
    if context_filter is None:
        raise HTTPException(
            status_code=400,
            detail="Evidence-aware retrieval is not enabled."
        )
    
    try:
        if dedup_threshold is not None:
            if not 0.0 <= dedup_threshold <= 1.0:
                raise HTTPException(status_code=400, detail="dedup_threshold must be between 0.0 and 1.0")
            context_filter.dedup_threshold = dedup_threshold
        
        if max_per_doc is not None:
            if max_per_doc < 1:
                raise HTTPException(status_code=400, detail="max_per_doc must be >= 1")
            context_filter.max_chunks_per_doc = max_per_doc
        
        if min_score is not None:
            if not 0.0 <= min_score <= 1.0:
                raise HTTPException(status_code=400, detail="min_score must be between 0.0 and 1.0")
            context_filter.min_score_threshold = min_score
        
        if max_chunks is not None:
            if max_chunks < 1:
                raise HTTPException(status_code=400, detail="max_chunks must be >= 1")
            context_filter.max_total_chunks = max_chunks
        
        if remove_boilerplate is not None:
            context_filter.remove_boilerplate = remove_boilerplate
        
        return {
            "message": "Evidence filtering parameters updated",
            "configuration": context_filter.get_config()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to tune evidence filter: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to tune evidence filter: {str(e)}")


@app.post("/admin/evidence/test-confidence")
async def test_evidence_confidence(
    query: str,
    _: None = Depends(require_api_key),
):
    """Test evidence confidence scoring for a query.
    
    This endpoint retrieves chunks and scores them for confidence
    without requiring the full pipeline.
    
    Args:
        query: The query to test
        
    Returns:
        Confidence score and component breakdown
    """
    evidence_scorer = getattr(app.state, 'evidence_scorer', None)
    retriever = getattr(app.state, 'hybrid_retriever', None)
    
    if evidence_scorer is None:
        raise HTTPException(
            status_code=400,
            detail="Evidence scoring is not enabled."
        )
    
    try:
        # Generate embedding
        ollama = OllamaClient()
        try:
            embedding = await ollama.generate_embedding(query)
        except Exception as e:
            embedding = None
        
        # Retrieve chunks
        if retriever:
            chunks = await retriever.retrieve(query, embedding, k=10)
        else:
            raise HTTPException(status_code=500, detail="Retriever not available")
        
        # Score evidence
        confidence = evidence_scorer.score_evidence(
            chunks=chunks,
            query=query
        )
        
        return {
            "query": query,
            "chunks_retrieved": len(chunks),
            "confidence": confidence.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Failed to test evidence confidence: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to test evidence confidence: {str(e)}")


@app.post("/admin/evidence/reset-stats")
async def reset_evidence_stats(
    _: None = Depends(require_api_key),
):
    """Reset evidence filtering statistics."""
    context_filter = getattr(app.state, 'context_filter', None)
    
    if context_filter is None:
        raise HTTPException(
            status_code=400,
            detail="Evidence-aware retrieval is not enabled."
        )
    
    context_filter.reset_stats()
    
    return {
        "message": "Evidence filtering statistics reset",
        "stats": context_filter.get_stats()
    }


# Phase 10: Evaluation & Calibration Endpoints

class CalibrationAuditRequest(BaseModel):
    """Request to run a calibration audit."""
    queries: Optional[List[str]] = Field(default=None, description="Custom queries to evaluate")
    use_default_suite: bool = Field(default=True, description="Use the default test suite")
    max_queries: int = Field(default=25, ge=1, le=100, description="Maximum queries to run")
    include_raw_results: bool = Field(default=False, description="Include individual query results")


class CalibrationAuditResponse(BaseModel):
    """Response from calibration audit."""
    status: str
    report: Dict[str, Any]
    raw_results: Optional[List[Dict[str, Any]]] = None
    execution_time_seconds: float


@app.get("/admin/evaluation/status")
async def get_evaluation_status(
    _: None = Depends(require_api_key),
):
    """Get evaluation system status and available test suites."""
    from shared.evaluation import Evaluator
    
    evaluator = Evaluator()
    default_suite = evaluator.create_test_suite()
    
    # Check if default test suite file exists
    suite_file = "/app/evaluation/default_test_suite.json"
    import os
    file_exists = os.path.exists(suite_file)
    
    return {
        "status": "available",
        "evaluation_module": "loaded",
        "default_suite": {
            "query_count": len(default_suite),
            "categories": list(set(t['category'] for t in default_suite)),
            "file_exists": file_exists,
            "file_path": suite_file if file_exists else None
        },
        "capabilities": [
            "confidence_calibration_audit",
            "groundedness_measurement",
            "citation_precision_recall",
            "false_confidence_detection"
        ]
    }


@app.post("/admin/evaluation/calibration-audit")
async def run_calibration_audit(
    request: CalibrationAuditRequest,
    _: None = Depends(require_api_key),
):
    """Run confidence calibration audit on test queries.
    
    This endpoint evaluates whether the system's confidence scores
    accurately predict answer quality. It runs a set of test queries
    through the RAG pipeline and measures:
    
    - Per-band accuracy (high/medium/low/insufficient)
    - False confidence rate (high confidence, poor quality)
    - Calibration error (ECE)
    - Confidence-quality correlation
    - Citation precision/recall
    
    Usage:
        POST /admin/evaluation/calibration-audit
        {
            "use_default_suite": true,
            "max_queries": 25
        }
    
    Returns:
        Calibration report with metrics and tuning recommendations
    """
    import time
    from shared.evaluation import Evaluator, CalibrationAuditor
    
    start_time = time.time()
    
    try:
        # Build test query list
        if request.queries:
            test_queries = [{'query': q} for q in request.queries[:request.max_queries]]
        elif request.use_default_suite:
            evaluator = Evaluator()
            suite = evaluator.create_test_suite()
            test_queries = suite[:request.max_queries]
        else:
            raise HTTPException(
                status_code=400,
                detail="Either provide queries or set use_default_suite=true"
            )
        
        # Create RAG endpoint wrapper
        async def rag_endpoint(query: str) -> Dict[str, Any]:
            """Internal RAG endpoint for evaluation."""
            from shared.ollama_client import OllamaClient
            from shared.database import document_repo
            
            ollama = OllamaClient()
            retriever = app.state.hybrid_retriever
            builder = app.state.context_builder
            evidence_scorer = getattr(app.state, 'evidence_scorer', None)
            citation_tracker = getattr(app.state, 'citation_tracker', None)
            context_filter = getattr(app.state, 'context_filter', None)
            
            # Generate embedding
            embedding = None
            try:
                embedding = await ollama.generate_embedding(query)
            except Exception:
                pass
            
            # Retrieve chunks
            chunks = await retriever.retrieve(query, embedding, k=10)
            
            # Apply context filtering if available
            if context_filter and context_filter.mode.value != 'off':
                filter_result = context_filter.filter_chunks(chunks, query=query)
                chunks = filter_result.chunks
            
            # Build context
            context_result = builder.build_context(chunks, query)
            
            # Generate answer
            prompt = RAG_PROMPT_TEMPLATE.format(
                context=context_result['context'],
                question=query
            )
            answer = await ollama.generate_response(prompt=prompt)
            
            # Calculate confidence
            confidence = {'score': 0.5, 'band': 'unknown'}
            if evidence_scorer:
                from shared.evidence_scorer import ConfidenceScore
                conf_score = evidence_scorer.score_evidence(chunks, query)
                confidence = conf_score.to_dict()
            
            # Track citations
            citations = {}
            if citation_tracker:
                report = citation_tracker.track_citations(answer, chunks)
                citations = report.to_dict()
            
            return {
                'answer': answer,
                'confidence': confidence,
                'chunks': chunks,
                'citations': citations
            }
        
        # Run audit
        auditor = CalibrationAuditor()
        report = await auditor.run_audit(test_queries, rag_endpoint)
        
        execution_time = time.time() - start_time
        
        response = {
            "status": "success",
            "report": report.to_dict(),
            "execution_time_seconds": round(execution_time, 2)
        }
        
        if request.include_raw_results:
            # Note: We'd need to store raw results during audit for this
            response["raw_results"] = []  # Placeholder
        
        return response
        
    except Exception as e:
        logger.error(f"Calibration audit failed: {e}")
        raise HTTPException(status_code=500, detail=f"Audit failed: {str(e)}")


@app.post("/admin/evaluation/single-query")
async def evaluate_single_query(
    query: str,
    _: None = Depends(require_api_key),
):
    """Evaluate a single query with detailed metrics.
    
    This endpoint runs a single query through the evaluation pipeline
    and returns detailed metrics including groundedness, citations,
    and confidence analysis.
    
    Useful for debugging specific queries or understanding why
    a particular query received its confidence score.
    
    Args:
        query: The query to evaluate
        
    Returns:
        Detailed evaluation result with metrics
    """
    from shared.evaluation import Evaluator
    from shared.ollama_client import OllamaClient
    from shared.evidence_scorer import EvidenceScorer
    from shared.citation_tracker import CitationTracker
    
    try:
        # Create evaluator
        evaluator = Evaluator()
        
        # Create RAG endpoint
        async def rag_endpoint(q: str) -> Dict[str, Any]:
            ollama = OllamaClient()
            retriever = app.state.hybrid_retriever
            builder = app.state.context_builder
            
            embedding = await ollama.generate_embedding(q)
            chunks = await retriever.retrieve(q, embedding, k=10)
            
            # Apply context filter
            context_filter = getattr(app.state, 'context_filter', None)
            if context_filter:
                filter_result = context_filter.filter_chunks(chunks, query=q)
                chunks = filter_result.chunks
            
            context_result = builder.build_context(chunks, q)
            
            prompt = RAG_PROMPT_TEMPLATE.format(
                context=context_result['context'],
                question=q
            )
            answer = await ollama.generate_response(prompt=prompt)
            
            # Score evidence
            evidence_scorer = getattr(app.state, 'evidence_scorer', EvidenceScorer())
            conf_score = evidence_scorer.score_evidence(chunks, q)
            
            # Track citations
            citation_tracker = getattr(app.state, 'citation_tracker', CitationTracker())
            report = citation_tracker.track_citations(answer, chunks)
            
            return {
                'answer': answer,
                'confidence': conf_score.to_dict(),
                'chunks': chunks,
                'citations': report.to_dict()
            }
        
        # Run evaluation
        result = await evaluator.evaluate_single_query(query, rag_endpoint)
        
        return {
            "status": "success",
            "evaluation": result.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Single query evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


async def log_policy_telemetry(trace: PolicyTrace):
    """Background task to log policy telemetry to database."""
    try:
        await policy_repo.log_telemetry(trace.to_dict())
    except Exception as e:
        logger.error(f"Failed to log policy telemetry: {e}")


@app.get("/admin/policy_route_explain")
async def explain_policy_route(
    query_id: str,
    _: None = Depends(require_api_key)
):
    """Explain why a specific policy route was chosen."""
    try:
        telemetry = await policy_repo.get_telemetry_by_id(query_id)
        if not telemetry:
            raise HTTPException(status_code=404, detail="Telemetry not found")
            
        return {
            "query_id": query_id,
            "query_text": telemetry.get("query_text"),
            "query_type": telemetry.get("query_type"),
            "confidence_band": telemetry.get("confidence_band"),
            "retrieval_state": telemetry.get("retrieval_state"),
            "evidence_shape": telemetry.get("evidence_shape"),
            "action_taken": telemetry.get("action_taken"),
            "execution_path": telemetry.get("execution_path"),
            "policy_version": telemetry.get("policy_version"),
            "explanation": f"Route '{telemetry.get('action_taken')}' chosen because qtype={telemetry.get('query_type')} and band={telemetry.get('confidence_band')} with {telemetry.get('retrieval_state')} retrieval state."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to explain policy route: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/route_distribution")
async def get_route_distribution(
    days: int = 7,
    _: None = Depends(require_api_key)
):
    """Get distribution of policy routes over recent period."""
    try:
        stats = await policy_repo.get_route_distribution(days=days)
        return {
            "period_days": days,
            "distribution": stats
        }
    except Exception as e:
        logger.error(f"Failed to get route distribution: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/evaluation/test-suite")
async def get_test_suite(
    category: Optional[str] = None,
    _: None = Depends(require_api_key),
):
    """Get the default test suite for evaluation.
    
    Returns the set of test queries used for calibration auditing.
    Optionally filter by category.
    
    Args:
        category: Optional filter by category (factual, ambiguous, sparse, etc.)
        
    Returns:
        List of test cases with metadata
    """
    from shared.evaluation import Evaluator
    
    evaluator = Evaluator()
    suite = evaluator.create_test_suite()
    
    if category:
        suite = [t for t in suite if t.get('category') == category]
    
    # Summary stats
    categories = {}
    for test in suite:
        cat = test.get('category', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    
    return {
        "total_queries": len(suite),
        "categories": categories,
        "test_cases": suite
    }


@app.post("/admin/evaluation/groundedness-check")
async def check_groundedness(
    answer: str,
    query: str,
    _: None = Depends(require_api_key),
):
    """Check if an answer is grounded in retrieved evidence.
    
    This endpoint retrieves chunks for a query and checks how well
    the provided answer is supported by that evidence.
    
    Args:
        answer: The answer text to check
        query: The query that generated the answer
        
    Returns:
        Groundedness score and unsupported claims
    """
    from shared.evaluation import GroundednessChecker
    from shared.ollama_client import OllamaClient
    
    try:
        # Retrieve chunks
        ollama = OllamaClient()
        retriever = app.state.hybrid_retriever
        
        embedding = await ollama.generate_embedding(query)
        chunks = await retriever.retrieve(query, embedding, k=10)
        
        # Check groundedness
        checker = GroundednessChecker()
        groundedness, unsupported = checker.check_groundedness(answer, chunks)
        
        return {
            "status": "success",
            "query": query,
            "groundedness_score": round(groundedness, 3),
            "is_well_grounded": groundedness >= 0.7,
            "chunks_retrieved": len(chunks),
            "unsupported_claims": unsupported[:10],
            "unsupported_count": len(unsupported)
        }
        
    except Exception as e:
        logger.error(f"Groundedness check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Check failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
