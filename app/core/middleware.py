import math
import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware

from .config import MAX_BODY_BYTES
from .errors import error_response

RATE_LIMIT = 100
RATE_WINDOW_S = 10.0


class BodySizeLimit(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return error_response(413, "PAYLOAD_TOO_LARGE", "Request body exceeds 1 MB.")
        return await call_next(request)


class RateLimit(BaseHTTPMiddleware):
    """Rolling 10 s window, 100 allowed requests per client IP, applied to /api/v1/* only."""

    def __init__(self, app):
        super().__init__(app)
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

    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/api/v1/"):
            ip = request.client.host if request.client else "unknown"
            retry = self.check(ip, time.monotonic())
            if retry is not None:
                return error_response(429, "RATE_LIMITED", "Too many requests. Try again shortly.",
                                      {"retry_after_seconds": retry}, headers={"Retry-After": str(retry)})
        return await call_next(request)
