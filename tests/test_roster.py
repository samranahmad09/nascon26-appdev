import copy
import datetime as dt

import pytest

SETUP = {
    "league": {"league_id": "lg-r1", "tenant_id": "tenant-alpha", "sport_id": "football",
               "transfer_window": {"open": "2026-09-01", "close": "2026-09-30"},
               "max_squad_size": 3, "required_positions": {"gk": 1}},
    "squads": [{"squad_id": "sq-a", "club_id": "club-a", "tenant_id": "tenant-alpha", "name": "A"},
               {"squad_id": "sq-b", "club_id": "club-b", "tenant_id": "tenant-alpha", "name": "B"}],
    "players": [{"player_id": "p1", "name": "Ali", "position": "gk", "squad_id": "sq-a", "role": "captain"},
                {"player_id": "p2", "name": "Bilal", "position": "df", "squad_id": "sq-a", "role": "starter"}],
    "matches_in_progress": [],
}

MOVE = {"request_id": "tr-001", "league_id": "lg-r1", "player_id": "p2", "type": "transfer",
        "from_squad_id": "sq-a", "to_squad_id": "sq-b", "effective_date": "2026-09-30",
        "loan_end_date": None, "reason": "Tactical move", "requested_by": "user-77"}


def make_league(client, league=None, **extra):
    body = copy.deepcopy(SETUP)
    body["league"].update(league or {})
    body.update(extra)
    r = client.post("/audit/v1/roster/setup", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def move(client, **fields):
    return client.post("/audit/v1/roster/transfer", json={**MOVE, **fields})


def history(client, player_id, as_of=None):
    url = f"/audit/v1/roster/history/{player_id}" + (f"?as_of={as_of}" if as_of else "")
    return client.get(url)


def error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    return r.json()["error"]


def fields(r):
    return error(r, 400, "VALIDATION_ERROR")["details"]["fields"]


def without_when(entry):
    assert entry["when"].endswith("Z") and len(entry["when"]) == 20
    return {k: v for k, v in entry.items() if k != "when"}


# ---------------------------------------------------------------- setup

def test_setup_response(fresh):
    assert make_league(fresh) == {"league_id": "lg-r1", "status": "configured", "squad_counts": {"sq-a": 2, "sq-b": 0}}


def test_setup_replaces_league_state(fresh):
    make_league(fresh)
    assert move(fresh).status_code == 201
    players = [{"player_id": "p2", "name": "Bilal", "position": "df", "squad_id": "sq-b", "role": "starter"}]
    assert make_league(fresh, players=players)["squad_counts"] == {"sq-a": 0, "sq-b": 1}
    h = history(fresh, "p2").json()
    assert h["history"] == [] and h["current_squad_id"] == "sq-b"
    # request ids and audit numbering start over with the new scenario
    r = move(fresh, from_squad_id="sq-b", to_squad_id="sq-a")
    assert r.status_code == 201 and r.json()["audit_entry"]["audit_id"] == "au-0001"


def test_setup_defaults_from_sport(fresh):
    league = dict(SETUP["league"])
    del league["max_squad_size"], league["required_positions"]
    r = fresh.post("/audit/v1/roster/setup", json={**SETUP, "league": league})
    assert r.status_code == 201
    # football: gk >= 1 is required, max 25
    error(move(fresh, player_id="p1"), 409, "MIN_POSITION_VIOLATION")
    assert move(fresh).status_code == 201


def test_setup_minimal_and_custom_sport(fresh):
    sport = {"sport_id": "kabaddi", "name": "Kabaddi", "scoring_model": "event",
             "periods": {"count": 2, "duration_minutes": 20}, "scoring_events": {"raid_point": {"points": 1}},
             "draw_allowed": False, "overtime": {"policy": "none"},
             "roster": {"min_squad_size": 1, "max_squad_size": 2, "required_positions": {"raider": 1}}}
    assert fresh.post("/audit/v1/sports", json=sport).status_code == 201
    body = {"league": {"league_id": "lg-k", "tenant_id": "tenant-beta", "sport_id": "kabaddi",
                       "transfer_window": {"open": "2026-01-01", "close": "2026-01-31"}},
            "squads": [{"squad_id": "k1"}, {"squad_id": "k2"}],
            "players": [{"player_id": "r1", "position": "raider", "squad_id": "k1"},
                        {"player_id": "r2", "position": "raider", "squad_id": "k1"},
                        {"player_id": "r3", "squad_id": "k2"}, {"player_id": "r4", "squad_id": "k2"},
                        {"player_id": "free"}]}
    r = fresh.post("/audit/v1/roster/setup", json=body)
    assert r.status_code == 201 and r.json()["squad_counts"] == {"k1": 2, "k2": 2}
    base = {**MOVE, "league_id": "lg-k", "player_id": "r1", "from_squad_id": "k1", "to_squad_id": "k2",
            "effective_date": "2026-01-10"}
    error(fresh.post("/audit/v1/roster/transfer", json=base), 409, "SQUAD_FULL")
    ok = fresh.post("/audit/v1/roster/transfer", json={**base, "type": "release", "to_squad_id": None})
    assert ok.status_code == 201
    error(fresh.post("/audit/v1/roster/transfer",
                     json={**base, "request_id": "tr-2", "player_id": "r2", "type": "release", "to_squad_id": None}),
          409, "MIN_POSITION_VIOLATION")


def test_setup_validation(fresh):
    def bad(body):
        return fields(fresh.post("/audit/v1/roster/setup", json=body))

    league = SETUP["league"]
    assert bad({**SETUP, "league": {**league, "league_id": "bad|id"}}) == ["league.league_id"]
    assert bad({**SETUP, "league": {**league, "transfer_window": {"open": "2026-10-01", "close": "2026-09-01"}}}) \
        == ["league.transfer_window"]
    assert bad({**SETUP, "league": {**league, "transfer_window": {"open": "2026-02-30", "close": "2026-09-01"}}}) \
        == ["league.transfer_window"]
    assert bad({**SETUP, "league": {k: v for k, v in league.items() if k != "transfer_window"}}) \
        == ["league.transfer_window"]
    assert bad({**SETUP, "league": {**league, "max_squad_size": "3"}}) == ["league.max_squad_size"]
    assert bad({**SETUP, "league": {**league, "required_positions": {"gk": -1}}}) == ["league.required_positions"]
    assert bad({**SETUP, "league": "lg-r1"}) == ["league"]
    assert bad({k: v for k, v in SETUP.items() if k != "squads"}) == ["squads"]
    assert bad({**SETUP, "squads": SETUP["squads"] * 2}) == ["squads"]
    assert bad({**SETUP, "players": [{**SETUP["players"][0], "role": "coach"}]}) == ["players"]
    assert bad({**SETUP, "players": SETUP["players"] * 2}) == ["players"]
    assert bad({**SETUP, "matches_in_progress": [{"match_id": "m1", "home_squad_id": "sq-a"}]}) \
        == ["matches_in_progress"]
    assert bad([SETUP]) == ["body"]
    r = fresh.post("/audit/v1/roster/setup", content=b"{not json", headers={"content-type": "application/json"})
    assert fields(r) == ["body"]


def test_setup_unknown_references(fresh):
    r = fresh.post("/audit/v1/roster/setup", json={**SETUP, "league": {**SETUP["league"], "sport_id": "quidditch"}})
    error(r, 404, "SPORT_NOT_FOUND")
    players = [{**SETUP["players"][0], "squad_id": "sq-zz"}]
    error(fresh.post("/audit/v1/roster/setup", json={**SETUP, "players": players}), 404, "SQUAD_NOT_FOUND")


# ---------------------------------------------------------------- transfer

def test_transfer_exact_response_and_replay(fresh):
    make_league(fresh)
    r = move(fresh)
    assert r.status_code == 201
    body = r.json()
    assert {k: v for k, v in body.items() if k != "audit_entry"} == {
        "request_id": "tr-001", "status": "accepted", "state": "active", "blocking_match_id": None,
        "registration": {"player_id": "p2", "league_id": "lg-r1", "squad_id": "sq-b", "parent_squad_id": None,
                         "registration_type": "transfer", "start_date": "2026-09-30", "loan_end_date": None,
                         "role": "substitute"}}
    assert without_when(body["audit_entry"]) == {
        "audit_id": "au-0001", "action": "transfer", "player_id": "p2", "league_id": "lg-r1",
        "from_squad_id": "sq-a", "to_squad_id": "sq-b", "effective_date": "2026-09-30", "state": "active",
        "who": "user-77", "why": "Tactical move"}
    replay = move(fresh)
    assert replay.status_code == 200 and replay.content == r.content
    # a reused request_id returns the stored outcome and changes nothing
    again = move(fresh, to_squad_id="sq-a", from_squad_id="sq-b")
    assert again.status_code == 200 and again.json() == body
    h = history(fresh, "p2", "2026-10-01").json()
    assert h["current_squad_id"] == "sq-b" and len(h["history"]) == 1


def test_in_progress_match_defers_on_last_window_day(fresh):
    make_league(fresh, matches_in_progress=[{"match_id": "m-other", "home_squad_id": "sq-x", "away_squad_id": "sq-y"},
                                      {"match_id": "m-live", "home_squad_id": "sq-b", "away_squad_id": "sq-a"}])
    r = move(fresh)
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "deferred_until_match_end" and body["blocking_match_id"] == "m-live"
    assert body["audit_entry"]["state"] == "deferred_until_match_end"
    assert body["registration"]["squad_id"] == "sq-b"


def test_transfer_validation(fresh):
    make_league(fresh)
    assert fields(move(fresh, to_squad_id=None)) == ["to_squad_id"]
    assert fields(move(fresh, to_squad_id="sq-a")) == ["to_squad_id"]
    assert fields(move(fresh, type="swap")) == ["type"]
    assert fields(move(fresh, effective_date="2026-09-31")) == ["effective_date"]
    assert fields(move(fresh, effective_date="30-09-2026")) == ["effective_date"]
    assert fields(move(fresh, type="loan")) == ["loan_end_date"]
    assert fields(move(fresh, type="loan", loan_end_date="2026-09-30")) == ["loan_end_date"]
    assert fields(move(fresh, type="loan", loan_end_date="2026-09-29")) == ["loan_end_date"]
    assert fields(move(fresh, request_id="")) == ["request_id"]
    assert fields(move(fresh, player_id=7, requested_by=None)) == ["player_id", "requested_by"]
    assert fields(move(fresh, reason=5)) == ["reason"]
    assert fields(move(fresh, type="release", to_squad_id=12)) == ["to_squad_id"]
    assert fields(fresh.post("/audit/v1/roster/transfer", json=["x"])) == ["body"]
    # validation (400) comes before existence (404)
    assert fields(move(fresh, league_id="nope", to_squad_id=None)) == ["to_squad_id"]
    assert history(fresh, "p2").json()["history"] == []


def test_not_found_order(fresh):
    make_league(fresh)
    error(move(fresh, league_id="lg-none", player_id="ghost", from_squad_id="sq-z"), 404, "LEAGUE_NOT_FOUND")
    error(move(fresh, player_id="ghost", from_squad_id="sq-z"), 404, "PLAYER_NOT_FOUND")
    e = error(move(fresh, from_squad_id="sq-z", to_squad_id="sq-y"), 404, "SQUAD_NOT_FOUND")
    assert e["details"] == {"squad_id": "sq-z"}
    e = error(move(fresh, to_squad_id="sq-y"), 404, "SQUAD_NOT_FOUND")
    assert e["details"] == {"squad_id": "sq-y"}
    # release ignores to_squad_id
    assert move(fresh, type="release", to_squad_id="sq-y", effective_date="2027-03-01").status_code == 201


def test_cross_tenant(fresh):
    squads = SETUP["squads"] + [{"squad_id": "sq-x", "club_id": "club-x", "tenant_id": "tenant-beta", "name": "X"}]
    make_league(fresh, squads=squads)
    error(move(fresh, to_squad_id="sq-x"), 409, "CROSS_TENANT_TRANSFER")
    # cross-tenant is checked before membership, window and loan rules
    error(move(fresh, from_squad_id="sq-x", effective_date="2027-01-01"), 409, "CROSS_TENANT_TRANSFER")
    # a squad configured under another tenant's league is also a cross-tenant destination
    error(move(fresh, to_squad_id="club-beta-1"), 409, "CROSS_TENANT_TRANSFER")
    # a same-tenant squad of another league does not exist in this league
    error(move(fresh, to_squad_id="club-alpha-2"), 404, "SQUAD_NOT_FOUND")


def test_not_in_squad(fresh):
    make_league(fresh)
    error(move(fresh, from_squad_id="sq-b", to_squad_id="sq-a"), 409, "NOT_IN_SQUAD")
    # a player of another league is not in this league's squads
    error(move(fresh, player_id="player-alpha-1"), 409, "NOT_IN_SQUAD")
    assert move(fresh, type="release", to_squad_id=None).status_code == 201
    error(move(fresh, request_id="tr-2", type="release", to_squad_id=None), 409, "NOT_IN_SQUAD")
    # NOT_IN_SQUAD is checked before the window
    error(move(fresh, request_id="tr-3", effective_date="2027-01-01"), 409, "NOT_IN_SQUAD")


def test_transfer_window(fresh):
    make_league(fresh)
    error(move(fresh, effective_date="2026-08-31"), 409, "TRANSFER_WINDOW_CLOSED")
    error(move(fresh, effective_date="2026-10-01"), 409, "TRANSFER_WINDOW_CLOSED")
    error(move(fresh, type="loan", effective_date="2026-10-01", loan_end_date="2026-12-01"),
          409, "TRANSFER_WINDOW_CLOSED")
    assert move(fresh, effective_date="2026-09-01").status_code == 201
    assert move(fresh, request_id="tr-2", player_id="p2", from_squad_id="sq-b", type="release",
                to_squad_id=None, effective_date="2027-06-01").status_code == 201


def test_loan_rules_and_auto_return(fresh):
    make_league(fresh)
    r = move(fresh, type="loan", effective_date="2026-09-10", loan_end_date="2026-09-20", reason="Loan out")
    assert r.status_code == 201
    body = r.json()
    assert body["registration"] == {"player_id": "p2", "league_id": "lg-r1", "squad_id": "sq-b",
                                    "parent_squad_id": "sq-a", "registration_type": "loan",
                                    "start_date": "2026-09-10", "loan_end_date": "2026-09-20", "role": "substitute"}
    assert body["audit_entry"]["action"] == "loan" and body["audit_entry"]["why"] == "Loan out"
    # on loan: from the lender is NOT_IN_SQUAD, from the borrower is PLAYER_ON_LOAN (any type)
    error(move(fresh, request_id="tr-2", effective_date="2026-09-15"), 409, "NOT_IN_SQUAD")
    error(move(fresh, request_id="tr-2", from_squad_id="sq-b", to_squad_id="sq-a", effective_date="2026-09-15"),
          409, "PLAYER_ON_LOAN")
    error(move(fresh, request_id="tr-2", from_squad_id="sq-b", type="release", to_squad_id=None,
               effective_date="2026-09-20"), 409, "PLAYER_ON_LOAN")
    during = history(fresh, "p2", "2026-09-20").json()
    assert during == {"player_id": "p2", "as_of": "2026-09-20", "league_id": "lg-r1", "current_squad_id": "sq-b",
                      "parent_squad_id": "sq-a", "history": [body["audit_entry"]]}
    before = history(fresh, "p2", "2026-09-05").json()
    assert before["current_squad_id"] == "sq-a" and before["parent_squad_id"] is None

    after = history(fresh, "p2", "2026-09-21").json()
    assert after["current_squad_id"] == "sq-a" and after["parent_squad_id"] is None
    assert len(after["history"]) == 2 and after["history"][0] == body["audit_entry"]
    assert without_when(after["history"][1]) == {
        "audit_id": "au-0002", "action": "loan_return", "player_id": "p2", "league_id": "lg-r1",
        "from_squad_id": "sq-b", "to_squad_id": "sq-a", "effective_date": "2026-09-21", "state": "active",
        "who": "system", "why": "Loan period ended"}
    # written once, never duplicated
    assert history(fresh, "p2", "2027-01-05").json()["history"] == after["history"]
    assert history(fresh, "p2").json()["history"] == after["history"]
    # the lender may move the player again after the return
    r = move(fresh, request_id="tr-3", effective_date="2026-09-25")
    assert r.status_code == 201 and r.json()["audit_entry"]["audit_id"] == "au-0003"


def test_transfer_after_loan_end_writes_the_return(fresh):
    make_league(fresh)
    assert move(fresh, type="loan", effective_date="2026-09-02", loan_end_date="2026-09-10").status_code == 201
    r = move(fresh, request_id="tr-2", effective_date="2026-09-11")
    assert r.status_code == 201 and r.json()["audit_entry"]["audit_id"] == "au-0003"
    h = history(fresh, "p2", "2026-09-30").json()
    assert [e["action"] for e in h["history"]] == ["loan", "loan_return", "transfer"]
    assert h["history"][1]["effective_date"] == "2026-09-11" and h["current_squad_id"] == "sq-b"


def test_rejected_transfer_after_loan_end_writes_nothing(fresh):
    make_league(fresh, league={"max_squad_size": 1})
    assert move(fresh, type="loan", effective_date="2026-09-02", loan_end_date="2026-09-10").status_code == 201
    players = history(fresh, "p2", "2026-09-05").json()["history"]
    # sq-b holds p2 until the 10th; on the 9th it is full, the request is rejected and nothing is written
    error(move(fresh, request_id="tr-2", player_id="p1", effective_date="2026-09-09"), 409, "SQUAD_FULL")
    assert history(fresh, "p2", "2026-09-05").json()["history"] == players


def test_squad_full_counts_loans(fresh):
    squads = SETUP["squads"] + [{"squad_id": "sq-c", "tenant_id": "tenant-alpha"}]
    players = SETUP["players"] + [
        {"player_id": "p3", "position": "df", "squad_id": "sq-c"},
        {"player_id": "b1", "position": "gk", "squad_id": "sq-b"},
        {"player_id": "b2", "position": "fw", "squad_id": "sq-b"}]
    make_league(fresh, squads=squads, players=players)
    assert move(fresh, type="loan", effective_date="2026-09-05", loan_end_date="2026-09-15").status_code == 201
    e = error(move(fresh, request_id="tr-2", player_id="p3", from_squad_id="sq-c", effective_date="2026-09-10"),
              409, "SQUAD_FULL")
    assert e["details"]["current_size"] == 3
    # the loaned player no longer counts for the lender
    assert move(fresh, request_id="tr-3", player_id="p3", from_squad_id="sq-c", to_squad_id="sq-a",
                effective_date="2026-09-10").status_code == 201
    # after the loan ends the borrower has room again
    r = move(fresh, request_id="tr-4", player_id="p3", from_squad_id="sq-a", to_squad_id="sq-b",
             effective_date="2026-09-16")
    assert r.status_code == 201
    error(move(fresh, request_id="tr-5", player_id="p1", from_squad_id="sq-a", effective_date="2026-09-16"),
          409, "SQUAD_FULL")


def test_window_checked_before_loan(fresh):
    make_league(fresh)
    assert move(fresh, type="loan", effective_date="2026-09-10", loan_end_date="2026-10-20").status_code == 201
    error(move(fresh, request_id="tr-2", from_squad_id="sq-b", to_squad_id="sq-a", effective_date="2026-10-15"),
          409, "TRANSFER_WINDOW_CLOSED")
    error(move(fresh, request_id="tr-2", from_squad_id="sq-b", type="release", to_squad_id=None,
               effective_date="2026-10-15"), 409, "PLAYER_ON_LOAN")
    h = history(fresh, "p2", "2026-10-21").json()
    assert h["current_squad_id"] == "sq-a" and [e["action"] for e in h["history"]] == ["loan", "loan_return"]


def test_min_position(fresh):
    make_league(fresh)
    e = error(move(fresh, player_id="p1"), 409, "MIN_POSITION_VIOLATION")
    assert e["details"]["position"] == "gk"
    error(move(fresh, player_id="p1", type="release", to_squad_id=None), 409, "MIN_POSITION_VIOLATION")
    error(move(fresh, player_id="p1", type="loan", loan_end_date="2026-12-31"), 409, "MIN_POSITION_VIOLATION")
    # SQUAD_FULL is checked before MIN_POSITION_VIOLATION
    make_league(fresh, league={"max_squad_size": 0})
    error(move(fresh, player_id="p1"), 409, "SQUAD_FULL")


def test_release(fresh):
    make_league(fresh)
    r = move(fresh, type="release", to_squad_id=None, effective_date="2026-12-01", reason="Released")
    assert r.status_code == 201
    body = r.json()
    assert body["registration"] is None and body["state"] == "active"
    assert body["audit_entry"]["to_squad_id"] is None and body["audit_entry"]["action"] == "release"
    h = history(fresh, "p2", "2026-12-01").json()
    assert h["current_squad_id"] is None and h["parent_squad_id"] is None
    assert history(fresh, "p2", "2026-11-30").json()["current_squad_id"] == "sq-a"


def test_history_errors_and_default_date(fresh):
    make_league(fresh)
    error(history(fresh, "ghost"), 404, "PLAYER_NOT_FOUND")
    assert fields(history(fresh, "p2", "2026-13-01")) == ["as_of"]
    assert fields(history(fresh, "ghost", "yesterday")) == ["as_of"]
    h = history(fresh, "p2").json()
    assert h["as_of"] == dt.datetime.now(dt.timezone.utc).date().isoformat()
    assert h["league_id"] == "lg-r1" and h["current_squad_id"] == "sq-a" and h["parent_squad_id"] is None


def test_baseline_mirror(fresh):
    h = history(fresh, "player-alpha-1").json()
    assert h["league_id"] == "league-alpha-1" and h["current_squad_id"] == "club-alpha-1"


@pytest.mark.parametrize("kind", ["transfer", "loan"])
def test_player_version_counts_entries(fresh, kind):
    from app.core import db
    from app.modules.roster import service

    make_league(fresh)
    with db.tx() as conn:
        assert service.player_version(conn, "p2") == 0
        assert service.player_version(conn, "p2", "lg-none") == 0
        assert service.player_version(conn, "ghost") is None
    assert move(fresh, type=kind, loan_end_date="2026-10-05" if kind == "loan" else None).status_code == 201
    with db.tx() as conn:
        assert service.player_version(conn, "p2", "lg-r1") == 1


def test_squad_members_for_club_profile(fresh):
    from app.core import db
    from app.modules.roster import service

    make_league(fresh)
    assert move(fresh, type="loan", effective_date="2026-09-10", loan_end_date="2026-09-20").status_code == 201
    on = dt.date(2026, 9, 15)
    with db.tx() as conn:
        assert service.squad_members(conn, "lg-none", "sq-a") is None
        assert [(m["player_id"], m["role"]) for m in service.squad_members(conn, "lg-r1", "sq-a", on)] == [("p1", "captain")]
        loaned = service.squad_members(conn, "lg-r1", "sq-b", on)
        assert loaned == [{"player_id": "p2", "name": "Bilal", "position": "df", "role": "substitute",
                           "shirt_number": None, "squad_id": "sq-b", "parent_squad_id": "sq-a"}]
        back = service.squad_members(conn, "lg-r1", "sq-a", as_of=dt.date(2026, 9, 21))
        assert [(m["player_id"], m["role"]) for m in back] == [("p1", "captain"), ("p2", "substitute")]
        mirrored = service.squad_members(conn, "league-alpha-1", "club-alpha-1")
        assert "player-alpha-1" in [m["player_id"] for m in mirrored]
        assert all(m["shirt_number"] is not None for m in mirrored)
