"""Read-only data behind the Guest Web Player (FR-4.2). Sibling Day 2 modules are optional: a lookup whose
module is missing or fails renders the page's empty state instead of breaking it, and runs inside a savepoint
so a failed query never poisons the caller's transaction."""
import importlib
import logging

from ...core.registry import register

log = logging.getLogger("web")

FEATURED_MATCH = "match-m1"
FALLBACK_PRODUCT = "prod-jersey-001"
PREFERRED_SIZE = "L"
NOTICE_STATES = ("postponed", "cancelled", "abandoned")


def optional(path: str):
    try:
        return importlib.import_module(path)
    except ImportError:
        return None


def _guarded(conn, fn, *args):
    try:
        with conn.transaction():
            return fn(conn, *args)
    except Exception:
        log.exception("web lookup %s failed", getattr(fn, "__name__", fn))
        return None


def match_route(match_id: str, status: str | None) -> str:
    if status == "upcoming":
        return f"/web/matches/{match_id}/preview"
    if status == "in_progress":
        return f"/web/matches/{match_id}/live"
    if status == "completed":
        return f"/web/matches/{match_id}/vod"
    return f"/web/matches/{match_id}/notice?state={status}"


def _projection(conn, match_id: str) -> dict | None:
    scoring = optional("app.modules.scoring.service")
    if scoring is None or not match_id:
        return None
    return _guarded(conn, scoring.project, match_id)


def _club_names(conn, ids) -> dict:
    rows = conn.execute("SELECT club_id, name FROM clubs WHERE club_id = ANY(%s)", (list(ids),)).fetchall()
    return {r["club_id"]: r["name"] for r in rows}


def score_text(home_score, away_score) -> str:
    return f"{home_score} - {away_score}"


def _side(block: dict, names: dict) -> dict:
    return {"team_id": block["team_id"], "name": names.get(block["team_id"], block["team_id"]),
            "score": block["score"]}


def cart_variant(product: dict | None) -> dict | None:
    variants = (product or {}).get("variants") or []
    for v in variants:
        if str(v.get("size") or "").upper() == PREFERRED_SIZE:
            return v
    return variants[0] if variants else None


def price_label(variant: dict | None) -> str:
    if not variant or variant.get("price_minor") is None:
        return ""
    minor, currency = int(variant["price_minor"]), variant.get("currency") or "PKR"
    amount = f"{minor // 100:,}" if minor % 100 == 0 else f"{minor / 100:,.2f}"
    return f"{currency} {amount}"


def product_view(product: dict | None) -> dict | None:
    if not product:
        return None
    variant = cart_variant(product)
    return {**product, "cart_variant_id": variant["variant_id"] if variant else None,
            "cart_variant_size": variant.get("size") if variant else None, "price_label": price_label(variant)}


def pinned_product(conn, match_id: str | None) -> dict | None:
    store = optional("app.modules.store.service")
    if store is None:
        return None
    product = _guarded(conn, store.pinned_product, match_id) if match_id else None
    if product is None:
        catalog = optional("app.modules.store.catalog")
        product = _guarded(conn, catalog.product_payload, FALLBACK_PRODUCT) if catalog else None
    return product_view(product)


def _active_keys(conn, player_ids):
    return optional("app.modules.streaming.service").active_keys(conn, player_ids)


def playback_url(conn, entity_id: str, club_ids=(), fallback: str | None = None) -> str | None:
    """Live HLS URL for an entity, or None (the page shows "stream starting"). The streaming module's
    playback_url_for knows what is publishing right now, so its answer is final when it exists; without it, the
    active solo key of a player from either club (stream keys belong to players), else `fallback`."""
    streaming = optional("app.modules.streaming.service")
    for name in ("playback_url_for", "active_playback_url"):
        fn = getattr(streaming, name, None)
        if callable(fn):
            url = _guarded(conn, fn, entity_id)
            return url if isinstance(url, str) and url else None
    if streaming is not None and club_ids and hasattr(streaming, "active_keys") and hasattr(streaming, "playback_url"):
        players = [r["player_id"] for r in conn.execute(
            "SELECT player_id FROM players WHERE club_id = ANY(%s) ORDER BY player_id", (list(club_ids),)).fetchall()]
        keys = _guarded(conn, _active_keys, players) or {}
        for player_id in players:
            if player_id in keys:
                return streaming.playback_url(keys[player_id])
    return fallback


def match_overlay(conn, match_id: str) -> dict | None:
    """Everything the player page shows for a match, or None if the match has no event stream."""
    p = _projection(conn, match_id)
    if p is None:
        return None
    names = _club_names(conn, (p["home"]["team_id"], p["away"]["team_id"]))
    return {
        "match_id": p["match_id"],
        "status": p["status"],
        "route": match_route(p["match_id"], p["status"]),
        "home": _side(p["home"], names),
        "away": _side(p["away"], names),
        "score_text": score_text(p["home"]["score"], p["away"]["score"]),
        "pinned_product": pinned_product(conn, p["match_id"]),
        "playback_url": playback_url(conn, p["match_id"], (p["home"]["team_id"], p["away"]["team_id"])),
        "chat_room_id": f"chat-{p['match_id']}",
        "ws_path": f"/ws/matches/{p['match_id']}",
    }


def costream_overlay(conn, session_id: str) -> dict:
    """Co-stream page data. The session may not exist yet (hosts join later), so this never returns None; the
    score banner follows the featured live match because a co-stream carries no match id."""
    costream = optional("app.modules.costream.service")
    fn = getattr(costream, "get_session", None)
    session = _guarded(conn, fn, session_id) if callable(fn) else None
    session = session if isinstance(session, dict) else None
    match = match_overlay(conn, (session or {}).get("match_id") or FEATURED_MATCH)
    participants = [p for p in (session or {}).get("active_participants") or [] if isinstance(p, str)]
    names = {r["player_id"]: r["name"] for r in conn.execute(
        "SELECT player_id, name FROM players WHERE player_id = ANY(%s)", (participants,)).fetchall()}
    return {
        "session_id": session_id,
        "exists": session is not None,
        "status": (session or {}).get("status", "waiting"),
        "active_host": (session or {}).get("active_host"),
        "active_participants": participants,
        "participant_names": names,
        "layout": (session or {}).get("layout"),
        "playback_url": playback_url(conn, session_id, fallback=(session or {}).get("composite_playback_url")
                                     if (session or {}).get("status") != "terminated" else None),
        "chat_room_id": (session or {}).get("chat_room_id") or f"chat-{session_id}",
        "match": match,
        "pinned_product": match["pinned_product"] if match else pinned_product(conn, None),
    }


def player_profile(conn, player_id: str) -> dict | None:
    row = conn.execute("SELECT p.player_id, p.name, p.position, p.role, p.shirt_number, p.club_id, c.name AS club_name "
                       "FROM players p LEFT JOIN clubs c ON c.club_id = p.club_id WHERE p.player_id = %s",
                       (player_id,)).fetchone()
    return dict(row) if row else None


def club_profile(conn, club_id: str) -> dict | None:
    row = conn.execute("SELECT club_id, name, league_id, profile FROM clubs WHERE club_id = %s", (club_id,)).fetchone()
    if row is None:
        return None
    players = conn.execute("SELECT player_id, name, position, shirt_number FROM players WHERE club_id = %s "
                           "ORDER BY shirt_number NULLS LAST, player_id", (club_id,)).fetchall()
    profile = row["profile"] or {}
    venue = profile.get("home_venue") or {}
    return {"club_id": row["club_id"], "name": row["name"], "league_id": row["league_id"],
            "venue": venue.get("name") if isinstance(venue, dict) else None,
            "players": [dict(p) for p in players]}


def seed_pin(conn, opts: dict) -> None:
    """Baseline: the featured match shows the club jersey unless another seeder already pinned something."""
    store = optional("app.modules.store.service")
    if store is None or not hasattr(store, "set_pin"):
        return
    if _guarded(conn, store.pinned_product, FEATURED_MATCH) is None:
        _guarded(conn, store.set_pin, FEATURED_MATCH, FALLBACK_PRODUCT, "system")


register("", [], seed_pin, order=920)
