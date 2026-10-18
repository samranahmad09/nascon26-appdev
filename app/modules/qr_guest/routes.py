"""Dynamic QR (spec 5.6), guest boundary and cart merge (spec 5.7), and the guest/member cart APIs the
Guest Web Player uses.

Guest web APIs (all JSON):
  POST /api/v1/guest/token                      -> 201 {guest_token, expires_at, scope}
  GET  /api/v1/guest/cart        (guest bearer) -> 200 {guest_token, items, count}
  POST /api/v1/guest/cart/items  (guest bearer) -> 200 same shape; body {variant_id, quantity}
  POST /api/v1/guest/merge       (member JWT)   -> 200 member cart; body {guest_token}
  GET  /api/v1/cart              (member JWT)   -> 200 {user_id, currency, total_items, subtotal_minor, items}
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ...core.auth import bearer_claims
from ...core.errors import ApiError
from ...core.middleware import GuestWriteBlock, guest_token_of
from ...core.validate import json_body
from . import service

router = APIRouter()

PROBE_PATH = "/api/v1/chat"
TRUE_WORDS = {"true", "1", "yes"}


def _json(body: bytes) -> Response:
    return Response(content=body, media_type="application/json")


async def qr_metadata(request: Request):
    """The k6 path (500 VUs): a plain Starlette route, no FastAPI parameter solving, no threadpool hop
    when the answer is in memory."""
    entity_id = request.path_params["entity_id"]
    inspect = request.query_params.get("inspect", "").strip().lower() in TRUE_WORDS
    if inspect:
        cached = service.inspect_cached(entity_id)
        if cached is not None:
            return _json(cached)
    device = service.device_family(request.headers)
    return _json(await run_in_threadpool(service.qr_metadata, entity_id, not inspect, device))


router.add_route("/audit/v1/qr/{entity_id}", qr_metadata, methods=["GET"])


@router.get("/audit/v1/qr/{entity_id}/export.{fmt}")
def qr_export(entity_id: str, fmt: str):
    content, media_type = service.export(entity_id, fmt)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'inline; filename="qr-{entity_id}.{fmt}"'})


@router.get("/q/{short_code}")
def short_link(short_code: str, request: Request):
    route = service.follow_short_code(short_code, service.device_family(request.headers))
    return Response(status_code=302, headers={"Location": route, "Cache-Control": "no-store"})


@router.get("/api/v1/qr/{entity_id}/analytics")
def qr_analytics(entity_id: str):
    return service.scan_analytics(entity_id)


async def probe_write_block(guest_token: str) -> tuple[bool, int]:
    """Send a simulated chat post carrying the guest token through the real GuestWriteBlock guard, driven
    as a plain ASGI app (works whether the guard is a BaseHTTPMiddleware or pure ASGI middleware).
    Returns (blocked with GUEST_WRITE_FORBIDDEN before reaching the write, status the client got)."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": PROBE_PATH, "raw_path": PROBE_PATH.encode(), "root_path": "",
             "query_string": b"", "headers": [(b"authorization", f"Bearer {guest_token}".encode("utf-8")),
                                              (b"content-type", b"application/json")],
             "server": ("backend", 8080), "client": ("127.0.0.1", 0)}
    reached = False
    sent = []

    async def write_endpoint(scope, receive, send):
        nonlocal reached
        reached = True
        await Response(status_code=201)(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b'{"text": "probe"}', "more_body": False}

    async def send(message):
        sent.append(message)

    await GuestWriteBlock(write_endpoint)(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    code = json.loads(body).get("error", {}).get("code") if status == 403 else None
    return (not reached and code == "GUEST_WRITE_FORBIDDEN"), status


@router.post("/audit/v1/guest/cart-merge")
async def guest_cart_merge(request: Request):
    req = service.parse_cart_merge(await json_body(request))
    if req["test_write_block"]:
        verified, rejection = await probe_write_block(req["guest_token"])
    else:
        verified, rejection = False, None
    return JSONResponse(await run_in_threadpool(service.cart_merge, req, verified, rejection))


def _require_guest(request: Request) -> str:
    token = guest_token_of(request)
    if token is None:
        raise service.unauthenticated("Authorization: Bearer guest_... is required.")
    return token


@router.post("/api/v1/guest/token")
def guest_token():
    return JSONResponse(status_code=201, content=service.issue_guest_token())


@router.get("/api/v1/guest/cart")
def guest_cart(request: Request):
    return service.guest_cart(_require_guest(request))


@router.post("/api/v1/guest/cart/items")
async def guest_cart_add(request: Request):
    token = _require_guest(request)
    body = await json_body(request)
    return await run_in_threadpool(service.add_guest_item, token, body)


@router.post("/api/v1/guest/merge")
async def guest_merge(request: Request):
    token = guest_token_of(request)
    if token is not None:
        raise ApiError(403, "GUEST_WRITE_FORBIDDEN", "Anonymous guest tokens cannot perform write mutations.",
                       {"guest_token": token})
    claims = bearer_claims(request)
    body = await json_body(request)
    return await run_in_threadpool(service.merge_staged_cart, claims, body)


@router.get("/api/v1/cart")
def member_cart(request: Request):
    return service.member_cart(bearer_claims(request))
