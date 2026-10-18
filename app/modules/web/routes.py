"""Guest Web Player (FR-4.2, 6.2.2, NFR-8): pages under /web/*, their static assets, and read-only JSON helpers
under /web/api/* that need no token. Nothing here writes; guest writes go to the /api/v1 routes and are refused
there by GuestWriteBlock."""
import hashlib
import os
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ...core import db
from ...core.errors import ApiError, not_found, validation_error
from . import pages, service

router = APIRouter()

# Browsers that cannot reach the media server's own port (Selenium Grid nodes, phones behind the venue Wi-Fi) play
# HLS through this origin instead: /web/hls/live/{key}/... is fetched from the media server's HLS port.
MEDIA_HLS = f"http://{os.environ.get('MEDIA_HOST', 'media')}:8888"
HLS_PATH = re.compile(r"(live|costream)/[A-Za-z0-9_.\-]{1,128}/[A-Za-z0-9_.\-]{1,128}\.(m3u8|ts|mp4|m4s)")
HLS_TYPES = {"m3u8": "application/vnd.apple.mpegurl", "ts": "video/mp2t", "mp4": "video/mp4", "m4s": "video/iso.segment"}
_hls_client: httpx.AsyncClient | None = None
CHAT_ROOM = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")

STATIC_DIR = Path(__file__).with_name("static")
MEDIA_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
               ".txt": "text/plain; charset=utf-8"}
STATIC = {p.name: (p.read_bytes(), MEDIA_TYPES[p.suffix]) for p in sorted(STATIC_DIR.iterdir())
          if p.is_file() and p.suffix in MEDIA_TYPES}
# Versioned URLs let browsers cache the bundle for good and still pick up a new build at once.
ASSETS = {name: f"/web/static/{name}?v={hashlib.sha256(body).hexdigest()[:12]}" for name, (body, _) in STATIC.items()}
NO_STORE = {"Cache-Control": "no-store"}


def _json(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers=NO_STORE)


@router.get("/web/static/{name}")
def static_asset(name: str):
    found = STATIC.get(name)
    if found is None:
        raise not_found("ASSET_NOT_FOUND", f"Asset {name} does not exist.", {"name": name})
    body, media_type = found
    return Response(content=body, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


@router.get("/web/hls/{path:path}")
async def hls_proxy(path: str, request: Request):
    global _hls_client
    if not HLS_PATH.fullmatch(path):
        raise not_found("STREAM_NOT_FOUND", "No such stream file.", {"path": path})
    if _hls_client is None:
        _hls_client = httpx.AsyncClient(timeout=15.0)
    try:
        upstream = await _hls_client.get(f"{MEDIA_HLS}/{path}", params=dict(request.query_params))
    except httpx.HTTPError:
        raise ApiError(502, "MEDIA_UNAVAILABLE", "The media server is not reachable.")
    kind = path.rsplit(".", 1)[1]
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", HLS_TYPES[kind]),
                    headers={"Cache-Control": upstream.headers.get("cache-control", "no-cache")})


# ------------------------------------------------------------------------------------------------
# Read-only JSON helpers (no token needed)
# ------------------------------------------------------------------------------------------------

@router.get("/web/api/matches/{match_id}/overlay")
def match_overlay(match_id: str):
    with db.tx() as conn:
        overlay = service.match_overlay(conn, match_id)
    if overlay is None:
        raise not_found("MATCH_NOT_FOUND", f"Match {match_id} does not exist.", {"match_id": match_id})
    return _json(overlay)


@router.get("/web/api/costream/{session_id}")
def costream_overlay(session_id: str):
    with db.tx() as conn:
        return _json(service.costream_overlay(conn, session_id))


@router.get("/web/api/chat/{room}/messages")
def chat_history(room: str, after: int | None = Query(None, ge=0), limit: int = Query(100, ge=1, le=500)):
    """Chat backlog for the page, off the /api/v1 per-IP limiter (a venue Wi-Fi puts every phone on one IP)."""
    if not CHAT_ROOM.fullmatch(room):
        raise validation_error(["chat_room_id"])
    moderation = service.optional("app.modules.moderation.service")
    if moderation is None or not hasattr(moderation, "list_chat"):
        raise ApiError(503, "CHAT_UNAVAILABLE", "Chat history is not available.")
    return _json(moderation.list_chat(room, after, limit))


@router.get("/web/api/players/{player_id}")
def player_profile(player_id: str):
    with db.tx() as conn:
        player = service.player_profile(conn, player_id)
    if player is None:
        raise not_found("PLAYER_NOT_FOUND", f"Player {player_id} does not exist.", {"player_id": player_id})
    return _json(player)


@router.get("/web/api/clubs/{club_id}")
def club_profile(club_id: str):
    with db.tx() as conn:
        club = service.club_profile(conn, club_id)
    if club is None:
        raise not_found("CLUB_NOT_FOUND", f"Club {club_id} does not exist.", {"club_id": club_id})
    return _json(club)


# ------------------------------------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------------------------------------

def _match_page(match_id: str, kind: str):
    with db.tx() as conn:
        overlay = service.match_overlay(conn, match_id)
    if overlay is None:
        return pages.missing_page("Match", ASSETS)
    return pages.player_page(kind, match_id, overlay, overlay["pinned_product"], overlay["playback_url"],
                             overlay["chat_room_id"], ASSETS)


@router.get("/web/matches/{match_id}/live")
def live_page(match_id: str):
    return _match_page(match_id, "live")


@router.get("/web/matches/{match_id}/preview")
def preview_page(match_id: str):
    return _match_page(match_id, "preview")


@router.get("/web/matches/{match_id}/vod")
def vod_page(match_id: str):
    return _match_page(match_id, "vod")


@router.get("/web/matches/{match_id}/notice")
def notice_page(match_id: str, state: str | None = None):
    with db.tx() as conn:
        overlay = service.match_overlay(conn, match_id)
    if overlay is None:
        return pages.missing_page("Match", ASSETS)
    shown = state if state in service.NOTICE_STATES else overlay["status"]
    return pages.notice_page(overlay, match_id, shown, ASSETS)


@router.get("/web/matches/{match_id}")
def match_entry(match_id: str):
    """Sends a bare match link to the page for the match's current state (same rule as the QR resolver)."""
    with db.tx() as conn:
        overlay = service.match_overlay(conn, match_id)
    if overlay is None:
        return pages.missing_page("Match", ASSETS)
    return RedirectResponse(overlay["route"], status_code=302, headers=NO_STORE)


@router.get("/web/costream/{session_id}")
def costream_page(session_id: str):
    with db.tx() as conn:
        cs = service.costream_overlay(conn, session_id)
    return pages.player_page("costream", session_id, cs["match"], cs["pinned_product"], cs["playback_url"],
                             cs["chat_room_id"], ASSETS, costream=cs)


@router.get("/web/players/{player_id}")
def player_page(player_id: str):
    with db.tx() as conn:
        player = service.player_profile(conn, player_id)
    if player is None:
        return pages.missing_page("Player", ASSETS)
    return pages.player_profile_page(player, ASSETS)


@router.get("/web/clubs/{club_id}")
def club_page(club_id: str):
    with db.tx() as conn:
        club = service.club_profile(conn, club_id)
    if club is None:
        return pages.missing_page("Club", ASSETS)
    return pages.club_page(club, ASSETS)
