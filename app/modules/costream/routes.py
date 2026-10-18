from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from ...core import db, locks, realtime
from ...core.errors import not_found
from ...core.validate import json_body
from ..streaming import media
from ..streaming import service as streaming
from . import service

router = APIRouter()


@router.post("/audit/v1/costream/session")
def costream_session(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        status, result, changed = service.handle(conn, body)
    if changed:
        streaming.invalidate_auth()
        if result["status"] == "terminated":
            media.sever([streaming.COSTREAM_PREFIX + result["session_id"]])
        realtime.publish(f"costream:{result['session_id']}", {"type": "costream_update", "session": result})
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
