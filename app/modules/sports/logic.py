"""Sport rule sets are pure data. Nothing in the codebase may branch on a sport id or event name."""
import json
import re
from pathlib import Path

from ...core.validate import Validator, is_int

MODELS = ("event", "innings", "round")
OVERTIME_POLICIES = ("none", "extra_time", "shootout", "golden_score", "super_over")
TIE_BREAK_TOKENS = ("h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots")
SPORT_ID_RE = r"[a-z0-9_-]{2,40}"
EVENT_KEY_RE = re.compile(r"[a-z0-9_]{1,40}")
PERIOD_KEYS = {
    "event": ("count", "duration_minutes"),
    "innings": ("innings_per_team", "overs_per_innings", "balls_per_over", "wickets_per_innings"),
    "round": ("best_of",),
}


def _valid_event_value(model: str, value) -> dict | None:
    if not isinstance(value, dict):
        return None
    if model == "event":
        p = value.get("points")
        return {"points": p} if is_int(p) and p >= 1 else None
    if model == "innings":
        runs, legal, wicket = value.get("runs"), value.get("legal_ball"), value.get("wicket")
        if is_int(runs) and runs >= 0 and isinstance(legal, bool) and isinstance(wicket, bool):
            return {"runs": runs, "legal_ball": legal, "wicket": wicket}
        return None
    r = value.get("rounds")
    return {"rounds": r} if is_int(r) and r >= 1 else None


def validate_sport(body) -> dict:
    v = Validator(body)
    sport_id = v.string("sport_id", min_len=2, max_len=40, regex=SPORT_ID_RE)
    name = v.string("name", min_len=1, max_len=80)
    model = v.enum("scoring_model", MODELS)
    periods = v.obj("periods")
    events = v.obj("scoring_events")
    draw_allowed = v.boolean("draw_allowed")
    overtime = v.obj("overtime")
    tie_break = v.array("tie_break_order", required=False)
    roster = v.obj("roster")

    norm_periods = None
    if periods is not None and model is not None:
        keys = PERIOD_KEYS[model]
        if all(is_int(periods.get(k)) and periods[k] >= 1 for k in keys) and \
                (model != "round" or periods["best_of"] % 2 == 1):
            norm_periods = {k: periods[k] for k in keys}
        else:
            v.bad("periods")

    norm_events = None
    if events is not None:
        if not events or not all(isinstance(k, str) and EVENT_KEY_RE.fullmatch(k) for k in events):
            v.bad("scoring_events")
        elif model is not None:
            norm_events = {}
            for key, value in events.items():
                ok = _valid_event_value(model, value)
                if ok is None:
                    v.bad("scoring_events")
                    break
                norm_events[key] = ok

    norm_overtime = None
    if overtime is not None:
        policy = overtime.get("policy")
        duration = overtime.get("duration_minutes")
        if policy not in OVERTIME_POLICIES or (duration is not None and not (is_int(duration) and duration >= 0)):
            v.bad("overtime")
        else:
            norm_overtime = {"policy": policy}
            if duration is not None:
                norm_overtime["duration_minutes"] = duration

    norm_tie_break = list(TIE_BREAK_TOKENS)
    if tie_break is not None:
        if not all(isinstance(t, str) and t in TIE_BREAK_TOKENS for t in tie_break) or len(set(tie_break)) != len(tie_break):
            v.bad("tie_break_order")
        else:
            norm_tie_break = list(tie_break)

    norm_roster = None
    if roster is not None:
        lo, hi, req = roster.get("min_squad_size"), roster.get("max_squad_size"), roster.get("required_positions")
        if is_int(lo) and is_int(hi) and 1 <= lo <= hi and isinstance(req, dict) and \
                all(isinstance(k, str) and k and is_int(n) and n >= 0 for k, n in req.items()):
            norm_roster = {"min_squad_size": lo, "max_squad_size": hi, "required_positions": dict(req)}
        else:
            v.bad("roster")

    v.check()
    return {
        "sport_id": sport_id,
        "name": name,
        "scoring_model": model,
        "periods": norm_periods,
        "scoring_events": norm_events,
        "draw_allowed": draw_allowed,
        "overtime": norm_overtime,
        "tie_break_order": norm_tie_break,
        "roster": norm_roster,
    }


def effective_tie_break(config: dict, override=None) -> list[str]:
    """The order actually applied: override (or the sport's), with draw_lots forced last."""
    order = [t for t in (override if override is not None else config.get("tie_break_order") or TIE_BREAK_TOKENS)
             if t != "draw_lots"]
    return order + ["draw_lots"]


def load_seed_sports() -> list[dict]:
    return json.loads(Path(__file__).with_name("seed_sports.json").read_text(encoding="utf-8"))
