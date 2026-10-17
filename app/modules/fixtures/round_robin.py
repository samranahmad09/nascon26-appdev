"""Round robin by the circle method (Section 5.7-A).

Circle teams sit at positions 0..M-1 (M odd); for even n the last team is the pivot.
Round k rotates the circle by k: the team at position 0 meets the pivot (or has the bye
for odd n) and position p meets position M-p. The team at position p (first half) is
home, which gives every circle team (M-1)/2 home and (M-1)/2 away games per leg; the
pivot game alternates by round, so every |home - away| stays <= 1. Leg 2 mirrors leg 1.
"""

POLICIES = ("forfeit_remaining", "void_all")


def rounds_per_leg(n: int) -> int:
    return n if n % 2 else n - 1


def _leg_rounds(teams: list[str]) -> list[tuple[list[tuple[str, str]], list[str]]]:
    n = len(teams)
    size = rounds_per_leg(n)
    out = []
    for k in range(size):
        pairs, byes = [], []
        if n % 2:
            byes.append(teams[k])
        else:
            pivot, other = teams[n - 1], teams[k]
            pairs.append((other, pivot) if k % 2 == 0 else (pivot, other))
        for p in range(1, (size - 1) // 2 + 1):
            pairs.append((teams[(k + p) % size], teams[(k - p) % size]))
        out.append((pairs, byes))
    return out


def _status(rnd: int, home: str, away: str, after: dict, policy: str) -> tuple[str, str | None]:
    void_all = policy == "void_all"
    home_out = home in after and (void_all or rnd > after[home])
    away_out = away in after and (void_all or rnd > after[away])
    if home_out and away_out:
        return "voided", None
    if home_out or away_out:
        if void_all:
            return "voided", None
        return "forfeit", away if home_out else home
    return "scheduled", None


def generate(teams: list[str], legs: int, after: dict, policy: str) -> dict:
    base = _leg_rounds(teams)
    per_leg = len(base)
    summary = {"total": 0, "scheduled": 0, "forfeit": 0, "voided": 0}
    rounds = []
    for leg in range(1, legs + 1):
        for k, (pairs, byes) in enumerate(base):
            rnd = (leg - 1) * per_leg + k + 1
            matches = []
            for index, (home, away) in enumerate(pairs, 1):
                if leg == 2:
                    home, away = away, home
                status, awarded = _status(rnd, home, away, after, policy)
                summary["total"] += 1
                summary[status] += 1
                matches.append({"match_id": f"RR-L{leg}-R{rnd}-M{index}", "leg": leg, "home": home,
                                "away": away, "status": status, "awarded_to": awarded})
            rounds.append({"round": rnd, "matches": matches, "byes": list(byes)})
    return {"format": "round_robin", "legs": legs, "team_count": len(teams),
            "round_count": per_leg * legs, "rounds": rounds, "summary": summary}
