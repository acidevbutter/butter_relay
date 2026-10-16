"""Fixed-window rate limit per API key (DVB-308).

One Redis counter per key per minute (INCR + EXPIRE). The backend is swappable so
tests run without Redis. When Redis is unreachable the limiter fails open (logs a
warning): an outage of the counter must not take sending down with it.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.settings.config import settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


class RateLimitBackend(Protocol):
    async def hit(self, bucket: str, ttl_seconds: int) -> int: ...


class RedisBackend:
    async def hit(self, bucket: str, ttl_seconds: int) -> int:
        client = aioredis.from_url(  # type: ignore[no-untyped-call]  # redis-py stub gap
            settings.redis_url, socket_timeout=1, socket_connect_timeout=1
        )
        try:
            async with client.pipeline(transaction=True) as pipe:
                pipe.incr(bucket)
                pipe.expire(bucket, ttl_seconds, nx=True)
                count, _ = await pipe.execute()
            return int(count)
        finally:
            await client.aclose()


class MemoryBackend:
    """For tests and local runs without Redis."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, bucket: str, ttl_seconds: int) -> int:
        self.counts[bucket] = self.counts.get(bucket, 0) + 1
        return self.counts[bucket]


backend: RateLimitBackend = RedisBackend()


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int, limit: int):
        self.retry_after = retry_after
        self.limit = limit
        self.detail = f"Rate limit of {limit} requests per minute exceeded"


async def check(key_id: str, limit: int) -> None:
    """Raises RateLimitExceeded with seconds until the window resets. limit <= 0 disables."""
    if limit <= 0:
        return
    now = int(time.time())
    window = now // WINDOW_SECONDS
    try:
        count = await backend.hit(f"relay:rl:{key_id}:{window}", WINDOW_SECONDS + 5)
    except (RedisError, OSError) as exc:
        logger.warning("rate limit unavailable, allowing request: %s", type(exc).__name__)
        return
    if count > limit:
        raise RateLimitExceeded(retry_after=(window + 1) * WINDOW_SECONDS - now, limit=limit)
