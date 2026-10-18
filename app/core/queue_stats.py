"""Celery backlog per queue, read straight from the Redis broker (DVB-188).

With the Redis transport each queue is a Redis list named after the queue, so its
length is the number of tasks waiting for a worker. Errors are reported, not raised:
the admin dashboard must render even when Redis is down.
"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import redis

from app.settings.config import settings

logger = logging.getLogger(__name__)


def _lengths(queues: list[str]) -> dict[str, int]:
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
    try:
        # The sync client returns plain ints; redis-py's type hints also cover asyncio.
        return {queue: int(cast(int, client.llen(queue))) for queue in queues}
    finally:
        client.close()


async def queue_backlog(queues: list[str]) -> dict[str, int] | None:
    try:
        return await asyncio.to_thread(_lengths, queues)
    except redis.RedisError as exc:
        logger.warning("could not read queue backlog: %s", type(exc).__name__)
        return None
