"""Consumes domain events (order status changes, match events, roster changes) from the Redis Stream."""
import json
import logging
import time

import redis

from .core.config import REDIS_URL
from .core.queue import STREAM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker %(message)s")
log = logging.getLogger("worker")


def main() -> None:
    last_id = "$"
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        try:
            batches = client.xread({STREAM: last_id}, block=5000, count=100) or []
            for _, entries in batches:
                for entry_id, fields in entries:
                    last_id = entry_id
                    data = json.loads(fields.get("data", "{}"))
                    log.info("processed %s %s", fields.get("type"), data)
                    client.incr(f"{STREAM}:processed")
        except redis.RedisError as exc:
            log.warning("redis unavailable: %s", exc)
            time.sleep(2)


if __name__ == "__main__":
    main()
