import itertools
import random
import time

import pytest

from app.modules.fixtures import matching, weighted

URL = "/audit/v1/fixtures"
POINTS = {"home": (2, 0), "away": (0, 2), "draw": (1, 1)}


def swiss(client, teams, **params):
    return client.post(URL, json={"format": "swiss", "teams": teams, "params": params})


def fields(r):
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    return r.json()["error"]["details"]["fields"]


def table(teams, history):
    half = {t: 0 for t in teams}
    opps = {t: [] for t in teams}
    byes = set()
    for entry in history:
        if entry["bye"]:
            half[entry["bye"]] += 2
            byes.add(entry["bye"])
        for m in entry["matches"]:
            h, a = POINTS[m["result"]]
            half[m["home"]] += h
            half[m["away"]] += a
            opps[m["home"]].append(m["away"])
            opps[m["away"]].append(m["home"])
    buch = {t: sum(half[o] for o in opps[t]) for t in teams}
    return half, buch, {t: set(o) for t, o in opps.items()}, byes


def perfect_matchings(items):
    if not items:
        yield []
        return
    first = items[0]
    for i in range(1, len(items)):
        for rest in perfect_matchings(items[1:i] + items[i + 1:]):
            yield [(first, items[i])] + rest


def brute(teams, half, played, byes):
    """Expected (bye, min cost) by exhaustive search, or None when no pairing exists."""
    n = len(teams)
    if n % 2:
        cands = sorted((t for t in teams if t not in byes), key=lambda t: (half[t], -teams.index(t)))
    else:
        cands = [None]
    for bye in cands:
        rest = [t for t in teams if t != bye]
        costs = [sum(abs(half[a] - half[b]) for a, b in pm) for pm in perfect_matchings(rest)
                 if all(b not in played[a] for a, b in pm)]
        if costs:
            return bye, min(costs)
    return None


def check_round(client, teams, total, rnd, history):
    r = swiss(client, teams, total_rounds=total, round=rnd, history=history)
    half, buch, played, byes = table(teams, history)
    expected = brute(teams, half, played, byes)
    if expected is None:
        assert r.status_code == 409, r.text
        assert r.json()["error"] == {"code": "NO_VALID_PAIRING", "message": r.json()["error"]["message"],
                                     "details": {"round": rnd}}
        return None
    assert r.status_code == 200, r.text
    body = r.json()
    order = sorted(teams, key=lambda t: (-half[t], -buch[t], teams.index(t)))
    assert body["standings"] == [{"rank": i, "team_id": t, "score": half[t] / 2, "buchholz": buch[t] / 2}
                                 for i, t in enumerate(order, 1)]
    assert all(isinstance(s["score"], float) and isinstance(s["buchholz"], float) for s in body["standings"])
    seen = [p["home"] for p in body["pairings"]] + [p["away"] for p in body["pairings"]]
    if body["bye"] is not None:
        seen.append(body["bye"])
    assert sorted(seen) == sorted(teams)
    assert all(p["away"] not in played[p["home"]] for p in body["pairings"])
    assert [p["match_id"] for p in body["pairings"]] == [f"SW-R{rnd}-M{i}" for i in range(1, len(body["pairings"]) + 1)]
    bye, cost = expected
    assert body["bye"] == bye
    assert sum(abs(half[p["home"]] - half[p["away"]]) for p in body["pairings"]) == cost
    return body


def play(rng, body, rnd):
    return {"round": rnd, "bye": body["bye"],
            "matches": [{"home": p["home"], "away": p["away"], "result": rng.choice(list(POINTS))}
                        for p in body["pairings"]]}


@pytest.mark.parametrize("n", range(2, 65))
def test_round_one_exact(client, n):
    teams = [f"s{i}" for i in range(1, n + 1)]
    body = swiss(client, teams).json()
    m = n // 2
    assert body["round"] == 1 and body["total_rounds"] == max(1, (n - 1).bit_length())
    assert body["pairings"] == [{"match_id": f"SW-R1-M{i}", "home": teams[i - 1], "away": teams[i - 1 + m]}
                                for i in range(1, m + 1)]
    assert body["bye"] == (teams[-1] if n % 2 else None)
    assert body["standings"] == [{"rank": i, "team_id": t, "score": 0.0, "buchholz": 0.0}
                                 for i, t in enumerate(teams, 1)]


def test_spec_example(client):
    teams = ["t1", "t2", "t3", "t4", "t5"]
    history = [{"round": 1, "bye": "t5", "matches": [{"home": "t1", "away": "t3", "result": "home"},
                                                     {"home": "t2", "away": "t4", "result": "draw"}]}]
    r = swiss(client, teams, total_rounds=3, round=2, history=history)
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "swiss" and body["round"] == 2 and body["total_rounds"] == 3
    assert body["pairings"] == [{"match_id": "SW-R2-M1", "home": "t1", "away": "t2"},
                                {"match_id": "SW-R2-M2", "home": "t5", "away": "t4"}]
    assert body["bye"] == "t3"
    assert body["standings"][:3] == [{"rank": 1, "team_id": "t1", "score": 1.0, "buchholz": 0.0},
                                     {"rank": 2, "team_id": "t5", "score": 1.0, "buchholz": 0.0},
                                     {"rank": 3, "team_id": "t2", "score": 0.5, "buchholz": 0.5}]


def test_optimal_against_brute_force(client):
    rng = random.Random(2026)
    checked = conflicts = 0
    for _ in range(70):
        n = rng.randint(3, 10)
        teams = [f"p{i}" for i in range(1, n + 1)]
        total = n - 1
        history = []
        body = swiss(client, teams, total_rounds=total).json()
        for rnd in range(2, total + 1):
            history.append(play(rng, body, rnd - 1))
            body = check_round(client, teams, total, rnd, history)
            checked += 1
            if body is None:
                conflicts += 1
                break
    assert checked > 150 and conflicts > 0


def test_random_histories_from_random_pairings(client):
    """Histories that our own engine would not produce (random rematch-free rounds)."""
    rng = random.Random(99)
    for _ in range(80):
        n = rng.randint(4, 10)
        teams = [f"q{i}" for i in range(1, n + 1)]
        played = {t: set() for t in teams}
        history, byes = [], set()
        for rnd in range(1, rng.randint(2, n - 1)):
            pool = teams[:]
            rng.shuffle(pool)
            bye = None
            if n % 2:
                options = [t for t in pool if t not in byes]
                bye = options[0]
                byes.add(bye)
                pool.remove(bye)
            pm = next((pm for pm in perfect_matchings(pool) if all(b not in played[a] for a, b in pm)), None)
            if pm is None:
                break
            for a, b in pm:
                played[a].add(b)
                played[b].add(a)
            history.append({"round": rnd, "bye": bye, "matches": [
                {"home": a, "away": b, "result": rng.choice(list(POINTS))} for a, b in pm]})
        if history:
            check_round(client, teams, n - 1, len(history) + 1, history)


def k33_history(teams):
    a, b = teams[:3], teams[3:]
    return [{"round": r + 1, "bye": None,
             "matches": [{"home": a[i], "away": b[(i + r) % 3], "result": "draw"} for i in range(3)]}
            for r in range(3)]


def test_no_valid_pairing_even(client):
    teams = ["a1", "a2", "a3", "b1", "b2", "b3"]
    r = swiss(client, teams, total_rounds=5, round=4, history=k33_history(teams))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NO_VALID_PAIRING"
    assert r.json()["error"]["details"] == {"round": 4}


def test_no_valid_pairing_odd(client):
    teams = ["o1", "o2", "o3", "o4", "o5"]
    history = [
        {"round": 1, "bye": "o2", "matches": [{"home": "o5", "away": "o3", "result": "draw"},
                                              {"home": "o4", "away": "o1", "result": "home"}]},
        {"round": 2, "bye": "o3", "matches": [{"home": "o1", "away": "o5", "result": "away"},
                                              {"home": "o2", "away": "o4", "result": "away"}]},
        {"round": 3, "bye": "o1", "matches": [{"home": "o3", "away": "o4", "result": "away"},
                                              {"home": "o2", "away": "o5", "result": "away"}]},
    ]
    assert check_round(client, teams, 4, 4, history) is None


def test_bye_falls_back_to_next_candidate(client):
    # E is the lowest-ranked candidate, but without E team A has no unplayed opponent left.
    teams = ["A", "B", "C", "D", "E"]
    history = [{"round": r, "bye": None, "matches": [{"home": "A", "away": x, "result": "home"}]}
               for r, x in enumerate(["B", "C", "D"], 1)]
    body = check_round(client, teams, 4, 4, history)
    assert body["bye"] == "D"
    assert body["pairings"] == [{"match_id": "SW-R4-M1", "home": "A", "away": "E"},
                                {"match_id": "SW-R4-M2", "home": "B", "away": "C"}]


def test_matching_unit():
    rng = random.Random(3)
    for _ in range(400):
        m = rng.choice([2, 4, 6, 8, 10])
        adj = [[] for _ in range(m)]
        for p, q in itertools.combinations(range(m), 2):
            if rng.random() < 0.45:
                adj[p].append(q)
                adj[q].append(p)
        mate = matching.perfect_matching(m, adj)
        exists = any(all(b in adj[a] for a, b in pm) for pm in perfect_matchings(list(range(m))))
        assert (mate is not None) == exists
        if mate is not None:
            assert all(mate[mate[v]] == v and mate[v] in adj[v] for v in range(m))


def test_weighted_matching_unit():
    rng = random.Random(8)
    for _ in range(1500):
        m = rng.choice([2, 4, 6, 8, 10])
        density = rng.random()
        cost = {e: rng.randint(0, rng.choice([1, 3, 12])) for e in itertools.combinations(range(m), 2)
                if rng.random() < density}
        best = min((sum(cost[e] for e in pm) for pm in perfect_matchings(list(range(m))) if all(e in cost for e in pm)),
                   default=None)
        top = max(cost.values(), default=0) + 1
        mate = weighted.max_weight_matching(m, [(p, q, top - c) for (p, q), c in cost.items()])
        assert all(mate[mate[v]] == v for v in range(m) if mate[v] != -1)
        if best is None:
            assert -1 in mate
        else:
            assert -1 not in mate
            assert sum(cost[(v, mate[v])] for v in range(m) if mate[v] > v) == best
    assert weighted.max_weight_matching(3, []) == [-1, -1, -1]


def test_search_timeout_keeps_exact_optimum(monkeypatch):
    monkeypatch.setattr(matching, "SEARCH_SECONDS", -1.0)
    score = [6, 4, 4, 2, 2, 0]
    played = [{1, 2}, {0}, {0}, set(), set(), set()]
    pairs = matching.min_cost_pairing(list(range(6)), score, played)
    assert sorted(x for p in pairs for x in p) == list(range(6))
    assert all(b not in played[a] for a, b in pairs)
    assert sum(score[a] - score[b] for a, b in pairs) == 6
    assert matching.min_cost_pairing([], score, played) == []
    with pytest.raises(matching._Timeout):
        matching._first_optimal([2, 0], [0b10, 0b01], -1, time.monotonic() + 5)


@pytest.mark.parametrize("mode", ["random", "home"])
def test_large_field_late_rounds(client, mode):
    rng = random.Random(11)
    teams = [f"L{i}" for i in range(1, 65)]
    body, history = swiss(client, teams, total_rounds=20).json(), []
    for rnd in range(2, 21):
        history.append(play(rng, body, rnd - 1))
        if mode == "home":
            for m in history[-1]["matches"]:
                m["result"] = "home"
        started = time.monotonic()
        r = swiss(client, teams, total_rounds=20, round=rnd, history=history)
        assert time.monotonic() - started < 2.0
        assert r.status_code == 200, r.text
        body = r.json()
        half, _, played, _ = table(teams, history)
        assert all(p["away"] not in played[p["home"]] for p in body["pairings"])
        assert len(body["pairings"]) == 32


def test_validation(client):
    teams = ["t1", "t2", "t3", "t4", "t5"]
    h1 = {"round": 1, "bye": "t5", "matches": [{"home": "t1", "away": "t3", "result": "home"},
                                               {"home": "t2", "away": "t4", "result": "draw"}]}
    hist = ["params", "params.history", "history"]
    assert fields(swiss(client, teams, total_rounds=0)) == ["params", "params.total_rounds", "total_rounds"]
    assert fields(swiss(client, teams, total_rounds=5)) == ["params", "params.total_rounds", "total_rounds"]
    assert swiss(client, teams, total_rounds=4).status_code == 200
    assert fields(swiss(client, teams, round=4)) == ["params", "params.round", "round"]
    assert fields(swiss(client, teams, round=0)) == ["params", "params.round", "round"]
    assert fields(swiss(client, teams, round=2)) == hist
    assert fields(swiss(client, teams, round=1, history=[h1])) == hist
    assert fields(swiss(client, teams, round=3, history=[h1])) == hist
    assert fields(swiss(client, teams, round=3, history=[h1, h1])) == hist
    bad = [
        {**h1, "bye": "zz"}, {**h1, "bye": 5}, {**h1, "round": "1"}, {**h1, "matches": None},
        {**h1, "matches": [{"home": "t1", "away": "t1", "result": "home"}]},
        {**h1, "matches": [{"home": "t1", "away": "t2", "result": "win"}]},
        {**h1, "matches": [{"home": "t1", "away": "t9", "result": "home"}]},
        {**h1, "matches": [{"home": "t5", "away": "t2", "result": "home"}]}, "round1",
    ]
    for entry in bad:
        assert fields(swiss(client, teams, round=2, history=[entry])) == hist
    assert fields(swiss(client, teams, round=2, history={"round": 1})) == hist
    assert fields(swiss(client, ["a"], round=1)) == ["teams"]
    assert fields(swiss(client, [f"t{i}" for i in range(65)])) == ["teams"]
    assert fields(swiss(client, ["a", "b"], total_rounds=2)) == ["params", "params.total_rounds", "total_rounds"]
    r = client.post(URL, json={"format": "swiss", "teams": ["a", "a"], "params": {"total_rounds": "x", "round": 9}})
    assert fields(r) == ["teams", "params", "params.total_rounds", "total_rounds"]
    no_bye = {"round": 1, "matches": [{"home": "t1", "away": "t2", "result": "away"}]}
    assert swiss(client, ["t1", "t2", "t3", "t4"], round=2, history=[no_bye]).status_code == 200
