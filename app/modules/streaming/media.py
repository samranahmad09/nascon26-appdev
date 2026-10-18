"""MediaMTX control-API client: severs publishers when a key dies, and feeds the /metrics stream gauges.
Every call is best effort with short timeouts: the media server being down never fails an API request."""
import datetime as dt
import logging
import re
import threading
import time

import httpx

from ...core import config, metrics

log = logging.getLogger("streaming.media")

CONN_KINDS = ("rtmpconns", "webrtcsessions", "rtspsessions", "srtconns")
SECOND_SWEEP_S = 1.0
READY_TTL_S = 2.0
METRICS_TTL_S = 1.0

# Tests swap in an httpx.MockTransport; None means real network.
transport: httpx.BaseTransport | None = None


def _base_url() -> str | None:
    """The MediaMTX control API, or None when no media server is configured (MEDIA_HOST unset, as in
    unit-test runs), so nothing waits on DNS for a host that is not there."""
    if config.MEDIA_HOST:
        return f"http://{config.MEDIA_HOST}:9997"
    return "http://media:9997" if transport is not None else None


def api_get(path: str, timeout: float = 1.0):
    base = _base_url()
    if base is None:
        return None
    try:
        with httpx.Client(base_url=base, timeout=timeout, transport=transport) as client:
            r = client.get(path)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def api_post(path: str, timeout: float = 1.0) -> bool:
    base = _base_url()
    if base is None:
        return False
    try:
        with httpx.Client(base_url=base, timeout=timeout, transport=transport) as client:
            return client.post(path).status_code == 200
    except Exception:
        return False


def kick_paths(paths) -> int:
    """Kicks every RTMP/WebRTC/RTSP/SRT connection publishing or reading one of the given paths."""
    wanted = set(paths)
    kicked = 0
    for kind in CONN_KINDS:
        listing = api_get(f"/v3/{kind}/list?itemsPerPage=1000")
        for item in (listing or {}).get("items") or []:
            if item.get("path") in wanted and api_post(f"/v3/{kind}/kick/{item.get('id')}"):
                kicked += 1
    return kicked


def _sweep(paths: list[str]) -> None:
    kick_paths(paths)
    # A publisher that passed the auth hook just before the key died is still mid-handshake on the
    # first sweep; a second one closes it well inside the 2 s budget.
    time.sleep(SECOND_SWEEP_S)
    kick_paths(paths)


def sever(paths, background: bool = True) -> None:
    paths = [p for p in dict.fromkeys(paths) if p]
    if not paths:
        return
    if background:
        threading.Thread(target=_sweep, args=(paths,), daemon=True, name="media-sever").start()
    else:
        _sweep(paths)


_live = {"at": -1.0, "paths": set()}


def ready_paths(max_age: float = 0.0) -> set[str]:
    """Paths with a publisher right now. max_age > 0 reuses a listing that recent (page renders)."""
    now = time.monotonic()
    if max_age and now - _live["at"] < max_age:
        return _live["paths"]
    listing = api_get("/v3/paths/list?itemsPerPage=1000", timeout=0.5)
    paths = {p.get("name") for p in (listing or {}).get("items") or [] if p.get("ready")}
    _live.update(at=now, paths=paths)
    return paths


_ready = {"at": -READY_TTL_S, "ok": False}


def media_ready() -> bool:
    """True when the MediaMTX control API answers. Cached briefly so /health stays cheap under load."""
    now = time.monotonic()
    if now - _ready["at"] >= READY_TTL_S:
        _ready["ok"] = api_get("/v3/paths/list?itemsPerPage=1", timeout=1.0) is not None
        _ready["at"] = now
    return _ready["ok"]


_FRACTION = re.compile(r"(\.\d{6})\d+")


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(_FRACTION.sub(r"\1", value).replace("Z", "+00:00"))
    except ValueError:
        return None


_stats = {"at": -METRICS_TTL_S, "streams": 0, "kbps": 0.0, "bytes": {}, "bytes_at": 0.0}
_stats_lock = threading.Lock()


def _refresh_stats() -> None:
    now = time.monotonic()
    if now - _stats["at"] < METRICS_TTL_S or not _stats_lock.acquire(blocking=False):
        return
    try:
        listing = api_get("/v3/paths/list?itemsPerPage=1000", timeout=0.5)
        ready = [p for p in (listing or {}).get("items") or [] if p.get("ready")]
        current = {p.get("name"): int(p.get("bytesReceived") or 0) for p in ready}
        previous, elapsed = _stats["bytes"], now - _stats["bytes_at"]
        if previous and 0 < elapsed <= 30:
            bits = sum(max(0, n - previous[name]) for name, n in current.items() if name in previous) * 8
            kbps = bits / 1000 / elapsed
        else:
            # First look at these streams: average since each went live.
            wall = dt.datetime.now(dt.timezone.utc)
            kbps = 0.0
            for p in ready:
                since = _parse_time(p.get("readyTime"))
                seconds = (wall - since).total_seconds() if since else 0
                if seconds > 0:
                    kbps += int(p.get("bytesReceived") or 0) * 8 / 1000 / seconds
        _stats.update(streams=len(ready), kbps=round(kbps, 1), bytes=current, bytes_at=now)
    except Exception:
        log.exception("media stats refresh failed")
        _stats.update(streams=0, kbps=0.0, bytes={}, bytes_at=now)
    finally:
        _stats["at"] = now
        _stats_lock.release()


def active_streams() -> int:
    _refresh_stats()
    return _stats["streams"]


def bitrate_kbps() -> float:
    _refresh_stats()
    return _stats["kbps"]


def reset_cache() -> None:
    _live.update(at=-1.0, paths=set())
    _ready["at"] = -READY_TTL_S
    _stats.update(at=-METRICS_TTL_S, streams=0, kbps=0.0, bytes={}, bytes_at=0.0)


metrics.register("nascon_active_streams_total", active_streams)
metrics.register("nascon_stream_bitrate_kbps", bitrate_kbps)
