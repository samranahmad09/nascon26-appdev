"""Exact minimum-cost rematch-free pairing for Swiss rounds (Section 5.7-B (d)).

Nodes are given in standings order, so their scores never increase along the list and
the cost of pairing positions p < q is score[p] - score[q]. Edmonds' cardinality blossom
algorithm decides whether any rematch-free pairing exists; the weighted blossom algorithm
(weighted.py) then gives the exact minimum cost in O(n^3). A short depth-first search
re-finds a pairing of that cost with the top of the table paired first (the spec
example's tie-break), pruned by the cost of pairing the remaining teams adjacently in
score order; past its time budget the weighted solution is used as is.
"""
import time
from collections import deque

from .weighted import max_weight_matching

SEARCH_SECONDS = 0.25
_INF = 1 << 40


class _Timeout(Exception):
    pass


def _augment(root: int, n: int, adj: list[list[int]], match: list[int]) -> bool:
    used = [False] * n
    parent = [-1] * n
    base = list(range(n))
    used[root] = True
    queue = deque([root])

    def lca(a: int, b: int) -> int:
        seen = [False] * n
        while True:
            a = base[a]
            seen[a] = True
            if match[a] == -1:
                break
            a = parent[match[a]]
        while True:
            b = base[b]
            if seen[b]:
                return b
            b = parent[match[b]]

    def mark_path(v: int, b: int, child: int, blossom: list[bool]) -> None:
        while base[v] != b:
            blossom[base[v]] = blossom[base[match[v]]] = True
            parent[v] = child
            child = match[v]
            v = parent[match[v]]

    while queue:
        v = queue.popleft()
        for to in adj[v]:
            if base[v] == base[to] or match[v] == to:
                continue
            if to == root or (match[to] != -1 and parent[match[to]] != -1):
                cur = lca(v, to)
                blossom = [False] * n
                mark_path(v, cur, to, blossom)
                mark_path(to, cur, v, blossom)
                for i in range(n):
                    if blossom[base[i]]:
                        base[i] = cur
                        if not used[i]:
                            used[i] = True
                            queue.append(i)
            elif parent[to] == -1:
                parent[to] = v
                if match[to] == -1:
                    while to != -1:
                        pv = parent[to]
                        nxt = match[pv]
                        match[to], match[pv] = pv, to
                        to = nxt
                    return True
                used[match[to]] = True
                queue.append(match[to])
    return False


def perfect_matching(n: int, adj: list[list[int]]) -> list[int] | None:
    """Edmonds' blossom algorithm; returns mate[] if a perfect matching exists."""
    match = [-1] * n
    for v in range(n):
        if match[v] == -1:
            for u in adj[v]:
                if match[u] == -1:
                    match[u], match[v] = v, u
                    break
    for root in range(n):
        if match[root] == -1 and not _augment(root, n, adj, match):
            return None
    return match


def _bits(mask: int) -> list[int]:
    out = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return out


def _first_optimal(score: list[int], allow: list[int], target: int, deadline: float) -> list[tuple[int, int]]:
    """Depth-first search in standings order for a pairing that costs exactly target.

    target is the known optimum, so the first complete pairing found under the cap is
    optimal; this only picks the natural tie-break (top of the table paired first).
    """
    lower: dict[int, int] = {}
    choice: dict[int, int] = {}

    def solve(mask: int, cap: int) -> int | None:
        if mask == 0:
            return 0
        if lower.get(mask, 0) >= cap:
            return None
        if time.monotonic() > deadline:
            raise _Timeout
        pos = _bits(mask)
        for p in pos:
            if not allow[p] & mask:
                lower[mask] = _INF
                return None
        x = [score[p] for p in pos]
        floor = sum(x[i] - x[i + 1] for i in range(0, len(x), 2))
        if floor >= cap:
            lower[mask] = max(lower.get(mask, 0), floor)
            return None
        first, rest = pos[0], x[1:]
        size = len(rest)
        pre = [0] * (size + 1)
        for t in range(2, size + 1, 2):
            pre[t] = pre[t - 2] + rest[t - 2] - rest[t - 1]
        suf = [0] * (size + 2)
        for i in range(size - 2, 0, -2):
            suf[i] = rest[i] - rest[i + 1] + suf[i + 2]
        without_first = mask & ~(1 << first)
        for t in range(size):
            cost = x[0] - rest[t]
            if cost >= cap:
                break
            q = pos[t + 1]
            if not (allow[first] >> q) & 1:
                continue
            bound = pre[t] + suf[t + 1] if t % 2 == 0 else pre[t - 1] + rest[t - 1] - rest[t + 1] + suf[t + 2]
            if cost + bound >= cap:
                continue
            sub = solve(without_first & ~(1 << q), cap - cost)
            if sub is not None:
                choice[mask] = q
                return cost + sub
        lower[mask] = max(lower.get(mask, 0), cap)
        return None

    mask = (1 << len(score)) - 1
    if solve(mask, target + 1) is None:
        raise _Timeout
    pairs = []
    while mask:
        first = (mask & -mask).bit_length() - 1
        q = choice[mask]
        pairs.append((first, q))
        mask &= ~((1 << first) | (1 << q))
    return pairs


def min_cost_pairing(nodes: list[int], score: list[int], played: list[set]) -> list[tuple[int, int]] | None:
    """Pairs every node, no rematches, minimum sum of |score difference|.

    nodes must be in standings order (score non-increasing). Returns (better, worse)
    pairs ordered by the better-placed node, or None when no rematch-free pairing exists.
    """
    m = len(nodes)
    if m == 0:
        return []
    adj = [[q for q in range(m) if q != p and nodes[q] not in played[nodes[p]]] for p in range(m)]
    if perfect_matching(m, adj) is None:
        return None
    s = [score[x] for x in nodes]
    top = s[0] - s[-1] + 1
    mate = max_weight_matching(m, [(p, q, top - (s[p] - s[q])) for p in range(m) for q in adj[p] if p < q])
    optimal = sorted((p, mate[p]) for p in range(m) if p < mate[p])
    target = sum(s[p] - s[q] for p, q in optimal)
    allow = [sum(1 << q for q in adj[p]) for p in range(m)]
    try:
        pairs = _first_optimal(s, allow, target, time.monotonic() + SEARCH_SECONDS)
    except _Timeout:
        pairs = optimal
    return [(nodes[p], nodes[q]) for p, q in pairs]
