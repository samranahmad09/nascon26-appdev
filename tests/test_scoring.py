"""POST /audit/v1/events and GET /audit/v1/score/{match_id} (spec 5.9).

Sport ids and event names are never written here: seeded sports are picked by their config,
mystery sports use invented names, so the tests prove behaviour is config-driven.
"""
import hashlib
import hmac
import itertools
import json
import os

import pytest

from app.core.signing import sign
from app.modules.sports.logic import load_seed_sports

EVENTS = "/audit/v1/events"
SEEDED = load_seed_sports()


def seeded(model, **flags):
    return next(s for s in SEEDED if s["scoring_model"] == model and all(s[k] == v for k, v in flags.items()))


def kind_of(sport, **props):
    """First scoring event of the sport whose config matches props."""
    return next(k for k, v in sport["scoring_events"].items() if all(v.get(p) == x for p, x in props.items()))


DRAWS = seeded("event", draw_allowed=True)
NO_DRAWS = seeded("event", draw_allowed=False)
INNINGS = seeded("innings")
ROUNDS = seeded("round")

_ids = itertools.count(1)


def new_id(prefix="e"):
    return f"{prefix}-{next(_ids)}"


def hmac_hex(event_id, canonical_payload):
    key = os.environ["DAY1_SALT"].encode("utf-8")
    return hmac.new(key, f"{event_id}|{canonical_payload}".encode("utf-8"), hashlib.sha256).hexdigest()


def err(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()["error"]
    assert body["code"] == code, r.text
    assert isinstance(body["message"], str) and isinstance(body["details"], dict)
    return body


def fields(r):
    return err(r, 400, "VALIDATION_ERROR")["details"]["fields"]


class Match:
    def __init__(self, client, sport_id, home="h", away="a", match_id=None, league_id=None, init=True):
        self.c = client
        self.id = match_id or new_id("m")
        self.home, self.away = home, away
        self.init_id = None
        if init:
            payload = {"sport_id": sport_id, "home_team_id": home, "away_team_id": away}
            if league_id is not None:
                payload["league_id"] = league_id
            self.init_id = self.ok("match_init", payload)["event_id"]

    def body(self, type_, payload=None, event_id=None):
        return {"event_id": event_id or new_id(), "match_id": self.id, "type": type_,
                "payload": {} if payload is None else payload}

    def post(self, type_, payload=None, event_id=None):
        return self.c.post(EVENTS, json=self.body(type_, payload, event_id))

    def ok(self, type_, payload=None, event_id=None):
        r = self.post(type_, payload, event_id)
        assert r.status_code == 201, r.text
        return r.json()

    def start(self):
        return self.ok("match_start")

    def end(self):
        return self.ok("match_end")

    def score(self, team, kind):
        return self.ok("score", {"team_id": team, "event_type": kind})

    def try_score(self, team, kind):
        return self.post("score", {"team_id": team, "event_type": kind})

    def compensate(self, target, reason="correction"):
        return self.ok("compensation", {"compensates": target, "reason": reason})

    def get(self):
        r = self.c.get(f"/audit/v1/score/{self.id}")
        assert r.status_code == 200, r.text
        return r.json()


def side(team, score, fair_play=0, wickets=None, balls=None, overs=None):
    return {"team_id": team, "score": score, "wickets": wickets, "balls": balls, "overs": overs, "fair_play": fair_play}


# ---------------------------------------------------------------------------------------------

def test_event_model_flow_signatures_and_projection(fresh):
    sid, point = DRAWS["sport_id"], kind_of(DRAWS, points=1)
    log = [
        ("evt-0001", "match_init", {"sport_id": sid, "home_team_id": "t1", "away_team_id": "t2"}),
        ("evt-0002", "match_start", {}),
        ("evt-0003", "score", {"team_id": "t1", "event_type": point}),
        ("evt-0004", "score", {"team_id": "t1", "event_type": point}),
        ("evt-0005", "score", {"team_id": "t2", "event_type": point}),
        ("evt-0006", "discipline", {"team_id": "t2", "points": -1}),
        ("evt-0007", "match_end", {}),
    ]
    for n, (event_id, type_, payload) in enumerate(log, start=1):
        r = fresh.post(EVENTS, json={"event_id": event_id, "match_id": "m1", "type": type_, "payload": payload})
        assert r.status_code == 201, r.text
        assert r.json() == {"event_id": event_id, "match_id": "m1", "type": type_, "sequence": n,
                            "match_version": n, "signature": sign(event_id, payload)}
        canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert r.json()["signature"] == hmac_hex(event_id, canon)
        assert len(r.json()["signature"]) == 64

    body = fresh.get("/audit/v1/score/m1").json()
    assert body == {
        "match_id": "m1", "sport_id": sid, "league_id": None,
        "status": "completed", "outcome": "home_win", "winner_team_id": "t1", "tiebreak_winner_team_id": None,
        "home": side("t1", 2), "away": side("t2", 1, fair_play=-1),
        "version": 7,
        "events": [{"sequence": n, "event_id": e, "type": t, "payload": p, "signature": sign(e, p),
                    "compensated": False, "compensated_by": None} for n, (e, t, p) in enumerate(log, start=1)],
    }
    assert list(body) == ["match_id", "sport_id", "league_id", "status", "outcome", "winner_team_id",
                          "tiebreak_winner_team_id", "home", "away", "version", "events"]


def test_status_outcome_and_winner_while_live(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"], league_id="lg-x")
    body = m.get()
    assert (body["status"], body["outcome"], body["winner_team_id"], body["league_id"]) == ("upcoming", "pending", None, "lg-x")
    m.start()
    m.score("a", point)
    body = m.get()
    assert (body["status"], body["outcome"], body["winner_team_id"]) == ("in_progress", "pending", "a")
    assert body["home"] == side("h", 0) and body["away"] == side("a", 1)


def test_multi_point_mystery_sport(fresh):
    sport = {
        "sport_id": "zorbing", "name": "Zorbing", "scoring_model": "event",
        "periods": {"count": 3, "duration_minutes": 10},
        "scoring_events": {"bump": {"points": 2}, "roll_over": {"points": 5}},
        "draw_allowed": False, "overtime": {"policy": "golden_score"},
        "roster": {"min_squad_size": 2, "max_squad_size": 4, "required_positions": {}},
    }
    assert fresh.post("/audit/v1/sports", json=sport).status_code == 201
    m = Match(fresh, "zorbing")
    m.start()
    m.score("h", "bump")
    m.score("a", "roll_over")
    m.score("h", "bump")
    err(m.try_score("h", "slam"), 400, "UNKNOWN_EVENT_TYPE")
    body = m.get()
    assert body["home"]["score"] == 4 and body["away"]["score"] == 5
    m.end()
    body = m.get()
    assert (body["outcome"], body["winner_team_id"]) == ("away_win", "a")


def test_idempotent_replay_and_duplicate_event_id(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    start = m.body("match_start")
    first_start = fresh.post(EVENTS, json=start)
    assert first_start.status_code == 201
    score = m.body("score", {"team_id": "h", "event_type": point}, event_id="dup-1")
    first = fresh.post(EVENTS, json=score)
    assert first.status_code == 201

    again = fresh.post(EVENTS, json=score)
    assert again.status_code == 200 and again.json() == first.json()
    assert list(again.json()) == list(first.json()) == ["event_id", "match_id", "type", "sequence", "match_version",
                                                         "signature"]
    reordered = {"payload": {"event_type": point, "team_id": "h"}, "type": "score", "match_id": m.id,
                 "event_id": "dup-1", "ignored_extra": True}
    assert fresh.post(EVENTS, json=reordered).json() == first.json()
    # A replay of an accepted event returns the stored result even though the state moved on.
    replay_start = fresh.post(EVENTS, json=start)
    assert replay_start.status_code == 200 and replay_start.json() == first_start.json()
    assert m.get()["version"] == 3 and m.get()["home"]["score"] == 1

    err(fresh.post(EVENTS, json={**score, "payload": {"team_id": "a", "event_type": point}}), 409, "DUPLICATE_EVENT_ID")
    err(fresh.post(EVENTS, json={**score, "payload": {**score["payload"], "minute": 9}}), 409, "DUPLICATE_EVENT_ID")
    err(fresh.post(EVENTS, json={**score, "type": "discipline", "payload": {"team_id": "h", "points": -1}}),
        409, "DUPLICATE_EVENT_ID")
    # The idempotency key is checked before the referenced match (documented order).
    err(fresh.post(EVENTS, json={**score, "match_id": "no-such-match"}), 409, "DUPLICATE_EVENT_ID")
    # Schema errors still win over the duplicate check (400 first).
    assert fields(fresh.post(EVENTS, json={**score, "payload": {"team_id": 5, "event_type": point}})) == ["payload.team_id"]
    assert m.get()["version"] == 3

    # A rejected request is not stored, so retrying it is evaluated afresh.
    end = m.body("match_end")
    fresh.post(EVENTS, json=m.body("match_end"))
    assert fresh.post(EVENTS, json=end).status_code == 409
    assert fresh.post(EVENTS, json=end).json()["error"]["code"] == "ILLEGAL_TRANSITION"


def test_state_machine(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    err(m.try_score("h", point), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("discipline", {"team_id": "h", "points": -1}), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("tiebreak_winner", {"team_id": "h"}), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("match_end"), 409, "ILLEGAL_TRANSITION")
    assert m.start()["sequence"] == 2
    err(m.post("match_start"), 409, "ILLEGAL_TRANSITION")
    assert m.end()["sequence"] == 3
    err(m.post("match_start"), 409, "ILLEGAL_TRANSITION")
    err(m.post("match_end"), 409, "ILLEGAL_TRANSITION")
    err(m.try_score("h", point), 409, "MATCH_NOT_IN_PROGRESS")
    err(m.post("discipline", {"team_id": "a", "points": -2}), 409, "MATCH_NOT_IN_PROGRESS")
    body = m.get()
    assert (body["status"], body["outcome"], body["winner_team_id"], body["version"]) == ("completed", "draw", None, 3)


def test_missing_streams_and_existing_matches(fresh):
    for type_, payload in [("match_start", {}), ("score", {"team_id": "h", "event_type": "x"}),
                           ("discipline", {"team_id": "h", "points": 1}), ("tiebreak_winner", {"team_id": "h"}),
                           ("match_end", {}), ("compensation", {"compensates": "e-1", "reason": "x"})]:
        r = fresh.post(EVENTS, json={"event_id": new_id(), "match_id": "ghost", "type": type_, "payload": payload})
        err(r, 404, "MATCH_NOT_FOUND")
    err(fresh.get("/audit/v1/score/ghost"), 404, "MATCH_NOT_FOUND")

    init = {"sport_id": "no-such-sport", "home_team_id": "h", "away_team_id": "a"}
    err(fresh.post(EVENTS, json={"event_id": new_id(), "match_id": "ghost", "type": "match_init", "payload": init}),
        404, "SPORT_NOT_FOUND")
    m = Match(fresh, DRAWS["sport_id"])
    again = {"sport_id": DRAWS["sport_id"], "home_team_id": "x", "away_team_id": "y"}
    err(m.post("match_init", again), 409, "MATCH_EXISTS")
    err(m.post("match_init", init), 404, "SPORT_NOT_FOUND")
    assert m.get()["version"] == 1


def test_event_validation(fresh):
    point = kind_of(DRAWS, points=1)
    base = {"event_id": "v-1", "match_id": "m", "type": "score", "payload": {"team_id": "h", "event_type": point}}

    def post(body):
        return fresh.post(EVENTS, json=body)

    assert fields(post([1, 2])) == ["body"]
    assert fields(fresh.post(EVENTS, content=b"{nope", headers={"content-type": "application/json"})) == ["body"]
    assert fields(fresh.post(EVENTS)) == ["body"]
    assert fields(post({})) == ["event_id", "match_id", "type", "payload"]
    assert fields(post({**base, "event_id": "bad|id"})) == ["event_id"]
    assert fields(post({**base, "event_id": "x" * 65})) == ["event_id"]
    assert fields(post({**base, "match_id": 7})) == ["match_id"]
    assert fields(post({**base, "type": "kickoff"})) == ["type"]
    assert fields(post({**base, "type": None})) == ["type"]
    assert fields(post({**base, "payload": []})) == ["payload"]
    assert fields(post({**base, "payload": None})) == ["payload"]
    assert fields(post({**base, "payload": {"event_type": point}})) == ["payload.team_id"]
    assert fields(post({**base, "payload": {"team_id": "h", "event_type": 3}})) == ["payload.event_type"]
    for bad_points in ("-1", True, -1.5, None):
        assert fields(post({**base, "type": "discipline", "payload": {"team_id": "h", "points": bad_points}})) == ["payload.points"]
    assert fields(post({**base, "type": "tiebreak_winner", "payload": {}})) == ["payload.team_id"]
    assert fields(post({**base, "type": "compensation", "payload": {"compensates": "e-1"}})) == ["payload.reason"]
    assert fields(post({**base, "type": "compensation", "payload": {"reason": "x"}})) == ["payload.compensates"]
    assert fields(post({**base, "type": "match_start", "payload": "now"})) == ["payload"]
    bad_init = {"sport_id": "", "home_team_id": "h", "away_team_id": "h", "league_id": "bad id"}
    assert fields(post({**base, "type": "match_init", "payload": bad_init})) == \
        ["payload.sport_id", "payload.league_id", "payload.away_team_id"]
    assert fields(post({**base, "type": "match_init", "payload": {"sport_id": DRAWS["sport_id"]}})) == \
        ["payload.home_team_id", "payload.away_team_id"]
    surrogate = b'{"event_id":"v-2","match_id":"m","type":"compensation","payload":{"compensates":"e-1","reason":"\\ud800"}}'
    assert fields(fresh.post(EVENTS, content=surrogate, headers={"content-type": "application/json"})) == ["payload"]


def test_team_and_event_type_checks_come_before_state(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    # Upcoming match: 400s still win over 409 MATCH_NOT_IN_PROGRESS.
    assert fields(m.try_score("stranger", point)) == ["payload.team_id"]
    assert fields(m.try_score("stranger", "no_such_event")) == ["payload.team_id"]
    err(m.try_score("h", "no_such_event"), 400, "UNKNOWN_EVENT_TYPE")
    assert fields(m.post("discipline", {"team_id": "stranger", "points": -1})) == ["payload.team_id"]
    assert fields(m.post("tiebreak_winner", {"team_id": "stranger"})) == ["payload.team_id"]
    m.start()
    err(m.try_score("h", "no_such_event"), 400, "UNKNOWN_EVENT_TYPE")
    assert m.get()["version"] == 2


def test_empty_payload_types_and_signed_payload_is_the_request_payload(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    r = fresh.post(EVENTS, json={"event_id": "no-payload", "match_id": m.id, "type": "match_start"})
    assert r.status_code == 201 and r.json()["signature"] == sign("no-payload", {})
    extra = {"team_id": "h", "event_type": point, "minute": 12, "note": "Überprüfung ✓", "meta": {"z": [3, 1], "a": None}}
    r = fresh.post(EVENTS, json={"event_id": "rich", "match_id": m.id, "type": "score", "payload": extra})
    assert r.status_code == 201 and r.json()["signature"] == sign("rich", extra)
    events = m.get()["events"]
    assert events[1]["payload"] == {} and events[2]["payload"] == extra
    assert events[2]["signature"] == sign("rich", events[2]["payload"])


def test_compensation_after_completion_flips_outcome(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"], "t1", "t2")
    m.start()
    m.score("t1", point)
    away_point = m.score("t2", point)
    late_point = m.score("t1", point)
    m.end()
    before = m.get()
    assert (before["outcome"], before["winner_team_id"]) == ("home_win", "t1")

    comp = m.compensate(late_point["event_id"], "offside")
    assert (comp["sequence"], comp["match_version"]) == (7, 7)
    assert comp["signature"] == sign(comp["event_id"], {"compensates": late_point["event_id"], "reason": "offside"})
    after = m.get()
    assert (after["status"], after["outcome"], after["winner_team_id"]) == ("completed", "draw", None)
    assert after["home"]["score"] == 1 and after["version"] == 7
    target, compensation = after["events"][4], after["events"][6]
    assert target == {**before["events"][4], "compensated": True, "compensated_by": comp["event_id"]}
    assert compensation == {"sequence": 7, "event_id": comp["event_id"], "type": "compensation",
                            "payload": {"compensates": late_point["event_id"], "reason": "offside"},
                            "signature": comp["signature"], "compensated": False, "compensated_by": None}
    assert after["events"][:4] == before["events"][:4]

    m.compensate(away_point["event_id"])
    flipped = m.get()
    assert (flipped["outcome"], flipped["winner_team_id"]) == ("home_win", "t1")
    assert (flipped["home"]["score"], flipped["away"]["score"]) == (1, 0)


def test_tie_not_allowed_tiebreak_winner_and_undecided(fresh):
    two, three = kind_of(NO_DRAWS, points=2), kind_of(NO_DRAWS, points=3)
    m = Match(fresh, NO_DRAWS["sport_id"])
    m.start()
    err(m.post("match_end"), 409, "TIE_NOT_ALLOWED")
    m.score("h", two)
    m.score("a", two)
    err(m.post("match_end"), 409, "TIE_NOT_ALLOWED")
    first_tb = m.ok("tiebreak_winner", {"team_id": "h"})
    tb = m.ok("tiebreak_winner", {"team_id": "a"})
    live = m.get()
    assert (live["status"], live["outcome"], live["winner_team_id"], live["tiebreak_winner_team_id"]) == \
        ("in_progress", "pending", "a", "a")
    m.end()
    done = m.get()
    assert (done["outcome"], done["winner_team_id"], done["tiebreak_winner_team_id"]) == ("away_win", "a", "a")
    assert done["home"]["score"] == done["away"]["score"] == 2

    m.compensate(tb["event_id"])
    reverted = m.get()
    assert (reverted["outcome"], reverted["winner_team_id"], reverted["tiebreak_winner_team_id"]) == ("home_win", "h", "h")
    m.compensate(first_tb["event_id"])
    undecided = m.get()
    assert (undecided["status"], undecided["outcome"], undecided["winner_team_id"], undecided["tiebreak_winner_team_id"]) == \
        ("completed", "undecided", None, None)

    # A decided game becomes undecided when a compensation levels it after completion.
    g = Match(fresh, NO_DRAWS["sport_id"])
    g.start()
    g.score("h", three)
    g.score("a", three)
    winner = g.score("h", two)
    g.end()
    assert g.get()["outcome"] == "home_win"
    g.compensate(winner["event_id"])
    assert (g.get()["outcome"], g.get()["winner_team_id"]) == ("undecided", None)

    # Compensated scores do not count for the level check.
    k = Match(fresh, NO_DRAWS["sport_id"])
    k.start()
    k.score("h", three)
    wrong = k.score("a", three)
    k.compensate(wrong["event_id"])
    assert k.end()["sequence"] == 6
    assert k.get()["outcome"] == "home_win"


def test_tiebreak_winner_decides_level_match_even_when_draws_allowed(fresh):
    m = Match(fresh, DRAWS["sport_id"])
    m.start()
    m.ok("tiebreak_winner", {"team_id": "h"})
    m.end()
    body = m.get()
    assert (body["outcome"], body["winner_team_id"], body["tiebreak_winner_team_id"]) == ("home_win", "h", "h")
    assert body["home"]["score"] == body["away"]["score"] == 0


def test_compensation_rules(fresh):
    point = kind_of(DRAWS, points=1)
    m = Match(fresh, DRAWS["sport_id"])
    start = m.start()
    scored = m.score("h", point)
    card = m.ok("discipline", {"team_id": "a", "points": -3})
    other = Match(fresh, DRAWS["sport_id"])
    other.start()
    foreign = other.score("h", point)

    err(m.post("compensation", {"compensates": "nope", "reason": "x"}), 404, "EVENT_NOT_FOUND")
    err(m.post("compensation", {"compensates": start["event_id"], "reason": "x"}), 409, "INVALID_COMPENSATION")
    err(m.post("compensation", {"compensates": m.init_id, "reason": "x"}), 409, "INVALID_COMPENSATION")
    err(m.post("compensation", {"compensates": foreign["event_id"], "reason": "x"}), 409, "INVALID_COMPENSATION")
    comp = m.compensate(scored["event_id"])
    err(m.post("compensation", {"compensates": scored["event_id"], "reason": "x"}), 409, "INVALID_COMPENSATION")
    err(m.post("compensation", {"compensates": comp["event_id"], "reason": "x"}), 409, "INVALID_COMPENSATION")
    assert m.get()["away"]["fair_play"] == -3
    m.compensate(card["event_id"])
    body = m.get()
    assert body["away"]["fair_play"] == 0 and body["home"]["score"] == 0 and body["version"] == 6
    assert [e["compensated"] for e in body["events"]] == [False, False, True, True, False, False]
    assert other.get()["home"]["score"] == 1


def test_innings_projection_from_config(fresh):
    sport = INNINGS
    per_over = sport["periods"]["balls_per_over"]
    single = kind_of(sport, runs=1, legal_ball=True, wicket=False)
    extra = kind_of(sport, legal_ball=False)
    out = kind_of(sport, wicket=True)
    big = max((k for k, v in sport["scoring_events"].items() if v["legal_ball"]),
              key=lambda k: sport["scoring_events"][k]["runs"])
    runs = {k: v["runs"] for k, v in sport["scoring_events"].items()}

    m = Match(fresh, sport["sport_id"])
    m.start()
    for _ in range(per_over + 2):
        m.score("h", single)
    m.score("h", extra)
    m.score("h", out)
    m.score("a", big)
    body = m.get()
    legal = per_over + 3
    assert body["home"] == side("h", (per_over + 2) * runs[single] + runs[extra] + runs[out], wickets=1, balls=legal,
                                overs=f"{legal // per_over}.{legal % per_over}")
    assert body["away"] == side("a", runs[big], wickets=0, balls=1, overs="0.1")
    assert body["winner_team_id"] == "h"


TINY_INNINGS = {
    "sport_id": "tiny-bat", "name": "Tiny Bat", "scoring_model": "innings",
    "periods": {"innings_per_team": 2, "overs_per_innings": 1, "balls_per_over": 2, "wickets_per_innings": 1},
    "scoring_events": {"hit": {"runs": 2, "legal_ball": True, "wicket": False},
                       "extra": {"runs": 1, "legal_ball": False, "wicket": False},
                       "gone": {"runs": 0, "legal_ball": True, "wicket": True}},
    "draw_allowed": False, "overtime": {"policy": "super_over"},
    "roster": {"min_squad_size": 2, "max_squad_size": 5, "required_positions": {}},
}


def test_innings_complete_by_wickets_and_balls(fresh):
    assert fresh.post("/audit/v1/sports", json=TINY_INNINGS).status_code == 201
    m = Match(fresh, "tiny-bat")
    m.start()
    m.score("h", "gone")
    second = m.score("h", "gone")
    err(m.try_score("h", "hit"), 409, "INNINGS_COMPLETE")
    err(m.try_score("h", "extra"), 409, "INNINGS_COMPLETE")
    m.compensate(second["event_id"])
    m.score("h", "hit")

    m.score("a", "extra")
    for _ in range(4):
        m.score("a", "hit")
    err(m.try_score("a", "extra"), 409, "INNINGS_COMPLETE")
    err(m.try_score("a", "hit"), 409, "INNINGS_COMPLETE")
    body = m.get()
    assert body["home"] == side("h", 2, wickets=1, balls=2, overs="1.0")
    assert body["away"] == side("a", 9, wickets=0, balls=4, overs="2.0")
    m.end()
    assert (m.get()["outcome"], m.get()["winner_team_id"]) == ("away_win", "a")


def test_round_model_series_decided(fresh):
    sport = ROUNDS
    win = kind_of(sport, rounds=1)
    needed = (sport["periods"]["best_of"] + 1) // 2
    m = Match(fresh, sport["sport_id"])
    m.start()
    m.score("a", win)
    last = None
    for _ in range(needed):
        last = m.score("h", win)
    err(m.try_score("a", win), 409, "SERIES_ALREADY_DECIDED")
    err(m.try_score("h", win), 409, "SERIES_ALREADY_DECIDED")
    body = m.get()
    assert body["home"] == side("h", needed) and body["away"] == side("a", 1)
    m.compensate(last["event_id"])
    for _ in range(needed - 1):
        m.score("a", win)
    err(m.try_score("h", win), 409, "SERIES_ALREADY_DECIDED")
    m.end()
    body = m.get()
    assert (body["home"]["score"], body["away"]["score"]) == (needed - 1, needed)
    assert (body["outcome"], body["winner_team_id"]) == ("away_win", "a")


def test_round_mystery_sport_multi_round_events(fresh):
    sport = {
        "sport_id": "speed-chess", "name": "Speed Chess", "scoring_model": "round", "periods": {"best_of": 5},
        "scoring_events": {"double_game": {"rounds": 2}, "game": {"rounds": 1}},
        "draw_allowed": False, "overtime": {"policy": "none"},
        "roster": {"min_squad_size": 1, "max_squad_size": 3, "required_positions": {}},
    }
    assert fresh.post("/audit/v1/sports", json=sport).status_code == 201
    m = Match(fresh, "speed-chess")
    m.start()
    m.score("h", "double_game")
    m.score("a", "game")
    m.score("h", "game")
    err(m.try_score("a", "double_game"), 409, "SERIES_ALREADY_DECIDED")
    m.end()
    body = m.get()
    assert (body["home"]["score"], body["away"]["score"], body["outcome"]) == (3, 1, "home_win")


def test_reset_wipes_matches(fresh):
    m = Match(fresh, DRAWS["sport_id"], match_id="wipe-me")
    m.start()
    assert fresh.post("/audit/v1/reset").status_code == 200
    err(fresh.get("/audit/v1/score/wipe-me"), 404, "MATCH_NOT_FOUND")
    again = Match(fresh, DRAWS["sport_id"], match_id="wipe-me")
    assert again.get()["version"] == 1


@pytest.mark.parametrize("model", ["event", "innings", "round"])
def test_every_seeded_model_runs_a_full_match(fresh, model):
    sport = seeded(model)
    kind = next(iter(sport["scoring_events"]))
    m = Match(fresh, sport["sport_id"])
    m.start()
    m.score("h", kind)
    m.score("h", kind)
    m.ok("tiebreak_winner", {"team_id": "h"})
    m.end()
    body = m.get()
    assert body["status"] == "completed" and body["version"] == 6
    assert body["outcome"] == "home_win" and body["winner_team_id"] == "h"
