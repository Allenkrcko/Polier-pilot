"""RQ queue access. One Redis connection + one default queue for the MVP."""
from __future__ import annotations

from functools import lru_cache

import redis
from rq import Queue

from app.config import settings

DEFAULT_QUEUE_NAME = "default"


@lru_cache(maxsize=1)
def redis_conn() -> redis.Redis:
    """Sync Redis client used by RQ (RQ does not support async)."""
    return redis.Redis.from_url(settings.redis_url)


@lru_cache(maxsize=1)
def default_queue() -> Queue:
    return Queue(DEFAULT_QUEUE_NAME, connection=redis_conn(), default_timeout=300)
