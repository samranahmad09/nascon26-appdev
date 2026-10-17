import random
from collections import Counter

import pytest

URL = "/audit/v1/fixtures"


def rr(client, n, **params):
    teams = [f"t{i}" for i in range(1, n + 1)]
    r = client.post(URL, json={"format": "round_robin", "teams": teams, "params": params})
    assert r.status_code == 200, r.text
    return teams, r.json()


def fields(client, body):
    r = client.post(URL, json=body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    return r.json()["error"]["details"]["fields"]


def all_matches(body):
    return [m for rnd in body["rounds"] for m in rnd["matches"]]


def test_spec_example(client):
    teams, body = rr(client, 5, legs=1, withdrawals=[{"team_id": "t3", "after_round": 2}],
                     withdrawal_policy="forfeit_remaining")
    assert (body["format"], body["legs"], body["team_count"], body["round_count"]) == ("round_robin", 1, 5, 5)
    assert body["rounds"][0] == {"round": 1, "byes": ["t1"], "matches": [
        {"match_id": "RR-L1-R1-M1", "leg": 1, "home": "t2", "away": "t5", "status": "scheduled", "awarded_to": None},
        {"match_id": "RR-L1-R1-M2", "leg": 1, "home": "t3", "away": "t4", "status": "scheduled", "awarded_to": None}]}
    assert body["summary"] == {"total": 10, "scheduled": 8, "forfeit": 2, "voided": 0}


@pytest.mark.parametrize("legs", [1, 2])
@pytest.mark.parametrize("n", range(2, 41))
def test_structure(client, n, legs):
    teams, body = rr(client, n, legs=legs)
    per_leg = n if n % 2 else n - 1
    assert body["round_count"] == per_leg * legs and body["team_count"] == n and body["legs"] == legs
    assert [r["round"] for r in body["rounds"]] == list(range(1, per_leg * legs + 1))
    pairs, ordered = Counter(), Counter()
    home = {leg: Counter() for leg in range(1, legs + 1)}
    away = {leg: Counter() for leg in range(1, legs + 1)}
    byes = {leg: Counter() for leg in range(1, legs + 1)}
    for rnd in body["rounds"]:
        leg = (rnd["round"] - 1) // per_leg + 1
        assert len(rnd["matches"]) == n // 2
        assert len(rnd["byes"]) == n % 2
        seen = list(rnd["byes"])
        for index, m in enumerate(rnd["matches"], 1):
            assert m["match_id"] == f"RR-L{leg}-R{rnd['round']}-M{index}"
            assert m["leg"] == leg and m["status"] == "scheduled" and m["awarded_to"] is None
            assert m["home"] != m["away"]
            seen += [m["home"], m["away"]]
            pairs[frozenset((m["home"], m["away"]))] += 1
            ordered[(m["home"], m["away"])] += 1
            home[leg][m["home"]] += 1
            away[leg][m["away"]] += 1
        assert sorted(seen) == sorted(teams)
        for t in rnd["byes"]:
            byes[leg][t] += 1
    assert len(pairs) == n * (n - 1) // 2 and set(pairs.values()) == {legs}
    if legs == 2:
        assert len(ordered) == n * (n - 1) and set(ordered.values()) == {1}
    for leg in home:
        for t in teams:
            assert abs(home[leg][t] - away[leg][t]) <= 1
            assert byes[leg][t] == n % 2
    assert body["summary"] == {"total": legs * n * (n - 1) // 2, "scheduled": legs * n * (n - 1) // 2,
                               "forfeit": 0, "voided": 0}


def expected_status(m, rnd, after, policy):
    out = [t for t in (m["home"], m["away"]) if t in after and (policy == "void_all" or rnd > after[t])]
    if len(out) == 2 or (out and policy == "void_all"):
        return "voided", None
    if out:
        return "forfeit", m["away"] if out[0] == m["home"] else m["home"]
    return "scheduled", None


@pytest.mark.parametrize("policy", ["forfeit_remaining", "void_all"])
def test_withdrawals_random(client, policy):
    rng = random.Random(7)
    for _ in range(60):
        n, legs = rng.randint(3, 16), rng.choice([1, 2])
        teams, plain = rr(client, n, legs=legs)
        out = rng.sample(teams, rng.randint(1, min(4, n)))
        after = {t: rng.randint(0, plain["round_count"]) for t in out}
        _, body = rr(client, n, legs=legs, withdrawal_policy=policy,
                     withdrawals=[{"team_id": t, "after_round": r} for t, r in after.items()])
        counts = Counter()
        for got, base in zip(body["rounds"], plain["rounds"]):
            assert got["byes"] == base["byes"]
            assert [(m["match_id"], m["home"], m["away"]) for m in got["matches"]] == \
                   [(m["match_id"], m["home"], m["away"]) for m in base["matches"]]
            for m in got["matches"]:
                assert (m["status"], m["awarded_to"]) == expected_status(m, got["round"], after, policy)
                counts[m["status"]] += 1
        assert body["summary"] == {"total": plain["summary"]["total"], "scheduled": counts["scheduled"],
                                   "forfeit": counts["forfeit"], "voided": counts["voided"]}


def test_odd_withdrawal_mid_season(client):
    teams, body = rr(client, 7, withdrawals=[{"team_id": "t4", "after_round": 3}])
    for rnd in body["rounds"]:
        assert len(rnd["byes"]) == 1
        for m in rnd["matches"]:
            if "t4" in (m["home"], m["away"]) and rnd["round"] > 3:
                assert m["status"] == "forfeit" and m["awarded_to"] == ({m["home"], m["away"]} - {"t4"}).pop()
            else:
                assert m["status"] == "scheduled"
    assert body["summary"]["forfeit"] == sum(
        1 for m in all_matches(body) if "t4" in (m["home"], m["away"]) and m["status"] == "forfeit")


def test_both_withdrawn_is_voided(client):
    teams, body = rr(client, 4, withdrawals=[{"team_id": "t1", "after_round": 0}, {"team_id": "t2", "after_round": 0}])
    for m in all_matches(body):
        both = {m["home"], m["away"]} == {"t1", "t2"}
        if both:
            assert (m["status"], m["awarded_to"]) == ("voided", None)
        elif {"t1", "t2"} & {m["home"], m["away"]}:
            assert m["status"] == "forfeit" and m["awarded_to"] in ("t3", "t4")
    assert body["summary"] == {"total": 6, "scheduled": 1, "forfeit": 4, "voided": 1}


def test_void_all_and_full_season(client):
    _, body = rr(client, 6, withdrawals=[{"team_id": "t2", "after_round": 5}], withdrawal_policy="void_all")
    assert body["summary"] == {"total": 15, "scheduled": 10, "forfeit": 0, "voided": 5}
    _, body = rr(client, 6, legs=2, withdrawals=[{"team_id": "t2", "after_round": 10}])
    assert body["summary"]["scheduled"] == 30


def test_validation(client):
    base = {"format": "round_robin", "teams": ["a", "b", "c"]}
    assert fields(client, {**base, "params": {"legs": 3}}) == ["params", "params.legs", "legs"]
    assert fields(client, {**base, "params": {"legs": True}}) == ["params", "params.legs", "legs"]
    assert fields(client, {**base, "params": {"withdrawal_policy": "drop"}})[1] == "params.withdrawal_policy"
    bad_withdrawals = [
        [{"team_id": "zz", "after_round": 1}], [{"team_id": "a", "after_round": 4}],
        [{"team_id": "a", "after_round": -1}], [{"team_id": "a", "after_round": 1}, {"team_id": "a", "after_round": 2}],
        ["a"], [{"team_id": "a"}], "a",
    ]
    for w in bad_withdrawals:
        assert fields(client, {**base, "params": {"withdrawals": w}}) == ["params", "params.withdrawals", "withdrawals"]
    assert client.post(URL, json={**base, "params": {"legs": 2, "withdrawals": [{"team_id": "a", "after_round": 6}]}}).status_code == 200
    assert fields(client, {**base, "params": {"legs": 2, "withdrawals": [{"team_id": "a", "after_round": 7}]}})[2] == "withdrawals"
    assert fields(client, {"format": "round_robin", "teams": ["a"]}) == ["teams"]
    assert fields(client, {"format": "round_robin", "teams": [f"t{i}" for i in range(41)]}) == ["teams"]
    assert fields(client, {"format": "round_robin", "teams": ["a", "a"]}) == ["teams"]
    assert fields(client, {"format": "round_robin", "teams": ["a", "b|c"]}) == ["teams"]
    assert fields(client, {**base, "params": [1]}) == ["params"]
    assert client.post(URL, json={**base, "params": None}).status_code == 200
    assert client.post(URL, json=base).json()["round_count"] == 3
