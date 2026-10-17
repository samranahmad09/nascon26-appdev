"""Demo league, fixtures, live match and roster shown in the mobile app. Seeded after every reset through the
same domain services the audit routes use. Failures here are logged and never break /audit/v1/reset."""
import importlib
import json
import logging
from pathlib import Path

from ...core.registry import register
from ..sports import get_sport

log = logging.getLogger("demo")
DEMO = json.loads(Path(__file__).with_name("demo.json").read_text(encoding="utf-8"))["league"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_fixture_meta (
    match_id TEXT PRIMARY KEY, league_id TEXT NOT NULL, round INT NOT NULL, scheduled_at TEXT NOT NULL, venue TEXT);
"""


def optional_module(path: str):
    try:
        return importlib.import_module(path)
    except ImportError:
        return None


def unit_event(sport: dict) -> str | None:
    """The scoring event worth the smallest positive amount, chosen from config (never by name)."""
    def worth(v: dict) -> int:
        return v.get("points", v.get("runs", v.get("rounds", 0)))
    candidates = sorted((worth(v), k) for k, v in sport["scoring_events"].items() if worth(v) > 0 and not v.get("wicket"))
    return candidates[0][1] if candidates else None


def _seed_matches(conn, scoring, league_row, sport) -> None:
    scoring.register_league(conn, {
        "league_id": DEMO["league_id"], "sport_id": sport["sport_id"], "teams": DEMO["teams"],
        "fixtures": [{"match_id": f["match_id"], "home": f["home"], "away": f["away"]} for f in DEMO["fixtures"]],
        "points": DEMO["points"], "qualification": {"spots": DEMO["qualification_spots"]},
    })
    unit = unit_event(sport)
    seq = 0

    def add(match_id, event_type, payload):
        nonlocal seq
        seq += 1
        scoring.append_event(conn, {"event_id": f"demo-ev-{seq:04d}", "match_id": match_id,
                                    "type": event_type, "payload": payload})

    for f in DEMO["fixtures"]:
        score = f.get("result") or f.get("live")
        if score is None:
            continue
        add(f["match_id"], "match_start", {})
        for team, count in ((f["home"], score[0]), (f["away"], score[1])):
            for _ in range(count):
                add(f["match_id"], "score", {"team_id": team, "event_type": unit})
        if "result" in f:
            add(f["match_id"], "match_end", {})


def _seed_roster(conn, roster, league_row) -> None:
    clubs = {r["club_id"]: r for r in conn.execute("SELECT club_id, name FROM clubs").fetchall()}
    players = conn.execute("SELECT * FROM players WHERE club_id = ANY(%s) ORDER BY player_id", (DEMO["teams"],)).fetchall()
    cfg = DEMO["roster"]
    roster.setup(conn, {
        "league": {"league_id": DEMO["league_id"], "tenant_id": league_row["tenant_id"], "sport_id": league_row["sport_id"],
                   "transfer_window": cfg["transfer_window"], "max_squad_size": cfg["max_squad_size"],
                   "required_positions": cfg["required_positions"]},
        "squads": [{"squad_id": c, "club_id": c, "tenant_id": league_row["tenant_id"], "name": clubs[c]["name"]}
                   for c in DEMO["teams"]],
        "players": [{"player_id": p["player_id"], "name": p["name"], "position": p["position"],
                     "squad_id": p["club_id"], "role": p["role"]} for p in players],
        "matches_in_progress": [],
    })


def seed_demo(conn, opts: dict) -> None:
    league_row = conn.execute("SELECT * FROM tenant_leagues WHERE league_id = %s", (DEMO["league_id"],)).fetchone()
    if league_row is None:
        return
    venues = {r["club_id"]: r["profile"]["home_venue"]["name"] for r in conn.execute("SELECT club_id, profile FROM clubs").fetchall()}
    for f in DEMO["fixtures"]:
        conn.execute("INSERT INTO app_fixture_meta VALUES (%s, %s, %s, %s, %s)",
                     (f["match_id"], DEMO["league_id"], f["round"], f["scheduled_at"], venues.get(f["home"])))
    sport = get_sport(conn, league_row["sport_id"])
    scoring = optional_module("app.modules.scoring.service")
    if scoring is not None and sport is not None:
        try:
            with conn.transaction():
                _seed_matches(conn, scoring, league_row, sport)
        except Exception:
            log.exception("demo league not seeded")
    roster = optional_module("app.modules.roster.service")
    if roster is not None:
        try:
            with conn.transaction():
                _seed_roster(conn, roster, league_row)
        except Exception:
            log.exception("demo roster not seeded")


register(SCHEMA, ["app_fixture_meta"], seed_demo, order=900)
