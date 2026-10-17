import pytest

pytest.importorskip("app.modules.scoring.service")

from app.core.signing import sign  # noqa: E402
from tests.test_offline import act, brief, sync  # noqa: E402
from tests.test_roster import MOVE, make_league  # noqa: E402
from tests.test_sports import KABADDI  # noqa: E402

POINT = {"team_id": "t1", "event_type": "raid_point"}


def event(event_id, match_id, kind, payload):
    return {"event_id": event_id, "match_id": match_id, "type": kind, "payload": payload}


def score(action_id, event_id, base, match_id="m-off", payload=POINT, queued_at="2026-10-17T11:00:00Z"):
    return act(action_id, event(event_id, match_id, "score", payload), base=base, queued_at=queued_at,
               kind="score_event")


def open_match(client, match_id="m-off", start=True):
    if client.post("/audit/v1/sports", json=KABADDI).status_code not in (201, 409):
        raise AssertionError("sport setup failed")
    init = {"sport_id": "kabaddi", "home_team_id": "t1", "away_team_id": "t2"}
    assert client.post("/audit/v1/events", json=event("init-" + match_id, match_id, "match_init", init)).status_code == 201
    if start:
        assert client.post("/audit/v1/events", json=event("start-" + match_id, match_id, "match_start", {})).status_code == 201


def test_score_event_applied_rebased_and_signed(fresh):
    open_match(fresh)
    body = sync(fresh, [score("a1", "evt-9", base=2)])
    assert body["results"] == [{"action_id": "a1", "status": "applied", "resolution": "appended", "error_code": None,
                                "processed_order": 1, "server_version": 3, "signature": sign("evt-9", POINT)}]
    body = sync(fresh, [score("a2", "evt-10", base=2)])
    assert brief(body["results"][0]) == ("applied", "rebased_append", None, 4)
    assert body["results"][0]["signature"] == sign("evt-10", POINT)
    assert fresh.get("/audit/v1/score/m-off").json()["version"] == 4


def test_score_event_conflicts(fresh):
    open_match(fresh)
    open_match(fresh, "m-idle", start=False)
    assert fresh.post("/audit/v1/events", json=event("evt-1", "m-off", "score", POINT)).status_code == 201
    actions = [
        score("dup", "evt-1", base=3),
        score("clash", "evt-1", base=3, payload={"team_id": "t2", "event_type": "raid_point"}),
        score("ahead", "evt-2", base=7),
        score("ghost", "evt-3", base=0, match_id="m-none"),
        score("idle", "evt-4", base=1, match_id="m-idle"),
        score("type", "evt-5", base=3, payload={"team_id": "t1", "event_type": "touchdown"}),
        act("schema", {"event_id": "evt-6", "type": "score", "payload": POINT}, kind="score_event"),
        score("ok", "evt-7", base=3),
    ]
    body = sync(fresh, actions)
    got = {r["action_id"]: brief(r) for r in body["results"]}
    assert got == {
        "dup": ("duplicate", "skipped_duplicate", None, 3),
        "clash": ("rejected", "server_wins", "DUPLICATE_EVENT_ID", 3),
        "ahead": ("rejected", "server_wins", "INVALID_BASE_VERSION", 3),
        "ghost": ("rejected", "server_wins", "MATCH_NOT_FOUND", 0),
        "idle": ("rejected", "server_wins", "MATCH_NOT_IN_PROGRESS", 1),
        "type": ("rejected", "server_wins", "UNKNOWN_EVENT_TYPE", 3),
        "schema": ("rejected", "server_wins", "VALIDATION_ERROR", 0),
        "ok": ("applied", "appended", None, 4),
    }
    assert body["summary"] == {"applied": 1, "rejected": 6, "duplicate": 1}
    # the stale-ahead event was rolled back
    assert fresh.get("/audit/v1/score/m-off").json()["version"] == 4


def test_mixed_queue_in_queued_at_order_and_resend(fresh):
    open_match(fresh)
    make_league(fresh)
    actions = [act("r1", MOVE, base=0, queued_at="2026-10-17T11:01:00Z"),
               score("s1", "evt-9", base=2, queued_at="2026-10-17T11:00:00Z")]
    body = sync(fresh, actions)
    assert [(r["action_id"], r["status"], r["processed_order"], r["server_version"]) for r in body["results"]] == [
        ("r1", "applied", 2, 1), ("s1", "applied", 1, 3)]
    assert body["results"][0]["signature"] is None and body["results"][1]["signature"] == sign("evt-9", POINT)
    again = sync(fresh, actions)
    assert [brief(r) for r in again["results"]] == [("duplicate", "skipped_duplicate", None, 1),
                                                    ("duplicate", "skipped_duplicate", None, 3)]
    assert [r["signature"] for r in again["results"]] == [None, None]
    assert fresh.get("/audit/v1/score/m-off").json()["version"] == 3
