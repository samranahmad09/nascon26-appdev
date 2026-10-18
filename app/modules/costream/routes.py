import asyncio
import threading

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from ...core import db, locks, realtime
from ...core.errors import not_found
from ...core.validate import json_body
from ..streaming import media
from ..streaming import service as streaming
from . import composite, service

router = APIRouter()

# Open host sockets per (session_id, player_id); the handoff fires when the active host's last one closes.
_host_sockets: dict[tuple[str, str], int] = {}


def topic(session_id: str) -> str:
    return f"costream:{session_id}"


def announce(result: dict) -> None:
    """After a committed state change: refresh media auth, start or stop the composite, notify sockets."""
    streaming.invalidate_auth()
    if result["status"] == "terminated":
        composite.submit(composite.stop, result["session_id"])
        media.sever([streaming.COSTREAM_PREFIX + result["session_id"]])
    else:
        composite.submit(composite.start, result["session_id"])
    realtime.publish(topic(result["session_id"]), {"type": "costream_update", "session": result})


@router.post("/audit/v1/costream/session")
def costream_session(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        status, result, changed = service.handle(conn, body)
    if changed:
        announce(result)
    return JSONResponse(status_code=status, content=result)


@router.get("/api/v1/costream/{session_id}")
def get_costream(session_id: str):
    with db.tx() as conn:
        result = service.get_session(conn, session_id)
    if result is None:
        raise not_found("STREAM_NOT_FOUND", f"Co-stream session {session_id} does not exist.",
                        {"session_id": session_id})
    return result


@router.get("/internal/costream/{session_id}/inputs", response_class=PlainTextResponse)
def composite_inputs(session_id: str):
    """For media/costream.sh: the live/ paths to compose, active host first, one per line."""
    with db.tx() as conn:
        session = service.get_session(conn, session_id)
        players = session["active_participants"] if session else []
        keys = streaming.active_keys(conn, players)
    live = media.ready_paths() if keys else set()
    paths = [streaming.live_path(keys[p]) for p in players if p in keys]
    return "\n".join([p for p in paths if p in live][:service.MAX_PARTICIPANTS])


def _snapshot(session_id: str):
    with db.tx() as conn:
        return service.get_session(conn, session_id)


def _host_left(session_id: str, player_id: str):
    with locks.GLOBAL, db.tx() as conn:
        result = service.host_left(conn, session_id, player_id)
    if result is not None:
        announce(result)
    return result


@router.websocket("/ws/costream/{session_id}")
async def costream_socket(websocket: WebSocket, session_id: str):
    """Live session updates (first frame: current state, then every costream_update). A broadcaster joins
    with ?player_id=<id>&role=host; when the active host's last host socket closes, hosting hands over
    to the next participant at once, as with simulate_host_drop."""
    player_id = websocket.query_params.get("player_id")
    host_key = (session_id, player_id) if player_id and websocket.query_params.get("role") == "host" else None
    await websocket.accept()
    realtime.subscribe(topic(session_id), websocket)
    if host_key:
        _host_sockets[host_key] = _host_sockets.get(host_key, 0) + 1
    try:
        state = await asyncio.to_thread(_snapshot, session_id)
        await websocket.send_json({"topic": topic(session_id), "type": "costream_state", "session": state})
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        realtime.unsubscribe(websocket)
        if host_key:
            _host_sockets[host_key] -= 1
            if _host_sockets[host_key] <= 0:
                del _host_sockets[host_key]
                # A plain thread, not an await: the handoff must finish even when this task is cancelled.
                threading.Thread(target=_host_left, args=host_key, daemon=True, name="costream-handoff").start()
