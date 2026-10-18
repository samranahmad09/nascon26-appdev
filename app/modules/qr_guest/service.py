"""Dynamic QR deep links (spec 5.6, FR-4.1, NFR-9) and guest access with progressive cart merge
(spec 5.7, FR-4.2 to FR-4.4).

QR mappings live in qg_qr_mappings and are mirrored in memory. A short code never changes once issued;
target_route is derived from live entity state on every resolve.

The k6 path (GET /audit/v1/qr/match-m1?inspect=true at 500 VUs) is answered from memory. A match status
is cached with the event count it was read at, and is trusted only while
  - it was checked less than TRUST_S ago (safety net for any writer that does not announce), and
  - the check started after the match's last realtime announcement plus DIRTY_S. Scoring announces
    inside the append transaction, before commit, so a check inside that window may still see the old
    state; such entries are never trusted.
Anything else revalidates against the database in the threadpool (event count first, then a replay only
when the count moved).

Scan telemetry keeps aggregate counters only: entity, UTC hour bucket, device family. No IP address,
cookie, User-Agent string or fingerprint is stored.

Carts are keyed by the owner's player id when the account is linked to one (user-player and
player-alpha-1 share a cart), otherwise by the user id. Merges never reserve inventory.
"""
import datetime as dt
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import secrets
import threading
import time
from urllib.parse import quote

from ...core import config, db, metrics, realtime
from ...core.errors import ApiError, conflict, not_found, validation_error
from ...core.registry import register
from ...core.signing import canonical, sign_day2
from ...core.validate import Validator, is_id, is_int, utc_now
from ..scoring import service as scoring
from ..store import catalog
from . import exports

log = logging.getLogger("qr_guest")

BASE_URL = os.environ.get("QR_PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
CURRENCY = catalog.CURRENCY
ACTIVE = "active"
TRUST_S = 0.2
DIRTY_S = 1.0

SEED_QR = (("match-m1", "match", "m1-live"), ("player-alpha-1", "player", "p-alpha-1"),
           ("club-alpha-1", "club", "c-alpha-1"))
SEED_GUEST_TOKEN = "guest_anon_tok_7721"
SEED_CART_USERS = ("player-alpha-1",)

GUEST_PREFIX = "guest_"
ANON_PREFIX = "guest_anon_tok_"
GUEST_TTL_S = 2 * 3600
SEED_GUEST_TTL_S = 24 * 3600
GUEST_SCOPE = ["stream:read", "score:subscribe", "cart:stage"]
MAX_TOKEN_LEN = 256
MAX_STAGED_QTY = 99

SCHEMA = """
CREATE TABLE IF NOT EXISTS qg_qr_mappings (
    entity_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, short_code TEXT NOT NULL UNIQUE,
    scan_count BIGINT NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS qg_scan_stats (
    entity_id TEXT NOT NULL, bucket TEXT NOT NULL, device_family TEXT NOT NULL, scans BIGINT NOT NULL,
    PRIMARY KEY (entity_id, bucket, device_family));
CREATE TABLE IF NOT EXISTS qg_guest_tokens (
    guest_token TEXT PRIMARY KEY, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    merged_user_id TEXT, merge_fingerprint TEXT, merged_at TEXT, revoked BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS qg_guest_cart (
    guest_token TEXT NOT NULL, variant_id TEXT NOT NULL, quantity BIGINT NOT NULL, added_seq BIGSERIAL,
    PRIMARY KEY (guest_token, variant_id));
CREATE TABLE IF NOT EXISTS qg_member_carts (
    user_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS qg_member_cart_items (
    user_id TEXT NOT NULL, variant_id TEXT NOT NULL, quantity BIGINT NOT NULL, added_seq BIGSERIAL,
    PRIMARY KEY (user_id, variant_id));
"""
TABLES = ["qg_qr_mappings", "qg_scan_stats", "qg_guest_tokens", "qg_guest_cart", "qg_member_carts",
          "qg_member_cart_items"]

MATCH_LEAVES = {"upcoming": "preview", "in_progress": "live", "completed": "vod"}
TYPE_PREFIXES = (("cs-session-", "costream"), ("player-", "player"), ("club-", "club"), ("match-", "match"))
CODE_PREFIXES = {"match": "m", "player": "p", "club": "c", "costream": "cs"}
MOBILE_UA = re.compile(r"mobi|android|iphone|ipad|ipod|windows phone|blackberry|bb10|opera mini|iemobile|"
                       r"kindle|silk", re.I)


def _iso(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _expiry(ttl_s: int) -> str:
    return _iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_s))


# ---------------------------------------------------------------------------------------------
# Seed and in-memory state
# ---------------------------------------------------------------------------------------------

def seed(conn, opts: dict) -> None:
    now = utc_now()
    for entity_id, kind, code in SEED_QR:
        conn.execute("INSERT INTO qg_qr_mappings (entity_id, entity_type, short_code, scan_count, created_at) "
                     "VALUES (%s, %s, %s, 0, %s)", (entity_id, kind, code, now))
    conn.execute("INSERT INTO qg_guest_tokens (guest_token, issued_at, expires_at) VALUES (%s, %s, %s)",
                 (SEED_GUEST_TOKEN, now, _expiry(SEED_GUEST_TTL_S)))
    for user_id in SEED_CART_USERS:
        conn.execute("INSERT INTO qg_member_carts (user_id, created_at, updated_at) VALUES (%s, %s, %s)",
                     (user_id, now, now))


class _Mapping:
    __slots__ = ("entity_id", "entity_type", "short_code", "short_url", "scan_count", "gen", "signed", "rendered")

    def __init__(self, row: dict, gen: int):
        self.entity_id = row["entity_id"]
        self.entity_type = row["entity_type"]
        self.short_code = row["short_code"]
        self.short_url = f"{BASE_URL}/q/{self.short_code}"
        self.scan_count = int(row["scan_count"])
        self.gen = gen
        self.signed = None    # (target_route, signature)
        self.rendered = None  # ((status, scan_count), json bytes)


class _MatchStatus:
    __slots__ = ("status", "version", "checked_at", "gen")

    def __init__(self, status: str, version: int, checked_at: float, gen: int):
        self.status, self.version, self.checked_at, self.gen = status, version, checked_at, gen


_lock = threading.Lock()
_gen = 0
_by_entity: dict[str, _Mapping] = {}
_by_code: dict[str, str] = {}
_match_status: dict[str, _MatchStatus] = {}
_dirty_until: dict[str, float] = {}
_artwork: dict[tuple, bytes] = {}


def _on_reset() -> None:
    global _gen
    with _lock:
        _gen += 1
        _by_entity.clear()
        _by_code.clear()
        _match_status.clear()
        _artwork.clear()


register(SCHEMA, TABLES, seed, order=920, on_reset=_on_reset)


def _note_announcement(topic, _message=None) -> None:
    if isinstance(topic, str) and topic.startswith("match:"):
        _dirty_until[topic[len("match:"):]] = time.monotonic() + DIRTY_S


def _install_announcement_tap() -> None:
    """Scoring announces every append on realtime topic match:{id}. Use a core tap hook when one exists,
    otherwise wrap realtime.publish (scoring calls it through the module attribute)."""
    add_tap = getattr(realtime, "add_tap", None)
    if callable(add_tap):
        add_tap(_note_announcement)
        return
    current = realtime.publish
    if getattr(current, "_qr_guest_tap", False):
        return

    def publish(topic, message):
        _note_announcement(topic)
        return current(topic, message)

    publish._qr_guest_tap = True
    realtime.publish = publish


_install_announcement_tap()


def _scans_total() -> int:
    row = db.fetchone("SELECT COALESCE(SUM(scan_count), 0)::bigint AS n FROM qg_qr_mappings")
    return int(row["n"]) if row else 0


metrics.register("nascon_qr_scans_total", _scans_total, kind="counter")


# ---------------------------------------------------------------------------------------------
# QR resolution
# ---------------------------------------------------------------------------------------------

def target_route(entity_type: str, entity_id: str, status: str) -> str:
    if entity_type == "match":
        leaf = MATCH_LEAVES.get(status)
        if leaf is not None:
            return f"/web/matches/{entity_id}/{leaf}"
        return f"/web/matches/{entity_id}/notice?state={quote(status, safe='')}"
    if entity_type == "player":
        return f"/web/players/{entity_id}"
    if entity_type == "club":
        return f"/web/clubs/{entity_id}"
    return f"/web/costream/{entity_id}"


def device_family(headers) -> str:
    """mobile | desktop, from client hints or the User-Agent. Only the family is kept, never the header."""
    if headers.get("sec-ch-ua-mobile", "").strip() == "?1":
        return "mobile"
    return "mobile" if MOBILE_UA.search(headers.get("user-agent", "")[:512]) else "desktop"


def _bucket() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


def _qr_missing(entity_id: str) -> ApiError:
    return not_found("QR_NOT_FOUND", f"No dynamic QR mapping exists for {entity_id}.", {"entity_id": entity_id})


def _payload(m: _Mapping, status: str, scan_count: int) -> dict:
    route = target_route(m.entity_type, m.entity_id, status)
    signed = m.signed
    if signed is None or signed[0] != route:
        signed = (route, sign_day2(m.entity_id, {"entity_id": m.entity_id, "short_url": m.short_url,
                                                 "target_route": route}))
        m.signed = signed
    base = f"{BASE_URL}/audit/v1/qr/{m.entity_id}/export"
    return {"entity_id": m.entity_id, "entity_type": m.entity_type, "entity_status": status,
            "short_url": m.short_url, "target_route": route, "deep_link_uri": f"nascon://entity/{m.entity_id}",
            "scan_count": scan_count,
            "export_formats": {"svg": f"{base}.svg", "png": f"{base}.png", "pdf": f"{base}.pdf"},
            "signature": signed[1]}


def _render(m: _Mapping, status: str) -> bytes:
    key = (status, m.scan_count)
    rendered = m.rendered
    if rendered is not None and rendered[0] == key:
        return rendered[1]
    body = json.dumps(_payload(m, status, key[1]), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    m.rendered = (key, body)
    return body


def _trusted_status(match_id: str, now: float) -> str | None:
    st = _match_status.get(match_id)
    if st is None or st.gen != _gen or now - st.checked_at >= TRUST_S:
        return None
    if st.checked_at <= _dirty_until.get(match_id, 0.0):
        return None
    return st.status


def inspect_cached(entity_id: str) -> bytes | None:
    """The ?inspect=true body straight from memory, or None when it needs the database."""
    m = _by_entity.get(entity_id)
    if m is None or m.gen != _gen:
        return None
    if m.entity_type == "match":
        status = _trusted_status(entity_id, time.monotonic())
        if status is None:
            return None
    elif m.entity_type == "costream":
        return None
    else:
        status = ACTIVE
    return _render(m, status)


def _remember(row: dict, gen: int) -> _Mapping:
    with _lock:
        current = _by_entity.get(row["entity_id"])
        if current is not None and current.gen == gen:
            return current
        m = _Mapping(row, gen)
        if gen == _gen:
            _by_entity[m.entity_id] = m
            _by_code[m.short_code] = m.entity_id
        return m


def _costream_session(conn, session_id: str):
    try:
        module = importlib.import_module("..costream.service", __package__)
    except ImportError:
        return None
    getter = getattr(module, "get_session", None)
    if getter is None:
        return None
    try:
        return getter(conn, session_id)
    except ApiError:
        return None


def _exists(conn, kind: str, entity_id: str) -> bool:
    if kind == "match":
        return scoring.match_version(conn, entity_id) is not None
    if kind == "player":
        return conn.execute("SELECT 1 FROM players WHERE player_id = %s", (entity_id,)).fetchone() is not None
    if kind == "club":
        return conn.execute("SELECT 1 FROM clubs WHERE club_id = %s", (entity_id,)).fetchone() is not None
    return _costream_session(conn, entity_id) is not None


def _detect_type(conn, entity_id: str) -> str | None:
    order = ["match", "player", "club", "costream"]
    for prefix, kind in TYPE_PREFIXES:
        if entity_id.startswith(prefix):
            order.remove(kind)
            order.insert(0, kind)
            break
    return next((kind for kind in order if _exists(conn, kind, entity_id)), None)


def _short_code(kind: str, entity_id: str, length: int) -> str:
    digest = hmac.new(config.DAY2_SALT.encode("utf-8"), f"qr|{entity_id}".encode("utf-8"), hashlib.sha256)
    return f"{CODE_PREFIXES[kind]}-{digest.hexdigest()[:length]}"


_MAP_COLS = "entity_id, entity_type, short_code, scan_count"


def _mapping_row(conn, entity_id: str, create: bool) -> dict | None:
    row = conn.execute(f"SELECT {_MAP_COLS} FROM qg_qr_mappings WHERE entity_id = %s", (entity_id,)).fetchone()
    if row is not None or not create:
        return row
    kind = _detect_type(conn, entity_id)
    if kind is None:
        return None
    for length in (10, 16, 64):
        row = conn.execute(
            "INSERT INTO qg_qr_mappings (entity_id, entity_type, short_code, scan_count, created_at) "
            f"VALUES (%s, %s, %s, 0, %s) ON CONFLICT DO NOTHING RETURNING {_MAP_COLS}",
            (entity_id, kind, _short_code(kind, entity_id, length), utc_now())).fetchone()
        if row is None:  # created concurrently, or (practically never) a short code collision
            row = conn.execute(f"SELECT {_MAP_COLS} FROM qg_qr_mappings WHERE entity_id = %s",
                               (entity_id,)).fetchone()
        if row is not None:
            return row
    return None  # pragma: no cover - 64 hex chars cannot collide


def _resolve_match_status(conn, match_id: str, gen: int) -> str | None:
    started = time.monotonic()
    version = scoring.match_version(conn, match_id)
    if version is None:
        return None
    cached = _match_status.get(match_id)
    if cached is not None and cached.gen == gen and cached.version == version:
        status = cached.status
    else:
        projection = scoring.project(conn, match_id)
        if projection is None:
            return None
        status = str(projection["status"])
    with _lock:
        current = _match_status.get(match_id)
        if gen == _gen and (current is None or current.gen != gen or current.checked_at <= started):
            _match_status[match_id] = _MatchStatus(status, version, started, gen)
    return status


def _entity_status(conn, kind: str, entity_id: str, gen: int) -> str | None:
    if kind == "match":
        return _resolve_match_status(conn, entity_id, gen)
    if kind == "costream":
        session = _costream_session(conn, entity_id)
        if session is None:
            return None
        status = session.get("status") if isinstance(session, dict) else None
        return str(status) if status else ACTIVE
    return ACTIVE


def _count_scan(conn, entity_id: str, device: str) -> int:
    row = conn.execute("UPDATE qg_qr_mappings SET scan_count = scan_count + 1 WHERE entity_id = %s "
                       "RETURNING scan_count", (entity_id,)).fetchone()
    if row is None:  # wiped by a concurrent reset
        raise _qr_missing(entity_id)
    conn.execute("INSERT INTO qg_scan_stats (entity_id, bucket, device_family, scans) VALUES (%s, %s, %s, 1) "
                 "ON CONFLICT (entity_id, bucket, device_family) DO UPDATE SET scans = qg_scan_stats.scans + 1",
                 (entity_id, _bucket(), device))
    return int(row["scan_count"])


def _resolve(entity_id: str, count: bool, device: str) -> tuple[_Mapping, str]:
    gen = _gen
    m = _by_entity.get(entity_id)
    if m is not None and m.gen != gen:
        m = None
    scans = None
    with db.tx() as conn:
        row = None
        if m is None:
            row = _mapping_row(conn, entity_id, create=True)
            if row is None:
                raise _qr_missing(entity_id)
        kind = m.entity_type if m is not None else row["entity_type"]
        status = _entity_status(conn, kind, entity_id, gen)
        if status is None:
            raise _qr_missing(entity_id)
        if count:
            scans = _count_scan(conn, entity_id, device)
    if m is None:
        m = _remember(row, gen)
    if scans is not None:
        with _lock:
            m.scan_count = max(m.scan_count, scans)
    return m, status


def qr_metadata(entity_id: str, count: bool, device: str = "desktop") -> bytes:
    """GET /audit/v1/qr/{entity_id} body (JSON bytes); counts a scan unless count is False."""
    if not is_id(entity_id):
        raise _qr_missing(entity_id)
    m, status = _resolve(entity_id, count, device)
    return _render(m, status)


def follow_short_code(short_code: str, device: str) -> str:
    """GET /q/{short_code}: counts a scan and returns the live target route."""
    entity_id = _by_code.get(short_code)
    if entity_id is None and is_id(short_code):
        row = db.fetchone("SELECT entity_id FROM qg_qr_mappings WHERE short_code = %s", (short_code,))
        entity_id = row["entity_id"] if row else None
    if entity_id is None:
        raise not_found("QR_NOT_FOUND", f"No dynamic QR mapping exists for short code {short_code}.",
                        {"short_code": short_code})
    m, status = _resolve(entity_id, True, device)
    return target_route(m.entity_type, m.entity_id, status)


def export(entity_id: str, fmt: str) -> tuple[bytes, str]:
    if fmt not in exports.MEDIA_TYPES:
        raise validation_error(["fmt"], "Export format must be svg, png or pdf.")
    if not is_id(entity_id):
        raise _qr_missing(entity_id)
    gen = _gen
    m = _by_entity.get(entity_id)
    if m is None or m.gen != gen:
        with db.tx() as conn:
            row = _mapping_row(conn, entity_id, create=True)
        if row is None:
            raise _qr_missing(entity_id)
        m = _remember(row, gen)
    key = (fmt, m.entity_id, m.short_url, m.entity_type)
    content = _artwork.get(key)
    if content is None:
        content = exports.render(fmt, m.short_url, m.entity_id, m.entity_type)
        with _lock:
            if gen == _gen:
                _artwork[key] = content
    return content, exports.MEDIA_TYPES[fmt]


def scan_analytics(entity_id: str) -> dict:
    """Aggregate, privacy-safe scan telemetry for one QR (NFR-9)."""
    if not is_id(entity_id):
        raise _qr_missing(entity_id)
    with db.tx() as conn:
        row = _mapping_row(conn, entity_id, create=False)
        if row is None:
            raise _qr_missing(entity_id)
        buckets = conn.execute("SELECT bucket, device_family, scans FROM qg_scan_stats WHERE entity_id = %s "
                               "ORDER BY bucket, device_family", (entity_id,)).fetchall()
    families = {"desktop": 0, "mobile": 0}
    for b in buckets:
        families[b["device_family"]] = families.get(b["device_family"], 0) + int(b["scans"])
    return {"entity_id": entity_id, "short_url": f"{BASE_URL}/q/{row['short_code']}",
            "scan_count": int(row["scan_count"]), "device_families": families,
            "buckets": [{"bucket": b["bucket"], "device_family": b["device_family"], "scans": int(b["scans"])}
                        for b in buckets],
            "stored_fields": ["entity_id", "bucket", "device_family", "scans"]}


# ---------------------------------------------------------------------------------------------
# Guest tokens and carts
# ---------------------------------------------------------------------------------------------

def unauthenticated(message: str = "A valid guest token is required.") -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", message)


def issue_guest_token() -> dict:
    token = ANON_PREFIX + secrets.token_hex(8)
    expires_at = _expiry(GUEST_TTL_S)
    db.execute("INSERT INTO qg_guest_tokens (guest_token, issued_at, expires_at) VALUES (%s, %s, %s)",
               (token, utc_now(), expires_at))
    return {"guest_token": token, "expires_at": expires_at, "scope": list(GUEST_SCOPE)}


def _live_guest(conn, token: str, lock: bool = False) -> dict:
    row = None
    if len(token) <= MAX_TOKEN_LEN:
        row = conn.execute("SELECT guest_token, expires_at, merged_user_id, merge_fingerprint, revoked "
                           "FROM qg_guest_tokens WHERE guest_token = %s" + (" FOR UPDATE" if lock else ""),
                           (token,)).fetchone()
    if row is None or row["revoked"] or row["expires_at"] <= utc_now():
        raise unauthenticated("Guest token is unknown, expired or revoked.")
    return row


def _lines_view(conn, rows: list[dict]) -> tuple[list[dict], int, int]:
    prices = catalog.variants_by_id(conn, [r["variant_id"] for r in rows]) if rows else {}
    items = []
    for r in rows:
        price = prices.get(r["variant_id"])
        items.append({"variant_id": r["variant_id"], "quantity": int(r["quantity"]),
                      "unit_price_minor": int(price["unit_price_minor"]) if price else 0})
    total = sum(i["quantity"] for i in items)
    subtotal = sum(i["quantity"] * i["unit_price_minor"] for i in items)
    return items, total, subtotal


def _guest_view(conn, token: str) -> dict:
    rows = conn.execute("SELECT variant_id, quantity FROM qg_guest_cart WHERE guest_token = %s ORDER BY added_seq",
                        (token,)).fetchall()
    items, total, _ = _lines_view(conn, rows)
    return {"guest_token": token, "items": items, "count": total}


def guest_cart(token: str) -> dict:
    with db.tx() as conn:
        _live_guest(conn, token)
        return _guest_view(conn, token)


def add_guest_item(token: str, body) -> dict:
    with db.tx() as conn:
        row = _live_guest(conn, token, lock=True)
        v = Validator(body)
        variant_id = v.ident("variant_id")
        quantity = v.integer("quantity", minimum=1, maximum=MAX_STAGED_QTY)
        v.check()
        variant = catalog.variants_by_id(conn, [variant_id]).get(variant_id)
        if variant is None:
            raise not_found("VARIANT_NOT_FOUND", f"Variant {variant_id} does not exist.", {"variant_id": variant_id})
        if row["merge_fingerprint"] is not None:
            raise conflict("GUEST_TOKEN_ALREADY_MERGED", "This guest cart was already merged into a member cart.",
                           {"guest_token": token})
        staged = conn.execute("SELECT quantity FROM qg_guest_cart WHERE guest_token = %s AND variant_id = %s",
                              (token, variant_id)).fetchone()
        wanted = (int(staged["quantity"]) if staged else 0) + quantity
        if wanted > int(variant["stock"]):
            raise conflict("OUT_OF_STOCK", f"Variant {variant_id} does not have {wanted} in stock.",
                           {"variant_id": variant_id})
        conn.execute("INSERT INTO qg_guest_cart (guest_token, variant_id, quantity) VALUES (%s, %s, %s) "
                     "ON CONFLICT (guest_token, variant_id) DO UPDATE SET quantity = EXCLUDED.quantity",
                     (token, variant_id, wanted))
        return _guest_view(conn, token)


def _cart_owner(conn, user_id: str) -> str | None:
    """Cart key for a registered account: its player id when linked to one, else the user id."""
    if conn.execute("SELECT 1 FROM players WHERE player_id = %s", (user_id,)).fetchone() is not None:
        return user_id
    user = conn.execute("SELECT user_id, player_id FROM users WHERE user_id = %s", (user_id,)).fetchone()
    if user is None:
        return None
    return user["player_id"] or user["user_id"]


def _member_view(conn, owner: str, user_id: str) -> dict:
    rows = conn.execute("SELECT variant_id, quantity FROM qg_member_cart_items WHERE user_id = %s "
                        "ORDER BY added_seq", (owner,)).fetchall()
    items, total, subtotal = _lines_view(conn, rows)
    return {"user_id": user_id, "currency": CURRENCY, "total_items": total, "subtotal_minor": subtotal,
            "items": items}


def _merge_lines(conn, owner: str, lines: list[tuple[str, int]], variants: dict) -> None:
    """Sum lines into the owner's cart under a row lock; 409 OUT_OF_STOCK before any write."""
    now = utc_now()
    conn.execute("INSERT INTO qg_member_carts (user_id, created_at, updated_at) VALUES (%s, %s, %s) "
                 "ON CONFLICT (user_id) DO NOTHING", (owner, now, now))
    conn.execute("SELECT user_id FROM qg_member_carts WHERE user_id = %s FOR UPDATE", (owner,))
    held = {r["variant_id"]: int(r["quantity"]) for r in conn.execute(
        "SELECT variant_id, quantity FROM qg_member_cart_items WHERE user_id = %s", (owner,)).fetchall()}
    for variant_id, quantity in lines:
        if held.get(variant_id, 0) + quantity > int(variants[variant_id]["stock"]):
            raise conflict("OUT_OF_STOCK", f"Variant {variant_id} does not have enough stock for this cart.",
                           {"variant_id": variant_id})
    for variant_id, quantity in lines:
        conn.execute("INSERT INTO qg_member_cart_items (user_id, variant_id, quantity) VALUES (%s, %s, %s) "
                     "ON CONFLICT (user_id, variant_id) DO UPDATE SET quantity = qg_member_cart_items.quantity "
                     "+ EXCLUDED.quantity", (owner, variant_id, quantity))
    conn.execute("UPDATE qg_member_carts SET updated_at = %s WHERE user_id = %s", (now, owner))


def _aggregate(cart: list[dict]) -> list[tuple[str, int]]:
    totals: dict[str, int] = {}
    for item in cart:
        totals[item["variant_id"]] = totals.get(item["variant_id"], 0) + item["quantity"]
    return list(totals.items())


def _fingerprint(user_id: str, lines: list[tuple[str, int]]) -> str:
    return hashlib.sha256(canonical({"registered_user_id": user_id,
                                     "guest_cart": sorted([vid, qty] for vid, qty in lines)}).encode("utf-8")
                          ).hexdigest()


def _lock_token(conn, token: str) -> dict:
    """Row-lock the token (registering an unseen guest_* token on first use) so two identical merges
    run one after the other: the second sees the first one's result."""
    conn.execute("INSERT INTO qg_guest_tokens (guest_token, issued_at, expires_at) VALUES (%s, %s, %s) "
                 "ON CONFLICT (guest_token) DO NOTHING", (token, utc_now(), _expiry(GUEST_TTL_S)))
    return conn.execute("SELECT merged_user_id, merge_fingerprint, revoked FROM qg_guest_tokens "
                        "WHERE guest_token = %s FOR UPDATE", (token,)).fetchone()


def _already_merged(token: str) -> ApiError:
    return conflict("GUEST_TOKEN_ALREADY_MERGED", f"Guest token {token} was already merged with a different user "
                    "or cart.", {"guest_token": token})


def _consume(conn, token: str, user_id: str, fingerprint: str) -> None:
    conn.execute("UPDATE qg_guest_tokens SET merged_user_id = %s, merge_fingerprint = %s, merged_at = %s "
                 "WHERE guest_token = %s", (user_id, fingerprint, utc_now(), token))
    conn.execute("DELETE FROM qg_guest_cart WHERE guest_token = %s", (token,))


def parse_cart_merge(body) -> dict:
    v = Validator(body)
    token = body.get("guest_token")
    if not (isinstance(token, str) and token.startswith(GUEST_PREFIX) and len(token) <= MAX_TOKEN_LEN):
        v.bad("guest_token")
    user_id = v.string("registered_user_id")
    cart = body.get("guest_cart")
    if not (isinstance(cart, list) and 1 <= len(cart) <= 10 and all(
            isinstance(i, dict) and isinstance(i.get("variant_id"), str) and i["variant_id"]
            and is_int(i.get("quantity")) and i["quantity"] >= 1 for i in cart)):
        v.bad("guest_cart")
    test_write_block = v.boolean("test_write_block", required=False)
    v.check()
    return {"guest_token": token, "registered_user_id": user_id,
            "lines": _aggregate(cart), "test_write_block": True if test_write_block is None else test_write_block}


def cart_merge(req: dict, write_block_verified: bool, write_rejection_code) -> dict:
    """POST /audit/v1/guest/cart-merge after schema validation and the write-block probe.
    Order: 404 PLAYER_NOT_FOUND -> 404 VARIANT_NOT_FOUND -> replay 200 / 409 GUEST_TOKEN_ALREADY_MERGED
    -> 409 OUT_OF_STOCK -> merge."""
    token, user_id, lines = req["guest_token"], req["registered_user_id"], req["lines"]
    with db.tx() as conn:
        owner = _cart_owner(conn, user_id)
        if owner is None:
            raise not_found("PLAYER_NOT_FOUND", f"Registered user {user_id} does not exist.",
                            {"registered_user_id": user_id})
        variants = catalog.variants_by_id(conn, [vid for vid, _ in lines])
        for variant_id, _ in lines:
            if variant_id not in variants:
                raise not_found("VARIANT_NOT_FOUND", f"Variant {variant_id} does not exist.",
                                {"variant_id": variant_id})
        fingerprint = _fingerprint(user_id, lines)
        state = _lock_token(conn, token)
        if state["revoked"] or state["merge_fingerprint"] is not None:
            if state["revoked"] or state["merge_fingerprint"] != fingerprint:
                raise _already_merged(token)
        else:
            _merge_lines(conn, owner, lines, variants)
            _consume(conn, token, user_id, fingerprint)
        cart = _member_view(conn, owner, user_id)
    signature = sign_day2(token, {"cart_merged": True, "guest_token": token, "registered_user_id": user_id,
                                  "total_items": cart["total_items"], "write_rejection_code": write_rejection_code})
    return {"guest_token": token, "write_block_verified": write_block_verified,
            "write_rejection_code": write_rejection_code, "cart_merged": True, "active_cart": cart,
            "signature": signature}


def merge_staged_cart(claims: dict, body) -> dict:
    """POST /api/v1/guest/merge: a member takes over the cart staged under a guest token."""
    v = Validator(body)
    token = body.get("guest_token")
    if not (isinstance(token, str) and token.startswith(GUEST_PREFIX) and len(token) <= MAX_TOKEN_LEN):
        v.bad("guest_token")
    v.check()
    with db.tx() as conn:
        owner = _cart_owner(conn, claims["sub"])
        if owner is None:
            raise not_found("PLAYER_NOT_FOUND", f"User {claims['sub']} does not exist.", {"user_id": claims["sub"]})
        known = conn.execute("SELECT 1 FROM qg_guest_tokens WHERE guest_token = %s", (token,)).fetchone()
        if known is None:
            raise not_found("GUEST_TOKEN_NOT_FOUND", f"Guest token {token} was never issued.", {"guest_token": token})
        state = _lock_token(conn, token)
        if state["revoked"] or state["merge_fingerprint"] is not None:
            if state["revoked"] or state["merged_user_id"] != owner:
                raise _already_merged(token)
            return _member_view(conn, owner, owner)
        staged = conn.execute("SELECT variant_id, quantity FROM qg_guest_cart WHERE guest_token = %s "
                              "ORDER BY added_seq", (token,)).fetchall()
        lines = [(r["variant_id"], int(r["quantity"])) for r in staged]
        variants = catalog.variants_by_id(conn, [vid for vid, _ in lines]) if lines else {}
        for variant_id, _ in lines:
            if variant_id not in variants:
                raise not_found("VARIANT_NOT_FOUND", f"Variant {variant_id} does not exist.",
                                {"variant_id": variant_id})
        _merge_lines(conn, owner, lines, variants)
        _consume(conn, token, owner, _fingerprint(owner, lines))
        return _member_view(conn, owner, owner)


def member_cart(claims: dict) -> dict:
    with db.tx() as conn:
        owner = _cart_owner(conn, claims["sub"])
        if owner is None:
            raise unauthenticated("The account behind this token no longer exists.")
        return _member_view(conn, owner, owner)


def _aliases(conn, user_id: str) -> list[str]:
    ids = {user_id}
    for row in conn.execute("SELECT user_id, player_id FROM users WHERE user_id = %s OR player_id = %s",
                            (user_id, user_id)).fetchall():
        ids.add(row["user_id"])
        if row["player_id"]:
            ids.add(row["player_id"])
    return sorted(ids)


def wipe_user_tokens(conn, user_id: str) -> int:
    """Privacy erasure (NFR-9): unlink and revoke every guest token merged into this account and drop its
    persistent cart. Runs inside the caller's transaction. Returns tokens unlinked plus carts removed."""
    ids = _aliases(conn, user_id)
    tokens = [r["guest_token"] for r in conn.execute(
        "UPDATE qg_guest_tokens SET merged_user_id = NULL, merge_fingerprint = NULL, revoked = TRUE "
        "WHERE merged_user_id = ANY(%s) RETURNING guest_token", (ids,)).fetchall()]
    if tokens:
        conn.execute("DELETE FROM qg_guest_cart WHERE guest_token = ANY(%s)", (tokens,))
    conn.execute("DELETE FROM qg_member_cart_items WHERE user_id = ANY(%s)", (ids,))
    carts = conn.execute("DELETE FROM qg_member_carts WHERE user_id = ANY(%s) RETURNING user_id", (ids,)).fetchall()
    return len(tokens) + len(carts)
