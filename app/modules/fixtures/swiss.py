"""Swiss pairing, one round per call (Section 5.7-B). Scores are kept in half points."""
from ...core.errors import conflict
from .matching import min_cost_pairing


def default_total_rounds(n: int) -> int:
    return max(1, (n - 1).bit_length())


def _later_round(n: int, half: list[int], played: list[set], had_bye: list[bool], order: list[int]):
    if n % 2:
        candidates = sorted((i for i in range(n) if not had_bye[i]), key=lambda i: (half[i], -i))
    else:
        candidates = [None]
    for bye in candidates:
        pairs = min_cost_pairing([i for i in order if i != bye], half, played)
        if pairs is not None:
            return pairs, bye
    return None, None


def pair_round(teams: list[str], total_rounds: int, rnd: int, history: list[dict]) -> dict:
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    half = [0] * n
    opponents: list[list[int]] = [[] for _ in range(n)]
    had_bye = [False] * n
    for entry in history:
        if entry["bye"] is not None:
            b = idx[entry["bye"]]
            half[b] += 2
            had_bye[b] = True
        for home, away, result in entry["matches"]:
            h, a = idx[home], idx[away]
            if result == "home":
                half[h] += 2
            elif result == "away":
                half[a] += 2
            else:
                half[h] += 1
                half[a] += 1
            opponents[h].append(a)
            opponents[a].append(h)
    buchholz = [sum(half[o] for o in opponents[i]) for i in range(n)]
    order = sorted(range(n), key=lambda i: (-half[i], -buchholz[i], i))
    standings = [{"rank": r, "team_id": teams[i], "score": half[i] / 2, "buchholz": buchholz[i] / 2}
                 for r, i in enumerate(order, 1)]
    if rnd == 1:
        m = n // 2
        pairs = [(i, i + m) for i in range(m)]
        bye = n - 1 if n % 2 else None
    else:
        played = [set(o) for o in opponents]
        pairs, bye = _later_round(n, half, played, had_bye, order)
        if pairs is None:
            raise conflict("NO_VALID_PAIRING", f"No rematch-free pairing exists for round {rnd}.", {"round": rnd})
    pairings = [{"match_id": f"SW-R{rnd}-M{k}", "home": teams[h], "away": teams[a]}
                for k, (h, a) in enumerate(pairs, 1)]
    return {"format": "swiss", "round": rnd, "total_rounds": total_rounds, "pairings": pairings,
            "bye": teams[bye] if bye is not None else None, "standings": standings}
