"""Standings (5.10) as a pure function of the league config, the sport config and the match projections.

Nothing here branches on a sport id or event name: only on scoring_model and config values.
"""
from itertools import groupby

from ...core.signing import lots_key
from ..sports.logic import TIE_BREAK_TOKENS, effective_tie_break

# outcome -> (home result, away result); "undecided" and "pending" are deliberately absent
DECIDED = {"home_win": ("won", "lost"), "away_win": ("lost", "won"), "draw": ("drawn", "drawn")}
DEFAULT_POINTS = {"win": 3, "draw": 1, "loss": 0}
TABLE_FIELDS = {"overall_diff": "diff", "scored": "scored", "fair_play": "fair_play"}


def is_played(projection) -> bool:
    """Only completed matches with a decided outcome count as played; every other fixture is remaining."""
    return bool(projection) and projection.get("status") == "completed" and projection.get("outcome") in DECIDED


def overs_faced(side: dict, periods: dict) -> float:
    """Legal balls / balls_per_over as a decimal; an all-out innings counts as the full overs quota."""
    innings = periods.get("innings_per_team") or 1
    if (side.get("wickets") or 0) >= innings * periods["wickets_per_innings"]:
        return innings * periods["overs_per_innings"]
    return (side.get("balls") or 0) / periods["balls_per_over"]


def net_run_rate(runs_for, overs_for, runs_against, overs_against) -> float:
    rate_for = runs_for / overs_for if overs_for else 0.0
    rate_against = runs_against / overs_against if overs_against else 0.0
    return round(rate_for - rate_against, 3) + 0.0


def match_sides(fixtures: list[dict], projections: list[dict], sport: dict) -> list[dict]:
    """Two team-perspective records per played fixture, in fixture order."""
    innings_model = sport.get("scoring_model") == "innings"
    periods = sport.get("periods") or {}
    by_id = {p["match_id"]: p for p in projections if p}
    sides = []
    for fixture in fixtures:
        p = by_id.get(fixture["match_id"])
        if not is_played(p):
            continue
        home, away = p["home"], p["away"]
        home_result, away_result = DECIDED[p["outcome"]]
        home_overs = overs_faced(home, periods) if innings_model else 0
        away_overs = overs_faced(away, periods) if innings_model else 0
        sides.append({"team": fixture["home"], "opp": fixture["away"], "result": home_result,
                      "for": home.get("score") or 0, "against": away.get("score") or 0,
                      "overs_for": home_overs, "overs_against": away_overs, "fair_play": home.get("fair_play") or 0})
        sides.append({"team": fixture["away"], "opp": fixture["home"], "result": away_result,
                      "for": away.get("score") or 0, "against": home.get("score") or 0,
                      "overs_for": away_overs, "overs_against": home_overs, "fair_play": away.get("fair_play") or 0})
    return sides


def tally(sides: list[dict], teams: list[str], points: dict, model: str) -> dict:
    """Per-team table figures over the given sides (all league matches, or a mini-league)."""
    rows = {t: {"played": 0, "won": 0, "drawn": 0, "lost": 0, "scored": 0, "conceded": 0,
                "overs_for": 0.0, "overs_against": 0.0, "fair_play": 0} for t in teams}
    for s in sides:
        row = rows[s["team"]]
        row["played"] += 1
        row[s["result"]] += 1
        row["scored"] += s["for"]
        row["conceded"] += s["against"]
        row["overs_for"] += s["overs_for"]
        row["overs_against"] += s["overs_against"]
        row["fair_play"] += s["fair_play"]
    for row in rows.values():
        row["points"] = row["won"] * points["win"] + row["drawn"] * points["draw"] + row["lost"] * points["loss"]
        if model == "innings":
            row["diff"] = net_run_rate(row["scored"], row["overs_for"], row["conceded"], row["overs_against"])
        else:
            row["diff"] = row["scored"] - row["conceded"]
    return rows


def resolve(group: list[str], criteria: list[str], sort_keys) -> tuple[list[str], list]:
    """Order a tied group. Returns (ordered teams, separators) where separators[i] is the criterion
    that placed ordered[i] above ordered[i + 1]. On the first criterion whose values differ, the group
    splits into sub-groups by value (best first) and each sub-group restarts from the first criterion."""
    if len(group) < 2:
        return list(group), []
    for criterion in criteria:
        keys = sort_keys(criterion, group)
        distinct = sorted(set(keys.values()))
        if len(distinct) > 1:
            ordered, separators = [], []
            for n, key in enumerate(distinct):
                sub_ordered, sub_separators = resolve([t for t in group if keys[t] == key], criteria, sort_keys)
                if n:
                    separators.append(criterion)
                ordered += sub_ordered
                separators += sub_separators
            return ordered, separators
    return list(group), [None] * (len(group) - 1)


def qualification_status(team: str, points: dict, best: dict, spots: int) -> str:
    """Points only, tie-breaks ignored. best[T] = max(T) = points(T) + W * R(T). Clinched is checked first."""
    others = [x for x in points if x != team]
    if sum(1 for x in others if best[x] >= points[team]) < spots:
        return "clinched"
    if sum(1 for x in others if points[x] > best[team]) >= spots:
        return "eliminated"
    return "still_possible"


def tie_break_criteria(league: dict, sport: dict) -> list[str]:
    """The league's order, else the sport's, else the full list; draw_lots always last."""
    order = league.get("tie_break_order")
    if order is None:
        order = sport.get("tie_break_order")
    if order is None:
        order = TIE_BREAK_TOKENS
    return effective_tie_break(sport, [t for t in order if t in TIE_BREAK_TOKENS])


def compute_standings(league: dict, sport: dict, projections: list[dict]) -> dict:
    league_id = league["league_id"]
    teams = list(league["teams"])
    fixtures = list(league["fixtures"])
    model = sport.get("scoring_model")
    points = {**DEFAULT_POINTS, **(league.get("points") or {})}
    spots = (league.get("qualification") or {}).get("spots")
    seed = league.get("lots_seed")

    sides = match_sides(fixtures, projections, sport)
    rows = tally(sides, teams, points, model)

    by_id = {p["match_id"]: p for p in projections if p}
    remaining = {t: 0 for t in teams}
    for fixture in fixtures:
        if not is_played(by_id.get(fixture["match_id"])):
            remaining[fixture["home"]] += 1
            remaining[fixture["away"]] += 1

    def sort_keys(criterion: str, group: list[str]) -> dict:
        """Per-team keys where a smaller key is better."""
        if criterion == "draw_lots":
            return {t: lots_key(seed, league_id, t) for t in group}
        if criterion in TABLE_FIELDS:
            return {t: -rows[t][TABLE_FIELDS[criterion]] for t in group}
        members = set(group)
        mini = tally([s for s in sides if s["team"] in members and s["opp"] in members], group, points, model)
        field = "points" if criterion == "h2h_points" else "diff"
        return {t: -mini[t][field] for t in group}

    table_points = {t: rows[t]["points"] for t in teams}
    best = {t: table_points[t] + points["win"] * remaining[t] for t in teams}
    criteria = tie_break_criteria(league, sport)
    ordered, resolved_by = [], {}
    for _, tied in groupby(sorted(teams, key=lambda t: -rows[t]["points"]), key=lambda t: rows[t]["points"]):
        group_order, separators = resolve(list(tied), criteria, sort_keys)
        ordered += group_order
        resolved_by.update(zip(group_order, separators + [None]))

    table = []
    for rank, team in enumerate(ordered, 1):
        row = rows[team]
        table.append({
            "rank": rank, "team_id": team, "played": row["played"], "won": row["won"], "drawn": row["drawn"],
            "lost": row["lost"], "scored": row["scored"], "conceded": row["conceded"], "diff": row["diff"],
            "points": row["points"], "fair_play": row["fair_play"],
            "status": qualification_status(team, table_points, best, spots),
            "resolved_by": resolved_by[team],
        })

    result_points = {"won": points["win"], "drawn": points["draw"], "lost": points["loss"]}
    h2h = {a: {b: {"played": 0, "points": 0, "scored": 0, "conceded": 0} for b in teams if b != a} for a in teams}
    for s in sides:
        record = h2h[s["team"]][s["opp"]]
        record["played"] += 1
        record["points"] += result_points[s["result"]]
        record["scored"] += s["for"]
        record["conceded"] += s["against"]

    return {"league_id": league_id, "sport_id": league["sport_id"], "qualification_spots": spots,
            "table": table, "h2h": h2h}
