"""Celery app + broker config, shared by the worker CLI and app.core.jobs-style
producers in other services (devbutter_backend, selolibertaria_backend) that
build their own lightweight Celery client against the same REDIS_URL and call
send_task(TASK_NAME, ...) by name -- they never import this module directly,
since they don't share a Python package with butter_relay.

Start the worker with:
    celery -A app.worker.celery_app:celery_app worker --loglevel=info -Q relay-shared
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging

from app.settings.config import settings
from app.settings.logging_config import setup_logging

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
    # Celery's remote-control commands (control.add_consumer, etc., used by
    # app.worker.queues to seed per-project queues) broadcast on a fanout
    # exchange that defaults to the same name ("celery") for every Celery app.
    # REDIS_URL is a broker shared with unrelated services (butter_blob,
    # devbutter_backend, selolibertaria_backend), so an unscoped broadcast
    # reaches their workers too -- they don't have butter_relay.process_message
    # registered, so they grab and silently discard the message before this
    # app's own worker gets it. A dedicated exchange name keeps these control
    # commands scoped to butter_relay's own worker fleet; it does not affect
    # task queues themselves (send_task(..., queue=...) is unrelated routing).
    control_exchange="butter_relay.pidbox",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=120,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)


@celery_setup_logging.connect  # type: ignore[untyped-decorator]  # celery ships no type information
def _configure_worker_logging(**kwargs: Any) -> None:
    """Fires only inside the worker process (never when a producer merely imports this
    module). Connecting a receiver to Celery's `setup_logging` signal makes Celery skip
    its own plain-text logging setup, so the worker emits the same structured logs as
    the API (acidevbutter/butter_relay#14).
    """
    setup_logging(settings.log_level)
