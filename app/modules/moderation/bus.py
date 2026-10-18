"""Notification bus and persistent dead-letter queue (FR-5.2).

notify() pushes an event to a realtime WebSocket topic. A delivery that fails (simulate_dlq=True, or
require_subscriber=True while nobody is subscribed to the topic, or the transport raising) is retried with
bounded exponential backoff: MAX_ATTEMPTS tries, sleeping BACKOFF_BASE_S * 2**(n-1) between tries. Whatever
still fails is written to md_dlq (Postgres, so it survives restarts; wiped by /audit/v1/reset).

Plain broadcasts to a topic with no listeners are NOT failures, so score updates and order transitions
never fill the DLQ just because no client happens to be connected.

Order status transitions reach the bus from the Redis domain-event stream (consume_forever, a daemon
thread started at import); co-stream invites are sent by calling notify_costream_invite().
"""
import datetime as dt
import json
import logging
import os
import sys
import threading
import time
import uuid

from ...core import config, db, metrics, queue, realtime
from ...core.db import Jsonb
from ...core.registry import register

log = logging.getLogger("moderation.bus")

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = float(os.environ.get("NASCON_DLQ_BACKOFF_S", "0.01"))
LIST_LIMIT = 500
_sleep = time.sleep

SCHEMA = """
CREATE TABLE IF NOT EXISTS md_dlq (
    dlq_id BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL, topic TEXT NOT NULL,
    payload JSONB NOT NULL, attempts INT NOT NULL, last_error TEXT NOT NULL, failed_at TEXT NOT NULL);
"""
register(SCHEMA, ["md_dlq"])


class DeliveryError(Exception):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def has_subscribers(topic: str) -> bool:
    return bool(realtime._subs.get(topic))


def _attempt(note: dict) -> None:
    if note["simulate_dlq"]:
        raise DeliveryError("simulated downstream push failure")
    if note["require_subscriber"] and not has_subscribers(note["topic"]):
        raise DeliveryError("target subscriber disconnected")
    realtime.publish(note["topic"], {"type": note["event_type"], **note["payload"]})


def deliver(notes: list[dict]) -> list[dict]:
    """Every note gets up to MAX_ATTEMPTS tries; all notes still failing after a round wait out the same
    backoff together, so N simulated failures cost one backoff schedule, not N."""
    pending = notes
    for attempt in range(1, MAX_ATTEMPTS + 1):
        failed = []
        for note in pending:
            note["attempts"] = attempt
            try:
                _attempt(note)
                note["delivered"] = True
            except Exception as exc:
                note["delivered"] = False
                note["last_error"] = str(exc) or exc.__class__.__name__
                failed.append(note)
        pending = failed
        if not pending:
            break
        if attempt < MAX_ATTEMPTS:
            _sleep(BACKOFF_BASE_S * 2 ** (attempt - 1))
    if pending:
        dead_letter(pending)
    return notes


def make_note(topic: str, event_type: str, payload: dict, simulate_dlq: bool = False,
              require_subscriber: bool = False) -> dict:
    return {"event_id": "evt-" + uuid.uuid4().hex[:20], "topic": topic, "event_type": event_type,
            "payload": payload, "simulate_dlq": bool(simulate_dlq), "require_subscriber": bool(require_subscriber),
            "attempts": 0, "delivered": False}


def notify(topic: str, event_type: str, payload: dict, simulate_dlq: bool = False,
           require_subscriber: bool = False) -> dict:
    return deliver([make_note(topic, event_type, payload, simulate_dlq, require_subscriber)])[0]


def notify_costream_invite(session_id: str, player_id: str, invite: dict, simulate_dlq: bool = False) -> list[dict]:
    """Co-stream invite to the invited player's personal topic and to the session topic."""
    payload = {"session_id": session_id, "player_id": player_id, "invite": invite}
    return deliver([make_note(f"user:{player_id}", "costream_invite", payload, simulate_dlq),
                    make_note(f"costream:{session_id}", "costream_invite", payload, simulate_dlq)])


def dead_letter(notes: list[dict]) -> None:
    at = now_iso()
    with db.tx() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO md_dlq (event_id, event_type, topic, payload, attempts, last_error, failed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                [(n["event_id"], n["event_type"], n["topic"], Jsonb(n["payload"]), n["attempts"], n["last_error"], at)
                 for n in notes])


def depth(conn=None) -> int:
    sql = "SELECT count(*) AS n FROM md_dlq"
    row = conn.execute(sql).fetchone() if conn is not None else db.fetchone(sql)
    return int(row["n"])


def undelivered(conn=None, limit: int = LIST_LIMIT) -> list[dict]:
    sql = ("SELECT event_id, event_type, topic, payload, attempts, last_error, failed_at FROM md_dlq "
           "ORDER BY dlq_id LIMIT %s")
    rows = conn.execute(sql, (limit,)).fetchall() if conn is not None else db.fetchall(sql, (limit,))
    return [dict(r) for r in rows]


metrics.register("nascon_dlq_depth", depth)

# Domain events (Redis stream) that become realtime notifications: event type -> topics for its data.
STREAM_ROUTES = {
    "order_status_changed": lambda d: ["orders", f"order:{d.get('order_id')}"],
}


def handle_domain_event(event_type: str | None, data: dict) -> list[dict]:
    route = STREAM_ROUTES.get(event_type or "")
    if route is None:
        return []
    return deliver([make_note(topic, event_type, {"data": data}) for topic in route(data)])


def consume_forever(stop: threading.Event | None = None) -> None:  # pragma: no cover - background loop
    import redis

    last_id = "$"
    client = None
    while stop is None or not stop.is_set():
        try:
            if client is None:
                client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True, socket_timeout=10,
                                              socket_connect_timeout=2)
            for _, entries in client.xread({queue.STREAM: last_id}, block=5000, count=100) or []:
                for entry_id, fields in entries:
                    last_id = entry_id
                    try:
                        handle_domain_event(fields.get("type"), json.loads(fields.get("data") or "{}"))
                    except Exception:
                        log.exception("notification for %s failed", fields.get("type"))
        except Exception as exc:
            log.debug("domain stream unavailable: %s", exc)
            client = None
            time.sleep(2)


if os.environ.get("NASCON_BUS_CONSUMER", "1") != "0" and "pytest" not in sys.modules:  # pragma: no cover
    threading.Thread(target=consume_forever, name="notification-bus", daemon=True).start()
