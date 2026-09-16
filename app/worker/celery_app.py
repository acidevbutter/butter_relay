"""Celery app + broker config for butter_relay's own worker.

Producers (devbutter_backend and any future caller) publish onto this app's
queue by convention only -- task name, kwargs shape, queue name -- without
importing this module; see
devbutter_backend/app/core/clients/relay_client.py.

Start the worker with:
    celery -A app.worker.celery_app:celery_app worker --loglevel=info
"""

from __future__ import annotations

from celery import Celery

from app.settings.config import settings

TASK_NAME = "butter_relay.process_message"

celery_app = Celery(
    "butter_relay",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_default_queue=settings.relay_queue_name,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)
