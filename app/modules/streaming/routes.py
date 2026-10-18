import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...core import db, locks
from ...core.validate import json_body
from . import media, service

router = APIRouter()


@router.post("/audit/v1/stream/key")
def stream_key(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        status, result, sever = service.handle(conn, body)
    # Committed first, so the auth hook already refuses the old key while its publishers are kicked.
    service.invalidate_auth()
    media.sever(sever)
    return JSONResponse(status_code=status, content=result)


@router.post("/internal/media/auth")
async def media_auth(request: Request):
    """MediaMTX authHTTPAddress target: 200 allows the publish/read, any other status denies it."""
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    denial = service.cached_auth(payload)
    if denial is service.MISS:
        denial = await asyncio.to_thread(service.check_auth, payload)
    if denial is not None:
        raise denial
    return {"allowed": True}


@router.get("/internal/media/ready")
def media_ready():
    return {"media_ready": service.media_ready()}
