from fastapi import APIRouter, Depends

from ...core.errors import validation_error
from ...core.validate import Validator, is_id, is_int, json_body
from . import elimination, round_robin, swiss

router = APIRouter()

LIMITS = {"round_robin": (2, 40), "swiss": (2, 64), "single_elim": (2, 128), "double_elim": (3, 128)}
RESULTS = ("home", "away", "draw")


def param_fields(keys: list[str]) -> list[str]:
    """A bad params.<key> is reported as "params", "params.<key>" and "<key>"."""
    if not keys:
        return []
    out = ["params"]
    for key in keys:
        out += [f"params.{key}", key]
    return out


class Params:
    def __init__(self, raw):
        self.raw = raw if isinstance(raw, dict) else {}
        self.bad: list[str] = []

    def fail(self, key: str) -> None:
        if key not in self.bad:
            self.bad.append(key)

    def present(self, key: str) -> bool:
        return self.raw.get(key) is not None

    def integer(self, key: str, default, low=None, high=None):
        if not self.present(key):
            return default
        v = self.raw[key]
        if not is_int(v) or (low is not None and v < low) or (high is not None and v > high):
            self.fail(key)
            return None
        return v

    def boolean(self, key: str, default: bool):
        if not self.present(key):
            return default
        v = self.raw[key]
        if not isinstance(v, bool):
            self.fail(key)
            return None
        return v

    def choice(self, key: str, choices, default: str):
        if not self.present(key):
            return default
        v = self.raw[key]
        if not isinstance(v, str) or v not in choices:
            self.fail(key)
            return None
        return v

    def array(self, key: str):
        if not self.present(key):
            return []
        v = self.raw[key]
        if not isinstance(v, list):
            self.fail(key)
            return None
        return v


def _teams(v: Validator, fmt):
    teams = v.array("teams")
    if teams is None:
        return None
    low, high = LIMITS.get(fmt, (2, 128))
    if not low <= len(teams) <= high or not all(is_id(t) for t in teams) or len(set(teams)) != len(teams):
        v.bad("teams")
        return None
    return teams


def _round_robin(p: Params, teams):
    legs = p.integer("legs", 1, 1, 2)
    policy = p.choice("withdrawal_policy", round_robin.POLICIES, "forfeit_remaining")
    after = {}
    for item in p.array("withdrawals") or []:
        tid = item.get("team_id") if isinstance(item, dict) else None
        rnd = item.get("after_round") if isinstance(item, dict) else None
        if not is_id(tid) or not is_int(rnd) or tid in after:
            p.fail("withdrawals")
            break
        after[tid] = rnd
    if teams is not None and legs is not None and "withdrawals" not in p.bad:
        round_count = legs * round_robin.rounds_per_leg(len(teams))
        if any(t not in teams or not 0 <= r <= round_count for t, r in after.items()):
            p.fail("withdrawals")
    return lambda: round_robin.generate(teams, legs, after, policy)


def _parse_history(items, known):
    parsed = []
    for entry in items:
        if not isinstance(entry, dict) or not is_int(entry.get("round")) or not isinstance(entry.get("matches"), list):
            return None
        bye = entry.get("bye")
        if bye is not None and not is_id(bye):
            return None
        seen = [bye] if bye is not None else []
        matches = []
        for m in entry["matches"]:
            if not isinstance(m, dict) or m.get("result") not in RESULTS:
                return None
            if not is_id(m.get("home")) or not is_id(m.get("away")):
                return None
            seen += [m["home"], m["away"]]
            matches.append((m["home"], m["away"], m["result"]))
        if len(set(seen)) != len(seen) or (known is not None and not set(seen) <= known):
            return None
        parsed.append({"round": entry["round"], "bye": bye, "matches": matches})
    return parsed


def _history(p: Params, teams, rnd):
    items = p.array("history")
    if items is None:
        return None
    parsed = _parse_history(items, set(teams) if teams is not None else None)
    if parsed is None or (rnd is not None and sorted(e["round"] for e in parsed) != list(range(1, rnd))):
        p.fail("history")
        return None
    return parsed


def _swiss(p: Params, teams):
    n = len(teams) if teams is not None else None
    total = p.integer("total_rounds", swiss.default_total_rounds(n) if n else None, 1, n - 1 if n else None)
    rnd = p.integer("round", 1, 1, total)
    history = _history(p, teams, rnd if total is not None else None)
    return lambda: swiss.pair_round(teams, total, rnd, history)


def _results(p: Params):
    out = []
    for item in p.array("results") or []:
        if not isinstance(item, dict) or not is_id(item.get("match_id")) or not is_id(item.get("winner")):
            p.fail("results")
            return None
        out.append((item["match_id"], item["winner"]))
    return out


def _bracket(build):
    try:
        return build()
    except elimination.ResultError as exc:
        raise validation_error(param_fields(["results"]), str(exc))


def _single(p: Params, teams):
    third = p.boolean("third_place_match", False)
    results = _results(p)
    return lambda: _bracket(lambda: elimination.single_elim(teams, third, results))


def _double(p: Params, teams):
    reset = p.boolean("bracket_reset", False)
    results = _results(p)
    return lambda: _bracket(lambda: elimination.double_elim(teams, reset, results))


FORMATS = {"round_robin": _round_robin, "swiss": _swiss, "single_elim": _single, "double_elim": _double}


@router.post("/audit/v1/fixtures")
def generate_fixtures(body=Depends(json_body)):
    v = Validator(body)
    fmt = v.enum("format", FORMATS)
    teams = _teams(v, fmt)
    p = Params(v.obj("params", required=False))
    job = FORMATS[fmt](p, teams) if fmt is not None else None
    fields = v.errors + param_fields(p.bad)
    if fields:
        raise validation_error(fields)
    return job()
