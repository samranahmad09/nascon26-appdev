"""GET /audit/v1/standings/{league_id} with the scoring service replaced by an in-memory stub."""
import sys
import types

import pytest

import app.modules.scoring as scoring_pkg
from app.core.signing import lots_key

INNINGS_SPORT = {
    "sport_id": "inn-sport", "name": "Innings Sport", "scoring_model": "innings",
    "periods": {"innings_per_team": 1, "overs_per_innings": 20, "balls_per_over": 6, "wickets_per_innings": 10},
    "scoring_events": {"hit": {"runs": 1, "legal_ball": True, "wicket": False},
                       "out": {"runs": 0, "legal_ball": True, "wicket": True}},
    "draw_allowed": True, "overtime": {"policy": "none"},
    "roster": {"min_squad_size": 2, "max_squad_size": 9, "required_positions": {}},
}


@pytest.fixture
def leagues(monkeypatch):
    store = {}
    fake = types.ModuleType("app.modules.scoring.service")
    fake.get_league = lambda conn, league_id: store[league_id][0] if league_id in store else None
    fake.league_projections = lambda conn, league_id: store[league_id][1]
    monkeypatch.setitem(sys.modules, "app.modules.scoring.service", fake)
    monkeypatch.setattr(scoring_pkg, "service", fake, raising=False)
    return store


def side(team, score, wickets, balls):
    return {"team_id": team, "score": score, "wickets": wickets, "balls": balls,
            "overs": f"{balls // 6}.{balls % 6}", "fair_play": 0}


def test_unknown_league_is_404(fresh, leagues):
    r = fresh.get("/audit/v1/standings/no-such-league")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "LEAGUE_NOT_FOUND", "message": "League no-such-league does not exist.",
                                  "details": {"league_id": "no-such-league"}}}


def test_standings_use_the_stored_sport_config(fresh, leagues):
    assert fresh.post("/audit/v1/sports", json=INNINGS_SPORT).status_code == 201
    league = {"league_id": "ipl-x", "sport_id": "inn-sport", "teams": ["a", "b"],
              "fixtures": [{"match_id": "x1", "home": "a", "away": "b"}],
              "points": {"win": 2, "draw": 1, "loss": 0}, "tie_break_order": None,
              "qualification": {"spots": 1}, "lots_seed": 42}
    # a all out for 120 in 10.0 overs (counts as 20); b chased 121 in 8.2 overs
    projection = {"match_id": "x1", "status": "completed", "outcome": "away_win", "winner_team_id": "b",
                  "home": side("a", 120, 10, 60), "away": side("b", 121, 2, 50)}
    leagues["ipl-x"] = (league, [projection])
    r = fresh.get("/audit/v1/standings/ipl-x")
    assert r.status_code == 200
    assert r.json() == {
        "league_id": "ipl-x", "sport_id": "inn-sport", "qualification_spots": 1,
        "table": [
            {"rank": 1, "team_id": "b", "played": 1, "won": 1, "drawn": 0, "lost": 0, "scored": 121,
             "conceded": 120, "diff": 8.52, "points": 2, "fair_play": 0, "status": "clinched", "resolved_by": None},
            {"rank": 2, "team_id": "a", "played": 1, "won": 0, "drawn": 0, "lost": 1, "scored": 120,
             "conceded": 121, "diff": -8.52, "points": 0, "fair_play": 0, "status": "eliminated",
             "resolved_by": None},
        ],
        "h2h": {"a": {"b": {"played": 1, "points": 0, "scored": 120, "conceded": 121}},
                "b": {"a": {"played": 1, "points": 2, "scored": 121, "conceded": 120}}},
    }


def test_missing_lots_seed_falls_back_to_the_last_reset_seed(client, leagues):
    assert client.post("/audit/v1/reset", json={"seed": 7}).status_code == 200
    teams = ["p", "q", "r", "s"]
    leagues["cup"] = ({"league_id": "cup", "sport_id": "nope", "teams": teams, "fixtures": [],
                       "points": {"win": 3, "draw": 1, "loss": 0}, "tie_break_order": [],
                       "qualification": {"spots": 4}, "lots_seed": None}, [])
    r = client.get("/audit/v1/standings/cup")
    assert r.status_code == 200
    table = r.json()["table"]
    assert [row["team_id"] for row in table] == sorted(teams, key=lambda t: lots_key(7, "cup", t))
    assert [row["resolved_by"] for row in table] == ["draw_lots", "draw_lots", "draw_lots", None]
    assert {row["status"] for row in table} == {"clinched"}
    client.post("/audit/v1/reset", json={})
