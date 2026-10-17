"""Domain events go to a Redis Stream consumed by the worker service. Best effort: never fails a request."""
import json
import logging
import time

import redis

from . import config

STREAM = "domain-events"
log = logging.getLogger("queue")
_client = None
_down_until = 0.0


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(config.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5)
    return _client


def emit(event_type: str, data: dict) -> None:
    global _down_until
    if time.time() < _down_until:
        return
    try:
        _redis().xadd(STREAM, {"type": event_type, "data": json.dumps(data, default=str)}, maxlen=10000, approximate=True)
    except Exception as exc:
        _down_until = time.time() + 15
        log.warning("domain event %s not queued: %s", event_type, exc)
