"""Event-sourced scoring engine and league registration (spec 5.8, 5.9).

Contract used by other modules. Every function takes an open connection inside the caller's
transaction; the caller holds locks.GLOBAL. Nothing here commits or opens its own transaction,
and every ApiError is raised before the first write, so a rejected call leaves no trace.

Storage is append-only: scoring_events rows are never updated or deleted, scoring_matches and
scoring_leagues rows are written once at creation. Match state is rebuilt from the log on every
read (logic.fold); there are no score counters.
"""
import json
import logging

from ...core import queue, realtime
from ...core.db import Jsonb
from ...core.errors import ApiError, conflict, not_found
from ...core.registry import register
from ...core.seed import get_last_seed
from ...core.signing import sign
from ...core.validate import utc_now
from ..sports import get_sport
from . import logic

__all__ = ["append_event", "match_version", "project", "register_league", "get_league", "league_projections",
           "ApiError"]

log = logging.getLogger("scoring")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scoring_leagues (
    league_id TEXT PRIMARY KEY, sport_id TEXT NOT NULL, config JSONB NOT NULL, created_seq BIGSERIAL);
CREATE TABLE IF NOT EXISTS scoring_matches (
    match_id TEXT PRIMARY KEY, sport_id TEXT NOT NULL, home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL,
    league_id TEXT, fixture_of TEXT, fixture_index INT, created_seq BIGSERIAL);
CREATE INDEX IF NOT EXISTS scoring_matches_fixture ON scoring_matches (fixture_of, fixture_index);
CREATE TABLE IF NOT EXISTS scoring_events (
    row_id BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, match_id TEXT NOT NULL,
    sequence INT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, request TEXT NOT NULL,
    signature TEXT NOT NULL, compensates TEXT, response JSONB NOT NULL, recorded_at TEXT NOT NULL,
    UNIQUE (match_id, sequence));
CREATE UNIQUE INDEX IF NOT EXISTS scoring_events_compensates ON scoring_events (compensates)
    WHERE compensates IS NOT NULL;
"""

register(SCHEMA, ["scoring_events", "scoring_matches", "scoring_leagues"])

_STREAM_COLS = "match_id, sport_id, home_team_id, away_team_id, league_id"
_EVENT_COLS = "sequence, event_id, type, payload, signature, compensates"
_RESPONSE_KEYS = ("event_id", "match_id", "type", "sequence", "match_version", "signature")


def _stream(conn, match_id: str) -> dict | None:
    return conn.execute(f"SELECT {_STREAM_COLS} FROM scoring_matches WHERE match_id = %s", (match_id,)).fetchone()


def _decode(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


def _rows(conn, match_id: str) -> list[dict]:
    return _decode(conn.execute(f"SELECT {_EVENT_COLS} FROM scoring_events WHERE match_id = %s ORDER BY sequence",
                                (match_id,)).fetchall())


# ---------------------------------------------------------------------------------------------
# POST /audit/v1/events
# ---------------------------------------------------------------------------------------------

def _check_compensation(conn, rows: list[dict], event: dict) -> None:
    target_id = event["payload"]["compensates"]
    target = next((r for r in rows if r["event_id"] == target_id), None)
    if target is None:
        elsewhere = conn.execute("SELECT match_id FROM scoring_events WHERE event_id = %s", (target_id,)).fetchone()
        if elsewhere is None:
            raise not_found("EVENT_NOT_FOUND", f"Event {target_id} does not exist.", {"event_id": target_id})
        raise conflict("INVALID_COMPENSATION", f"Event {target_id} belongs to another match.",
                       {"event_id": target_id, "match_id": elsewhere["match_id"]})
    if target["type"] not in logic.COMPENSABLE:
        raise conflict("INVALID_COMPENSATION", f"Event {target_id} of type {target['type']} cannot be compensated.",
                       {"event_id": target_id, "type": target["type"]})
    done = next((r["event_id"] for r in rows if r["compensates"] == target_id), None)
    if done is not None:
        raise conflict("INVALID_COMPENSATION", f"Event {target_id} is already compensated by {done}.",
                       {"event_id": target_id, "compensated_by": done})


def _announce(stream: dict, sport: dict, rows: list[dict], response: dict, payload: dict) -> None:
    """Realtime fan-out and domain event; best effort, never fails the append."""
    try:
        match = logic.projection(stream, sport, rows)
        realtime.publish(f"match:{stream['match_id']}", {"type": "score_update", "match": match})
        queue.emit("match_event", {**response, "payload": payload, "status": match["status"],
                                   "outcome": match["outcome"], "league_id": match["league_id"]})
    except Exception as exc:  # pragma: no cover - transport failures only
        log.warning("score update for %s not announced: %s", stream["match_id"], exc)


def append_event(conn, body) -> tuple[int, dict]:
    """Full /audit/v1/events semantics on a parsed body.

    Returns (201, response) for a new event or (200, stored_response) for an identical replay;
    raises ApiError for every 400 / 404 / 409.
    Order: schema 400 -> event_id idempotency (200 replay / 409 DUPLICATE_EVENT_ID) -> 404 -> 400 -> 409.
    """
    event = logic.validate_event(body)
    prior = conn.execute("SELECT request, response FROM scoring_events WHERE event_id = %s",
                         (event["event_id"],)).fetchone()
    if prior is not None:
        if prior["request"] == event["request"]:
            stored = prior["response"]
            return 200, {k: stored[k] for k in _RESPONSE_KEYS}
        raise conflict("DUPLICATE_EVENT_ID", f"Event {event['event_id']} was already recorded with a different body.",
                       {"event_id": event["event_id"]})

    match_id, kind, payload = event["match_id"], event["type"], event["payload"]
    if kind == "match_init":
        sport = get_sport(conn, payload["sport_id"])
        if sport is None:
            raise not_found("SPORT_NOT_FOUND", f"Sport {payload['sport_id']} does not exist.",
                            {"sport_id": payload["sport_id"]})
        if _stream(conn, match_id) is not None:
            raise conflict("MATCH_EXISTS", f"Match {match_id} already exists.", {"match_id": match_id})
        stream = {"match_id": match_id, "sport_id": payload["sport_id"], "home_team_id": payload["home_team_id"],
                  "away_team_id": payload["away_team_id"], "league_id": payload.get("league_id")}
        conn.execute("INSERT INTO scoring_matches (match_id, sport_id, home_team_id, away_team_id, league_id) "
                     "VALUES (%(match_id)s, %(sport_id)s, %(home_team_id)s, %(away_team_id)s, %(league_id)s)", stream)
        rows = []
    else:
        stream = _stream(conn, match_id)
        if stream is None:
            raise not_found("MATCH_NOT_FOUND", f"Match {match_id} does not exist.", {"match_id": match_id})
        sport = get_sport(conn, stream["sport_id"])
        rows = _rows(conn, match_id)
        if kind == "compensation":
            _check_compensation(conn, rows, event)
        else:
            logic.check_append(event, stream, sport, logic.fold(stream, sport, rows))

    sequence = len(rows) + 1
    signature = sign(event["event_id"], payload)
    response = {"event_id": event["event_id"], "match_id": match_id, "type": kind, "sequence": sequence,
                "match_version": sequence, "signature": signature}
    compensates = payload["compensates"] if kind == "compensation" else None
    conn.execute(
        "INSERT INTO scoring_events (event_id, match_id, sequence, type, payload, request, signature, compensates, "
        "response, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (event["event_id"], match_id, sequence, kind, logic.dump(payload), event["request"], signature, compensates,
         Jsonb(response), utc_now()))
    rows.append({"sequence": sequence, "event_id": event["event_id"], "type": kind, "payload": payload,
                 "signature": signature, "compensates": compensates})
    _announce(stream, sport, rows, response, payload)
    return 201, response


def match_version(conn, match_id: str) -> int | None:
    """Number of events in the match log; None if the stream does not exist."""
    if _stream(conn, match_id) is None:
        return None
    return conn.execute("SELECT count(*) AS n FROM scoring_events WHERE match_id = %s", (match_id,)).fetchone()["n"]


def project(conn, match_id: str) -> dict | None:
    """Exactly the GET /audit/v1/score/{match_id} body, or None if the stream does not exist."""
    stream = _stream(conn, match_id)
    if stream is None:
        return None
    return logic.projection(stream, get_sport(conn, stream["sport_id"]), _rows(conn, match_id))


# ---------------------------------------------------------------------------------------------
# POST /audit/v1/leagues
# ---------------------------------------------------------------------------------------------

def register_league(conn, body) -> dict:
    """/audit/v1/leagues semantics: returns the 201 body or raises ApiError (400 -> 404 -> 409)."""
    league = logic.validate_league(body)
    league_id, sport_id = league["league_id"], league["sport_id"]
    if get_sport(conn, sport_id) is None:
        raise not_found("SPORT_NOT_FOUND", f"Sport {sport_id} does not exist.", {"sport_id": sport_id})
    if conn.execute("SELECT 1 FROM scoring_leagues WHERE league_id = %s", (league_id,)).fetchone() is not None:
        raise conflict("LEAGUE_EXISTS", f"League {league_id} already exists.", {"league_id": league_id})
    match_ids = [f["match_id"] for f in league["fixtures"]]
    seen: set[str] = set()
    for match_id in match_ids:
        if match_id in seen:
            raise conflict("MATCH_EXISTS", f"Match {match_id} appears twice in the fixture list.", {"match_id": match_id})
        seen.add(match_id)
    if match_ids:
        used = conn.execute("SELECT match_id FROM scoring_matches WHERE match_id = ANY(%s)", (match_ids,)).fetchall()
        if used:
            taken = {r["match_id"] for r in used}
            first = next(m for m in match_ids if m in taken)
            raise conflict("MATCH_EXISTS", f"Match {first} already exists.", {"match_id": first})

    if league["lots_seed"] is None:
        league["lots_seed"] = get_last_seed(conn)
    config = {k: league[k] for k in ("teams", "points", "tie_break_order", "qualification", "lots_seed")}
    conn.execute("INSERT INTO scoring_leagues (league_id, sport_id, config) VALUES (%s, %s, %s)",
                 (league_id, sport_id, Jsonb(config)))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO scoring_matches (match_id, sport_id, home_team_id, away_team_id, league_id, fixture_of, "
            "fixture_index) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(f["match_id"], sport_id, f["home"], f["away"], league_id, league_id, i)
             for i, f in enumerate(league["fixtures"])])
    queue.emit("league_registered", {"league_id": league_id, "sport_id": sport_id,
                                     "team_count": len(league["teams"]), "fixture_count": len(league["fixtures"])})
    return {"league_id": league_id, "status": "created", "team_count": len(league["teams"]),
            "fixture_count": len(league["fixtures"])}


def _fixture_streams(conn, league_id: str) -> list[dict]:
    return conn.execute(f"SELECT {_STREAM_COLS} FROM scoring_matches WHERE fixture_of = %s ORDER BY fixture_index",
                        (league_id,)).fetchall()


def get_league(conn, league_id: str) -> dict | None:
    """Registered league config; tie_break_order None means "use the sport's order"."""
    row = conn.execute("SELECT league_id, sport_id, config FROM scoring_leagues WHERE league_id = %s",
                       (league_id,)).fetchone()
    if row is None:
        return None
    config = row["config"]
    return {
        "league_id": row["league_id"],
        "sport_id": row["sport_id"],
        "teams": config["teams"],
        "fixtures": [{"match_id": s["match_id"], "home": s["home_team_id"], "away": s["away_team_id"]}
                     for s in _fixture_streams(conn, league_id)],
        "points": config["points"],
        "tie_break_order": config["tie_break_order"],
        "qualification": config["qualification"],
        "lots_seed": config["lots_seed"],
    }


def league_projections(conn, league_id: str) -> list[dict]:
    """project() of every fixture of the league, in fixture order (empty list for an unknown league)."""
    streams = _fixture_streams(conn, league_id)
    if not streams:
        return []
    events = _decode(conn.execute(
        "SELECT e.match_id, e.sequence, e.event_id, e.type, e.payload, e.signature, e.compensates "
        "FROM scoring_events e JOIN scoring_matches m ON m.match_id = e.match_id "
        "WHERE m.fixture_of = %s ORDER BY e.match_id, e.sequence", (league_id,)).fetchall())
    by_match: dict[str, list[dict]] = {}
    for r in events:
        by_match.setdefault(r.pop("match_id"), []).append(r)
    sport = get_sport(conn, streams[0]["sport_id"])
    return [logic.projection(s, sport, by_match.get(s["match_id"], [])) for s in streams]
