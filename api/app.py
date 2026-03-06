from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import logging
import os
from contextlib import asynccontextmanager

from shared.ollama_client import OllamaClient
from shared.database import document_repo
from shared.processor import article_processor, embedding_manager
from shared.celery_client import celery_app
from celery.result import AsyncResult

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


class BatchArticles(BaseModel):
    articles: List[ArticleCreate] = Field(..., min_items=1, max_items=10)
    max_concurrent: Optional[int] = Field(default=3, ge=1, le=5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting up article index API...")
    
    # Ensure Ollama models are available
    models_status = await embedding_manager.ensure_models_available()
    logger.info(f"Model availability: {models_status}")
    
    # Test basic functionality
    embedding_test = await embedding_manager.test_embedding_generation()
    generation_test = await embedding_manager.test_text_generation()
    
    if not embedding_test:
        logger.warning("Embedding generation test failed")
    if not generation_test:
        logger.warning("Text generation test failed")
    
    logger.info("Startup complete")
    
    yield
    
    # Shutdown
    logger.info("Shutting down article index API...")


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
            "/tasks/{task_id}",
            "/search",
            "/rag",
            "/admin"
        ]
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Test database connection
        stats = await document_repo.get_stats()
        
        # Test Ollama connection
        ollama = OllamaClient()
        embedding_test = await embedding_manager.test_embedding_generation()
        generation_test = await embedding_manager.test_text_generation()
        
        return {
            "status": "healthy",
            "database": "connected",
            "ollama_embeddings": "working" if embedding_test else "failed",
            "ollama_generation": "working" if generation_test else "failed",
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@app.get("/stats")
async def get_stats():
    """Get database statistics."""
    try:
        stats = await document_repo.get_stats()
        return stats
    except Exception as e:
        logger.error(f"Failed to get stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve statistics: {str(e)}")


@app.post("/articles/")
async def create_article(article: ArticleCreate):
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
async def create_article_async(article: ArticleCreate):
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
async def create_html_article(article: HTMLArticleCreate):
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
async def create_html_article_async(article: HTMLArticleCreate):
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


@app.post("/articles/batch")
async def create_batch_articles(batch: BatchArticles):
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


@app.post("/rag")
async def rag_query(query: RAGQuery):
    """Answer a question using RAG (Retrieval-Augmented Generation)."""
    try:
        # Generate embedding for the question
        ollama = OllamaClient()
        question_embedding = await ollama.generate_embedding(query.question)
        
        # Get relevant context
        context_result = await document_repo.get_rag_context(
            embedding=question_embedding,
            limit=query.context_limit,
            similarity_threshold=query.similarity_threshold
        )
        
        if not context_result or not context_result.get('context'):
            return {
                "question": query.question,
                "answer": "I couldn't find relevant information to answer your question.",
                "context": None,
                "sources": []
            }
        
        # Generate response using the context
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
    except Exception as e:
        logger.error(f"RAG query failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"RAG query failed: {str(e)}")


# Admin endpoints
@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get status and result of an async task."""
    try:
        result = AsyncResult(task_id, app=celery_app)
        response = {
            "task_id": task_id,
            "status": result.status,
        }
        if result.status == "SUCCESS":
            response["result"] = result.result
        elif result.status == "FAILURE":
            response["error"] = str(result.result) if result.result else "Unknown error"
        elif result.status in ("PENDING", "STARTED"):
            response["message"] = "Task in progress"
        return response
    except Exception as e:
        logger.error(f"Failed to get task status: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get task status: {str(e)}"
        )


@app.post("/admin/reindex/{article_id}")
async def reindex_article(article_id: int, background_tasks: BackgroundTasks):
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
async def check_models():
    """Check availability of required models."""
    try:
        models_status = await embedding_manager.ensure_models_available()
        return models_status
    except Exception as e:
        logger.error(f"Failed to check models: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to check models: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
