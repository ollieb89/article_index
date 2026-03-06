import sys
import logging

# Add the parent directory to the path to import from api
sys.path.append('/app/api')

from celery_app import celery_app

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting article worker...")
    celery_app.worker_main()
