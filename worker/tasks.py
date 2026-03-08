import asyncio
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
from celery import current_app
from celery.exceptions import Retry

from celery_app import celery_app
from shared.processor import article_processor
from shared.rss_parser import RSSFeedParser
from shared.database import get_db_connection

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


@celery_app.task(bind=True, max_retries=3)
def process_feed_task(
    self,
    feed_url: str,
    max_entries: int = 50,
    auto_process_entries: bool = True
) -> Dict[str, Any]:
    """Process RSS feed and optionally enqueue entries for article processing."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            parser = RSSFeedParser()
            
            # Parse feed entries
            entries = loop.run_until_complete(parser.parse_feed(feed_url, max_entries))
            
            if not entries:
                logger.info(f"No entries found in feed: {feed_url}")
                return {
                    "status": "success",
                    "feed_url": feed_url,
                    "entries_found": 0,
                    "entries_processed": 0
                }
            
            # Update or create feed record
            feed_id = loop.run_until_complete(update_feed_record(feed_url, entries))
            
            processed_count = 0
            skipped_count = 0
            error_count = 0
            
            if auto_process_entries:
                for entry in entries:
                    try:
                        # Check if entry was already processed
                        if loop.run_until_complete(is_entry_already_processed(feed_id, entry)):
                            skipped_count += 1
                            continue
                        
                        # Record feed entry
                        entry_id = loop.run_until_complete(record_feed_entry(feed_id, entry))
                        
                        # Enqueue article processing
                        loop.run_until_complete(enqueue_article_from_feed_entry(entry, entry_id))
                        processed_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error processing feed entry: {e}")
                        error_count += 1
                        continue
            
            logger.info(
                f"Feed processing completed: {feed_url} - "
                f"{processed_count} processed, {skipped_count} skipped, {error_count} errors"
            )
            
            return {
                "status": "success",
                "feed_url": feed_url,
                "feed_id": feed_id,
                "entries_found": len(entries),
                "entries_processed": processed_count,
                "entries_skipped": skipped_count,
                "entries_error": error_count
            }
            
        finally:
            loop.run_until_complete(parser.close())
            loop.close()
            
    except Exception as exc:
        logger.error(f"Failed to process feed '{feed_url}': {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            logger.info(f"Retrying feed processing in {countdown} seconds...")
            raise self.retry(countdown=countdown, exc=exc)
        else:
            raise exc


@celery_app.task(bind=True, max_retries=3)
def process_feed_entry_task(
    self,
    entry_data: Dict[str, Any],
    feed_id: int,
    entry_id: int = None
) -> Dict[str, Any]:
    """Process individual feed entry into article."""
    try:
        # Extract entry data
        title = entry_data.get("title", "No title")
        content = entry_data.get("content", "")
        url = entry_data.get("url", "")
        published = entry_data.get("published")
        author = entry_data.get("author")
        summary = entry_data.get("summary")
        tags = entry_data.get("tags", [])
        
        if not content.strip():
            logger.warning(f"Skipping entry with empty content: {title}")
            return {
                "status": "skipped",
                "reason": "empty_content",
                "title": title
            }
        
        # Prepare metadata
        metadata = {
            "source_url": url,
            "feed_id": feed_id,
            "author": author,
            "summary": summary,
            "tags": tags,
            "entry_type": "rss_feed"
        }
        
        if published:
            try:
                if isinstance(published, str):
                    published_dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                else:
                    published_dt = published
                metadata["published_at"] = published_dt.isoformat()
            except Exception as e:
                logger.warning(f"Could not parse published date: {e}")
        
        # Process article using existing task
        result = process_article_task(
            title=title,
            content=content,
            metadata=metadata,
            chunk_size=500,
            chunk_overlap=50
        )
        
        # Update feed entry record with document ID
        if "document_id" in result and entry_id:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(update_feed_entry_document_id(entry_id, result["document_id"]))
            finally:
                loop.close()
        
        logger.info(f"Successfully processed feed entry: {title}")
        
        return {
            "status": "success",
            "title": title,
            "document_id": result.get("document_id"),
            "chunks_created": result.get("chunks_created", 0)
        }
        
    except Exception as exc:
        logger.error(f"Failed to process feed entry: {str(exc)}")
        
        # Update feed entry with error status
        if entry_id:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(update_feed_entry_status(entry_id, "error"))
            finally:
                loop.close()
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            logger.info(f"Retrying feed entry processing in {countdown} seconds...")
            raise self.retry(countdown=countdown, exc=exc)
        else:
            raise exc


# Helper functions for RSS processing

async def update_feed_record(feed_url: str, entries: List) -> int:
    """Update or create feed record in database."""
    conn = await get_db_connection()
    
    try:
        # Get feed info from first entry (or use URL as title)
        feed_title = f"RSS Feed: {feed_url}"
        if entries:
            # Try to extract feed title from parser context
            feed_title = f"RSS Feed: {feed_url}"
        
        # Use upsert function
        query = "SELECT upsert_feed($1, $2) as feed_id"
        result = await conn.fetchrow(query, feed_url, feed_title)
        
        # Update last fetched time
        await conn.execute(
            "UPDATE intelligence.feeds SET last_fetched_at = NOW() WHERE url = $1",
            feed_url
        )
        
        return result["feed_id"]
        
    finally:
        await conn.close()


async def is_entry_already_processed(feed_id: int, entry) -> bool:
    """Check if feed entry was already processed."""
    conn = await get_db_connection()
    
    try:
        entry_hash = entry.get_content_hash()
        entry_url = entry.url
        
        query = "SELECT is_entry_processed($1, $2, $3) as processed"
        result = await conn.fetchrow(query, feed_id, entry_hash, entry_url)
        
        return result["processed"]
        
    finally:
        await conn.close()


async def record_feed_entry(feed_id: int, entry) -> int:
    """Record feed entry in database."""
    conn = await get_db_connection()
    
    try:
        entry_hash = entry.get_content_hash()
        published_at = entry.published if entry.published else None
        
        query = "SELECT record_feed_entry($1, $2, $3, $4, $5) as entry_id"
        result = await conn.fetchrow(
            query, 
            feed_id, 
            entry.url, 
            entry.title, 
            entry_hash,
            published_at
        )
        
        return result["entry_id"]
        
    finally:
        await conn.close()


async def enqueue_article_from_feed_entry(entry, entry_id: int):
    """Enqueue article processing task for feed entry."""
    entry_data = entry.to_dict()
    
    # Dispatch task for processing
    process_feed_entry_task.delay(entry_data, entry_id)


async def update_feed_entry_document_id(entry_id: int, document_id: int):
    """Update feed entry with processed document ID."""
    conn = await get_db_connection()
    
    try:
        await conn.execute(
            """
            UPDATE intelligence.feed_entries 
            SET document_id = $1, processed_at = NOW(), status = 'processed'
            WHERE id = $2
            """,
            document_id, entry_id
        )
        
    finally:
        await conn.close()


async def update_feed_entry_status(entry_id: int, status: str):
    """Update feed entry status."""
    conn = await get_db_connection()
    
    try:
        await conn.execute(
            "UPDATE intelligence.feed_entries SET status = $1 WHERE id = $2",
            status, entry_id
        )
        
    finally:
        await conn.close()
