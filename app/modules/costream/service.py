"""Synchronized 4-way co-stream sessions (5.5).

A session's state is a pure function of its last accepted request, so every evaluation is
deterministic: an identical replay returns the stored body (200); a different body for an existing
session_id is evaluated afresh and replaces the stored state (201), which is how a later
simulate_host_drop request hands an existing session over. chat_room_id and composite_playback_url
derive from session_id alone, so they never change across handoffs."""
from psycopg.types.json import Json

from ...core.db import Jsonb
from ...core.errors import ApiError, conflict, not_found, validation_error
from ...core.registry import register
from ...core.signing import sign_day2
from ...core.validate import Validator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cs_sessions (
    session_id TEXT PRIMARY KEY, request JSONB NOT NULL, response JSON NOT NULL, status TEXT NOT NULL,
    revision INT NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""

MAX_PARTICIPANTS = 4
LAYOUT_CAPACITY = {"solo": 1, "grid_2": 2, "grid_4": MAX_PARTICIPANTS, "spotlight": MAX_PARTICIPANTS}
DEFAULT_LAYOUT = "grid_4"


def composite_playback_url(session_id: str) -> str:
    return f"http://localhost:8888/costream/{session_id}/index.m3u8"


def chat_room_id(session_id: str) -> str:
    return f"chat-{session_id}"


def parse(body) -> dict:
    v = Validator(body)
    session_id = v.string("session_id", max_len=128)
    host = v.string("host_player_id")
    participants = v.array("participants", min_len=1)
    if participants is not None and (not all(isinstance(p, str) and p for p in participants)
                                     or len(set(participants)) != len(participants)):
        v.bad("participants")
    layout = v.enum("layout", tuple(LAYOUT_CAPACITY), required=False)
    drop = v.boolean("simulate_host_drop", required=False)
    v.check()
    return {"session_id": session_id, "host_player_id": host, "participants": list(participants),
            "layout": layout or DEFAULT_LAYOUT, "simulate_host_drop": bool(drop)}


def handoff(req: dict) -> tuple[str, str | None, bool, list[str]]:
    """(status, active_host, handoff_occurred, active_participants) per rule 5."""
    host, participants = req["host_player_id"], req["participants"]
    if not req["simulate_host_drop"]:
        return "active", host, False, list(participants)
    remaining = [p for p in participants if p != host]
    if remaining:
        return "active", remaining[0], True, remaining
    return "terminated", None, False, []


def evaluate(conn, req: dict) -> dict:
    """Rules 2-5 in the spec's order; returns the response body."""
    participants, host = req["participants"], req["host_player_id"]
    referenced = participants + [host]
    known = {r["player_id"] for r in conn.execute("SELECT player_id FROM players WHERE player_id = ANY(%s)",
                                                  (referenced,)).fetchall()}
    for player_id in referenced:
        if player_id not in known:
            raise not_found("PLAYER_NOT_FOUND", f"Player {player_id} does not exist.", {"player_id": player_id})
    if host not in participants:
        raise validation_error(["host_player_id"], "host_player_id must be one of participants.")
    if len(participants) > MAX_PARTICIPANTS:
        raise conflict("COSTREAM_SESSION_FULL", f"A co-stream supports at most {MAX_PARTICIPANTS} broadcasters.",
                       {"session_id": req["session_id"], "participant_count": len(participants),
                        "max_participants": MAX_PARTICIPANTS})
    status, active_host, handed_off, active = handoff(req)
    capacity = LAYOUT_CAPACITY[req["layout"]]
    if len(active) > capacity:
        raise ApiError(400, "UNSUPPORTED_LAYOUT",
                       f"Layout {req['layout']} fits at most {capacity} active participant(s).",
                       {"layout": req["layout"], "participant_count": len(active), "max_participants": capacity})
    session_id = req["session_id"]
    sig_payload = {"active_host": active_host, "handoff_occurred": handed_off, "layout": req["layout"],
                   "participant_count": len(active), "session_id": session_id, "status": status}
    return {
        "session_id": session_id,
        "status": status,
        "active_host": active_host,
        "handoff_occurred": handed_off,
        "participant_count": len(active),
        "active_participants": active,
        "layout": req["layout"],
        "composite_playback_url": composite_playback_url(session_id),
        "chat_room_id": chat_room_id(session_id),
        "signature": sign_day2(session_id, sig_payload),
    }


def handle(conn, body) -> tuple[int, dict, bool]:
    """Returns (http status, response body, whether the stored state changed)."""
    req = parse(body)
    response = evaluate(conn, req)
    stored = conn.execute("SELECT request, response FROM cs_sessions WHERE session_id = %s FOR UPDATE",
                          (req["session_id"],)).fetchone()
    if stored is not None and stored["request"] == req:
        return 200, stored["response"], False
    store(conn, req, response)
    return 201, response, True


def store(conn, req: dict, response: dict) -> None:
    conn.execute(
        "INSERT INTO cs_sessions (session_id, request, response, status) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (session_id) DO UPDATE SET request = EXCLUDED.request, response = EXCLUDED.response, "
        "status = EXCLUDED.status, revision = cs_sessions.revision + 1, updated_at = now()",
        # json (not jsonb) keeps the response's key order for replays and get_session.
        (req["session_id"], Jsonb(req), Json(response), response["status"]))


def host_left(conn, session_id: str, player_id: str) -> dict | None:
    """Edge case 1 over WebSocket: the active host's last host socket closed. The session is re-evaluated
    as a simulate_host_drop of its current active participants, so hosting moves to the next one in array
    order (or the session terminates). Returns the new body, or None when player_id is not the active host."""
    row = conn.execute("SELECT response FROM cs_sessions WHERE session_id = %s FOR UPDATE", (session_id,)).fetchone()
    current = row["response"] if row else None
    if current is None or current["status"] != "active" or current["active_host"] != player_id:
        return None
    req = {"session_id": session_id, "host_player_id": player_id, "participants": current["active_participants"],
           "layout": current["layout"], "simulate_host_drop": True}
    response = evaluate(conn, req)
    store(conn, req, response)
    return response


def get_session(conn, session_id: str) -> dict | None:
    """The session's current state: exactly the last accepted POST response body (signature included)."""
    row = conn.execute("SELECT response FROM cs_sessions WHERE session_id = %s", (session_id,)).fetchone()
    return row["response"] if row else None


def session_status(conn, session_id: str) -> str | None:
    row = conn.execute("SELECT status FROM cs_sessions WHERE session_id = %s", (session_id,)).fetchone()
    return row["status"] if row else None


def on_reset() -> None:
    from . import composite

    composite.submit(composite.stop_all)


register(SCHEMA, ["cs_sessions"], on_reset=on_reset)
