import asyncio
import logging
from typing import Dict, Any, List
from celery import current_app
from celery.exceptions import Retry

from celery_app import celery_app
from shared.processor import article_processor

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def process_article_task(
    self, 
    title: str, 
    content: str, 
    metadata: Dict[str, Any] = None,
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> Dict[str, Any]:
    """Process an article in the background."""
    try:
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(
                article_processor.process_article(
                    title=title,
                    content=content,
                    metadata=metadata or {},
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )
            )
            logger.info(f"Successfully processed article: {title}")
            return result
        finally:
            loop.close()
            
    except Exception as exc:
        logger.error(f"Failed to process article '{title}': {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            logger.info(f"Retrying article processing in {countdown} seconds...")
            raise self.retry(countdown=countdown, exc=exc)
        else:
            raise exc


@celery_app.task(bind=True, max_retries=3)
def process_html_article_task(
    self, 
    title: str, 
    html_content: str, 
    metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Process an HTML article in the background."""
    try:
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(
                article_processor.process_html_article(
                    title=title,
                    html_content=html_content,
                    metadata=metadata or {}
                )
            )
            logger.info(f"Successfully processed HTML article: {title}")
            return result
        finally:
            loop.close()
            
    except Exception as exc:
        logger.error(f"Failed to process HTML article '{title}': {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            logger.info(f"Retrying HTML article processing in {countdown} seconds...")
            raise self.retry(countdown=countdown, exc=exc)
        else:
            raise exc


@celery_app.task(bind=True, max_retries=3)
def update_embeddings_task(self, document_id: int) -> Dict[str, Any]:
    """Update embeddings for a document in the background."""
    try:
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(
                article_processor.update_embeddings_for_document(document_id)
            )
            logger.info(f"Successfully updated embeddings for document {document_id}")
            return result
        finally:
            loop.close()
            
    except Exception as exc:
        logger.error(f"Failed to update embeddings for document {document_id}: {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            logger.info(f"Retrying embedding update in {countdown} seconds...")
            raise self.retry(countdown=countdown, exc=exc)
        else:
            raise exc


@celery_app.task(bind=True, max_retries=2)
def batch_process_articles_task(
    self, 
    articles_data: List[Dict[str, Any]], 
    max_concurrent: int = 3
) -> List[Dict[str, Any]]:
    """Process multiple articles in batch."""
    try:
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            results = loop.run_until_complete(
                article_processor.batch_process_articles(
                    articles_data,
                    max_concurrent=max_concurrent
                )
            )
            successful_count = len([r for r in results if 'error' not in r])
            logger.info(f"Batch processing completed: {successful_count}/{len(results)} successful")
            return results
        finally:
            loop.close()
            
    except Exception as exc:
        logger.error(f"Failed to batch process articles: {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            logger.info(f"Retrying batch processing in {countdown} seconds...")
            raise self.retry(countdown=countdown, exc=exc)
        else:
            raise exc


@celery_app.task
def cleanup_old_embeddings_task(days_old: int = 30) -> Dict[str, Any]:
    """Clean up old embeddings (placeholder for future implementation)."""
    # This is a placeholder for future implementation
    # Could be used to clean up orphaned embeddings or optimize storage
    logger.info(f"Cleanup task called for {days_old} days old data")
    return {
        "message": "Cleanup task completed",
        "days_old": days_old,
        "cleaned_items": 0
    }


@celery_app.task(bind=True)
def health_check_task(self) -> Dict[str, Any]:
    """Health check task for monitoring worker status."""
    try:
        # Test basic functionality
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            from shared.processor import embedding_manager
            embedding_test = loop.run_until_complete(
                embedding_manager.test_embedding_generation()
            )
            generation_test = loop.run_until_complete(
                embedding_manager.test_text_generation()
            )
            
            return {
                "status": "healthy",
                "embedding_test": embedding_test,
                "generation_test": generation_test,
                "worker_id": self.request.id
            }
        finally:
            loop.close()
            
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "worker_id": self.request.id
        }
