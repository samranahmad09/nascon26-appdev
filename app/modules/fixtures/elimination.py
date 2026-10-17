"""Single and double elimination brackets (Section 5.7-C/D).

A bracket is a graph of matches whose winner_to / loser_to links carry teams forward.
A slot that can never be filled (a missing seed, or the loser of a bye) holds EMPTY;
a match with an EMPTY slot is a bye and settles as soon as the other slot is known.
"""


class _Empty:
    def __repr__(self) -> str:
        return "EMPTY"


EMPTY = _Empty()


class ResultError(Exception):
    pass


def seed_order(size: int) -> list[int]:
    order = [1, 2]
    while len(order) < size:
        total = 2 * len(order) + 1
        order = [x for s in order for x in (s, total - s)]
    return order


def bracket_size(n: int) -> int:
    size = 2
    while size < n:
        size *= 2
    return size


class Match:
    __slots__ = ("match_id", "bracket", "round", "index", "home", "away", "bye", "winner", "loser",
                 "winner_to", "loser_to", "decided", "gate")

    def __init__(self, match_id: str, bracket: str, rnd: int, index: int):
        self.match_id, self.bracket, self.round, self.index = match_id, bracket, rnd, index
        self.home = self.away = self.winner = self.loser = None
        self.winner_to = self.loser_to = self.gate = None
        self.bye = self.decided = False


def _team(x):
    return x if isinstance(x, str) else None


def _link(target):
    return None if target is None else {"match_id": target[0].match_id, "slot": target[1]}


class Bracket:
    def __init__(self, teams: list[str]):
        self.teams = teams
        self.seed = {t: i + 1 for i, t in enumerate(teams)}
        self.matches: dict[str, Match] = {}

    def add(self, match_id: str, bracket: str, rnd: int, index: int) -> Match:
        m = Match(match_id, bracket, rnd, index)
        self.matches[match_id] = m
        return m

    def place(self, m: Match, slot: str, team) -> None:
        setattr(m, slot, team)
        if team is EMPTY:
            m.bye = True
        if not m.decided and EMPTY in (m.home, m.away):
            other = m.away if m.home is EMPTY else m.home
            if other is not None:
                self.decide(m, other, EMPTY)

    def decide(self, m: Match, winner, loser) -> None:
        m.decided, m.winner, m.loser = True, winner, loser
        if m.winner_to is not None:
            self.place(*m.winner_to, winner)
        if m.loser_to is not None:
            self.place(*m.loser_to, loser)

    def seed_first_round(self, first: list[Match]) -> None:
        order = seed_order(2 * len(first))
        n = len(self.teams)
        for i, m in enumerate(first):
            high, low = order[2 * i], order[2 * i + 1]
            self.place(m, "home", self.teams[high - 1])
            self.place(m, "away", self.teams[low - 1] if low <= n else EMPTY)

    def _ready(self, m: Match) -> bool:
        return isinstance(m.home, str) and isinstance(m.away, str) and (m.gate is None or m.gate())

    def apply_results(self, results: list[tuple[str, str]]) -> None:
        """Applies results in dependency order; any result that cannot apply is a 400."""
        pending = []
        for match_id, winner in results:
            if match_id not in self.matches:
                raise ResultError(f"Unknown match_id {match_id}.")
            pending.append((self.matches[match_id], winner))
        while pending:
            waiting = []
            for m, winner in pending:
                if m.decided:
                    if m.winner != winner:
                        raise ResultError(f"{winner} is not the winner of {m.match_id}.")
                elif not self._ready(m):
                    waiting.append((m, winner))
                elif winner not in (m.home, m.away):
                    raise ResultError(f"{winner} is not a participant of {m.match_id}.")
                else:
                    self.decide(m, winner, m.away if winner == m.home else m.home)
            if len(waiting) == len(pending):
                m, winner = waiting[0]
                raise ResultError(f"{winner} is not a participant of {m.match_id}.")
            pending = waiting

    def render(self, m: Match) -> dict:
        return {"match_id": m.match_id, "bracket": m.bracket, "round": m.round, "index": m.index,
                "home": _team(m.home), "away": _team(m.away),
                "home_seed": self.seed.get(_team(m.home)), "away_seed": self.seed.get(_team(m.away)),
                "bye": m.bye, "winner": _team(m.winner), "loser": _team(m.loser),
                "winner_to": _link(m.winner_to), "loser_to": _link(m.loser_to)}


def _slot(i: int) -> str:
    return "home" if i % 2 == 0 else "away"


def _winners_rounds(br: Bracket, size: int, prefix: str, bracket: str) -> list[list[Match]]:
    rounds, count, r = [], size // 2, 1
    while count >= 1:
        rounds.append([br.add(f"{prefix}{r}M{i}", bracket, r, i) for i in range(1, count + 1)])
        count //= 2
        r += 1
    for r in range(len(rounds) - 1):
        for i, m in enumerate(rounds[r]):
            m.winner_to = (rounds[r + 1][i // 2], _slot(i))
    return rounds


def single_elim(teams: list[str], third_place: bool, results: list[tuple[str, str]]) -> dict:
    size = bracket_size(len(teams))
    br = Bracket(teams)
    rounds = _winners_rounds(br, size, "R", "main")
    third = None
    if third_place and len(rounds) >= 2:
        third = br.add("TP", "third_place", len(rounds), 1)
        for i, semi in enumerate(rounds[-2]):
            semi.loser_to = (third, _slot(i))
    br.seed_first_round(rounds[0])
    br.apply_results(results)
    out = [{"round": r, "matches": [br.render(m) for m in ms]} for r, ms in enumerate(rounds, 1)]
    if third is not None:
        out[-1]["matches"].append(br.render(third))
    return {"format": "single_elim", "bracket_size": size, "rounds": out}


def double_elim(teams: list[str], bracket_reset: bool, results: list[tuple[str, str]]) -> dict:
    size = bracket_size(len(teams))
    br = Bracket(teams)
    wb = _winners_rounds(br, size, "WR", "winners")
    k = len(wb)
    lb = []
    for r in range(1, 2 * (k - 1) + 1):
        count = size // 2 ** ((r + 1) // 2 + 1)
        lb.append([br.add(f"LR{r}M{i}", "losers", r, i) for i in range(1, count + 1)])
    gf1 = br.add("GF1", "grand_final", 1, 1)
    gf2 = br.add("GF2", "grand_final", 2, 1) if bracket_reset else None

    wb[-1][0].winner_to = (gf1, "home")
    for i, m in enumerate(wb[0]):
        m.loser_to = (lb[0][i // 2], _slot(i))
    for r in range(2, k + 1):
        drop = lb[2 * r - 3]
        for i, m in enumerate(wb[r - 1]):
            m.loser_to = (drop[len(drop) - 1 - i], "away")
    for idx, ms in enumerate(lb):
        for i, m in enumerate(ms):
            if idx == len(lb) - 1:
                m.winner_to = (gf1, "away")
            elif idx % 2 == 0:
                m.winner_to = (lb[idx + 1][i], "home")
            else:
                m.winner_to = (lb[idx + 1][i // 2], _slot(i))
    if gf2 is not None:
        gf1.winner_to, gf1.loser_to = (gf2, "away"), (gf2, "home")
        gf2.gate = lambda: gf1.decided and gf1.winner == gf1.away

    br.seed_first_round(wb[0])
    br.apply_results(results)

    def render(m: Match) -> dict:
        out = br.render(m)
        if m is gf2:
            out["conditional"] = True
            out["required"] = (gf1.winner == gf1.away) if gf1.decided else None
        else:
            out["conditional"] = False
            out["required"] = True
        return out

    def rounds_of(rounds):
        return [{"round": r, "matches": [render(m) for m in ms]} for r, ms in enumerate(rounds, 1)]

    return {"format": "double_elim", "bracket_size": size, "bracket_reset": bracket_reset,
            "winners": rounds_of(wb), "losers": rounds_of(lb),
            "grand_final": [render(m) for m in (gf1, gf2) if m is not None]}
