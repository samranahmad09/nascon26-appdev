"""Stream key lifecycle (5.4) and the MediaMTX publish/read authorisation hook.

One sk_keys row per key ever issued. A player's current key is the one issued (or re-issued) most
recently; every other key of theirs is revoked. Keys are global because a key names one media path,
live/{stream_key}."""
import secrets
import threading
import time

from ...core import db
from ...core.errors import ApiError, conflict, not_found
from ...core.registry import register
from ...core.signing import sign_day2
from ...core.validate import Validator
from . import media

SCHEMA = """
CREATE TABLE IF NOT EXISTS sk_keys (
    stream_key TEXT PRIMARY KEY, player_id TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL,
    seq BIGSERIAL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS sk_keys_player_seq ON sk_keys (player_id, seq);
"""

ACTIONS = ("generate", "rotate", "revoke")
STATUS_FOR_ACTION = {"generate": 201, "rotate": 200, "revoke": 200}
DEFAULT_LABEL = "default"
KEY_PREFIX = "sk_live_"
LIVE_PREFIX = "live/"
COSTREAM_PREFIX = "costream/"
OPEN_ACTIONS = ("api", "metrics", "pprof")
MEDIA_ACTIONS = ("publish", "read", "playback")
AUTH_TTL_S = 2.0
AUTH_CACHE_MAX = 10000
MISS = object()


def live_path(stream_key: str) -> str:
    return LIVE_PREFIX + stream_key


def ingest_endpoints(stream_key: str) -> dict:
    return {"rtmp": f"rtmp://localhost:1935/live/{stream_key}",
            "webrtc": f"http://localhost:8889/live/{stream_key}/whip"}


def playback_url(stream_key: str) -> str:
    return f"http://localhost:8888/live/{stream_key}/index.m3u8"


def key_body(action: str, player_id: str, label: str, stream_key: str, status: str) -> dict:
    active = status == "active"
    sig_payload = {"action": action, "player_id": player_id, "status": status, "stream_key": stream_key}
    return {
        "player_id": player_id,
        "label": label,
        "stream_key": stream_key,
        "status": status,
        "ingest_endpoints": ingest_endpoints(stream_key) if active else None,
        "playback_url": playback_url(stream_key) if active else None,
        "signature": sign_day2(stream_key, sig_payload),
    }


def player_exists(conn, player_id: str) -> bool:
    return conn.execute("SELECT 1 FROM players WHERE player_id = %s", (player_id,)).fetchone() is not None


def new_key(conn) -> str:
    while True:
        key = KEY_PREFIX + secrets.token_hex(8)
        if conn.execute("SELECT 1 FROM sk_keys WHERE stream_key = %s", (key,)).fetchone() is None:
            return key


def handle(conn, body) -> tuple[int, dict, list[str]]:
    """Returns (http status, response body, media paths to sever once the transaction commits)."""
    v = Validator(body)
    action = v.enum("action", ACTIONS)
    player_id = v.string("player_id")
    label = v.string("label", required=False, max_len=64)
    override = v.string("stream_key_override", required=False, max_len=128)
    v.check()
    if not player_exists(conn, player_id):
        raise not_found("PLAYER_NOT_FOUND", f"Player {player_id} does not exist.", {"player_id": player_id})
    if action == "revoke":
        return revoke(conn, player_id, label, override)
    return issue(conn, action, player_id, label or DEFAULT_LABEL, override)


def issue(conn, action: str, player_id: str, label: str, override: str | None):
    """generate and rotate: the new key becomes the player's only active key."""
    key = override if override is not None else new_key(conn)
    retired = conn.execute(
        "UPDATE sk_keys SET status = 'revoked', updated_at = now() "
        "WHERE player_id = %s AND status = 'active' AND stream_key <> %s RETURNING stream_key",
        (player_id, key)).fetchall()
    conn.execute(
        "INSERT INTO sk_keys (stream_key, player_id, label, status) VALUES (%s, %s, %s, 'active') "
        "ON CONFLICT (stream_key) DO UPDATE SET player_id = EXCLUDED.player_id, label = EXCLUDED.label, "
        "status = 'active', seq = EXCLUDED.seq, updated_at = now()",
        (key, player_id, label))
    body = key_body(action, player_id, label, key, "active")
    return STATUS_FOR_ACTION[action], body, [live_path(r["stream_key"]) for r in retired]


def revoke(conn, player_id: str, label: str | None, override: str | None):
    keys = conn.execute("SELECT stream_key, label, status FROM sk_keys WHERE player_id = %s ORDER BY seq DESC",
                        (player_id,)).fetchall()
    if not keys:
        raise not_found("STREAM_NOT_FOUND", f"Player {player_id} has no stream key.", {"player_id": player_id})
    target = keys[0]
    if override is not None:
        target = next((k for k in keys if k["stream_key"] == override), None)
        if target is None:
            raise not_found("STREAM_NOT_FOUND", f"Stream key {override} does not exist for player {player_id}.",
                            {"player_id": player_id, "stream_key": override})
    key = target["stream_key"]
    if target["status"] != "active":
        raise conflict("KEY_REVOKED", f"Stream key {key} has already been revoked.",
                       {"player_id": player_id, "stream_key": key})
    conn.execute("UPDATE sk_keys SET status = 'revoked', updated_at = now() WHERE stream_key = %s", (key,))
    body = key_body("revoke", player_id, label or target["label"], key, "revoked")
    return STATUS_FOR_ACTION["revoke"], body, [live_path(key)]


def active_keys(conn, player_ids) -> dict[str, str]:
    rows = conn.execute("SELECT player_id, stream_key FROM sk_keys WHERE player_id = ANY(%s) AND status = 'active'",
                        (list(player_ids),)).fetchall()
    return {r["player_id"]: r["stream_key"] for r in rows}


def stream_players(conn, entity_id: str) -> list[str]:
    """Players whose live stream belongs on an entity's page, in preference order: a co-stream's active
    participants (active host first), the player itself, a club's players, or a match's home then away
    club players."""
    from ..costream import service as costream

    session = costream.get_session(conn, entity_id)
    if session is not None:
        return list(session["active_participants"])
    if player_exists(conn, entity_id):
        return [entity_id]
    match = conn.execute("SELECT home_team_id, away_team_id FROM scoring_matches WHERE match_id = %s",
                         (entity_id,)).fetchone()
    clubs = [match["home_team_id"], match["away_team_id"]] if match else [entity_id]
    rows = conn.execute("SELECT player_id, club_id FROM players WHERE club_id = ANY(%s) ORDER BY player_id",
                        (clubs,)).fetchall()
    return [r["player_id"] for club in clubs for r in rows if r["club_id"] == club]


def playback_url_for(conn, entity_id: str) -> str | None:
    """HLS URL to play on a guest page for a match, club, player or co-stream session while something is
    publishing, else None (show "stream starting"). Read-only; a co-stream prefers its composite."""
    from ..costream import service as costream

    live = media.ready_paths(max_age=1.0)
    if not live:
        return None
    if COSTREAM_PREFIX + entity_id in live:
        return costream.composite_playback_url(entity_id)
    players = stream_players(conn, entity_id)
    keys = active_keys(conn, players)
    for player_id in players:
        if player_id in keys and live_path(keys[player_id]) in live:
            return playback_url(keys[player_id])
    return None


active_playback_url = playback_url_for


def key_status(conn, stream_key: str) -> str | None:
    row = conn.execute("SELECT status FROM sk_keys WHERE stream_key = %s", (stream_key,)).fetchone()
    return row["status"] if row else None


def authorize(conn, request) -> None:
    """MediaMTX HTTP auth hook. Returns normally to allow; raises ApiError (any non-2xx denies).
    live/{key}: only an active key may publish or play; a revoked one gets 409 KEY_REVOKED.
    costream/{id}: composite playback stays open unless that session has terminated."""
    from ..costream import service as costream

    req = request if isinstance(request, dict) else {}
    action, path = req.get("action"), req.get("path")
    if action in OPEN_ACTIONS:
        return
    if action not in MEDIA_ACTIONS or not isinstance(path, str):
        raise ApiError(401, "UNAUTHENTICATED", "Unknown media action or path.")
    if path.startswith(LIVE_PREFIX) and "/" not in path[len(LIVE_PREFIX):]:
        stream_key = path[len(LIVE_PREFIX):]
        status = key_status(conn, stream_key)
        if status == "active":
            return
        if status is not None:
            raise conflict("KEY_REVOKED", f"Stream key {stream_key} has been revoked.", {"stream_key": stream_key})
        raise ApiError(401, "UNAUTHENTICATED", "Unknown stream key.", {"stream_key": stream_key})
    if path.startswith(COSTREAM_PREFIX) and len(path) > len(COSTREAM_PREFIX):
        session_id = path[len(COSTREAM_PREFIX):]
        if costream.session_status(conn, session_id) == "terminated":
            raise conflict("STREAM_TERMINATED", f"Co-stream {session_id} has terminated.", {"session_id": session_id})
        return
    raise ApiError(401, "UNAUTHENTICATED", "Publishing and playback are only allowed on live/ and costream/ paths.")


# MediaMTX asks again for every HLS playlist and segment request, so decisions are cached per path.
# The API is a single process: every key or session change bumps the generation and drops the cache,
# and a lookup that raced with a change is not stored.
_auth_cache: dict[str, tuple[float, ApiError | None]] = {}
_auth_gen = [0]


def invalidate_auth() -> None:
    _auth_gen[0] += 1
    _auth_cache.clear()


def _cache_key(request):
    if isinstance(request, dict) and request.get("action") in MEDIA_ACTIONS and isinstance(request.get("path"), str):
        return request["path"]
    return None


def cached_auth(request):
    """The cached denial (None = allowed) for this request, or MISS."""
    key = _cache_key(request)
    hit = _auth_cache.get(key) if key is not None else None
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    return MISS


def check_auth(request) -> ApiError | None:
    gen = _auth_gen[0]
    try:
        with db.tx() as conn:
            authorize(conn, request)
        denial = None
    except ApiError as exc:
        denial = exc
    key = _cache_key(request)
    if key is not None and gen == _auth_gen[0]:
        if len(_auth_cache) >= AUTH_CACHE_MAX:
            _auth_cache.clear()
        _auth_cache[key] = (time.monotonic() + AUTH_TTL_S, denial)
    return denial


def sever_all(background: bool = True) -> None:
    """Reset wipes every key and session, so whatever still publishes or plays loses its authorisation."""
    if not media.media_ready():
        return
    listing = media.api_get("/v3/paths/list?itemsPerPage=1000", timeout=0.5)
    media.sever([p.get("name") for p in (listing or {}).get("items") or []
                 if str(p.get("name", "")).startswith((LIVE_PREFIX, COSTREAM_PREFIX))], background=background)


def on_reset() -> None:
    invalidate_auth()
    threading.Thread(target=sever_all, daemon=True, name="media-reset").start()


def media_ready() -> bool:
    return media.media_ready()


register(SCHEMA, ["sk_keys"], on_reset=on_reset)
