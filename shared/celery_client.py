"""Celery client for API to enqueue tasks and check status.

Uses the same broker/backend as the worker so tasks can be sent and
results can be queried via AsyncResult.
"""
import os
from celery import Celery

broker_url = os.getenv(
    "CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0")
)
backend_url = os.getenv(
    "CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/1")
)

celery_app = Celery(
    "article_worker",
    broker=broker_url,
    backend=backend_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    result_expires=86400,  # 24 hours
)
