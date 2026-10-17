import random
from collections import Counter

import pytest

from app.modules.fixtures import elimination

URL = "/audit/v1/fixtures"
SINGLE_SIZES = [2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 16, 17, 31, 33, 64, 100, 127, 128]


def post(client, fmt, n, **params):
    teams = [f"t{i}" for i in range(1, n + 1)]
    return teams, client.post(URL, json={"format": fmt, "teams": teams, "params": params})


def ok(client, fmt, n, **params):
    teams, r = post(client, fmt, n, **params)
    assert r.status_code == 200, r.text
    return teams, r.json()


def bad(client, fmt, n, **params):
    _, r = post(client, fmt, n, **params)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    return r.json()["error"]["details"]["fields"]


def flat(body):
    if body["format"] == "single_elim":
        return [m for r in body["rounds"] for m in r["matches"]]
    return [m for key in ("winners", "losers") for r in body[key] for m in r["matches"]] + body["grand_final"]


def by_id(body):
    return {m["match_id"]: m for m in flat(body)}


def order(size):
    if size == 2:
        return [1, 2]
    return [x for s in order(size // 2) for x in (s, size + 1 - s)]


def size_of(n):
    b = 1
    while b < n:
        b *= 2
    return b


def simulate(client, fmt, n, rng, **params):
    """Plays every playable match with random winners, resubmitting all results in shuffled order."""
    results = []
    while True:
        shuffled = results[:]
        rng.shuffle(shuffled)
        teams, body = ok(client, fmt, n, results=shuffled, **params)
        ready = [m for m in flat(body)
                 if m["home"] and m["away"] and m["winner"] is None and m.get("required", True) is True]
        if not ready:
            return teams, body, results
        results += [{"match_id": m["match_id"], "winner": rng.choice([m["home"], m["away"]])} for m in ready]


def check_first_round(teams, matches, size):
    n, seeds = len(teams), order(size)
    for i, m in enumerate(matches, 1):
        high, low = seeds[2 * i - 2], seeds[2 * i - 1]
        assert (m["home"], m["home_seed"]) == (teams[high - 1], high)
        if low <= n:
            assert (m["away"], m["away_seed"], m["bye"], m["winner"]) == (teams[low - 1], low, False, None)
        else:
            assert (m["away"], m["away_seed"], m["bye"], m["winner"], m["loser"]) == (None, low, True, teams[high - 1], None)


def test_helpers():
    assert elimination.seed_order(2) == [1, 2]
    assert elimination.seed_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    assert elimination.seed_order(64) == order(64)
    assert [elimination.bracket_size(n) for n in (2, 3, 4, 5, 9, 128)] == [2, 4, 4, 8, 16, 128]
    assert repr(elimination.EMPTY) == "EMPTY"


def test_single_spec_example(client):
    teams, body = ok(client, "single_elim", 6, third_place_match=False, results=[{"match_id": "R1M2", "winner": "t4"}])
    assert body["format"] == "single_elim" and body["bracket_size"] == 8 and len(body["rounds"]) == 3
    r1 = body["rounds"][0]["matches"]
    assert [(m["home"], m["away"], m["bye"]) for m in r1] == [("t1", None, True), ("t4", "t5", False),
                                                              ("t2", None, True), ("t3", "t6", False)]
    assert [(m["home_seed"], m["away_seed"]) for m in r1] == [(1, 8), (4, 5), (2, 7), (3, 6)]
    assert r1[1] == {"match_id": "R1M2", "bracket": "main", "round": 1, "index": 2, "home": "t4", "away": "t5",
                     "home_seed": 4, "away_seed": 5, "bye": False, "winner": "t4", "loser": "t5",
                     "winner_to": {"match_id": "R2M1", "slot": "away"}, "loser_to": None}
    r2 = body["rounds"][1]["matches"]
    assert (r2[0]["home"], r2[0]["away"], r2[0]["home_seed"], r2[0]["away_seed"]) == ("t1", "t4", 1, 4)
    assert (r2[1]["home"], r2[1]["away"]) == ("t2", None)
    assert body["rounds"][2]["matches"][0]["winner_to"] is None


@pytest.mark.parametrize("n", range(2, 129))
def test_single_structure(client, n):
    teams, body = ok(client, "single_elim", n)
    size = size_of(n)
    k = size.bit_length() - 1
    assert body["bracket_size"] == size and [r["round"] for r in body["rounds"]] == list(range(1, k + 1))
    for r, rnd in enumerate(body["rounds"], 1):
        assert len(rnd["matches"]) == size >> r
        for i, m in enumerate(rnd["matches"], 1):
            assert (m["match_id"], m["bracket"], m["round"], m["index"]) == (f"R{r}M{i}", "main", r, i)
            assert m["loser_to"] is None and m["loser"] is None
            target = {"match_id": f"R{r + 1}M{(i + 1) // 2}", "slot": "home" if i % 2 else "away"}
            assert m["winner_to"] == (target if r < k else None)
            if r > 1:
                assert m["bye"] is False and m["winner"] is None
    check_first_round(teams, body["rounds"][0]["matches"], size)
    matches = by_id(body)
    for m in body["rounds"][0]["matches"]:
        if m["bye"] and k > 1:
            nxt = matches[m["winner_to"]["match_id"]]
            assert nxt[m["winner_to"]["slot"]] == m["home"]
            assert nxt[m["winner_to"]["slot"] + "_seed"] == m["home_seed"]


@pytest.mark.parametrize("n", SINGLE_SIZES)
def test_single_full_simulation(client, n):
    rng = random.Random(n)
    teams, body, results = simulate(client, "single_elim", n, rng, third_place_match=True)
    final = body["rounds"][-1]["matches"][0]
    assert final["winner"] in teams
    losses = Counter(m["loser"] for m in flat(body) if m["loser"] and m["bracket"] == "main")
    assert set(losses.values()) <= {1} and len(losses) == n - 1 and final["winner"] not in losses
    for m in flat(body):
        if m["winner_to"] and m["winner"]:
            assert by_id(body)[m["winner_to"]["match_id"]][m["winner_to"]["slot"]] == m["winner"]
    if n >= 3:
        tp = body["rounds"][-1]["matches"][-1]
        semis = body["rounds"][-2]["matches"]
        assert (tp["match_id"], tp["bracket"], tp["round"]) == ("TP", "third_place", len(body["rounds"]))
        assert [s["loser_to"] for s in semis] == [{"match_id": "TP", "slot": "home"}, {"match_id": "TP", "slot": "away"}]
        assert (tp["home"], tp["away"]) == (semis[0]["loser"], semis[1]["loser"])
        assert tp["winner"] in teams and tp["winner_to"] is None and tp["loser_to"] is None


def test_third_place_edges(client):
    _, body = ok(client, "single_elim", 2, third_place_match=True)
    assert [m["match_id"] for m in flat(body)] == ["R1M1"]
    _, body = ok(client, "single_elim", 3, third_place_match=True, results=[{"match_id": "R1M2", "winner": "t3"}])
    tp = by_id(body)["TP"]
    assert (tp["home"], tp["away"], tp["bye"], tp["winner"]) == (None, "t2", True, "t2")
    _, body = ok(client, "single_elim", 8, third_place_match=False)
    assert "TP" not in by_id(body)


def test_single_result_errors(client):
    fields = ["params", "params.results", "results"]
    assert bad(client, "single_elim", 8, results=[{"match_id": "R9M1", "winner": "t1"}]) == fields
    assert bad(client, "single_elim", 8, results=[{"match_id": "R1M1", "winner": "t2"}]) == fields
    assert bad(client, "single_elim", 8, results=[{"match_id": "R2M1", "winner": "t1"}]) == fields
    assert bad(client, "single_elim", 8, results=[{"match_id": "R1M1", "winner": "t1"},
                                                  {"match_id": "R1M1", "winner": "t8"}]) == fields
    assert bad(client, "single_elim", 6, results=[{"match_id": "R1M1", "winner": "t8"}]) == fields
    assert bad(client, "single_elim", 8, results=[{"match_id": "R1M1"}]) == fields
    assert bad(client, "single_elim", 8, results=["R1M1"]) == fields
    assert bad(client, "single_elim", 8, results={"R1M1": "t1"}) == fields
    assert bad(client, "single_elim", 8, third_place_match="yes") == ["params", "params.third_place_match", "third_place_match"]
    _, body = ok(client, "single_elim", 6, results=[{"match_id": "R1M1", "winner": "t1"},
                                                    {"match_id": "R2M1", "winner": "t1"},
                                                    {"match_id": "R1M2", "winner": "t5"}])
    assert by_id(body)["R2M1"]["loser"] == "t5" and by_id(body)["R3M1"]["home"] == "t1"
    assert bad(client, "single_elim", 129) == ["teams"]


@pytest.mark.parametrize("reset", [True, False])
@pytest.mark.parametrize("n", range(3, 129))
def test_double_graph(client, n, reset):
    teams, body = ok(client, "double_elim", n, bracket_reset=reset)
    size = size_of(n)
    k = size.bit_length() - 1
    assert body["format"] == "double_elim" and body["bracket_size"] == size and body["bracket_reset"] is reset
    for r, rnd in enumerate(body["winners"], 1):
        assert rnd["round"] == r and [m["match_id"] for m in rnd["matches"]] == [f"WR{r}M{i}" for i in range(1, (size >> r) + 1)]
    assert len(body["winners"]) == k and len(body["losers"]) == 2 * (k - 1)
    for r, rnd in enumerate(body["losers"], 1):
        count = size // 2 ** ((r + 1) // 2 + 1)
        assert rnd["round"] == r and [m["match_id"] for m in rnd["matches"]] == [f"LR{r}M{i}" for i in range(1, count + 1)]
    assert sum(len(r["matches"]) for r in body["losers"]) == size - 2
    assert [m["match_id"] for m in body["grand_final"]] == (["GF1", "GF2"] if reset else ["GF1"])
    matches = by_id(body)
    terminal = "GF2" if reset else "GF1"
    feeds = Counter()
    for m in flat(body):
        kind = m["match_id"][:2]
        assert m["bracket"] == {"WR": "winners", "LR": "losers", "GF": "grand_final"}[kind]
        assert (m["winner_to"] is None) == (m["match_id"] == terminal)
        if kind == "WR":
            assert m["loser_to"] is not None
        if kind == "LR":
            assert m["loser_to"] is None
        for link in (m["winner_to"], m["loser_to"]):
            if link:
                assert link["match_id"] in matches and link["slot"] in ("home", "away")
                feeds[(link["match_id"], link["slot"])] += 1
        if m["match_id"] == "GF2":
            assert (m["conditional"], m["required"]) == (True, None)
        else:
            assert (m["conditional"], m["required"]) == (False, True)
    expected = {(mid, s) for mid in matches if not mid.startswith("WR1M") for s in ("home", "away")}
    assert set(feeds) == expected and set(feeds.values()) == {1}
    wfinal, lfinal = matches[f"WR{k}M1"], matches[f"LR{2 * (k - 1)}M1"]
    assert wfinal["winner_to"] == {"match_id": "GF1", "slot": "home"}
    assert wfinal["loser_to"] == {"match_id": f"LR{2 * (k - 1)}M1", "slot": "away"}
    assert lfinal["winner_to"] == {"match_id": "GF1", "slot": "away"}
    check_first_round(teams, body["winners"][0]["matches"], size)
    _, single = ok(client, "single_elim", n)
    strip = ("home", "away", "home_seed", "away_seed", "bye", "winner", "loser")
    assert [[m[f] for f in strip] for m in body["winners"][0]["matches"]] == \
           [[m[f] for f in strip] for m in single["rounds"][0]["matches"]]


@pytest.mark.parametrize("n", [3, 4, 5, 6, 7, 8, 9, 11, 16, 23, 32, 45, 64, 100, 128])
@pytest.mark.parametrize("reset", [True, False])
def test_double_full_simulation(client, n, reset):
    rng = random.Random(n * 2 + reset)
    teams, body, _ = simulate(client, "double_elim", n, rng, bracket_reset=reset)
    matches = by_id(body)
    gf1 = matches["GF1"]
    assert gf1["winner"] in teams and {gf1["home"], gf1["away"]} == {gf1["winner"], gf1["loser"]}
    for m in flat(body):
        if m["match_id"] != "GF2" and m["home"] and m["away"]:
            assert m["winner"] is not None
        if m["winner_to"] and isinstance(m["winner"], str) and m["match_id"] != "GF1":
            assert matches[m["winner_to"]["match_id"]][m["winner_to"]["slot"]] == m["winner"]
    lb_champion_won = gf1["winner"] == gf1["away"]
    losses = Counter(m["loser"] for m in flat(body) if m["loser"])
    if reset:
        gf2 = matches["GF2"]
        assert gf2["conditional"] is True and gf2["required"] is lb_champion_won
        assert [gf2[k] for k in ("home", "away", "home_seed", "away_seed")] == \
               [gf1[k] for k in ("home", "away", "home_seed", "away_seed")]
        if lb_champion_won:
            assert gf2["winner"] in (gf1["home"], gf1["away"])
            champion = gf2["winner"]
        else:
            assert gf2["winner"] is None
            champion = gf1["winner"]
    else:
        champion = gf1["winner"]
    for t in teams:
        if t == champion:
            assert losses[t] <= 1
        elif not reset and lb_champion_won and t == gf1["loser"]:
            assert losses[t] == 1
        else:
            assert losses[t] == 2, (t, losses[t])


def test_bracket_reset_required_flag(client):
    path = [("WR1M1", "t1"), ("WR1M2", "t2"), ("WR2M1", "t1"), ("LR1M1", "t4"), ("LR2M1", "t4")]
    results = [{"match_id": m, "winner": w} for m, w in path]
    _, body = ok(client, "double_elim", 4, bracket_reset=True)
    assert [body["grand_final"][1][k] for k in ("home", "away", "home_seed", "away_seed")] == [None] * 4
    _, body = ok(client, "double_elim", 4, bracket_reset=True, results=results[:3])
    gf = body["grand_final"]
    assert (gf[1]["home"], gf[1]["home_seed"], gf[1]["away"], gf[1]["away_seed"]) == ("t1", 1, None, None)
    _, body = ok(client, "double_elim", 4, bracket_reset=True, results=results)
    gf = body["grand_final"]
    assert (gf[0]["home"], gf[0]["away"], gf[1]["required"], gf[1]["home"], gf[1]["away"]) == ("t1", "t4", None, "t1", "t4")
    assert (gf[1]["home_seed"], gf[1]["away_seed"], gf[1]["winner"]) == (1, 4, None)
    _, body = ok(client, "double_elim", 4, bracket_reset=True, results=results + [{"match_id": "GF1", "winner": "t4"}])
    gf1, gf2 = body["grand_final"]
    assert (gf2["conditional"], gf2["required"], gf2["home"], gf2["away"]) == (True, True, "t1", "t4")
    _, body = ok(client, "double_elim", 4, bracket_reset=True,
                 results=results + [{"match_id": "GF1", "winner": "t4"}, {"match_id": "GF2", "winner": "t1"}])
    assert body["grand_final"][1]["winner"] == "t1"
    _, body = ok(client, "double_elim", 4, bracket_reset=True, results=results + [{"match_id": "GF1", "winner": "t1"}])
    gf2 = body["grand_final"][1]
    assert (gf2["required"], gf2["home"], gf2["away"], gf2["winner"]) == (False, "t1", "t4", None)
    assert bad(client, "double_elim", 4, bracket_reset=True,
               results=results + [{"match_id": "GF1", "winner": "t1"}, {"match_id": "GF2", "winner": "t1"}])[1] == "params.results"
    _, body = ok(client, "double_elim", 4, results=results + [{"match_id": "GF1", "winner": "t4"}])
    assert [m["match_id"] for m in body["grand_final"]] == ["GF1"]
    assert body["grand_final"][0]["winner_to"] is None and body["grand_final"][0]["loser_to"] is None
    assert bad(client, "double_elim", 4, results=[{"match_id": "GF2", "winner": "t1"}])[2] == "results"


def test_double_validation(client):
    assert bad(client, "double_elim", 2) == ["teams"]
    assert bad(client, "double_elim", 8, bracket_reset="true") == ["params", "params.bracket_reset", "bracket_reset"]
    assert bad(client, "double_elim", 8, results=[{"match_id": "LR1M1", "winner": "t1"}])[0] == "params"
    _, body = ok(client, "double_elim", 3)
    lr1 = by_id(body)["LR1M1"]
    assert (lr1["home"], lr1["bye"], lr1["winner"]) == (None, True, None)
    _, body = ok(client, "double_elim", 3, results=[{"match_id": "WR1M2", "winner": "t3"}])
    lr1 = by_id(body)["LR1M1"]
    assert (lr1["away"], lr1["winner"]) == ("t2", "t2")
    assert by_id(body)["LR2M1"]["home"] == "t2"
    _, body = ok(client, "double_elim", 9)
    assert by_id(body)["LR1M2"]["winner"] is None and by_id(body)["LR1M2"]["bye"] is True
    assert bad(client, "double_elim", 9, results=[{"match_id": "LR1M2", "winner": "t1"}])[0] == "params"
