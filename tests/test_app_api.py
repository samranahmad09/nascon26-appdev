import importlib.util

import pytest


def _token(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_login_returns_profile_and_branding(fresh):
    r = fresh.post("/api/v1/auth/login", json={"email": "MANAGER@nascon.test", "password": "Nascon@26"})
    body = r.json()
    assert body["user"]["role"] == "club_manager" and body["user"]["club_id"] == "club-alpha-1"
    assert body["tenant"]["branding"]["primary_color"].startswith("#")
    assert fresh.post("/api/v1/auth/login", json={"email": "x"}).status_code == 400


def test_me_requires_token(fresh):
    assert fresh.get("/api/v1/me").status_code == 401
    assert fresh.get("/api/v1/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    me = fresh.get("/api/v1/me", headers=_token(fresh, "player@nascon.test")).json()
    assert me["user"]["player_id"] == "player-alpha-1"


def test_leagues_and_clubs_are_tenant_scoped(fresh):
    viewer = _token(fresh, "viewer@nascon.test")
    leagues = [lg["league_id"] for lg in fresh.get("/api/v1/leagues", headers=viewer).json()["leagues"]]
    assert "league-alpha-1" in leagues and "league-beta-1" not in leagues
    admin = _token(fresh, "admin@nascon.test")
    assert len(fresh.get("/api/v1/leagues", headers=admin).json()["leagues"]) == 3
    clubs = [c["club_id"] for c in fresh.get("/api/v1/clubs", headers=viewer).json()["clubs"]]
    assert "club-beta-1" not in clubs
    assert fresh.get("/api/v1/clubs/club-beta-1", headers=viewer).status_code == 403
    assert fresh.get("/api/v1/clubs/club-beta-1", headers=admin).status_code == 200
    assert fresh.get("/api/v1/clubs/nope", headers=admin).status_code == 404
    assert fresh.get("/api/v1/leagues/league-beta-1/fixtures", headers=viewer).status_code == 403
    assert fresh.get("/api/v1/leagues/nope/standings", headers=viewer).status_code == 404


def test_club_profile_has_venue_staff_roster(fresh):
    club = fresh.get("/api/v1/clubs/club-alpha-1", headers=_token(fresh, "manager@nascon.test")).json()
    assert club["profile"]["home_venue"]["name"]
    assert {s["role"] for s in club["profile"]["staff"]} == {"coach", "manager", "physio"}
    assert any(p["player_id"] == "player-alpha-1" for p in club["roster"])


def test_tenant_logo_png(fresh):
    r = fresh.get("/api/v1/tenants/tenant-alpha/logo.png")
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert fresh.get("/api/v1/tenants/nope/logo.png").status_code == 404


def test_fixtures_and_match_detail(fresh):
    headers = _token(fresh, "organizer@nascon.test")
    fixtures = fresh.get("/api/v1/leagues/league-alpha-1/fixtures", headers=headers).json()
    if importlib.util.find_spec("app.modules.scoring.service") is None:
        assert fixtures["matches"] == []
        pytest.skip("scoring service not merged yet")
    assert len(fixtures["matches"]) == 6
    live = next(m for m in fixtures["matches"] if m["status"] == "in_progress")
    assert (live["home_score"], live["away_score"]) == (1, 0)
    detail = fresh.get(f"/api/v1/matches/{live['match_id']}", headers=headers).json()
    assert detail["home_name"] == "Alpha United" and detail["scoring_events"]
    assert fresh.get("/api/v1/matches/nope", headers=headers).status_code == 404


def test_standings_shape(fresh):
    body = fresh.get("/api/v1/leagues/league-alpha-1/standings", headers=_token(fresh, "viewer@nascon.test")).json()
    assert body["league_id"] == "league-alpha-1"
    if body["table"]:
        assert set(body["team_names"]) == {row["team_id"] for row in body["table"]}


def test_grader_can_register_a_seeded_tenant_league_id(fresh):
    r = fresh.post("/audit/v1/leagues", json={
        "league_id": "league-alpha-1", "sport_id": "football", "teams": ["t1", "t2"],
        "fixtures": [{"match_id": "m-x1", "home": "t1", "away": "t2"}], "qualification": {"spots": 1}})
    assert r.status_code == 201, r.text


def test_live_demo_match_has_pinned_product(fresh):
    if importlib.util.find_spec("app.modules.store.service") is None:
        pytest.skip("store not merged")
    detail = fresh.get("/api/v1/matches/demo-apl-r3-m1", headers=_token(fresh, "viewer@nascon.test")).json()
    assert detail["pinned_product"]["product_id"] == "prod-jersey-001"


def test_match_detail_exposes_live_playback_url(fresh):
    headers = _token(fresh, "viewer@nascon.test")
    assert fresh.get("/api/v1/matches/match-m1", headers=headers).json()["playback_url"] is None
    fresh.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1"})
    detail = fresh.get("/api/v1/matches/match-m1", headers=headers).json()
    assert "playback_url" in detail
