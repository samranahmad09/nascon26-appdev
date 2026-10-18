"""Always-on co-stream composites (FR-3.3, media probe GET :8888/costream/{id}/index.m3u8).

While a session is active, MediaMTX runs media/costream.sh for costream/{session_id} as a runOnInit path
added through its control API, so the composite HLS playlist answers on the first request (an on-demand
source cannot: MediaMTX answers 404 while a muxer starts, and with hlsAlwaysRemux HLS never triggers one).
Terminating the session or /audit/v1/reset deletes the path, which stops that ffmpeg. At most
MAX_COMPOSITES run at once (least recently started evicted). All MediaMTX calls run in order on one worker
thread, so an API request never waits on the media server and a reset can never overtake a later start."""
import logging
import queue
import threading
import time
from collections import OrderedDict

from ...core.validate import is_id
from ..streaming import media

log = logging.getLogger("costream.composite")

PREFIX = "costream/"
MAX_COMPOSITES = 8
HEAL_INTERVAL_S = 3.0

_running: "OrderedDict[str, float]" = OrderedDict()
_healed: dict[str, float] = {}
_jobs: queue.Queue = queue.Queue()
_worker_lock = threading.Lock()
_worker: list[threading.Thread] = []


def path_conf(session_id: str) -> dict:
    return {"runOnInit": f"sh /costream.sh {session_id}", "runOnInitRestart": True}


def start(session_id: str) -> None:
    if not is_id(session_id):
        return
    _running.pop(session_id, None)
    _running[session_id] = time.monotonic()
    while len(_running) > MAX_COMPOSITES:
        evicted, _ = _running.popitem(last=False)
        media.api_send("DELETE", f"/v3/config/paths/delete/{PREFIX}{evicted}")
    # Already running answers 400 "path already exists", which is fine.
    media.api_send("POST", f"/v3/config/paths/add/{PREFIX}{session_id}", path_conf(session_id))


def stop(session_id: str) -> None:
    _running.pop(session_id, None)
    _healed.pop(session_id, None)
    media.api_send("DELETE", f"/v3/config/paths/delete/{PREFIX}{session_id}")


def stop_all() -> None:
    _running.clear()
    _healed.clear()
    listing = media.api_get("/v3/config/paths/list?itemsPerPage=1000")
    for item in (listing or {}).get("items") or []:
        name = str(item.get("name", ""))
        if name.startswith(PREFIX):
            media.api_send("DELETE", f"/v3/config/paths/delete/{name}")


def _work() -> None:
    while True:
        fn, args = _jobs.get()
        try:
            fn(*args)
        except Exception:
            log.exception("composite job %s%s failed", fn.__name__, args)
        finally:
            _jobs.task_done()


def submit(fn, *args) -> None:
    with _worker_lock:
        if not _worker or not _worker[0].is_alive():
            _worker[:] = [threading.Thread(target=_work, daemon=True, name="costream-composites")]
            _worker[0].start()
    _jobs.put((fn, args))


def drain() -> None:
    """Waits until every submitted job has run (tests)."""
    _jobs.join()


def heal(session_id: str) -> None:
    """A reader asked for an active session's composite that may not be running (MediaMTX restarted, or it
    was evicted): (re)start it, at most once per HEAL_INTERVAL_S per session."""
    now = time.monotonic()
    if now - _healed.get(session_id, -HEAL_INTERVAL_S) < HEAL_INTERVAL_S:
        return
    _healed[session_id] = now
    submit(start, session_id)
