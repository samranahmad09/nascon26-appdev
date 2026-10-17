"""Roster scenarios, transfers, loans, releases and the immutable audit trail (Section 5.11, FR-2.2).

Time model. A player's registration is the result of every accepted change in this league, oldest first.
Rule checks are judged on the request's effective_date: a loan whose loan_end_date is before that date has
ended and the player is back with the lender. The return is written once to the audit trail as a loan_return
entry (who "system", effective the day after loan_end_date) by the first history read whose as_of, or the
first accepted change of that player whose effective_date, is past the loan end.

Stored entries and responses are JSON (not JSONB) so a replay is byte-identical.
Audit ids are numbered per league ("au-0001" is the first entry after a setup); re-sending a setup replaces
the league's whole roster state, including its audit trail and stored requests. Idempotency is keyed by
(league_id, request_id): any reuse of an accepted request_id returns the stored response with HTTP 200.
"""
import datetime as dt
import logging

from psycopg.types.json import Json

from ...core import queue, realtime
from ...core.db import Jsonb
from ...core.errors import conflict, not_found
from ...core.registry import register
from ...core.validate import Validator, is_id, is_int, parse_date, utc_now
from ..sports import get_sport

log = logging.getLogger("roster")

ROLES = ("captain", "vice_captain", "starter", "substitute")
TYPES = ("transfer", "loan", "release")
MOVES = ("transfer", "loan")
ACTIVE = "active"
DEFERRED = "deferred_until_match_end"
NEW_ROLE = "substitute"
SYSTEM = "system"
RETURN_REASON = "Loan period ended"
NO_LIMIT = 1_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS ro_leagues (
    league_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, sport_id TEXT NOT NULL,
    window_open DATE NOT NULL, window_close DATE NOT NULL, max_squad_size INT NOT NULL,
    required_positions JSONB NOT NULL, audit_count INT NOT NULL DEFAULT 0, setup_seq BIGSERIAL);
CREATE TABLE IF NOT EXISTS ro_squads (
    league_id TEXT NOT NULL, squad_id TEXT NOT NULL, club_id TEXT, tenant_id TEXT NOT NULL, name TEXT,
    ord INT NOT NULL, PRIMARY KEY (league_id, squad_id));
CREATE TABLE IF NOT EXISTS ro_players (
    league_id TEXT NOT NULL, player_id TEXT NOT NULL, name TEXT, position TEXT, squad_id TEXT, role TEXT,
    ord INT NOT NULL, PRIMARY KEY (league_id, player_id));
CREATE TABLE IF NOT EXISTS ro_matches (
    league_id TEXT NOT NULL, match_id TEXT NOT NULL, home_squad_id TEXT, away_squad_id TEXT, ord INT NOT NULL,
    PRIMARY KEY (league_id, match_id));
CREATE TABLE IF NOT EXISTS ro_audit (
    seq BIGSERIAL PRIMARY KEY, league_id TEXT NOT NULL, player_id TEXT NOT NULL, action TEXT NOT NULL,
    from_squad_id TEXT, to_squad_id TEXT, effective_date DATE NOT NULL, loan_end_date DATE, entry JSON NOT NULL);
CREATE INDEX IF NOT EXISTS ro_audit_player ON ro_audit (league_id, player_id, seq);
CREATE TABLE IF NOT EXISTS ro_requests (
    league_id TEXT NOT NULL, request_id TEXT NOT NULL, body JSONB NOT NULL, response JSON NOT NULL,
    PRIMARY KEY (league_id, request_id));
"""

TABLES = ["ro_leagues", "ro_squads", "ro_players", "ro_matches", "ro_audit", "ro_requests"]


# ---------------------------------------------------------------- setup

def _opt(value, check) -> bool:
    return value is None or check(value)


def _is_text(value) -> bool:
    return isinstance(value, str)


def _parse_league(v: Validator, league) -> dict:
    lv = Validator(league)
    cfg = {
        "league_id": lv.ident("league_id"),
        "tenant_id": lv.ident("tenant_id"),
        "sport_id": lv.ident("sport_id"),
        "window": None,
        "max_squad_size": lv.integer("max_squad_size", required=False, minimum=0),
        "required_positions": lv.obj("required_positions", required=False),
    }
    window = lv.obj("transfer_window")
    if window is not None:
        wv = Validator(window)
        opens, closes = wv.date("open"), wv.date("close")
        if wv.errors or opens > closes:
            lv.bad("transfer_window")
        else:
            cfg["window"] = (opens, closes)
    positions = cfg["required_positions"]
    if positions is not None and not all(isinstance(k, str) and k and is_int(n) and n >= 0
                                         for k, n in positions.items()):
        lv.bad("required_positions")
    for field in lv.errors:
        v.bad("league." + field)
    return cfg


def _parse_squads(v: Validator, squads) -> list[dict]:
    out, seen = [], set()
    for s in squads or []:
        if not isinstance(s, dict) or not is_id(s.get("squad_id")) or s["squad_id"] in seen \
                or not _opt(s.get("tenant_id"), is_id) or not _opt(s.get("club_id"), is_id) \
                or not _opt(s.get("name"), _is_text):
            v.bad("squads")
            return []
        seen.add(s["squad_id"])
        out.append({"squad_id": s["squad_id"], "club_id": s.get("club_id"), "tenant_id": s.get("tenant_id"),
                    "name": s.get("name")})
    return out


def _parse_players(v: Validator, players) -> list[dict]:
    out, seen = [], set()
    for p in players or []:
        if not isinstance(p, dict) or not is_id(p.get("player_id")) or p["player_id"] in seen \
                or not _opt(p.get("squad_id"), is_id) or not _opt(p.get("name"), _is_text) \
                or not _opt(p.get("position"), _is_text) or not _opt(p.get("role"), lambda r: r in ROLES):
            v.bad("players")
            return []
        seen.add(p["player_id"])
        out.append({"player_id": p["player_id"], "name": p.get("name"), "position": p.get("position"),
                    "squad_id": p.get("squad_id"), "role": p.get("role") or NEW_ROLE})
    return out


def _parse_matches(v: Validator, matches) -> list[dict]:
    out, seen = [], set()
    for m in matches or []:
        if not isinstance(m, dict) or not is_id(m.get("match_id")) or m["match_id"] in seen \
                or not is_id(m.get("home_squad_id")) or not is_id(m.get("away_squad_id")):
            v.bad("matches_in_progress")
            return []
        seen.add(m["match_id"])
        out.append({"match_id": m["match_id"], "home_squad_id": m["home_squad_id"],
                    "away_squad_id": m["away_squad_id"]})
    return out


def _store_league(conn, cfg: dict, squads: list[dict], players: list[dict], matches: list[dict]) -> dict:
    lid = cfg["league_id"]
    for table in TABLES:
        conn.execute(f"DELETE FROM {table} WHERE league_id = %s", (lid,))
    opens, closes = cfg["window"]
    conn.execute("INSERT INTO ro_leagues (league_id, tenant_id, sport_id, window_open, window_close, max_squad_size, "
                 "required_positions) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (lid, cfg["tenant_id"], cfg["sport_id"], opens, closes, cfg["max_squad_size"],
                  Jsonb(cfg["required_positions"])))
    for i, s in enumerate(squads):
        conn.execute("INSERT INTO ro_squads (league_id, squad_id, club_id, tenant_id, name, ord) "
                     "VALUES (%s, %s, %s, %s, %s, %s)",
                     (lid, s["squad_id"], s["club_id"], s["tenant_id"] or cfg["tenant_id"], s["name"], i))
    for i, p in enumerate(players):
        conn.execute("INSERT INTO ro_players (league_id, player_id, name, position, squad_id, role, ord) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (lid, p["player_id"], p["name"], p["position"], p["squad_id"], p["role"], i))
    for i, m in enumerate(matches):
        conn.execute("INSERT INTO ro_matches (league_id, match_id, home_squad_id, away_squad_id, ord) "
                     "VALUES (%s, %s, %s, %s, %s)", (lid, m["match_id"], m["home_squad_id"], m["away_squad_id"], i))
    counts = {s["squad_id"]: 0 for s in squads}
    for p in players:
        if p["squad_id"] in counts:
            counts[p["squad_id"]] += 1
    return {"league_id": lid, "status": "configured", "squad_counts": counts}


def setup(conn, body) -> dict:
    v = Validator(body)
    league = v.obj("league")
    squads = v.array("squads")
    players = v.array("players", required=False)
    matches = v.array("matches_in_progress", required=False)
    cfg = _parse_league(v, league) if league is not None else None
    squad_list = _parse_squads(v, squads)
    player_list = _parse_players(v, players)
    match_list = _parse_matches(v, matches)
    v.check()

    sport = get_sport(conn, cfg["sport_id"])
    if sport is None:
        raise not_found("SPORT_NOT_FOUND", f"Sport {cfg['sport_id']} does not exist.", {"sport_id": cfg["sport_id"]})
    known = {s["squad_id"] for s in squad_list}
    for p in player_list:
        if p["squad_id"] is not None and p["squad_id"] not in known:
            raise not_found("SQUAD_NOT_FOUND", f"Squad {p['squad_id']} is not part of this setup.",
                            {"squad_id": p["squad_id"]})
    roster = sport.get("roster") or {}
    if cfg["max_squad_size"] is None:
        cfg["max_squad_size"] = roster.get("max_squad_size") or NO_LIMIT
    if cfg["required_positions"] is None:
        cfg["required_positions"] = dict(roster.get("required_positions") or {})
    return _store_league(conn, cfg, squad_list, player_list, match_list)


def seed(conn, opts: dict) -> None:
    """Mirror the baseline tenant leagues, clubs and players as roster scenarios (squad_id = club_id)."""
    try:
        with conn.transaction():
            year = dt.datetime.now(dt.timezone.utc).year
            for lg in conn.execute("SELECT league_id, tenant_id, sport_id FROM tenant_leagues ORDER BY league_id").fetchall():
                sport = get_sport(conn, lg["sport_id"]) or {}
                roster = sport.get("roster") or {}
                clubs = conn.execute("SELECT club_id, tenant_id, name FROM clubs WHERE league_id = %s ORDER BY club_id",
                                     (lg["league_id"],)).fetchall()
                club_ids = [c["club_id"] for c in clubs]
                players = conn.execute("SELECT player_id, name, position, club_id, role FROM players "
                                       "WHERE club_id = ANY(%s) ORDER BY player_id", (club_ids,)).fetchall()
                cfg = {"league_id": lg["league_id"], "tenant_id": lg["tenant_id"], "sport_id": lg["sport_id"],
                       "window": (dt.date(year, 1, 1), dt.date(year, 12, 31)),
                       "max_squad_size": roster.get("max_squad_size") or NO_LIMIT,
                       "required_positions": dict(roster.get("required_positions") or {})}
                squads = [{"squad_id": c["club_id"], "club_id": c["club_id"], "tenant_id": c["tenant_id"],
                           "name": c["name"]} for c in clubs]
                roster_players = [{"player_id": p["player_id"], "name": p["name"], "position": p["position"],
                                   "squad_id": p["club_id"], "role": p["role"] if p["role"] in ROLES else NEW_ROLE}
                                  for p in players]
                _store_league(conn, cfg, squads, roster_players, [])
    except Exception:
        log.exception("roster baseline mirror failed; continuing without it")


register(SCHEMA, TABLES, seed, order=60)


# ---------------------------------------------------------------- registration state

def _league(conn, league_id: str):
    return conn.execute("SELECT * FROM ro_leagues WHERE league_id = %s", (league_id,)).fetchone()


def _player_league(conn, player_id: str):
    """The most recently configured league that lists this player."""
    row = conn.execute("SELECT p.league_id FROM ro_players p JOIN ro_leagues l ON l.league_id = p.league_id "
                       "WHERE p.player_id = %s ORDER BY l.setup_seq DESC LIMIT 1", (player_id,)).fetchone()
    return row["league_id"] if row else None


def _entries(conn, league_id: str, player_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM ro_audit WHERE league_id = %s"
    params = [league_id]
    if player_id is not None:
        sql += " AND player_id = %s"
        params.append(player_id)
    return conn.execute(sql + " ORDER BY seq", params).fetchall()


def _fold(initial_squad, entries: list[dict], as_of: dt.date | None = None, initial_role=None) -> dict:
    """Registration after the given entries (oldest first); with as_of, only entries effective by then."""
    reg = {"squad_id": initial_squad, "parent_squad_id": None, "type": "initial", "loan_end": None,
           "role": initial_role}
    for e in entries:
        if as_of is not None and e["effective_date"] > as_of:
            continue
        if e["action"] == "release":
            reg = {"squad_id": None, "parent_squad_id": None, "type": "release", "loan_end": None, "role": None}
        elif e["action"] == "loan":
            reg = {"squad_id": e["to_squad_id"], "parent_squad_id": e["from_squad_id"], "type": "loan",
                   "loan_end": e["loan_end_date"], "role": NEW_ROLE}
        else:
            reg = {"squad_id": e["to_squad_id"], "parent_squad_id": None, "type": e["action"], "loan_end": None,
                   "role": NEW_ROLE}
    return reg


def _on(reg: dict, day: dt.date) -> tuple[dict, bool]:
    """The registration on `day`, and whether a loan has ended by then (a loan_return is due)."""
    if reg["type"] == "loan" and day > reg["loan_end"]:
        return {"squad_id": reg["parent_squad_id"], "parent_squad_id": None, "type": "loan_return",
                "loan_end": None, "role": NEW_ROLE}, True
    return reg, False


def _league_state(conn, league_id: str, day: dt.date) -> dict[str, dict]:
    """player_id -> {squad_id, position} for every player of the league on `day`."""
    by_player: dict[str, list] = {}
    for e in _entries(conn, league_id):
        by_player.setdefault(e["player_id"], []).append(e)
    state = {}
    for p in conn.execute("SELECT player_id, position, squad_id FROM ro_players WHERE league_id = %s",
                          (league_id,)).fetchall():
        reg, _ = _on(_fold(p["squad_id"], by_player.get(p["player_id"], [])), day)
        state[p["player_id"]] = {"squad_id": reg["squad_id"], "position": p["position"]}
    return state


def squad_members(conn, league_id: str, squad_id: str, day: dt.date | None = None) -> list[dict] | None:
    """Players registered to the squad on `day` (default today) for the club profile; None if no roster league."""
    if _league(conn, league_id) is None:
        return None
    day = day or today()
    by_player: dict[str, list] = {}
    for e in _entries(conn, league_id):
        by_player.setdefault(e["player_id"], []).append(e)
    shirts = {r["player_id"]: r["shirt_number"] for r in conn.execute("SELECT player_id, shirt_number FROM players")}
    members = []
    for p in conn.execute("SELECT * FROM ro_players WHERE league_id = %s ORDER BY ord", (league_id,)).fetchall():
        reg, _ = _on(_fold(p["squad_id"], by_player.get(p["player_id"], []), day, p["role"]), day)
        if reg["squad_id"] == squad_id:
            members.append({"player_id": p["player_id"], "name": p["name"], "position": p["position"],
                            "role": reg["role"], "shirt_number": shirts.get(p["player_id"]), "squad_id": squad_id,
                            "parent_squad_id": reg["parent_squad_id"]})
    members.sort(key=lambda m: (m["shirt_number"] is None, m["shirt_number"] or 0, m["player_id"]))
    return members


def _append(conn, league_id: str, player_id: str, action: str, from_squad, to_squad, effective: dt.date,
            loan_end: dt.date | None, state: str, who: str, why) -> dict:
    count = conn.execute("UPDATE ro_leagues SET audit_count = audit_count + 1 WHERE league_id = %s "
                         "RETURNING audit_count", (league_id,)).fetchone()["audit_count"]
    entry = {"audit_id": f"au-{count:04d}", "action": action, "player_id": player_id, "league_id": league_id,
             "from_squad_id": from_squad, "to_squad_id": to_squad, "effective_date": effective.isoformat(),
             "state": state, "who": who, "when": utc_now(), "why": why}
    conn.execute("INSERT INTO ro_audit (league_id, player_id, action, from_squad_id, to_squad_id, effective_date, "
                 "loan_end_date, entry) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (league_id, player_id, action, from_squad, to_squad, effective, loan_end, Json(entry)))
    queue.emit("roster." + action, entry)
    realtime.publish("roster:" + league_id, {"type": "roster_change", "data": entry})
    return entry


def _return_loan(conn, league_id: str, player_id: str, reg: dict) -> dict:
    return _append(conn, league_id, player_id, "loan_return", reg["squad_id"], reg["parent_squad_id"],
                   reg["loan_end"] + dt.timedelta(days=1), None, ACTIVE, SYSTEM, RETURN_REASON)


def player_version(conn, player_id: str, league_id: str | None = None) -> int | None:
    """Number of audit entries of the player (offline-sync base_version); None if the player is unknown."""
    if league_id is None or _league(conn, league_id) is None:
        league_id = _player_league(conn, player_id)
        if league_id is None:
            return None
    row = conn.execute("SELECT count(*) AS n FROM ro_audit WHERE league_id = %s AND player_id = %s",
                       (league_id, player_id)).fetchone()
    return row["n"]


# ---------------------------------------------------------------- transfer / loan / release

def validate_transfer(body) -> dict:
    """Rule 1: schema, dates, loan_end_date > effective_date, to_squad_id present unless release."""
    v = Validator(body)
    req = {
        "request_id": v.ident("request_id"),
        "league_id": v.ident("league_id"),
        "player_id": v.ident("player_id"),
        "type": v.enum("type", TYPES),
        "from_squad_id": v.ident("from_squad_id"),
        "to_squad_id": None,
    }
    if req["type"] == "release":
        if not _opt(v.body.get("to_squad_id"), is_id):
            v.bad("to_squad_id")
    else:
        req["to_squad_id"] = v.ident("to_squad_id")
        if req["to_squad_id"] is not None and req["to_squad_id"] == req["from_squad_id"]:
            v.bad("to_squad_id")
    req["effective_date"] = v.date("effective_date")
    req["loan_end_date"] = v.date("loan_end_date", required=req["type"] == "loan")
    if req["type"] == "loan" and req["effective_date"] and req["loan_end_date"] \
            and req["loan_end_date"] <= req["effective_date"]:
        v.bad("loan_end_date")
    if req["type"] != "loan":
        req["loan_end_date"] = None
    req["reason"] = v.string("reason", min_len=0)
    req["requested_by"] = v.string("requested_by")
    v.check()
    return req


def stored_response(conn, league_id: str, request_id: str):
    row = conn.execute("SELECT response FROM ro_requests WHERE league_id = %s AND request_id = %s",
                       (league_id, request_id)).fetchone()
    return row["response"] if row else None


def _squad(conn, league, squad_id: str):
    row = conn.execute("SELECT * FROM ro_squads WHERE league_id = %s AND squad_id = %s",
                       (league["league_id"], squad_id)).fetchone()
    if row is not None:
        return row
    # A squad configured under another tenant still exists: rule 3 reports it as a cross-tenant move.
    row = conn.execute("SELECT s.* FROM ro_squads s JOIN ro_leagues l ON l.league_id = s.league_id "
                       "WHERE s.squad_id = %s AND s.tenant_id <> %s ORDER BY l.setup_seq DESC LIMIT 1",
                       (squad_id, league["tenant_id"])).fetchone()
    if row is not None:
        return row
    raise not_found("SQUAD_NOT_FOUND", f"Squad {squad_id} does not exist in league {league['league_id']}.",
                    {"squad_id": squad_id})


def transfer(conn, body, before_rules=None) -> tuple[int, dict]:
    """Apply one transfer/loan/release. (201, response) when accepted, (200, stored) on a replay.

    before_rules(league_id, player_id) runs after rules 1-2 (schema, existence) and before rules 3-8.
    """
    req = validate_transfer(body)
    lid, pid, kind = req["league_id"], req["player_id"], req["type"]
    stored = stored_response(conn, lid, req["request_id"])
    if stored is not None:
        return 200, stored

    # Rule 2: league, player, squads exist.
    league = _league(conn, lid)
    if league is None:
        raise not_found("LEAGUE_NOT_FOUND", f"League {lid} does not exist.", {"league_id": lid})
    player = conn.execute("SELECT * FROM ro_players WHERE league_id = %s AND player_id = %s", (lid, pid)).fetchone()
    if player is None and _player_league(conn, pid) is None:
        raise not_found("PLAYER_NOT_FOUND", f"Player {pid} does not exist.", {"player_id": pid})
    from_squad = _squad(conn, league, req["from_squad_id"])
    to_squad = _squad(conn, league, req["to_squad_id"]) if kind in MOVES else None
    if before_rules is not None:
        before_rules(lid, pid)

    # Rule 3: both squads belong to the league's tenant.
    for squad in (from_squad, to_squad):
        if squad is not None and squad["tenant_id"] != league["tenant_id"]:
            raise conflict("CROSS_TENANT_TRANSFER",
                           f"Squad {squad['squad_id']} does not belong to tenant {league['tenant_id']}.",
                           {"squad_id": squad["squad_id"], "tenant_id": league["tenant_id"]})

    day = req["effective_date"]
    entries = _entries(conn, lid, pid) if player is not None else []
    latest = _fold(player["squad_id"] if player is not None else None, entries)
    reg, return_due = _on(latest, day)

    # Rule 4: the player's squad in this league on effective_date is from_squad_id.
    if player is None or reg["squad_id"] != req["from_squad_id"]:
        raise conflict("NOT_IN_SQUAD", f"Player {pid} is not in squad {req['from_squad_id']} of league {lid}.",
                       {"player_id": pid, "squad_id": req["from_squad_id"], "current_squad_id": reg["squad_id"]})

    # Rule 5: transfers and loans need an open window (inclusive); release is allowed on any date.
    if kind in MOVES and not (league["window_open"] <= day <= league["window_close"]):
        raise conflict("TRANSFER_WINDOW_CLOSED", f"{day.isoformat()} is outside the transfer window.",
                       {"effective_date": day.isoformat(), "open": league["window_open"].isoformat(),
                        "close": league["window_close"].isoformat()})

    # Rule 6: a player currently on loan cannot be transferred, loaned or released.
    if reg["type"] == "loan":
        raise conflict("PLAYER_ON_LOAN", f"Player {pid} is on loan until {reg['loan_end'].isoformat()}.",
                       {"player_id": pid, "parent_squad_id": reg["parent_squad_id"],
                        "loan_end_date": reg["loan_end"].isoformat()})

    state = _league_state(conn, lid, day)

    # Rule 7: the destination squad has room.
    if kind in MOVES:
        size = sum(1 for p in state.values() if p["squad_id"] == req["to_squad_id"])
        if size + 1 > league["max_squad_size"]:
            raise conflict("SQUAD_FULL", f"Squad {req['to_squad_id']} is full.",
                           {"squad_id": req["to_squad_id"], "max_squad_size": league["max_squad_size"],
                            "current_size": size})

    # Rule 8: the from squad keeps the minimum for the player's position.
    position = player["position"]
    required = (league["required_positions"] or {}).get(position) if position is not None else None
    if required:
        left = sum(1 for other, p in state.items()
                   if other != pid and p["squad_id"] == req["from_squad_id"] and p["position"] == position)
        if left < required:
            raise conflict("MIN_POSITION_VIOLATION",
                           f"Squad {req['from_squad_id']} would have {left} {position}, minimum {required}.",
                           {"squad_id": req["from_squad_id"], "position": position, "required": required,
                            "remaining": left})

    match = conn.execute("SELECT match_id FROM ro_matches WHERE league_id = %s AND "
                         "(home_squad_id = %s OR away_squad_id = %s) ORDER BY ord LIMIT 1",
                         (lid, req["from_squad_id"], req["from_squad_id"])).fetchone()
    blocking = match["match_id"] if match else None
    outcome = DEFERRED if blocking else ACTIVE

    if return_due:
        _return_loan(conn, lid, pid, latest)
    entry = _append(conn, lid, pid, kind, req["from_squad_id"], req["to_squad_id"], day, req["loan_end_date"],
                    outcome, req["requested_by"], req["reason"])
    registration = None
    if kind in MOVES:
        registration = {
            "player_id": pid, "league_id": lid, "squad_id": req["to_squad_id"],
            "parent_squad_id": req["from_squad_id"] if kind == "loan" else None,
            "registration_type": kind, "start_date": day.isoformat(),
            "loan_end_date": req["loan_end_date"].isoformat() if req["loan_end_date"] else None,
            "role": NEW_ROLE,
        }
    response = {"request_id": req["request_id"], "status": "accepted", "state": outcome,
                "blocking_match_id": blocking, "registration": registration, "audit_entry": entry}
    conn.execute("INSERT INTO ro_requests (league_id, request_id, body, response) VALUES (%s, %s, %s, %s)",
                 (lid, req["request_id"], Jsonb(body), Json(response)))
    return 201, response


# ---------------------------------------------------------------- history

def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def history(conn, player_id: str, as_of: dt.date) -> dict:
    lid = _player_league(conn, player_id)
    if lid is None:
        raise not_found("PLAYER_NOT_FOUND", f"Player {player_id} does not exist.", {"player_id": player_id})
    player = conn.execute("SELECT squad_id FROM ro_players WHERE league_id = %s AND player_id = %s",
                          (lid, player_id)).fetchone()
    latest = _fold(player["squad_id"], _entries(conn, lid, player_id))
    if _on(latest, as_of)[1]:
        _return_loan(conn, lid, player_id, latest)
    entries = _entries(conn, lid, player_id)
    reg = _fold(player["squad_id"], entries, as_of)
    reg, _ = _on(reg, as_of)
    return {"player_id": player_id, "as_of": as_of.isoformat(), "league_id": lid,
            "current_squad_id": reg["squad_id"], "parent_squad_id": reg["parent_squad_id"],
            "history": [e["entry"] for e in entries]}


def parse_as_of(value):
    return today() if value is None else parse_date(value)
