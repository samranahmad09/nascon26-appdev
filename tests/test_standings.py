"""Unit tests of the pure standings computation (5.10) with hand-built score projections.

Every expected figure below was worked out by hand from the spec, not by running the code."""
import math

import pytest

from app.core.signing import lots_key
from app.modules.standings.logic import (compute_standings, net_run_rate, overs_faced, qualification_status,
                                         resolve, tie_break_criteria)

FULL = ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"]
EVENT = {"sport_id": "evt-sport", "scoring_model": "event", "periods": {"count": 2, "duration_minutes": 45},
         "draw_allowed": True, "tie_break_order": FULL}
INNINGS = {"sport_id": "inn-sport", "scoring_model": "innings",
           "periods": {"innings_per_team": 1, "overs_per_innings": 20, "balls_per_over": 6, "wickets_per_innings": 10},
           "draw_allowed": True, "tie_break_order": FULL}
ROUND = {"sport_id": "rnd-sport", "scoring_model": "round", "periods": {"best_of": 3},
         "draw_allowed": False, "tie_break_order": FULL}
ZERO = {"played": 0, "points": 0, "scored": 0, "conceded": 0}


def make_league(teams, pairs, spots=2, sport=EVENT, **overrides):
    league = {"league_id": "lg-1", "sport_id": sport["sport_id"], "teams": list(teams),
              "fixtures": [{"match_id": f"m{i}", "home": h, "away": a} for i, (h, a) in enumerate(pairs, 1)],
              "points": {"win": 3, "draw": 1, "loss": 0}, "tie_break_order": None,
              "qualification": {"spots": spots}, "lots_seed": 42}
    league.update(overrides)
    return league


def side(team, score, fair_play=0, wickets=None, balls=None):
    overs = None if balls is None else f"{balls // 6}.{balls % 6}"
    return {"team_id": team, "score": score, "wickets": wickets, "balls": balls, "overs": overs, "fair_play": fair_play}


def proj(match_id, home, away, status="completed", outcome=None):
    if outcome is None:
        if status != "completed":
            outcome = "pending"
        elif home["score"] == away["score"]:
            outcome = "draw"
        else:
            outcome = "home_win" if home["score"] > away["score"] else "away_win"
    winner = {"home_win": home["team_id"], "away_win": away["team_id"]}.get(outcome)
    return {"match_id": match_id, "status": status, "outcome": outcome, "winner_team_id": winner,
            "home": home, "away": away}


def game(league, match_id, home_score, away_score, status="completed", outcome=None, home_fp=0, away_fp=0):
    fixture = next(f for f in league["fixtures"] if f["match_id"] == match_id)
    return proj(match_id, side(fixture["home"], home_score, home_fp), side(fixture["away"], away_score, away_fp),
                status, outcome)


def row(rank, team, played, won, drawn, lost, scored, conceded, diff, points, fair_play, status, resolved_by=None):
    return {"rank": rank, "team_id": team, "played": played, "won": won, "drawn": drawn, "lost": lost,
            "scored": scored, "conceded": conceded, "diff": diff, "points": points, "fair_play": fair_play,
            "status": status, "resolved_by": resolved_by}


def order(result):
    return [(r["team_id"], r["resolved_by"]) for r in result["table"]]


# --- full table, no ties ------------------------------------------------------------------------------------

def test_single_round_robin_table_shape_and_values():
    lg = make_league(["t1", "t2", "t3", "t4"],
                     [("t1", "t2"), ("t3", "t4"), ("t1", "t3"), ("t2", "t4"), ("t1", "t4"), ("t2", "t3")])
    projections = [game(lg, "m1", 2, 1), game(lg, "m2", 0, 0), game(lg, "m3", 3, 0),
                   game(lg, "m4", 1, 1), game(lg, "m5", 1, 1), game(lg, "m6", 2, 0)]
    result = compute_standings(lg, EVENT, projections)
    assert list(result) == ["league_id", "sport_id", "qualification_spots", "table", "h2h"]
    assert result["league_id"] == "lg-1" and result["sport_id"] == "evt-sport" and result["qualification_spots"] == 2
    assert result["table"] == [
        row(1, "t1", 3, 2, 1, 0, 6, 2, 4, 7, 0, "clinched"),
        row(2, "t2", 3, 1, 1, 1, 4, 3, 1, 4, 0, "clinched"),
        row(3, "t4", 3, 0, 3, 0, 2, 2, 0, 3, 0, "eliminated"),
        row(4, "t3", 3, 0, 1, 2, 0, 5, -5, 1, 0, "eliminated"),
    ]
    assert list(result["table"][0]) == ["rank", "team_id", "played", "won", "drawn", "lost", "scored", "conceded",
                                        "diff", "points", "fair_play", "status", "resolved_by"]
    h2h = result["h2h"]
    assert sorted(h2h) == ["t1", "t2", "t3", "t4"]
    assert all(sorted(h2h[a]) == sorted(t for t in h2h if t != a) for a in h2h)
    assert h2h["t1"]["t2"] == {"played": 1, "points": 3, "scored": 2, "conceded": 1}
    assert h2h["t2"]["t1"] == {"played": 1, "points": 0, "scored": 1, "conceded": 2}
    assert h2h["t3"]["t4"] == {"played": 1, "points": 1, "scored": 0, "conceded": 0}
    assert h2h["t4"]["t1"] == {"played": 1, "points": 1, "scored": 1, "conceded": 1}


def test_points_config_and_double_round_robin_h2h_accumulates():
    lg = make_league(["t1", "t2"], [("t1", "t2"), ("t2", "t1"), ("t1", "t2")], spots=1,
                     points={"win": 2, "draw": 1, "loss": -1})
    projections = [game(lg, "m1", 2, 0), game(lg, "m2", 1, 1), game(lg, "m3", 0, 3)]
    result = compute_standings(lg, EVENT, projections)
    # t1: W, D, L = 2 + 1 - 1 = 2; t2: L, D, W = -1 + 1 + 2 = 2 -> tied on points and h2h_points;
    # with two teams the mini-league is every match, so h2h_diff (t2 +1) decides
    assert result["table"] == [
        row(1, "t2", 3, 1, 1, 1, 4, 3, 1, 2, 0, "still_possible", "h2h_diff"),
        row(2, "t1", 3, 1, 1, 1, 3, 4, -1, 2, 0, "still_possible"),
    ]
    assert result["h2h"]["t1"]["t2"] == {"played": 3, "points": 2, "scored": 3, "conceded": 4}
    assert result["h2h"]["t2"]["t1"] == {"played": 3, "points": 2, "scored": 4, "conceded": 3}


# --- tie-breaks ---------------------------------------------------------------------------------------------

def test_two_way_tie_head_to_head_beats_better_overall_diff():
    lg = make_league(["t1", "t2", "t3", "t4"], [("t1", "t2"), ("t2", "t3"), ("t3", "t4"), ("t1", "t4")])
    projections = [game(lg, "m1", 1, 0), game(lg, "m2", 5, 0), game(lg, "m3", 0, 0), game(lg, "m4", 0, 2)]
    result = compute_standings(lg, EVENT, projections)
    assert result["table"] == [
        row(1, "t4", 2, 1, 1, 0, 2, 0, 2, 4, 0, "clinched"),
        row(2, "t1", 2, 1, 0, 1, 1, 2, -1, 3, 0, "still_possible", "h2h_points"),
        row(3, "t2", 2, 1, 0, 1, 5, 1, 4, 3, 0, "still_possible"),
        row(4, "t3", 2, 0, 1, 1, 0, 5, -5, 1, 0, "eliminated"),
    ]
    assert result["h2h"]["t1"]["t3"] == ZERO and result["h2h"]["t2"]["t4"] == ZERO


def test_three_way_mini_league_split_then_pair_restarts_from_h2h_points():
    # Cycle t1>t2>t3>t1 so h2h_points tie at 3; h2h_diff in the 3-way mini-league is t1 +2, t2 -1, t3 -1.
    # The {t2, t3} pair then restarts from h2h_points over its own match (t2 beat t3), although t3 has the
    # better overall_diff (4 vs 0): carrying on to overall_diff instead of restarting would put t3 first.
    lg = make_league(["t1", "t2", "t3", "t4"],
                     [("t1", "t2"), ("t2", "t3"), ("t3", "t1"), ("t1", "t4"), ("t2", "t4"), ("t3", "t4")])
    projections = [game(lg, "m1", 3, 0), game(lg, "m2", 2, 0), game(lg, "m3", 1, 0),
                   game(lg, "m4", 1, 0), game(lg, "m5", 1, 0), game(lg, "m6", 5, 0)]
    result = compute_standings(lg, EVENT, projections)
    assert result["table"] == [
        row(1, "t1", 3, 2, 0, 1, 4, 1, 3, 6, 0, "still_possible", "h2h_diff"),
        row(2, "t2", 3, 2, 0, 1, 3, 3, 0, 6, 0, "still_possible", "h2h_points"),
        row(3, "t3", 3, 2, 0, 1, 6, 2, 4, 6, 0, "still_possible"),
        row(4, "t4", 3, 0, 0, 3, 0, 7, -7, 0, 0, "eliminated"),
    ]


def test_three_way_tie_fully_split_by_one_criterion():
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t2", "t3"), ("t3", "t1")], sport=ROUND)
    projections = [game(lg, "m1", 2, 1), game(lg, "m2", 2, 0), game(lg, "m3", 2, 1)]
    result = compute_standings(lg, ROUND, projections)
    # round model: diff = rounds won - rounds lost (map differential)
    assert result["table"] == [
        row(1, "t2", 2, 1, 0, 1, 3, 2, 1, 3, 0, "still_possible", "h2h_diff"),
        row(2, "t1", 2, 1, 0, 1, 3, 3, 0, 3, 0, "still_possible", "h2h_diff"),
        row(3, "t3", 2, 1, 0, 1, 2, 3, -1, 3, 0, "still_possible"),
    ]


def test_draw_lots_matches_appendix_a_vector_2():
    assert lots_key(42, "lg-1", "t1") == "0613316991ce0ac31e88d6003e3ff4d5e21bb072ac5f81b42c0cdc6598453293"
    assert lots_key(42, "lg-1", "t2") == "e3e3a14e22727f936e1f288cf5d02a8a9362b1da13c5432725fc10b2c7955dd9"
    assert lots_key(42, "lg-1", "t3") == "c8f28e3c2f1eb6c4f636b4f57050e3362a2b39c07fda9b61742a26dd20397028"
    lg = make_league(["t2", "t3", "t1"], [("t1", "t2"), ("t2", "t3"), ("t3", "t1")])
    projections = [game(lg, "m1", 1, 1), game(lg, "m2", 1, 1), game(lg, "m3", 1, 1)]
    result = compute_standings(lg, EVENT, projections)
    assert result["table"] == [
        row(1, "t1", 2, 0, 2, 0, 2, 2, 0, 2, 0, "still_possible", "draw_lots"),
        row(2, "t3", 2, 0, 2, 0, 2, 2, 0, 2, 0, "still_possible", "draw_lots"),
        row(3, "t2", 2, 0, 2, 0, 2, 2, 0, 2, 0, "still_possible"),
    ]


def test_draw_lots_uses_the_league_seed():
    lg = make_league(["t1", "t2", "t3", "t4", "t5"], [], lots_seed=7, league_id="cup-9")
    expected = sorted(lg["teams"], key=lambda t: lots_key(7, "cup-9", t))
    result = compute_standings(lg, EVENT, [])
    assert [r["team_id"] for r in result["table"]] == expected
    assert [r["resolved_by"] for r in result["table"]] == ["draw_lots"] * 4 + [None]
    assert [r["rank"] for r in result["table"]] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("m3, first, criterion", [
    ((3, 0), "t2", "overall_diff"),   # t1 diff 1, t2 diff 3
    ((3, 2), "t2", "scored"),         # both diff 1, t2 scored 4 vs 2
    ((1, 0), "t1", "draw_lots"),      # identical records: lots put t1 before t2
])
def test_overall_criteria_after_equal_head_to_head(m3, first, criterion):
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t1", "t3"), ("t2", "t3")], spots=1)
    projections = [game(lg, "m1", 1, 1), game(lg, "m2", 1, 0), game(lg, "m3", *m3)]
    result = compute_standings(lg, EVENT, projections)
    assert order(result)[:2] == [(first, criterion), ({"t1": "t2", "t2": "t1"}[first], None)]
    assert [r["status"] for r in result["table"]] == ["still_possible", "still_possible", "eliminated"]


def test_fair_play_decides_and_is_reported():
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t1", "t3"), ("t2", "t3")], spots=1)
    projections = [game(lg, "m1", 0, 0, home_fp=-2), game(lg, "m2", 1, 0), game(lg, "m3", 1, 0, home_fp=-1)]
    result = compute_standings(lg, EVENT, projections)
    assert result["table"] == [
        row(1, "t2", 2, 1, 1, 0, 1, 0, 1, 4, -1, "still_possible", "fair_play"),
        row(2, "t1", 2, 1, 1, 0, 1, 0, 1, 4, -2, "still_possible"),
        row(3, "t3", 2, 0, 0, 2, 0, 2, -2, 0, 0, "eliminated"),
    ]


def _h2h_favours_t2():
    # t1 and t2 on 3 points; t2 won their meeting, t1 scored more (2 vs 1), lots put t1 first.
    lg = make_league(["t1", "t2", "t3", "t4"], [("t1", "t2"), ("t2", "t3"), ("t3", "t4"), ("t1", "t4")])
    projections = [game(lg, "m1", 0, 1), game(lg, "m2", 0, 5), game(lg, "m3", 0, 0), game(lg, "m4", 2, 0)]
    return lg, projections


@pytest.mark.parametrize("league_order, sport_order, expected", [
    (None, FULL, [("t2", "h2h_points"), ("t1", None)]),
    (["scored", "h2h_points"], FULL, [("t1", "scored"), ("t2", None)]),
    ([], FULL, [("t1", "draw_lots"), ("t2", None)]),
    (None, ["fair_play"], [("t1", "draw_lots"), ("t2", None)]),
    (None, ["h2h_diff", "overall_diff"], [("t2", "h2h_diff"), ("t1", None)]),
])
def test_league_order_overrides_sport_order(league_order, sport_order, expected):
    lg, projections = _h2h_favours_t2()
    lg["tie_break_order"] = league_order
    result = compute_standings(lg, {**EVENT, "tie_break_order": sport_order}, projections)
    assert order(result)[0] == ("t3", None)
    assert order(result)[1:3] == expected


def test_tie_break_criteria_resolution():
    assert tie_break_criteria({"tie_break_order": None}, EVENT) == FULL
    assert tie_break_criteria({}, {"scoring_model": "event"}) == FULL
    assert tie_break_criteria({"tie_break_order": []}, EVENT) == ["draw_lots"]
    assert tie_break_criteria({"tie_break_order": None}, {**EVENT, "tie_break_order": []}) == ["draw_lots"]
    assert tie_break_criteria({"tie_break_order": ["draw_lots", "scored"]}, EVENT) == ["scored", "draw_lots"]
    assert tie_break_criteria({"tie_break_order": ["fair_play", "bogus"]}, EVENT) == ["fair_play", "draw_lots"]


def test_resolve_keeps_input_order_when_nothing_separates():
    assert resolve(["b", "a", "c"], [], lambda criterion, group: {}) == (["b", "a", "c"], [None, None])
    assert resolve(["solo"], ["draw_lots"], lambda criterion, group: {}) == (["solo"], [])


# --- innings model: net run rate ----------------------------------------------------------------------------

def innings(match_id, home, away):
    return proj(match_id, side(*home), side(*away))


def test_net_run_rate_with_all_out_innings_and_rounding():
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t2", "t3"), ("t3", "t1")], sport=INNINGS)
    projections = [
        innings("m1", ("t1", 180, 0, 5, 120), ("t2", 150, 0, 10, 100)),   # t2 all out in 16.4: counts as 20 overs
        innings("m2", ("t2", 160, 0, 3, 120), ("t3", 161, 0, 4, 110)),
        innings("m3", ("t3", 140, 0, 10, 90), ("t1", 141, 0, 2, 80)),     # t3 all out in 15.0: counts as 20 overs
    ]
    result = compute_standings(lg, INNINGS, projections)
    # t1: 321/(20+13.333) - 290/(20+20) = 9.63 - 7.25 = 2.38
    # t3: 301/(18.333+20) - 301/(20+13.333) = 7.85217 - 9.03 = -1.17783 -> -1.178
    # t2: 310/(20+20) - 341/(20+18.333) = 7.75 - 8.89565 = -1.14565 -> -1.146
    assert result["table"] == [
        row(1, "t1", 2, 2, 0, 0, 321, 290, 2.38, 6, 0, "clinched"),
        row(2, "t3", 2, 1, 0, 1, 301, 301, -1.178, 3, 0, "clinched"),
        row(3, "t2", 2, 0, 0, 2, 310, 341, -1.146, 0, 0, "eliminated"),
    ]
    assert result["h2h"]["t1"]["t2"] == {"played": 1, "points": 3, "scored": 180, "conceded": 150}
    assert result["h2h"]["t1"]["t3"] == {"played": 1, "points": 3, "scored": 141, "conceded": 140}


def test_innings_h2h_diff_is_net_run_rate_over_the_mini_league():
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t1", "t3"), ("t2", "t3")], spots=1, sport=INNINGS)
    projections = [
        innings("m1", ("t1", 150, 0, 6, 120), ("t2", 150, 0, 3, 100)),   # tied match -> draw
        innings("m2", ("t1", 100, 0, 5, 120), ("t3", 90, 0, 5, 120)),
        innings("m3", ("t2", 100, 0, 5, 120), ("t3", 90, 0, 5, 120)),
    ]
    result = compute_standings(lg, INNINGS, projections)
    # mini-league NRR over m1 only: t1 150/20 - 150/16.667 = -1.5, t2 +1.5
    assert result["table"] == [
        row(1, "t2", 2, 1, 1, 0, 250, 240, 0.818, 4, 0, "still_possible", "h2h_diff"),
        row(2, "t1", 2, 1, 1, 0, 250, 240, -0.295, 4, 0, "still_possible"),
        row(3, "t3", 2, 0, 0, 2, 180, 200, -0.5, 0, 0, "eliminated"),
    ]


def test_overs_faced_rules():
    periods = INNINGS["periods"]
    assert overs_faced({"wickets": 10, "balls": 100}, periods) == 20
    assert overs_faced({"wickets": 9, "balls": 100}, periods) == 100 / 6
    assert overs_faced({"wickets": None, "balls": None}, periods) == 0
    two = {**periods, "innings_per_team": 2}
    assert overs_faced({"wickets": 19, "balls": 200}, two) == 200 / 6
    assert overs_faced({"wickets": 20, "balls": 200}, two) == 40


def test_net_run_rate_edges():
    assert net_run_rate(10, 0, 0, 0) == 0.0
    assert net_run_rate(0, 0, 12, 2) == -6.0
    assert net_run_rate(1, 3, 0, 1) == 0.333
    tiny = net_run_rate(0, 1, 1, 10000)
    assert tiny == 0 and math.copysign(1, tiny) == 1.0


# --- remaining fixtures and qualification ---------------------------------------------------------------------

def test_undecided_in_progress_and_missing_matches_are_remaining():
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t1", "t3"), ("t2", "t3"), ("t3", "t1")], spots=1)
    projections = [
        game(lg, "m1", 1, 1, outcome="undecided"),
        game(lg, "m2", 2, 0, status="in_progress"),
        game(lg, "m3", 1, 0),
    ]  # m4 has no projection at all
    result = compute_standings(lg, EVENT, projections)
    # remaining: t1 3 (m1, m2, m4), t2 1 (m1), t3 2 (m2, m4); max: t1 9, t2 6, t3 6
    assert result["table"] == [
        row(1, "t2", 1, 1, 0, 0, 1, 0, 1, 3, 0, "still_possible"),
        row(2, "t1", 0, 0, 0, 0, 0, 0, 0, 0, 0, "still_possible", "overall_diff"),
        row(3, "t3", 1, 0, 0, 1, 0, 1, -1, 0, 0, "still_possible"),
    ]
    assert result["h2h"]["t1"]["t2"] == ZERO and result["h2h"]["t1"]["t3"] == ZERO
    assert result["h2h"]["t2"]["t3"] == {"played": 1, "points": 3, "scored": 1, "conceded": 0}


def test_discipline_in_unplayed_matches_is_ignored():
    lg = make_league(["t1", "t2"], [("t1", "t2"), ("t2", "t1")], spots=1)
    projections = [game(lg, "m1", 1, 0, home_fp=-3), game(lg, "m2", 0, 0, status="in_progress", away_fp=-5)]
    result = compute_standings(lg, EVENT, projections)
    assert [(r["team_id"], r["fair_play"], r["status"]) for r in result["table"]] == [
        ("t1", -3, "still_possible"), ("t2", 0, "still_possible")]


def test_mid_season_status_boundaries():
    pairs = [("t1", "t2"), ("t1", "t3"), ("t2", "t3"), ("t3", "t2")]
    lg = make_league(["t1", "t2", "t3"], pairs, spots=1)
    # t1 6 (R 0, max 6); t2 3 (R 1, max 6); t3 0 (R 1, max 3)
    result = compute_standings(lg, EVENT, [game(lg, "m1", 1, 0), game(lg, "m2", 1, 0), game(lg, "m3", 1, 0)])
    assert [(r["team_id"], r["status"]) for r in result["table"]] == [
        ("t1", "still_possible"), ("t2", "still_possible"), ("t3", "eliminated")]
    # t2 and t3 draw instead: both 1 point with max 4, so t1 clinches and both are eliminated
    result = compute_standings(lg, EVENT, [game(lg, "m1", 1, 0), game(lg, "m2", 1, 0), game(lg, "m3", 0, 0)])
    assert [(r["team_id"], r["status"], r["resolved_by"]) for r in result["table"]] == [
        ("t1", "clinched", None), ("t3", "eliminated", "draw_lots"), ("t2", "eliminated", None)]


def test_points_only_status_ignores_who_plays_whom():
    # t2 and t3 meet in m6, so they cannot both reach 6, but the exact rule counts both maxima
    lg = make_league(["t1", "t2", "t3", "t4"],
                     [("t1", "t2"), ("t3", "t4"), ("t1", "t3"), ("t2", "t4"), ("t1", "t4"), ("t2", "t3")])
    projections = [game(lg, "m1", 1, 0), game(lg, "m2", 1, 0), game(lg, "m3", 1, 0), game(lg, "m4", 1, 0),
                   game(lg, "m6", 2, 0, status="in_progress")]
    result = compute_standings(lg, EVENT, projections)
    assert [(r["team_id"], r["points"], r["status"]) for r in result["table"]] == [
        ("t1", 6, "still_possible"), ("t3", 3, "still_possible"), ("t2", 3, "still_possible"),
        ("t4", 0, "still_possible")]


@pytest.mark.parametrize("points, best, spots, expected", [
    ({"T": 5, "X": 4}, {"T": 3, "X": 4}, 1, "clinched"),            # clinched is checked before eliminated
    ({"T": 6, "X": 3}, {"T": 6, "X": 6}, 1, "still_possible"),      # max(X) == points(T) blocks clinching
    ({"T": 3, "X": 6}, {"T": 6, "X": 6}, 1, "still_possible"),      # points(X) == max(T) does not eliminate
    ({"T": 3, "X": 7}, {"T": 6, "X": 7}, 1, "eliminated"),
    ({"T": 0, "X": 9}, {"T": 0, "X": 9}, 2, "clinched"),            # every team qualifies
    ({"T": 4, "X": 9, "Y": 1}, {"T": 4, "X": 9, "Y": 7}, 2, "still_possible"),
    ({"T": 9, "X": 4, "Y": 1}, {"T": 9, "X": 10, "Y": 7}, 2, "clinched"),
    ({"T": 1, "X": 8, "Y": 8}, {"T": 7, "X": 8, "Y": 8}, 2, "eliminated"),
])
def test_qualification_status_rules(points, best, spots, expected):
    assert qualification_status("T", points, best, spots) == expected


# --- edge case 5: a compensation changes a completed result ---------------------------------------------------

def test_recompute_after_result_corrected_by_compensation():
    lg = make_league(["t1", "t2", "t3"], [("t1", "t2"), ("t1", "t3"), ("t2", "t3")], spots=1)
    before = compute_standings(lg, EVENT, [game(lg, "m1", 1, 0), game(lg, "m2", 1, 0), game(lg, "m3", 1, 0)])
    assert [(r["team_id"], r["points"], r["status"]) for r in before["table"]] == [
        ("t1", 6, "clinched"), ("t2", 3, "eliminated"), ("t3", 0, "eliminated")]
    assert before["h2h"]["t1"]["t2"] == {"played": 1, "points": 3, "scored": 1, "conceded": 0}
    # t1's only goal in m1 is compensated: the projection now shows 0-0, a draw
    after = compute_standings(lg, EVENT, [game(lg, "m1", 0, 0), game(lg, "m2", 1, 0), game(lg, "m3", 1, 0)])
    assert after["table"] == [
        row(1, "t1", 2, 1, 1, 0, 1, 0, 1, 4, 0, "still_possible", "draw_lots"),
        row(2, "t2", 2, 1, 1, 0, 1, 0, 1, 4, 0, "still_possible"),
        row(3, "t3", 2, 0, 0, 2, 0, 2, -2, 0, 0, "eliminated"),
    ]
    assert after["h2h"]["t1"]["t2"] == {"played": 1, "points": 1, "scored": 0, "conceded": 0}
    assert after["h2h"]["t2"]["t1"] == {"played": 1, "points": 1, "scored": 0, "conceded": 0}
    # the tie-break winner of a level match is compensated: undecided, so m1 becomes remaining again
    undecided = compute_standings(lg, {**EVENT, "draw_allowed": False},
                                  [game(lg, "m1", 0, 0, outcome="undecided"), game(lg, "m2", 1, 0),
                                   game(lg, "m3", 1, 0)])
    assert [(r["team_id"], r["played"], r["points"], r["status"]) for r in undecided["table"]] == [
        ("t1", 1, 3, "still_possible"), ("t2", 1, 3, "still_possible"), ("t3", 2, 0, "eliminated")]
    assert undecided["h2h"]["t1"]["t2"] == ZERO


def test_tiebreak_winner_counts_as_a_win():
    lg = make_league(["t1", "t2"], [("t1", "t2")], spots=1)
    level = game(lg, "m1", 2, 2, outcome="away_win")
    result = compute_standings(lg, {**EVENT, "draw_allowed": False}, [level])
    assert result["table"] == [
        row(1, "t2", 1, 1, 0, 0, 2, 2, 0, 3, 0, "clinched"),
        row(2, "t1", 1, 0, 0, 1, 2, 2, 0, 0, 0, "eliminated"),
    ]
    assert result["h2h"]["t2"]["t1"] == {"played": 1, "points": 3, "scored": 2, "conceded": 2}
