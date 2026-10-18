"""Chat moderation probe (Section 5.8), member chat with per-room sequencing (FR-3.3, FR-5.3).

Probe semantics (POST /audit/v1/moderation/test), decided where the contract is silent:
- kicked_users / muted_users describe THIS request: senders whose messages in incoming_messages were
  kicked / muted, deduplicated, in order of first violation. The same actions are also persisted per room
  (md_room_actions, one row per (room, user, action), INSERT .. ON CONFLICT DO NOTHING), so concurrent
  identical kicks are idempotent and never duplicated or deadlocked.
- flagged_count (signed, not returned) = number of entries in this response's filtered_messages with
  flagged true. Dropped messages (flood, rate limit) are not counted: the value is recomputable from the
  response body alone. Flip FLAGGED_COUNT_INCLUDES_DROPPED to count them.
- Rate limit (FR-5.3): more than RATE_MAX distinct message_ids from one sender in one room within
  RATE_WINDOW_S seconds, across requests. Counted per message_id, so replaying the identical request (edge
  case 3) never trips it. It only blocks messages that are otherwise clean (profanity and flood keep their
  own outcome); a blocked message is dropped and its sender kicked and muted. The window is in memory and
  cleared by /audit/v1/reset.
- queue_depth is the whole DLQ (it is one system-wide queue, also exported as nascon_dlq_depth).
  check_dlq false still reports the depth (cheap count) but skips listing undelivered_events.
"""
import threading
import time
import uuid
from collections import OrderedDict

from ...core import db, realtime
from ...core.db import Jsonb
from ...core.errors import ApiError
from ...core.registry import register
from ...core.signing import sign_day2
from . import bus, rules

RATE_MAX = 3
RATE_WINDOW_S = 2.0
MUTE_S = 10.0
FLAGGED_COUNT_INCLUDES_DROPPED = False
ANON_SENDER = "anonymized-user"
ANON_NAME = "Anonymized User"

SCHEMA = """
CREATE TABLE IF NOT EXISTS md_room_actions (
    chat_room_id TEXT NOT NULL, user_id TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL,
    at TEXT NOT NULL, PRIMARY KEY (chat_room_id, user_id, action));
CREATE TABLE IF NOT EXISTS md_audit (
    audit_id BIGSERIAL PRIMARY KEY, chat_room_id TEXT NOT NULL, message_count INT NOT NULL,
    flagged_count INT NOT NULL, kicked_users JSONB NOT NULL, muted_users JSONB NOT NULL, queue_depth INT NOT NULL,
    signature TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS md_chat_rooms (chat_room_id TEXT PRIMARY KEY, last_sequence BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS md_chat_messages (
    chat_room_id TEXT NOT NULL, sequence BIGINT NOT NULL, message_id TEXT NOT NULL, sender TEXT NOT NULL,
    sender_name TEXT NOT NULL, text TEXT NOT NULL, flagged BOOLEAN NOT NULL, reason TEXT, sent_at TEXT NOT NULL,
    PRIMARY KEY (chat_room_id, sequence), UNIQUE (chat_room_id, message_id));
"""
TABLES = ["md_room_actions", "md_audit", "md_chat_rooms", "md_chat_messages"]


class RateWindow:
    """Sliding window of distinct message ids per key; thread-safe, in memory (one API worker)."""

    def __init__(self, limit: int = RATE_MAX, window_s: float = RATE_WINDOW_S, sweep_every: int = 1000):
        self.limit = limit
        self.window_s = window_s
        self.sweep_every = sweep_every
        self._hits: dict[tuple, OrderedDict] = {}
        self._calls = 0
        self._lock = threading.Lock()

    def hit(self, key: tuple, message_id: str, now: float) -> bool:
        """Records message_id under key; True when key now has more than `limit` ids inside the window."""
        with self._lock:
            self._calls += 1
            if self._calls % self.sweep_every == 0:
                # drop senders whose newest message left the window, so one-off senders do not pile up
                for stale in [k for k, v in self._hits.items() if next(reversed(v.values())) <= now - self.window_s]:
                    del self._hits[stale]
            ids = self._hits.setdefault(key, OrderedDict())
            while ids and next(iter(ids.values())) <= now - self.window_s:
                ids.popitem(last=False)
            if message_id not in ids:
                ids[message_id] = now
            return len(ids) > self.limit

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


window = RateWindow()
_recorded: set[tuple] = set()
_recorded_lock = threading.Lock()
_mutes: dict[tuple, float] = {}


def _on_reset() -> None:
    window.clear()
    with _recorded_lock:
        _recorded.clear()
        _mutes.clear()


register(SCHEMA, TABLES, on_reset=_on_reset)


def _append_once(seq: list, item) -> None:
    if item not in seq:
        seq.append(item)


def record_action(room: str, user: str, action: str, reason: str) -> None:
    """Idempotent moderator action: one autocommit statement, so concurrent identical kicks just collide
    on the primary key (DO NOTHING) and no transaction ever holds two row locks."""
    key = (room, user, action)
    with _recorded_lock:
        if key in _recorded:
            return
    db.execute("INSERT INTO md_room_actions (chat_room_id, user_id, action, reason, at) VALUES (%s, %s, %s, %s, %s) "
               "ON CONFLICT (chat_room_id, user_id, action) DO NOTHING", (room, user, action, reason, bus.now_iso()))
    with _recorded_lock:
        _recorded.add(key)


def run_probe(req: dict) -> dict:
    room = req["chat_room_id"]
    filtered, kicked, muted = [], [], []
    dropped = 0
    actions: list[tuple[str, str, str]] = []
    now = time.monotonic()
    for msg in req["incoming_messages"]:
        sender = msg["sender"]
        reason, text = rules.evaluate(msg["text"])
        over_rate = window.hit(("probe", room, sender), msg["message_id"], now)
        if reason == rules.FLOOD:
            dropped += 1
            _append_once(muted, sender)
            actions.append((sender, "mute", reason))
        elif reason == rules.PROFANITY:
            filtered.append({"message_id": msg["message_id"], "sender": sender, "text": text,
                             "flagged": True, "reason": reason})
            _append_once(kicked, sender)
            actions.append((sender, "kick", reason))
        elif over_rate:
            dropped += 1
            _append_once(kicked, sender)
            _append_once(muted, sender)
            actions.append((sender, "kick", rules.RATE_LIMIT))
            actions.append((sender, "mute", rules.RATE_LIMIT))
        else:
            filtered.append({"message_id": msg["message_id"], "sender": sender, "text": text,
                             "flagged": False, "reason": None})
    for sender, action, reason in sorted(set(actions)):
        record_action(room, sender, action, reason)

    failed = req["simulate_failed_notifications"]
    notes = bus.deliver([bus.make_note(f"chat:{room}", "chat_notification",
                                       {"chat_room_id": room, "notification": n + 1}, simulate_dlq=True)
                         for n in range(failed)], dead_letter_now=False)

    flagged_count = sum(1 for m in filtered if m["flagged"])
    if FLAGGED_COUNT_INCLUDES_DROPPED:
        flagged_count += dropped
    with db.tx() as conn:
        if notes:
            # enqueue + read in one transaction under the DLQ lock: concurrent probes report k, 2k, .. 10k
            bus.dead_letter(conn, [n for n in notes if not n["delivered"]])
        queue_depth = bus.depth(conn)
        events = bus.undelivered(conn) if req["check_dlq"] else []
        signature = sign_day2(room, {"chat_room_id": room, "flagged_count": flagged_count,
                                     "kicked_users": kicked, "queue_depth": queue_depth})
        conn.execute("INSERT INTO md_audit (chat_room_id, message_count, flagged_count, kicked_users, muted_users, "
                     "queue_depth, signature, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                     (room, len(req["incoming_messages"]), flagged_count, Jsonb(kicked), Jsonb(muted), queue_depth,
                      signature, bus.now_iso()))
    return {"chat_room_id": room, "filtered_messages": filtered, "kicked_users": kicked, "muted_users": muted,
            "dlq_status": {"queue_depth": queue_depth, "undelivered_events": events},
            "signature": signature}


# ---- member chat ---------------------------------------------------------------------------------------

def _message_view(row: dict) -> dict:
    return {"sequence": row["sequence"], "message_id": row["message_id"], "sender": row["sender"],
            "sender_name": row["sender_name"], "text": row["text"], "flagged": row["flagged"],
            "reason": row["reason"], "sent_at": row["sent_at"]}


def _muted_for(room: str, sender: str, now: float) -> float:
    with _recorded_lock:
        until = _mutes.get((room, sender), 0.0)
    return until - now


def _mute(room: str, sender: str, now: float) -> None:
    with _recorded_lock:
        _mutes[(room, sender)] = now + MUTE_S


def post_chat(room: str, sender: str, text: str, message_id: str | None) -> tuple[int, dict]:
    if message_id is not None:
        row = db.fetchone("SELECT * FROM md_chat_messages WHERE chat_room_id = %s AND message_id = %s",
                          (room, message_id))
        if row is not None:
            return 200, {"chat_room_id": room, "blocked": False, "message": _message_view(row)}
    now = time.monotonic()
    left = _muted_for(room, sender, now)
    if left > 0:
        raise ApiError(429, "RATE_LIMITED", "Sender is muted in this chat room.",
                       {"chat_room_id": room, "sender": sender, "reason": "muted",
                        "retry_after_seconds": max(1, int(left + 0.999))})
    message_id = message_id or "msg-" + uuid.uuid4().hex[:16]
    reason, shown = rules.evaluate(text)
    over_rate = window.hit(("chat", room, sender), message_id, now)
    if reason == rules.FLOOD:
        _mute(room, sender, now)
        record_action(room, sender, "mute", reason)
        _broadcast_action(room, sender, "mute", reason)
        return 200, {"chat_room_id": room, "blocked": True, "reason": reason, "muted": True, "kicked": False,
                     "message": None}
    if over_rate:
        _mute(room, sender, now)
        record_action(room, sender, "kick", rules.RATE_LIMIT)
        record_action(room, sender, "mute", rules.RATE_LIMIT)
        _broadcast_action(room, sender, "mute", rules.RATE_LIMIT)
        raise ApiError(429, "RATE_LIMITED", "Chat flood threshold exceeded.",
                       {"chat_room_id": room, "sender": sender, "reason": rules.RATE_LIMIT,
                        "retry_after_seconds": int(MUTE_S)})
    with db.tx() as conn:
        user = conn.execute("SELECT display_name FROM users WHERE user_id = %s", (sender,)).fetchone()
        seq = conn.execute("INSERT INTO md_chat_rooms (chat_room_id, last_sequence) VALUES (%s, 1) "
                           "ON CONFLICT (chat_room_id) DO UPDATE SET last_sequence = md_chat_rooms.last_sequence + 1 "
                           "RETURNING last_sequence", (room,)).fetchone()["last_sequence"]
        row = conn.execute("INSERT INTO md_chat_messages (chat_room_id, sequence, message_id, sender, sender_name, text, "
                           "flagged, reason, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                           (room, seq, message_id, sender, user["display_name"] if user else sender, shown,
                            reason is not None, reason, bus.now_iso())).fetchone()
    message = _message_view(row)
    realtime.publish(f"chat:{room}", {"type": "chat_message", "chat_room_id": room, "message": message})
    if reason == rules.PROFANITY:
        record_action(room, sender, "kick", reason)
        _broadcast_action(room, sender, "kick", reason)
    return 201, {"chat_room_id": room, "blocked": False, "message": message}


def _broadcast_action(room: str, user: str, action: str, reason: str) -> None:
    realtime.publish(f"chat:{room}", {"type": "moderation", "chat_room_id": room, "action": action, "user": user,
                                      "reason": reason})


def list_chat(room: str, after: int | None, limit: int) -> dict:
    if after is not None:
        rows = db.fetchall("SELECT * FROM md_chat_messages WHERE chat_room_id = %s AND sequence > %s "
                           "ORDER BY sequence LIMIT %s", (room, after, limit))
    else:
        rows = db.fetchall("SELECT * FROM (SELECT * FROM md_chat_messages WHERE chat_room_id = %s "
                           "ORDER BY sequence DESC LIMIT %s) latest ORDER BY sequence", (room, limit))
    return {"chat_room_id": room, "messages": [_message_view(r) for r in rows]}


def anonymize_user(conn, user_ids: list[str]) -> None:
    """Right to erasure: chat history keeps the text but loses who wrote it; moderation rows go."""
    conn.execute("UPDATE md_chat_messages SET sender = %s, sender_name = %s WHERE sender = ANY(%s)",
                 (ANON_SENDER, ANON_NAME, user_ids))
    conn.execute("DELETE FROM md_room_actions WHERE user_id = ANY(%s)", (user_ids,))
    with _recorded_lock:
        for key in [k for k in _recorded if k[1] in user_ids]:
            _recorded.discard(key)
        for key in [k for k in _mutes if k[1] in user_ids]:
            _mutes.pop(key, None)
