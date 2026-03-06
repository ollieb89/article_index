import os
import logging
from celery import Celery
from celery.signals import worker_ready
import asyncio

from shared.processor import article_processor, embedding_manager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Celery configuration
celery_app = Celery(
    'article_worker',
    broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    include=['tasks']
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    broker_connection_retry_on_startup=True,
)


@worker_ready.connect
def worker_ready_handler(sender=None, **kwargs):
    """Handler called when worker is ready."""
    logger.info("Article worker is ready!")
    
    # Ensure models are available
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        models_status = loop.run_until_complete(embedding_manager.ensure_models_available())
        logger.info(f"Models availability: {models_status}")
        
        loop.close()
    except Exception as e:
        logger.error(f"Failed to check models: {str(e)}")


# Import tasks after celery app is configured
from tasks import *
