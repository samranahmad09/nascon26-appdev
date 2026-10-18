"""Cross-cutting request guards, written as pure ASGI middleware (BaseHTTPMiddleware adds a task and memory
streams per request, which costs a large share of throughput under the k6 load profile)."""
import math
import threading
import time
from collections import deque

from .config import MAX_BODY_BYTES
from .errors import error_response

RATE_LIMIT = 100
RATE_WINDOW_S = 10.0


def _header(scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", ()):
        if key == name:
            return value
    return None


class BodySizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            length = _header(scope, b"content-length")
            if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
                await error_response(413, "PAYLOAD_TOO_LARGE", "Request body exceeds 1 MB.")(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RateLimit:
    """Rolling 10 s window, 100 allowed requests per client IP, applied to /api/v1/* only."""

    def __init__(self, app):
        self.app = app
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, ip: str, now: float):
        with self._lock:
            hits = self._hits.setdefault(ip, deque())
            while hits and hits[0] <= now - RATE_WINDOW_S:
                hits.popleft()
            if len(hits) >= RATE_LIMIT:
                return max(1, math.ceil(hits[0] + RATE_WINDOW_S - now))
            hits.append(now)
            return None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/v1/"):
            client = scope.get("client")
            retry = self.check(client[0] if client else "unknown", time.monotonic())
            if retry is not None:
                response = error_response(429, "RATE_LIMITED", "Too many requests. Try again shortly.",
                                          {"retry_after_seconds": retry}, headers={"Retry-After": str(retry)})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


GUEST_TOKEN_PREFIX = "guest_"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Guests may stage a cart and hand it over for merging; every other write is refused server-side.
GUEST_WRITE_ALLOWED = ("/api/v1/guest/", "/audit/v1/guest/", "/web/api/guest/")


def _guest_token(authorization: str) -> str | None:
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token.startswith(GUEST_TOKEN_PREFIX):
        return token
    return None


def guest_token_of(request) -> str | None:
    return _guest_token(request.headers.get("authorization", ""))


class GuestWriteBlock:
    """FR-4.3: any write carrying an anonymous guest token gets 403 GUEST_WRITE_FORBIDDEN before
    any other processing (so the probe gets 403 whatever body it sends)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in WRITE_METHODS \
                and not scope["path"].startswith(GUEST_WRITE_ALLOWED):
            raw = _header(scope, b"authorization")
            token = _guest_token(raw.decode("latin-1")) if raw else None
            if token is not None:
                response = error_response(403, "GUEST_WRITE_FORBIDDEN",
                                          "Anonymous guest tokens cannot perform write mutations.",
                                          {"guest_token": token})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
