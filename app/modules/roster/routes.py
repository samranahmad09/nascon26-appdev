from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...core import db, locks
from ...core.errors import validation_error
from ...core.validate import json_body
from . import service

router = APIRouter()


@router.post("/audit/v1/roster/setup")
def roster_setup(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        result = service.setup(conn, body)
    return JSONResponse(status_code=201, content=result)


@router.post("/audit/v1/roster/transfer")
def roster_transfer(body=Depends(json_body)):
    with locks.GLOBAL, db.tx() as conn:
        status, result = service.transfer(conn, body)
    return JSONResponse(status_code=status, content=result)


@router.get("/audit/v1/roster/history/{player_id}")
def roster_history(player_id: str, as_of: str | None = None):
    day = service.parse_as_of(as_of)
    if day is None:
        raise validation_error(["as_of"], "as_of must be a date (YYYY-MM-DD).")
    with locks.GLOBAL, db.tx() as conn:
        return service.history(conn, player_id, day)
