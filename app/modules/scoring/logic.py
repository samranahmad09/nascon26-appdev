"""Pure scoring rules: request validation and the fold that rebuilds a match from its event log.

Nothing here knows a sport or an event name. Behaviour comes only from the sport config:
scoring_model, periods, scoring_events (points / runs + legal_ball + wicket / rounds) and draw_allowed.
"""
import json

from ...core.errors import ApiError, conflict, validation_error
from ...core.signing import canonical
from ...core.validate import Validator, is_id, is_int
from ..sports.logic import TIE_BREAK_TOKENS

TEAM_TYPES = ("score", "discipline", "tiebreak_winner")
COMPENSABLE = TEAM_TYPES
# Day 2 status transitions (FR-1.3 state machine): event type -> projected status. They are allowed from
# upcoming or in_progress only; a postponed match can be started again, cancelled / abandoned are final.
STATUS_EVENTS = {"match_postpone": "postponed", "match_cancel": "cancelled", "match_abandon": "abandoned"}
# Tolerated spellings. The log keeps the type exactly as sent; the fold and the rules use the canonical one.
TYPE_ALIASES = {
    "postpone": "match_postpone", "postponed": "match_postpone", "match_postponed": "match_postpone",
    "cancel": "match_cancel", "cancelled": "match_cancel", "match_cancelled": "match_cancel",
    "canceled": "match_cancel", "match_canceled": "match_cancel",
    "abandon": "match_abandon", "abandoned": "match_abandon", "match_abandoned": "match_abandon",
}
TYPES = ("match_init", "match_start", "score", "discipline", "tiebreak_winner", "match_end", "compensation",
         *STATUS_EVENTS, *TYPE_ALIASES)
EMPTY_PAYLOAD_TYPES = ("match_start", "match_end", *STATUS_EVENTS, *TYPE_ALIASES)
RESTARTABLE = ("upcoming", "postponed")
DEFAULT_POINTS = {"win": 3, "draw": 1, "loss": 0}


def canonical_type(kind: str) -> str:
    return TYPE_ALIASES.get(kind, kind)


def dump(obj) -> str:
    """Lossless ASCII JSON (sorted keys) used to store payloads and compare replayed requests."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------------------------
# /events request validation (step 1 of the 400 -> 404 -> 409 order)
# ---------------------------------------------------------------------------------------------

def _payload_errors(kind: str, p: dict) -> list[str]:
    bad: list[str] = []

    def need_id(key: str, required: bool = True) -> None:
        if p.get(key) is None and not required:
            return
        if not is_id(p.get(key)):
            bad.append("payload." + key)

    if kind == "match_init":
        if not is_id(p.get("sport_id")):
            bad.append("payload.sport_id")
        need_id("home_team_id")
        need_id("away_team_id")
        need_id("league_id", required=False)
        if is_id(p.get("home_team_id")) and p.get("home_team_id") == p.get("away_team_id"):
            bad.append("payload.away_team_id")
    elif kind in TEAM_TYPES:
        need_id("team_id")
        if kind == "score" and not isinstance(p.get("event_type"), str):
            bad.append("payload.event_type")
        if kind == "discipline" and not is_int(p.get("points")):
            bad.append("payload.points")
    elif kind == "compensation":
        need_id("compensates")
        if not isinstance(p.get("reason"), str):
            bad.append("payload.reason")
    return bad


def validate_event(body) -> dict:
    """Schema check of an /events body. Returns the normalised event or raises 400 VALIDATION_ERROR."""
    if not isinstance(body, dict):
        raise validation_error(["body"], "Request body must be a JSON object.")
    bad: list[str] = []
    event_id, match_id, kind = body.get("event_id"), body.get("match_id"), body.get("type")
    if not is_id(event_id):
        bad.append("event_id")
    if not is_id(match_id):
        bad.append("match_id")
    if not isinstance(kind, str) or kind not in TYPES:
        bad.append("type")
        kind = None
    payload = body.get("payload")
    if payload is None and kind in EMPTY_PAYLOAD_TYPES:
        payload = {}
    if not isinstance(payload, dict):
        bad.append("payload")
    elif kind is not None:
        bad.extend(_payload_errors(kind, payload))
    if bad:
        raise validation_error(bad)
    try:
        canonical(payload).encode("utf-8")
    except UnicodeEncodeError:
        raise validation_error(["payload"], "Payload is not encodable as UTF-8.")
    return {
        "event_id": event_id,
        "match_id": match_id,
        "type": kind,
        "payload": payload,
        "request": dump({"match_id": match_id, "type": kind, "payload": payload}),
    }


# ---------------------------------------------------------------------------------------------
# Fold: the only way match state is ever computed (score is never stored as a counter)
# ---------------------------------------------------------------------------------------------

def _blank(team_id: str) -> dict:
    return {"team_id": team_id, "score": 0, "wickets": 0, "balls": 0, "fair_play": 0}


def fold(stream: dict, sport: dict, rows: list[dict]) -> dict:
    """Replays the event rows (ordered by sequence) of one match and returns its derived state."""
    model = sport["scoring_model"]
    table = sport["scoring_events"]
    home_id, away_id = stream["home_team_id"], stream["away_team_id"]
    sides = {home_id: _blank(home_id), away_id: _blank(away_id)}
    compensated_by = {r["compensates"]: r["event_id"] for r in rows if r["type"] == "compensation"}
    status, tiebreak = "upcoming", None
    for r in rows:
        kind = canonical_type(r["type"])
        if kind == "match_start":
            status = "in_progress"
        elif kind == "match_end":
            status = "completed"
        elif kind in STATUS_EVENTS:
            status = STATUS_EVENTS[kind]
        elif kind in COMPENSABLE and r["event_id"] not in compensated_by:
            p = r["payload"]
            side = sides.get(p.get("team_id"))
            if side is None:
                continue
            if kind == "score":
                value = table.get(p.get("event_type")) or {}
                if model == "innings":
                    side["score"] += value.get("runs", 0)
                    side["balls"] += 1 if value.get("legal_ball") else 0
                    side["wickets"] += 1 if value.get("wicket") else 0
                elif model == "round":
                    side["score"] += value.get("rounds", 0)
                else:
                    side["score"] += value.get("points", 0)
            elif kind == "discipline":
                side["fair_play"] += p.get("points", 0)
            else:
                tiebreak = p.get("team_id")

    home, away = sides[home_id], sides[away_id]
    if home["score"] != away["score"]:
        winner = home_id if home["score"] > away["score"] else away_id
    else:
        winner = tiebreak
    if status != "completed":
        outcome = "pending"  # also for postponed / cancelled / abandoned: only a completed match has a result
    elif winner == home_id:
        outcome = "home_win"
    elif winner == away_id:
        outcome = "away_win"
    else:
        outcome = "draw" if sport["draw_allowed"] else "undecided"
    return {"status": status, "outcome": outcome, "winner": winner, "tiebreak": tiebreak,
            "home": home, "away": away, "sides": sides, "compensated_by": compensated_by}


def projection(stream: dict, sport: dict, rows: list[dict]) -> dict:
    """GET /audit/v1/score/{match_id} body, rebuilt from the log."""
    state = fold(stream, sport, rows)
    innings = sport["scoring_model"] == "innings"
    per_over = sport["periods"].get("balls_per_over") if innings else None

    def block(side: dict) -> dict:
        return {
            "team_id": side["team_id"],
            "score": side["score"],
            "wickets": side["wickets"] if innings else None,
            "balls": side["balls"] if innings else None,
            "overs": f"{side['balls'] // per_over}.{side['balls'] % per_over}" if innings else None,
            "fair_play": side["fair_play"],
        }

    compensated_by = state["compensated_by"]
    return {
        "match_id": stream["match_id"],
        "sport_id": stream["sport_id"],
        "league_id": stream.get("league_id"),
        "status": state["status"],
        "outcome": state["outcome"],
        "winner_team_id": state["winner"],
        "tiebreak_winner_team_id": state["tiebreak"],
        "home": block(state["home"]),
        "away": block(state["away"]),
        "version": len(rows),
        "events": [
            {
                "sequence": r["sequence"],
                "event_id": r["event_id"],
                "type": r["type"],
                "payload": r["payload"],
                "signature": r["signature"],
                "compensated": r["event_id"] in compensated_by,
                "compensated_by": compensated_by.get(r["event_id"]),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------------------------
# Rules for appending to an existing stream (everything except match_init and compensation)
# ---------------------------------------------------------------------------------------------

def _limit_reached(sport: dict, state: dict, team_id: str) -> ApiError | None:
    model, periods = sport["scoring_model"], sport["periods"]
    if model == "innings":
        side = state["sides"][team_id]
        per_team = periods["innings_per_team"]
        max_balls = per_team * periods["overs_per_innings"] * periods["balls_per_over"]
        max_out = per_team * periods["wickets_per_innings"]
        if side["balls"] >= max_balls or side["wickets"] >= max_out:
            return conflict("INNINGS_COMPLETE", f"The innings of team {team_id} is complete.",
                            {"team_id": team_id, "balls": side["balls"], "wickets": side["wickets"]})
    elif model == "round":
        needed = (periods["best_of"] + 1) // 2
        if any(s["score"] >= needed for s in state["sides"].values()):
            return conflict("SERIES_ALREADY_DECIDED", "The series is already decided.",
                            {"best_of": periods["best_of"], "rounds_needed": needed})
    return None


def check_append(event: dict, stream: dict, sport: dict, state: dict) -> None:
    """400 (team / event type) then 409 (state machine, config limits) for an existing stream."""
    kind, p, match_id = canonical_type(event["type"]), event["payload"], event["match_id"]
    if kind in TEAM_TYPES:
        if p["team_id"] not in (stream["home_team_id"], stream["away_team_id"]):
            raise validation_error(["payload.team_id"], "team_id must be the home or away team of the match.")
        if kind == "score" and p["event_type"] not in sport["scoring_events"]:
            raise ApiError(400, "UNKNOWN_EVENT_TYPE",
                           f"Event type {p['event_type']} is not defined for sport {sport['sport_id']}.",
                           {"event_type": p["event_type"], "sport_id": sport["sport_id"]})
    status = state["status"]
    if kind == "match_start":
        if status not in RESTARTABLE:
            raise conflict("ILLEGAL_TRANSITION", f"Cannot start a match that is {status}.",
                           {"match_id": match_id, "status": status})
    elif kind in STATUS_EVENTS:
        if status not in ("upcoming", "in_progress"):
            raise conflict("ILLEGAL_TRANSITION", f"Cannot move a match that is {status} to {STATUS_EVENTS[kind]}.",
                           {"match_id": match_id, "status": status})
    elif kind == "match_end":
        if status != "in_progress":
            raise conflict("ILLEGAL_TRANSITION", f"Cannot end a match that is {status}.",
                           {"match_id": match_id, "status": status})
        if state["home"]["score"] == state["away"]["score"] and not sport["draw_allowed"] and state["tiebreak"] is None:
            raise conflict("TIE_NOT_ALLOWED", "Scores are level, draws are not allowed and no tiebreak winner is set.",
                           {"match_id": match_id})
    elif kind in TEAM_TYPES:
        if status != "in_progress":
            raise conflict("MATCH_NOT_IN_PROGRESS", f"Match {match_id} is {status}.",
                           {"match_id": match_id, "status": status})
        if kind == "score":
            err = _limit_reached(sport, state, p["team_id"])
            if err is not None:
                raise err


# ---------------------------------------------------------------------------------------------
# /leagues request validation
# ---------------------------------------------------------------------------------------------

def validate_league(body) -> dict:
    v = Validator(body)
    league_id = v.ident("league_id")
    sport_id = v.ident("sport_id")
    teams = v.array("teams", min_len=2, max_len=40)
    fixtures = v.array("fixtures")
    points = v.obj("points", required=False)
    tie_break = v.array("tie_break_order", required=False)
    qualification = v.obj("qualification")
    lots_seed = v.integer("lots_seed", required=False)

    team_set = None
    if teams is not None:
        if all(is_id(t) for t in teams) and len(set(teams)) == len(teams):
            team_set = set(teams)
        else:
            v.bad("teams")

    norm_fixtures = []
    for f in fixtures or []:
        ok = isinstance(f, dict) and is_id(f.get("match_id")) and is_id(f.get("home")) and is_id(f.get("away")) \
            and f["home"] != f["away"] and (team_set is None or (f["home"] in team_set and f["away"] in team_set))
        if not ok:
            v.bad("fixtures")
            break
        norm_fixtures.append({"match_id": f["match_id"], "home": f["home"], "away": f["away"]})

    norm_points = dict(DEFAULT_POINTS)
    for key in DEFAULT_POINTS:
        value = (points or {}).get(key)
        if value is None:
            continue
        if is_int(value):
            norm_points[key] = value
        else:
            v.bad("points." + key)

    norm_tie_break = None
    if tie_break is not None:
        if all(isinstance(t, str) and t in TIE_BREAK_TOKENS for t in tie_break) and len(set(tie_break)) == len(tie_break):
            norm_tie_break = list(tie_break)
        else:
            v.bad("tie_break_order")

    spots = None
    if qualification is not None:
        spots = qualification.get("spots")
        if not is_int(spots) or spots < 1 or (team_set is not None and spots > len(team_set)):
            v.bad("qualification.spots")

    v.check()
    return {
        "league_id": league_id,
        "sport_id": sport_id,
        "teams": list(teams),
        "fixtures": norm_fixtures,
        "points": norm_points,
        "tie_break_order": norm_tie_break,
        "qualification": {"spots": spots},
        "lots_seed": lots_seed,
    }
