"""Queue provisioning for per-project Celery queues.

Celery's Redis transport needs no queue pre-creation the way SQS did --
consuming from a queue is just telling a running worker to start reading a
Redis list key. `add_consumer` (a standard Celery remote-control command)
does exactly that, live, on any already-running worker, with no restart.

Two call sites:
- `add_consumer_for_project` runs in the API process right after a project is
  created, so a worker that's already up picks up the new queue immediately.
- `seed_all_queues` runs once per worker boot (via the `worker_ready` signal),
  so a freshly (re)started worker consumes every active project's queue plus
  the legacy shared queue, without depending on the live broadcast above
  having reached it.
"""

from __future__ import annotations

import asyncio
import logging

from celery.signals import worker_ready

from app.db.session import session_factory
from app.projects.models import Project, queue_name_for
from app.projects.repository import ProjectRepository
from app.settings.config import settings
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def add_consumer_for_project(project: Project) -> None:
    """Best-effort: if the broadcast fails (e.g. broker briefly unreachable),
    the next worker boot's seed_all_queues() still picks the project's queue
    up, so project creation should not fail because of this.
    """
    try:
        celery_app.control.add_consumer(project.queue_name, reply=False)
    except Exception:
        logger.warning(
            "failed to broadcast add_consumer for queue %s", project.queue_name, exc_info=True
        )


async def _active_project_queue_names() -> set[str]:
    async with session_factory() as session:
        projects = await ProjectRepository(session).list()
    return {queue_name_for(p.slug) for p in projects if p.is_active}


def seed_all_queues() -> None:
    queue_names = {settings.relay_queue_name, *asyncio.run(_active_project_queue_names())}
    for queue_name in queue_names:
        celery_app.control.add_consumer(queue_name, reply=False)
    logger.info("worker seeded consumers for queues: %s", sorted(queue_names))


@worker_ready.connect  # type: ignore[untyped-decorator]  # celery ships no type information
def _on_worker_ready(sender: object = None, **kwargs: object) -> None:
    seed_all_queues()
