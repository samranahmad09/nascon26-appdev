"""POST /audit/v1/leagues (5.8), the scoring service contract, and Section 6 edge case 5."""
import pytest

from app.core import db, locks, queue, realtime
from app.core.errors import ApiError
from app.modules.scoring import service

from .test_scoring import DRAWS, INNINGS, Match, err, fields, kind_of, side

LEAGUES = "/audit/v1/leagues"


def league(**over):
    body = {
        "league_id": "lg-1", "sport_id": DRAWS["sport_id"], "teams": ["t1", "t2", "t3", "t4"],
        "fixtures": [{"match_id": "m1", "home": "t1", "away": "t2"}, {"match_id": "m2", "home": "t3", "away": "t4"}],
        "points": {"win": 3, "draw": 1, "loss": 0},
        "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
        "qualification": {"spots": 2}, "lots_seed": 42,
    }
    body.update(over)
    return body


def fixture(match_id, home, away):
    return {"match_id": match_id, "home": home, "away": away}


def test_register_league_creates_upcoming_fixture_streams(fresh):
    r = fresh.post(LEAGUES, json=league())
    assert r.status_code == 201
    assert r.json() == {"league_id": "lg-1", "status": "created", "team_count": 4, "fixture_count": 2}
    assert fresh.get("/audit/v1/score/m2").json() == {
        "match_id": "m2", "sport_id": DRAWS["sport_id"], "league_id": "lg-1", "status": "upcoming",
        "outcome": "pending", "winner_team_id": None, "tiebreak_winner_team_id": None,
        "home": side("t3", 0), "away": side("t4", 0), "version": 0, "events": [],
    }
    m = Match(fresh, None, "t1", "t2", match_id="m1", init=False)
    start = m.start()
    assert (start["sequence"], start["match_version"]) == (1, 1)
    err(m.post("match_init", {"sport_id": DRAWS["sport_id"], "home_team_id": "t1", "away_team_id": "t2"}),
        409, "MATCH_EXISTS")
    assert fields(m.try_score("t3", kind_of(DRAWS, points=1))) == ["payload.team_id"]
    m.score("t2", kind_of(DRAWS, points=1))
    assert m.get()["version"] == 2 and m.get()["league_id"] == "lg-1"

    assert fresh.post("/audit/v1/reset").status_code == 200
    err(fresh.get("/audit/v1/score/m1"), 404, "MATCH_NOT_FOUND")
    assert fresh.post(LEAGUES, json=league()).status_code == 201


def test_league_not_found_and_conflicts(fresh):
    err(fresh.post(LEAGUES, json=league(sport_id="no-such-sport")), 404, "SPORT_NOT_FOUND")
    assert fresh.post(LEAGUES, json=league()).status_code == 201
    # Re-posting a league: the league id conflict is reported, not its fixtures'.
    assert err(fresh.post(LEAGUES, json=league()), 409, "LEAGUE_EXISTS")["details"] == {"league_id": "lg-1"}
    used = league(league_id="lg-2", fixtures=[fixture("m9", "t1", "t3"), fixture("m2", "t1", "t2")])
    assert err(fresh.post(LEAGUES, json=used), 409, "MATCH_EXISTS")["details"] == {"match_id": "m2"}
    Match(fresh, DRAWS["sport_id"], match_id="solo")
    err(fresh.post(LEAGUES, json=league(league_id="lg-3", fixtures=[fixture("solo", "t1", "t2")])), 409, "MATCH_EXISTS")
    twice = league(league_id="lg-4", fixtures=[fixture("x1", "t1", "t2"), fixture("x1", "t3", "t4")])
    err(fresh.post(LEAGUES, json=twice), 409, "MATCH_EXISTS")
    # Rejected registrations leave nothing behind.
    err(fresh.get("/audit/v1/score/m9"), 404, "MATCH_NOT_FOUND")
    err(fresh.get("/audit/v1/score/x1"), 404, "MATCH_NOT_FOUND")
    assert fresh.post(LEAGUES, json=league(league_id="lg-4", fixtures=[fixture("x1", "t1", "t2")])).status_code == 201
    # 400 beats 404.
    assert fields(fresh.post(LEAGUES, json=league(league_id="lg-5", sport_id="no-such-sport", teams=["t1"]))) == ["teams"]


def test_league_validation(fresh):
    def bad(**over):
        return fields(fresh.post(LEAGUES, json=league(**over)))

    assert fields(fresh.post(LEAGUES, json=[])) == ["body"]
    assert fields(fresh.post(LEAGUES)) == ["body"]
    assert fields(fresh.post(LEAGUES, json={})) == ["league_id", "sport_id", "teams", "fixtures", "qualification"]
    assert bad(league_id="has space") == ["league_id"]
    assert bad(sport_id=7) == ["sport_id"]
    assert bad(teams=["t1"]) == ["teams"]
    assert bad(teams=[f"t{i}" for i in range(41)]) == ["teams"]
    assert bad(teams=["t1", "t1", "t2"]) == ["teams"]
    assert bad(teams=["t1", 2]) == ["teams"]
    assert bad(teams="t1,t2") == ["teams"]
    assert bad(fixtures=[fixture("m1", "t1", "t9")]) == ["fixtures"]
    assert bad(fixtures=[fixture("m1", "t1", "t1")]) == ["fixtures"]
    assert bad(fixtures=[fixture("bad id", "t1", "t2")]) == ["fixtures"]
    assert bad(fixtures=[{"match_id": "m1", "home": "t1"}]) == ["fixtures"]
    assert bad(fixtures=["m1"]) == ["fixtures"]
    assert bad(fixtures={"match_id": "m1"}) == ["fixtures"]
    assert bad(points={"win": "3", "draw": 1.5, "loss": 0}) == ["points.win", "points.draw"]
    assert bad(points=[3, 1, 0]) == ["points"]
    assert bad(tie_break_order=["scored", "scored"]) == ["tie_break_order"]
    assert bad(tie_break_order=["coin_toss"]) == ["tie_break_order"]
    assert bad(qualification={"spots": 0}) == ["qualification.spots"]
    assert bad(qualification={"spots": 5}) == ["qualification.spots"]
    assert bad(qualification={"spots": True}) == ["qualification.spots"]
    assert bad(qualification={}) == ["qualification.spots"]
    assert bad(qualification=2) == ["qualification"]
    assert bad(lots_seed="42") == ["lots_seed"]
    assert bad(lots_seed=True) == ["lots_seed"]
    ok = fresh.post(LEAGUES, json=league(points=None, tie_break_order=None, lots_seed=None))
    assert ok.status_code == 201, ok.text


def test_get_league_defaults_and_explicit_values(client):
    assert client.post("/audit/v1/reset", json={"seed": 7}).status_code == 200
    body = {"league_id": "lg-d", "sport_id": DRAWS["sport_id"], "teams": ["a", "b", "c"],
            "fixtures": [fixture("d1", "a", "b"), fixture("d2", "c", "a")], "qualification": {"spots": 1}}
    assert client.post(LEAGUES, json=body).status_code == 201
    explicit = {**body, "league_id": "lg-e", "fixtures": [], "points": {"win": 2, "loss": -1},
                "tie_break_order": [], "lots_seed": 0, "qualification": {"spots": 3}}
    r = client.post(LEAGUES, json=explicit)
    assert r.json() == {"league_id": "lg-e", "status": "created", "team_count": 3, "fixture_count": 0}
    with locks.GLOBAL, db.tx() as conn:
        defaults = service.get_league(conn, "lg-d")
        chosen = service.get_league(conn, "lg-e")
        assert service.get_league(conn, "nope") is None
        assert service.league_projections(conn, "lg-e") == []
        assert service.league_projections(conn, "nope") == []
    assert defaults == {
        "league_id": "lg-d", "sport_id": DRAWS["sport_id"], "teams": ["a", "b", "c"],
        "fixtures": [fixture("d1", "a", "b"), fixture("d2", "c", "a")],
        "points": {"win": 3, "draw": 1, "loss": 0}, "tie_break_order": None,
        "qualification": {"spots": 1}, "lots_seed": 7,
    }
    assert chosen["points"] == {"win": 2, "draw": 1, "loss": -1}
    assert (chosen["tie_break_order"], chosen["lots_seed"], chosen["fixtures"]) == ([], 0, [])
    assert client.post("/audit/v1/reset", json={}).status_code == 200


def test_service_contract(fresh):
    assert fresh.post(LEAGUES, json=league()).status_code == 201
    init = {"event_id": "s-1", "match_id": "solo", "type": "match_init",
            "payload": {"sport_id": DRAWS["sport_id"], "home_team_id": "h", "away_team_id": "a", "league_id": "lg-1"}}
    with locks.GLOBAL, db.tx() as conn:
        assert service.match_version(conn, "ghost") is None
        assert service.project(conn, "ghost") is None
        assert service.match_version(conn, "m1") == 0
        status, response = service.append_event(conn, init)
        assert status == 201 and response["sequence"] == 1
        assert service.append_event(conn, init) == (200, response)
        assert service.match_version(conn, "solo") == 1
        assert service.project(conn, "solo")["league_id"] == "lg-1"
        # A stand-alone match naming the league is not one of its fixtures.
        assert [p["match_id"] for p in service.league_projections(conn, "lg-1")] == ["m1", "m2"]
    for call, expected in [(lambda c: service.append_event(c, {**init, "event_id": "s-2"}), (409, "MATCH_EXISTS")),
                           (lambda c: service.register_league(c, league()), (409, "LEAGUE_EXISTS")),
                           (lambda c: service.append_event(c, {"event_id": "s-3"}), (400, "VALIDATION_ERROR"))]:
        with pytest.raises(ApiError) as exc:
            with locks.GLOBAL, db.tx() as conn:
                call(conn)
        assert (exc.value.status, exc.value.code) == expected
    with locks.GLOBAL, db.tx() as conn:
        assert service.match_version(conn, "solo") == 1
        assert service.register_league(conn, league(league_id="lg-svc", fixtures=[])) == \
            {"league_id": "lg-svc", "status": "created", "team_count": 4, "fixture_count": 0}


def test_appends_publish_score_updates_and_domain_events(fresh, monkeypatch):
    published, emitted = [], []
    monkeypatch.setattr(realtime, "publish", lambda topic, message: published.append((topic, message)))
    monkeypatch.setattr(queue, "emit", lambda kind, data: emitted.append((kind, data)))
    m = Match(fresh, DRAWS["sport_id"])
    start_body = m.body("match_start")
    start = fresh.post("/audit/v1/events", json=start_body).json()
    assert fresh.post("/audit/v1/events", json=start_body).status_code == 200
    err(m.post("match_start"), 409, "ILLEGAL_TRANSITION")
    assert [topic for topic, _ in published] == [f"match:{m.id}"] * 2
    topic, message = published[-1]
    assert message == {"type": "score_update", "match": m.get()}
    kind, data = emitted[-1]
    assert kind == "match_event" and data["event_id"] == start["event_id"] and data["status"] == "in_progress"
    assert len(emitted) == 2
    assert fresh.post(LEAGUES, json=league()).status_code == 201
    assert emitted[-1] == ("league_registered", {"league_id": "lg-1", "sport_id": DRAWS["sport_id"],
                                                 "team_count": 4, "fixture_count": 2})


def test_result_corrected_after_qualification_is_recomputed(fresh):
    """Section 6 #5: compensation on a completed league match; projections recompute, the log stays intact."""
    point = kind_of(DRAWS, points=1)
    body = league(league_id="lg-q", teams=["t1", "t2", "t3"], qualification={"spots": 1},
                  fixtures=[fixture("q1", "t1", "t2"), fixture("q2", "t2", "t3"), fixture("q3", "t3", "t1")])
    assert fresh.post(LEAGUES, json=body).status_code == 201
    goals = {}
    for match_id, home, away, home_goals, away_goals in [("q1", "t1", "t2", 2, 1), ("q2", "t2", "t3", 0, 0),
                                                         ("q3", "t3", "t1", 0, 1)]:
        m = Match(fresh, None, home, away, match_id=match_id, init=False)
        m.start()
        goals[match_id] = [m.score(home, point) for _ in range(home_goals)] + \
                          [m.score(away, point) for _ in range(away_goals)]
        m.end()
    with locks.GLOBAL, db.tx() as conn:
        before = service.league_projections(conn, "lg-q")
    assert [(p["match_id"], p["outcome"]) for p in before] == [("q1", "home_win"), ("q2", "draw"), ("q3", "away_win")]
    original = fresh.get("/audit/v1/score/q1").json()["events"]

    q1 = Match(fresh, None, "t1", "t2", match_id="q1", init=False)
    comp = q1.compensate(goals["q1"][0]["event_id"], "disallowed")
    with locks.GLOBAL, db.tx() as conn:
        after = service.league_projections(conn, "lg-q")
        assert after[0] == service.project(conn, "q1")
    assert [(p["match_id"], p["outcome"]) for p in after] == [("q1", "draw"), ("q2", "draw"), ("q3", "away_win")]
    assert after[1:] == before[1:]
    events = fresh.get("/audit/v1/score/q1").json()["events"]
    assert len(events) == len(original) + 1
    stable = ("sequence", "event_id", "type", "payload", "signature")
    assert [{k: e[k] for k in stable} for e in events[:-1]] == [{k: e[k] for k in stable} for e in original]
    assert (events[1]["compensated"], events[1]["compensated_by"]) == (True, comp["event_id"])


def test_innings_league_projections_carry_balls_and_wickets(fresh):
    per_over = INNINGS["periods"]["balls_per_over"]
    single = kind_of(INNINGS, runs=1, legal_ball=True, wicket=False)
    body = league(league_id="lg-i", sport_id=INNINGS["sport_id"], teams=["x", "y"], fixtures=[fixture("i1", "x", "y")],
                  qualification={"spots": 1})
    assert fresh.post(LEAGUES, json=body).status_code == 201
    m = Match(fresh, None, "x", "y", match_id="i1", init=False)
    m.start()
    for _ in range(per_over + 1):
        m.score("y", single)
    with locks.GLOBAL, db.tx() as conn:
        [projection] = service.league_projections(conn, "lg-i")
    assert projection["away"] == side("y", per_over + 1, wickets=0, balls=per_over + 1, overs="1.1")
    assert projection["home"] == side("x", 0, wickets=0, balls=0, overs="0.0")
