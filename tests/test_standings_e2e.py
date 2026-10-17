"""End to end: mystery sports, POST /audit/v1/leagues and /audit/v1/events, then GET /audit/v1/standings."""
import itertools

from app.core.signing import lots_key

ROSTER = {"min_squad_size": 2, "max_squad_size": 9, "required_positions": {}}
TALLY_SPORT = {"sport_id": "tally-sport", "name": "Tally", "scoring_model": "event",
               "periods": {"count": 2, "duration_minutes": 30}, "scoring_events": {"tally": {"points": 1}},
               "draw_allowed": True, "overtime": {"policy": "none"}, "roster": ROSTER}
DUEL_SPORT = {**TALLY_SPORT, "sport_id": "duel-sport", "name": "Duel", "draw_allowed": False,
              "overtime": {"policy": "shootout"}}
BAT_SPORT = {"sport_id": "bat-sport", "name": "Bat", "scoring_model": "innings",
             "periods": {"innings_per_team": 1, "overs_per_innings": 2, "balls_per_over": 6, "wickets_per_innings": 2},
             "scoring_events": {"run1": {"runs": 1, "legal_ball": True, "wicket": False},
                                "run6": {"runs": 6, "legal_ball": True, "wicket": False},
                                "xtra": {"runs": 1, "legal_ball": False, "wicket": False},
                                "fall": {"runs": 0, "legal_ball": True, "wicket": True}},
             "draw_allowed": True, "overtime": {"policy": "none"}, "roster": ROSTER}

_ids = itertools.count(1)


def event(client, match_id, kind, payload=None):
    body = {"event_id": f"ev-{next(_ids)}", "match_id": match_id, "type": kind, "payload": payload or {}}
    r = client.post("/audit/v1/events", json=body)
    assert r.status_code == 201, r.text
    return body["event_id"]


def play(client, match_id, scoring, end=True):
    """scoring: list of (team_id, event_type); returns the event ids of the score events."""
    event(client, match_id, "match_start")
    ids = [event(client, match_id, "score", {"team_id": team, "event_type": kind}) for team, kind in scoring]
    if end:
        event(client, match_id, "match_end")
    return ids


def setup(client, sport, league):
    assert client.post("/audit/v1/sports", json=sport).status_code == 201
    r = client.post("/audit/v1/leagues", json=league)
    assert r.status_code == 201, r.text


def standings(client, league_id):
    r = client.get(f"/audit/v1/standings/{league_id}")
    assert r.status_code == 200, r.text
    return r.json()


def brief(body):
    return [(r["rank"], r["team_id"], r["points"], r["diff"], r["status"], r["resolved_by"]) for r in body["table"]]


def test_unknown_league_through_the_scoring_service(fresh):
    r = fresh.get("/audit/v1/standings/lg-missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "LEAGUE_NOT_FOUND"


def test_three_way_tie_mini_league_and_restart(fresh):
    pairs = [("t1", "t2"), ("t2", "t3"), ("t3", "t1"), ("t1", "t4"), ("t2", "t4"), ("t3", "t4")]
    setup(fresh, TALLY_SPORT, {
        "league_id": "lg-3way", "sport_id": "tally-sport", "teams": ["t1", "t2", "t3", "t4"],
        "fixtures": [{"match_id": f"w{i}", "home": h, "away": a} for i, (h, a) in enumerate(pairs, 1)],
        "qualification": {"spots": 2}})
    for match_id, (home, away), (hs, as_) in zip([f"w{i}" for i in range(1, 7)], pairs,
                                                 [(3, 0), (2, 0), (1, 0), (1, 0), (1, 0), (5, 0)]):
        play(fresh, match_id, [(home, "tally")] * hs + [(away, "tally")] * as_)
    body = standings(fresh, "lg-3way")
    assert body["league_id"] == "lg-3way" and body["sport_id"] == "tally-sport" and body["qualification_spots"] == 2
    assert brief(body) == [
        (1, "t1", 6, 3, "still_possible", "h2h_diff"),
        (2, "t2", 6, 0, "still_possible", "h2h_points"),
        (3, "t3", 6, 4, "still_possible", None),
        (4, "t4", 0, -7, "eliminated", None),
    ]
    assert body["h2h"]["t1"]["t2"] == {"played": 1, "points": 3, "scored": 3, "conceded": 0}
    assert body["h2h"]["t3"]["t2"] == {"played": 1, "points": 0, "scored": 0, "conceded": 2}


def test_compensation_after_qualification_recomputes_everything(fresh):
    setup(fresh, TALLY_SPORT, {
        "league_id": "lg-1", "sport_id": "tally-sport", "teams": ["t1", "t2", "t3"],
        "fixtures": [{"match_id": "c1", "home": "t1", "away": "t2"}, {"match_id": "c2", "home": "t1", "away": "t3"},
                     {"match_id": "c3", "home": "t2", "away": "t3"}],
        "qualification": {"spots": 1}})
    goal = play(fresh, "c1", [("t1", "tally")])[0]
    play(fresh, "c2", [("t1", "tally")])
    event(fresh, "c3", "match_start")
    event(fresh, "c3", "score", {"team_id": "t2", "event_type": "tally"})
    event(fresh, "c3", "discipline", {"team_id": "t3", "points": -2})
    event(fresh, "c3", "match_end")
    before = standings(fresh, "lg-1")
    assert brief(before) == [(1, "t1", 6, 2, "clinched", None), (2, "t2", 3, 0, "eliminated", None),
                             (3, "t3", 0, -2, "eliminated", None)]
    assert before["table"][2]["fair_play"] == -2

    event(fresh, "c1", "compensation", {"compensates": goal, "reason": "goal disallowed after review"})
    after = standings(fresh, "lg-1")
    # 0-0 now: t1 and t2 level on everything, the default seed 42 lots put t1 before t2 (Appendix A)
    assert after["table"][:2] == [
        {"rank": 1, "team_id": "t1", "played": 2, "won": 1, "drawn": 1, "lost": 0, "scored": 1, "conceded": 0,
         "diff": 1, "points": 4, "fair_play": 0, "status": "still_possible", "resolved_by": "draw_lots"},
        {"rank": 2, "team_id": "t2", "played": 2, "won": 1, "drawn": 1, "lost": 0, "scored": 1, "conceded": 0,
         "diff": 1, "points": 4, "fair_play": 0, "status": "still_possible", "resolved_by": None},
    ]
    assert after["h2h"]["t1"]["t2"] == {"played": 1, "points": 1, "scored": 0, "conceded": 0}
    score = fresh.get("/audit/v1/score/c1").json()
    original = next(e for e in score["events"] if e["event_id"] == goal)
    assert original["compensated"] is True and original["payload"] == {"team_id": "t1", "event_type": "tally"}


def test_compensated_tiebreak_winner_makes_the_match_remaining(fresh):
    setup(fresh, DUEL_SPORT, {
        "league_id": "lg-duel", "sport_id": "duel-sport", "teams": ["a", "b"],
        "fixtures": [{"match_id": "d1", "home": "a", "away": "b"}],
        "points": {"win": 2, "draw": 1, "loss": 0}, "qualification": {"spots": 1}, "lots_seed": 5})
    play(fresh, "d1", [("a", "tally"), ("b", "tally")], end=False)
    shootout = event(fresh, "d1", "tiebreak_winner", {"team_id": "b"})
    event(fresh, "d1", "match_end")
    body = standings(fresh, "lg-duel")
    # b won the level match on the tie-break, which counts as a win
    assert [(r["team_id"], r["played"], r["won"], r["lost"], r["points"], r["status"]) for r in body["table"]] == [
        ("b", 1, 1, 0, 2, "clinched"), ("a", 1, 0, 1, 0, "eliminated")]

    event(fresh, "d1", "compensation", {"compensates": shootout, "reason": "shootout void"})
    body = standings(fresh, "lg-duel")
    # level, no draws, no active tie-break winner: undecided, so d1 is remaining again (max 2 each)
    expected = sorted([("a", 0, 0, 0, "still_possible"), ("b", 0, 0, 0, "still_possible")],
                      key=lambda row: lots_key(5, "lg-duel", row[0]))
    assert [(r["team_id"], r["played"], r["points"], r["scored"], r["status"]) for r in body["table"]] == expected
    assert body["h2h"]["a"]["b"] == {"played": 0, "points": 0, "scored": 0, "conceded": 0}


def test_innings_net_run_rate_through_the_event_log(fresh):
    setup(fresh, BAT_SPORT, {
        "league_id": "lg-bat", "sport_id": "bat-sport", "teams": ["h", "v"],
        "fixtures": [{"match_id": "b1", "home": "h", "away": "v"}], "qualification": {"spots": 1}})
    # h: 18 runs, all out (2 wickets) after 5 legal balls -> counts as the full 2 overs
    # v: 20 runs off 4 legal balls (one extra is not a legal ball) -> 0.667 overs
    play(fresh, "b1", [("h", "run6")] * 3 + [("h", "fall")] * 2 +
         [("v", "run6"), ("v", "run6"), ("v", "xtra"), ("v", "run6"), ("v", "run1")])
    assert fresh.get("/audit/v1/score/b1").json()["away"]["overs"] == "0.4"
    body = standings(fresh, "lg-bat")
    # v: 20 / (4/6) - 18 / 2 = 30 - 9 = 21.0; h: -21.0
    assert body["table"] == [
        {"rank": 1, "team_id": "v", "played": 1, "won": 1, "drawn": 0, "lost": 0, "scored": 20, "conceded": 18,
         "diff": 21.0, "points": 3, "fair_play": 0, "status": "clinched", "resolved_by": None},
        {"rank": 2, "team_id": "h", "played": 1, "won": 0, "drawn": 0, "lost": 1, "scored": 18, "conceded": 20,
         "diff": -21.0, "points": 0, "fair_play": 0, "status": "eliminated", "resolved_by": None},
    ]
    assert body["h2h"] == {"h": {"v": {"played": 1, "points": 0, "scored": 18, "conceded": 20}},
                           "v": {"h": {"played": 1, "points": 3, "scored": 20, "conceded": 18}}}
