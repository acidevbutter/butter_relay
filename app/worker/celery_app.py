"""Celery app + broker config, shared by the worker CLI and app.core.jobs-style
producers in other services (devbutter_backend, selolibertaria_backend) that
build their own lightweight Celery client against the same REDIS_URL and call
send_task(TASK_NAME, ...) by name -- they never import this module directly,
since they don't share a Python package with butter_relay.

Start the worker with:
    celery -A app.worker.celery_app:celery_app worker --loglevel=info -Q relay-shared
"""

from __future__ import annotations

from celery import Celery

from app.settings.config import settings

TASK_NAME = "butter_relay.process_message"

celery_app = Celery(
    "butter_relay",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Deferred import: app.worker.tasks/app.worker.queues are only loaded
    # inside the worker process (`celery -A app.worker.celery_app:celery_app
    # worker`), not by anything that merely imports this module for its
    # Celery instance. app.worker.queues registers the worker_ready handler
    # that seeds per-project queue consumers on boot (see that module).
    include=["app.worker.tasks", "app.worker.queues"],
)
celery_app.conf.update(
    # Fallback/default queue for producers that don't pass an explicit
    # `queue=` -- per-project queues (see app.projects.models.Project.queue_name)
    # are consumed dynamically, added via app.worker.queues.
    task_default_queue=settings.relay_queue_name,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=120,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)
