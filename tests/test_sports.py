KABADDI = {
    "sport_id": "kabaddi", "name": "Kabaddi", "scoring_model": "event",
    "periods": {"count": 2, "duration_minutes": 20},
    "scoring_events": {"raid_point": {"points": 1}, "tackle_point": {"points": 1}, "all_out": {"points": 2}},
    "draw_allowed": False, "overtime": {"policy": "golden_score", "duration_minutes": 5},
    "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
    "roster": {"min_squad_size": 7, "max_squad_size": 12, "required_positions": {"raider": 2, "defender": 3}},
}


def test_create_mystery_sport(fresh):
    r = fresh.post("/audit/v1/sports", json=KABADDI)
    assert r.status_code == 201
    body = r.json()
    assert body["sport_id"] == "kabaddi" and body["status"] == "created"
    assert body["config"]["scoring_events"]["all_out"] == {"points": 2}
    r = fresh.post("/audit/v1/sports", json=KABADDI)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SPORT_EXISTS"


def test_reset_removes_custom_sports(fresh):
    fresh.post("/audit/v1/sports", json=KABADDI)
    fresh.post("/audit/v1/reset")
    assert fresh.post("/audit/v1/sports", json=KABADDI).status_code == 201


def test_innings_and_round_models(fresh):
    innings = {**KABADDI, "sport_id": "t10", "scoring_model": "innings",
               "periods": {"innings_per_team": 1, "overs_per_innings": 10, "balls_per_over": 6, "wickets_per_innings": 10},
               "scoring_events": {"super_six": {"runs": 6, "legal_ball": True, "wicket": False},
                                  "out": {"runs": 0, "legal_ball": True, "wicket": True}}}
    assert fresh.post("/audit/v1/sports", json=innings).status_code == 201
    rounds = {**KABADDI, "sport_id": "chess-960", "scoring_model": "round", "periods": {"best_of": 5},
              "scoring_events": {"map_win": {"rounds": 1}}}
    assert fresh.post("/audit/v1/sports", json=rounds).status_code == 201


def _fields(fresh, body):
    r = fresh.post("/audit/v1/sports", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    return r.json()["error"]["details"]["fields"]


def test_validation_errors(fresh):
    assert _fields(fresh, {**KABADDI, "sport_id": "x'; DROP TABLE sports;--"}) == ["sport_id"]
    assert _fields(fresh, {**KABADDI, "periods": {"best_of": 4}, "scoring_model": "round"}) == ["periods", "scoring_events"]
    assert _fields(fresh, {**KABADDI, "scoring_events": {}}) == ["scoring_events"]
    assert _fields(fresh, {**KABADDI, "draw_allowed": "no"}) == ["draw_allowed"]
    assert _fields(fresh, {**KABADDI, "overtime": {"policy": "coin_toss"}}) == ["overtime"]
    assert _fields(fresh, {**KABADDI, "tie_break_order": ["scored", "scored"]}) == ["tie_break_order"]
    assert _fields(fresh, {**KABADDI, "roster": {"min_squad_size": 9, "max_squad_size": 3, "required_positions": {}}}) == ["roster"]
    assert _fields(fresh, {**KABADDI, "periods": {"count": True, "duration_minutes": 20}}) == ["periods"]
    missing = {k: v for k, v in KABADDI.items() if k != "name"}
    assert _fields(fresh, missing) == ["name"]
    assert _fields(fresh, [1, 2]) == ["body"]
