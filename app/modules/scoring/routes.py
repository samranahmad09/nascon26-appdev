from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...core import db, locks
from ...core.errors import not_found
from ...core.validate import json_body
from . import service

router = APIRouter()


@router.post("/audit/v1/leagues")
def create_league(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        result = service.register_league(conn, body)
    return JSONResponse(status_code=201, content=result)


@router.post("/audit/v1/events")
def append_event(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        status, result = service.append_event(conn, body)
    return JSONResponse(status_code=status, content=result)


@router.get("/audit/v1/score/{match_id}")
def get_score(match_id: str):
    with locks.GLOBAL, db.tx() as conn:
        result = service.project(conn, match_id)
    if result is None:
        raise not_found("MATCH_NOT_FOUND", f"Match {match_id} does not exist.", {"match_id": match_id})
    return result
