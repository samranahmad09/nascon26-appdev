"""Offline sync: replay the actions a device queued while offline (Section 5.14, NFR-4).

Actions are processed one by one in queued_at order (ties keep array order), each against the state the previous
one left; results come back in request order. Each action runs in its own savepoint, so a rejected action changes
nothing while the others still apply. Precedence for one action:
  1. (device_id, action_id) processed before (in an earlier request or earlier in this one) -> duplicate
  2. identical replay of an existing event / accepted roster request                     -> duplicate
  3. payload schema problem                                                              -> rejected, rule's 400 code
  4. unknown match / league / player / squad                                             -> rejected, *_NOT_FOUND
  5. base_version > current version                                                      -> rejected, INVALID_BASE_VERSION
  6. any other rule of 5.9 / 5.11                                                        -> rejected, the rule's code
  7. otherwise applied: "appended" if base_version == current version, else "rebased_append"
Every processed (device_id, action_id) is recorded, rejected ones included. server_version is the match version
or the player's audit-entry count after processing (unchanged for rejected / duplicate, 0 for an unknown id).
"""
import datetime as dt
import logging

from ...core.db import Jsonb
from ...core.errors import ApiError
from ...core.registry import register
from ...core.validate import TS_RE, Validator, is_id, is_int
from ..roster import service as roster

log = logging.getLogger("offline")

ACTION_TYPES = ("score_event", "roster_transfer")
MAX_ACTIONS = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS offline_actions (
    device_id TEXT NOT NULL, action_id TEXT NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL,
    result JSONB NOT NULL, seq BIGSERIAL, PRIMARY KEY (device_id, action_id));
"""

register(SCHEMA, ["offline_actions"])


class _Stale(Exception):
    """base_version is ahead of the server: the action must not apply."""


def _is_timestamp(value) -> bool:
    if not isinstance(value, str) or not TS_RE.fullmatch(value):
        return False
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _valid_action(a) -> bool:
    return isinstance(a, dict) and is_id(a.get("action_id")) and isinstance(a.get("type"), str) \
        and a["type"] in ACTION_TYPES and _is_timestamp(a.get("queued_at")) \
        and is_int(a.get("base_version")) and a["base_version"] >= 0 and isinstance(a.get("payload"), dict)


def validate_envelope(body) -> tuple[str, list[dict]]:
    v = Validator(body)
    device_id = v.ident("device_id")
    actions = v.array("actions", min_len=1, max_len=MAX_ACTIONS)
    if actions is not None and not all(_valid_action(a) for a in actions):
        v.bad("actions")
    v.check()
    return device_id, actions


def _result(status: str, resolution: str, error_code, version, signature=None) -> dict:
    return {"status": status, "resolution": resolution, "error_code": error_code,
            "server_version": version, "signature": signature}


def _duplicate(version) -> dict:
    return _result("duplicate", "skipped_duplicate", None, version)


def _rejected(code: str, version) -> dict:
    return _result("rejected", "server_wins", code, version)


def _applied(base: int, current: int, version, signature=None) -> dict:
    return _result("applied", "appended" if base == current else "rebased_append", None, version, signature)


def _payload_id(payload: dict, key: str):
    value = payload.get(key)
    return value if is_id(value) else None


# ---------------------------------------------------------------- per-type handlers

def _roster_version(conn, payload: dict) -> int:
    player_id = _payload_id(payload, "player_id")
    if player_id is None:
        return 0
    return roster.player_version(conn, player_id, _payload_id(payload, "league_id")) or 0


def _roster_transfer(conn, action: dict, check_only: bool) -> dict:
    payload = action["payload"]
    current = _roster_version(conn, payload)
    if check_only:
        return _duplicate(current)

    def gate(_league_id, _player_id):
        if action["base_version"] > current:
            raise _Stale()

    try:
        with conn.transaction():
            status, _ = roster.transfer(conn, payload, before_rules=gate)
    except _Stale:
        return _rejected("INVALID_BASE_VERSION", current)
    except ApiError as exc:
        return _rejected(exc.code, current)
    if status == 200:
        return _duplicate(current)
    return _applied(action["base_version"], current, _roster_version(conn, payload))


def _scoring():
    from ..scoring import service as scoring  # merged from the scoring branch; imported lazily
    return scoring


def _score_version(conn, scoring, payload: dict) -> int:
    match_id = _payload_id(payload, "match_id")
    if match_id is None:
        return 0
    return scoring.match_version(conn, match_id) or 0


def _score_event(conn, action: dict, check_only: bool) -> dict:
    try:
        scoring = _scoring()
    except ImportError:
        log.error("scoring service is not available; score_event actions cannot be applied")
        return _duplicate(0) if check_only else _rejected("MATCH_NOT_FOUND", 0)
    payload = action["payload"]
    current = _score_version(conn, scoring, payload)
    if check_only:
        return _duplicate(current)
    stale = action["base_version"] > current
    try:
        with conn.transaction():
            status, response = scoring.append_event(conn, payload)
            if status == 201 and stale:
                raise _Stale()
    except _Stale:
        return _rejected("INVALID_BASE_VERSION", current)
    except ApiError as exc:
        if exc.status == 409 and stale:
            return _rejected("INVALID_BASE_VERSION", current)
        return _rejected(exc.code, current)
    if status == 200:
        return _duplicate(current)
    version = response.get("match_version")
    if not is_int(version):
        version = _score_version(conn, scoring, payload)
    return _applied(action["base_version"], current, version, response.get("signature"))


HANDLERS = {"score_event": _score_event, "roster_transfer": _roster_transfer}


def _process(conn, device_id: str, action: dict) -> dict:
    seen = conn.execute("SELECT 1 FROM offline_actions WHERE device_id = %s AND action_id = %s",
                        (device_id, action["action_id"])).fetchone()
    handler = HANDLERS[action["type"]]
    try:
        result = handler(conn, action, seen is not None)
    except Exception:
        log.exception("offline action %s/%s failed", device_id, action["action_id"])
        result = _rejected("VALIDATION_ERROR", 0)
    if seen is None:
        conn.execute("INSERT INTO offline_actions (device_id, action_id, type, status, result) "
                     "VALUES (%s, %s, %s, %s, %s)",
                     (device_id, action["action_id"], action["type"], result["status"], Jsonb(result)))
    return result


def sync(conn, body) -> dict:
    device_id, actions = validate_envelope(body)
    order = sorted(range(len(actions)), key=lambda i: actions[i]["queued_at"])
    results: list[dict | None] = [None] * len(actions)
    summary = {"applied": 0, "rejected": 0, "duplicate": 0}
    for position, index in enumerate(order, start=1):
        action = actions[index]
        r = _process(conn, device_id, action)
        summary[r["status"]] += 1
        results[index] = {"action_id": action["action_id"], "status": r["status"], "resolution": r["resolution"],
                          "error_code": r["error_code"], "processed_order": position,
                          "server_version": r["server_version"], "signature": r["signature"]}
    return {"device_id": device_id, "results": results, "summary": summary}
