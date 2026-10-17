from tests.test_roster import MOVE, history, make_league


def act(action_id, payload, base=0, queued_at="2026-10-17T11:00:00Z", kind="roster_transfer"):
    return {"action_id": action_id, "type": kind, "queued_at": queued_at, "base_version": base, "payload": payload}


def sync(client, actions, device="dev-1"):
    r = client.post("/audit/v1/offline-sync", json={"device_id": device, "actions": actions})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_id"] == device and [x["action_id"] for x in body["results"]] == [a["action_id"] for a in actions]
    return body


def brief(result):
    return result["status"], result["resolution"], result["error_code"], result["server_version"]


def bad_fields(client, body):
    r = client.post("/audit/v1/offline-sync", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    return r.json()["error"]["details"]["fields"]


def test_envelope_validation(fresh):
    good = act("a1", MOVE)
    assert bad_fields(fresh, {"actions": [good]}) == ["device_id"]
    assert bad_fields(fresh, {"device_id": "dev 1", "actions": [good]}) == ["device_id"]
    assert bad_fields(fresh, {"device_id": "dev-1", "actions": []}) == ["actions"]
    assert bad_fields(fresh, {"device_id": "dev-1", "actions": [good] * 101}) == ["actions"]
    assert bad_fields(fresh, {"device_id": "dev-1", "actions": {"a1": good}}) == ["actions"]
    for broken in ({**good, "type": "chat"}, {**good, "queued_at": "2026-10-17 11:00:00"},
                   {**good, "queued_at": "2026-02-30T11:00:00Z"}, {**good, "base_version": -1},
                   {**good, "base_version": True}, {**good, "base_version": "3"}, {**good, "payload": [MOVE]},
                   {**good, "action_id": None}, "a1"):
        assert bad_fields(fresh, {"device_id": "dev-1", "actions": [broken]}) == ["actions"], broken
    assert bad_fields(fresh, ["dev-1"]) == ["body"]
    # nothing was recorded: the same action applies afterwards
    make_league(fresh)
    assert sync(fresh, [good])["results"][0]["status"] == "applied"


def test_roster_applied_then_rebased(fresh):
    make_league(fresh)
    body = sync(fresh, [act("a1", MOVE)])
    assert body["results"] == [{"action_id": "a1", "status": "applied", "resolution": "appended", "error_code": None,
                                "processed_order": 1, "server_version": 1, "signature": None}]
    assert body["summary"] == {"applied": 1, "rejected": 0, "duplicate": 0}
    back = {**MOVE, "request_id": "tr-002", "from_squad_id": "sq-b", "to_squad_id": "sq-a"}
    body = sync(fresh, [act("a2", back, base=0)])
    assert brief(body["results"][0]) == ("applied", "rebased_append", None, 2)
    h = history(fresh, "p2", "2026-10-01").json()
    assert h["current_squad_id"] == "sq-a" and [e["who"] for e in h["history"]] == ["user-77", "user-77"]


def test_roster_conflicts(fresh):
    make_league(fresh, league={"max_squad_size": 2},
                players=[{"player_id": "p1", "position": "gk", "squad_id": "sq-a"},
                         {"player_id": "p2", "position": "df", "squad_id": "sq-a"},
                         {"player_id": "b1", "position": "gk", "squad_id": "sq-b"},
                         {"player_id": "b2", "position": "df", "squad_id": "sq-b"}])
    actions = [
        act("full", MOVE),
        act("ahead", {**MOVE, "request_id": "tr-2", "type": "release", "to_squad_id": None}, base=3),
        act("ghost", {**MOVE, "request_id": "tr-3", "player_id": "ghost"}, base=9),
        act("league", {**MOVE, "request_id": "tr-4", "league_id": "lg-x"}),
        act("squad", {**MOVE, "request_id": "tr-5", "to_squad_id": "sq-x"}),
        act("schema", {**MOVE, "request_id": "tr-6", "effective_date": "soon"}),
        act("window", {**MOVE, "request_id": "tr-7", "effective_date": "2026-11-01"}),
        act("ok", {**MOVE, "request_id": "tr-8", "type": "release", "to_squad_id": None}),
    ]
    body = sync(fresh, actions)
    got = {r["action_id"]: brief(r) for r in body["results"]}
    assert got == {
        "full": ("rejected", "server_wins", "SQUAD_FULL", 0),
        "ahead": ("rejected", "server_wins", "INVALID_BASE_VERSION", 0),
        "ghost": ("rejected", "server_wins", "PLAYER_NOT_FOUND", 0),
        "league": ("rejected", "server_wins", "LEAGUE_NOT_FOUND", 0),
        "squad": ("rejected", "server_wins", "SQUAD_NOT_FOUND", 0),
        "schema": ("rejected", "server_wins", "VALIDATION_ERROR", 0),
        "window": ("rejected", "server_wins", "TRANSFER_WINDOW_CLOSED", 0),
        "ok": ("applied", "appended", None, 1),
    }
    assert [r["processed_order"] for r in body["results"]] == list(range(1, 9))
    assert all(r["signature"] is None for r in body["results"])
    assert body["summary"] == {"applied": 1, "rejected": 7, "duplicate": 0}
    # a rejected rule reports the unchanged current version
    again = sync(fresh, [act("after", {**MOVE, "request_id": "tr-9"}, base=1)])
    assert brief(again["results"][0]) == ("rejected", "server_wins", "NOT_IN_SQUAD", 1)


def test_processing_order_and_request_order(fresh):
    make_league(fresh)
    there = act("a-there", MOVE, queued_at="2026-10-17T11:00:00Z")
    back = act("a-back", {**MOVE, "request_id": "tr-2", "from_squad_id": "sq-b", "to_squad_id": "sq-a"},
               base=1, queued_at="2026-10-17T11:05:00Z")
    body = sync(fresh, [back, there])
    assert [(r["action_id"], r["processed_order"], r["resolution"], r["server_version"]) for r in body["results"]] == [
        ("a-back", 2, "appended", 2), ("a-there", 1, "appended", 1)]


def test_ties_keep_array_order(fresh):
    make_league(fresh)
    first = act("x1", MOVE, queued_at="2026-10-17T11:00:00Z")
    second = act("x2", {**MOVE, "request_id": "tr-2", "from_squad_id": "sq-b", "to_squad_id": "sq-a"},
                 queued_at="2026-10-17T11:00:00Z")
    body = sync(fresh, [first, second])
    assert [brief(r) for r in body["results"]] == [("applied", "appended", None, 1),
                                                   ("applied", "rebased_append", None, 2)]
    assert [r["processed_order"] for r in body["results"]] == [1, 2]


def test_resend_is_duplicate_and_changes_nothing(fresh):
    make_league(fresh)
    actions = [act("a1", MOVE), act("a2", {**MOVE, "request_id": "tr-2", "player_id": "p1"})]
    first = sync(fresh, actions)
    assert [r["status"] for r in first["results"]] == ["applied", "rejected"]
    before = history(fresh, "p2", "2026-10-01").json()
    again = sync(fresh, actions)
    assert [brief(r) for r in again["results"]] == [("duplicate", "skipped_duplicate", None, 1),
                                                    ("duplicate", "skipped_duplicate", None, 0)]
    assert again["summary"] == {"applied": 0, "rejected": 0, "duplicate": 2}
    assert history(fresh, "p2", "2026-10-01").json() == before
    # another device with the same action ids is processed on its own
    other = sync(fresh, [act("a1", {**MOVE, "request_id": "tr-3", "from_squad_id": "sq-b", "to_squad_id": "sq-a"},
                             base=1)], device="dev-2")
    assert brief(other["results"][0]) == ("applied", "appended", None, 2)


def test_same_request_or_action_twice_is_duplicate(fresh):
    make_league(fresh)
    assert fresh.post("/audit/v1/roster/transfer", json=MOVE).status_code == 201
    body = sync(fresh, [act("a1", MOVE, base=1), act("a1", {**MOVE, "request_id": "tr-2"}, base=1)])
    assert [brief(r) for r in body["results"]] == [("duplicate", "skipped_duplicate", None, 1),
                                                   ("duplicate", "skipped_duplicate", None, 1)]
    assert len(history(fresh, "p2", "2026-10-01").json()["history"]) == 1


def test_reset_forgets_processed_actions(fresh):
    make_league(fresh)
    assert sync(fresh, [act("a1", MOVE)])["results"][0]["status"] == "applied"
    fresh.post("/audit/v1/reset")
    make_league(fresh)
    assert sync(fresh, [act("a1", MOVE)])["results"][0]["status"] == "applied"
