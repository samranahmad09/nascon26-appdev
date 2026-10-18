"""Day 2 match status transitions on the event log: postponed / cancelled / abandoned (Part II edge case 2)."""
import pytest

from app.core import db, locks, realtime
from app.core.signing import sign
from app.modules.scoring import service

from .test_scoring import DRAWS, EVENTS, Match, err, fields, kind_of, new_id

STATUS_OF = {
    "match_postpone": "postponed", "postpone": "postponed", "postponed": "postponed", "match_postponed": "postponed",
    "match_cancel": "cancelled", "cancel": "cancelled", "cancelled": "cancelled", "match_cancelled": "cancelled",
    "canceled": "cancelled", "match_canceled": "cancelled",
    "match_abandon": "abandoned", "abandon": "abandoned", "abandoned": "abandoned", "match_abandoned": "abandoned",
}
TRANSITIONS = ("match_postpone", "match_cancel", "match_abandon")


def test_postponed_match_rejects_play_and_can_restart(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    m.start()
    m.score("h", point)
    postponed = m.ok("match_postpone", {"reason": "rain"})
    assert (postponed["type"], postponed["sequence"], postponed["match_version"]) == ("match_postpone", 4, 4)
    assert postponed["signature"] == sign(postponed["event_id"], {"reason": "rain"})
    body = m.get()
    assert (body["status"], body["outcome"], body["version"], body["home"]["score"]) == ("postponed", "pending", 4, 1)

    err(m.try_score("h", point), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("discipline", {"team_id": "a", "points": -1}), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("tiebreak_winner", {"team_id": "h"}), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("match_end"), 409, "ILLEGAL_TRANSITION")
    for kind in TRANSITIONS:
        err(m.post(kind), 409, "ILLEGAL_TRANSITION")

    assert m.start()["sequence"] == 5
    m.score("a", point)
    m.score("h", point)
    m.end()
    body = m.get()
    assert (body["status"], body["outcome"], body["winner_team_id"]) == ("completed", "home_win", "h")
    assert [e["type"] for e in body["events"]] == ["match_init", "match_start", "score", "match_postpone",
                                                   "match_start", "score", "score", "match_end"]
    for kind in TRANSITIONS:
        err(m.post(kind), 409, "ILLEGAL_TRANSITION")


def test_upcoming_fixtures_can_be_postponed_cancelled_or_abandoned(fresh):
    league = {"league_id": "lg-st", "sport_id": DRAWS["sport_id"], "teams": ["t1", "t2", "t3", "t4"],
              "fixtures": [{"match_id": "f1", "home": "t1", "away": "t2"}, {"match_id": "f2", "home": "t3", "away": "t4"},
                           {"match_id": "f3", "home": "t1", "away": "t3"}, {"match_id": "f4", "home": "t2", "away": "t4"}],
              "qualification": {"spots": 2}}
    assert fresh.post("/audit/v1/leagues", json=league).status_code == 201
    fixtures = {mid: Match(fresh, None, home, away, match_id=mid, init=False)
                for mid, home, away in [("f1", "t1", "t2"), ("f2", "t3", "t4"), ("f3", "t1", "t3")]}
    for (mid, m), kind in zip(fixtures.items(), TRANSITIONS):
        assert m.ok(kind)["sequence"] == 1
    with locks.GLOBAL, db.tx() as conn:
        statuses = [(p["match_id"], p["status"], p["outcome"]) for p in service.league_projections(conn, "lg-st")]
        assert service.match_version(conn, "f1") == 1
    assert statuses == [("f1", "postponed", "pending"), ("f2", "cancelled", "pending"),
                        ("f3", "abandoned", "pending"), ("f4", "upcoming", "pending")]
    assert fixtures["f1"].start()["sequence"] == 2
    assert fixtures["f1"].get()["status"] == "in_progress"
    for mid in ("f2", "f3"):
        err(fixtures[mid].post("match_start"), 409, "ILLEGAL_TRANSITION")


def test_cancelled_and_abandoned_are_final_but_compensation_still_applies(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    m.start()
    scored = m.score("h", point)
    abandon = m.ok("match_abandon", {"reason": "pitch invasion"})
    body = m.get()
    assert (body["status"], body["outcome"], body["home"]["score"]) == ("abandoned", "pending", 1)
    err(m.post("match_start"), 409, "ILLEGAL_TRANSITION")
    err(m.post("match_end"), 409, "ILLEGAL_TRANSITION")
    err(m.try_score("a", point), 409, "MATCH_NOT_IN_PROGRESS")
    # Day 1 rule kept: a compensation is allowed in any status after init; status events are not compensable.
    m.compensate(scored["event_id"])
    assert (m.get()["status"], m.get()["home"]["score"]) == ("abandoned", 0)
    err(m.post("compensation", {"compensates": abandon["event_id"], "reason": "x"}), 409, "INVALID_COMPENSATION")

    c = Match(fresh, DRAWS["sport_id"])
    c.ok("match_cancel")
    err(c.post("match_start"), 409, "ILLEGAL_TRANSITION")
    assert (c.get()["status"], c.get()["version"]) == ("cancelled", 2)

    done = Match(fresh, DRAWS["sport_id"])
    done.start()
    done.end()
    for kind in TRANSITIONS:
        err(done.post(kind), 409, "ILLEGAL_TRANSITION")
    assert done.get()["status"] == "completed"


@pytest.mark.parametrize("alias, status", sorted(STATUS_OF.items()))
def test_type_spellings(fresh, alias, status):
    m = Match(fresh, DRAWS["sport_id"])
    m.start()
    event_id = f"st-{alias}"
    r = fresh.post(EVENTS, json={"event_id": event_id, "match_id": m.id, "type": alias})
    assert r.status_code == 201, r.text
    assert r.json() == {"event_id": event_id, "match_id": m.id, "type": alias, "sequence": 3, "match_version": 3,
                        "signature": sign(event_id, {})}
    body = m.get()
    assert (body["status"], body["outcome"]) == (status, "pending")
    assert (body["events"][-1]["type"], body["events"][-1]["payload"]) == (alias, {})


def test_status_event_idempotency_and_validation(fresh):
    m = Match(fresh, DRAWS["sport_id"])
    body = m.body("postponed", {"reason": "floodlights"})
    first = fresh.post(EVENTS, json=body)
    assert first.status_code == 201
    again = fresh.post(EVENTS, json=body)
    assert again.status_code == 200 and again.json() == first.json()
    err(fresh.post(EVENTS, json={**body, "type": "match_postpone"}), 409, "DUPLICATE_EVENT_ID")
    err(fresh.post(EVENTS, json={**body, "payload": {"reason": "rain"}}), 409, "DUPLICATE_EVENT_ID")
    assert fields(m.post("match_cancel", "now")) == ["payload"]
    assert fields(fresh.post(EVENTS, json={**body, "event_id": new_id(), "type": "suspend"})) == ["type"]
    err(fresh.post(EVENTS, json={"event_id": new_id(), "match_id": "ghost", "type": "match_cancel"}), 404, "MATCH_NOT_FOUND")
    assert m.get()["version"] == 2


def test_route_announces_after_commit_and_contract_function_inside_the_transaction(fresh, monkeypatch):
    seen = []

    def probe(topic, message):
        match = message["match"]
        committed = db.fetchone("SELECT count(*) AS n FROM scoring_events WHERE match_id = %s", (match["match_id"],))
        seen.append((topic, match["status"], match["version"], committed["n"]))

    monkeypatch.setattr(realtime, "publish", probe)
    m = Match(fresh, DRAWS["sport_id"])
    m.start()
    postpone = m.body("match_postpone")
    assert fresh.post(EVENTS, json=postpone).status_code == 201
    assert fresh.post(EVENTS, json=postpone).status_code == 200
    topic = f"match:{m.id}"
    assert seen == [(topic, "upcoming", 1, 1), (topic, "in_progress", 2, 2), (topic, "postponed", 3, 3)]

    with locks.GLOBAL, db.tx() as conn:
        status, _ = service.append_event(conn, m.body("match_start"))
    assert status == 201 and seen[-1] == (topic, "in_progress", 4, 3)
    assert m.get()["version"] == 4
