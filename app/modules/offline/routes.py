from fastapi import APIRouter, Depends

from ...core import db, locks
from ...core.validate import json_body
from . import service

router = APIRouter()


@router.post("/audit/v1/offline-sync")
def offline_sync(body=Depends(json_body)):
    service.validate_envelope(body)
    with locks.GLOBAL, db.tx() as conn:
        return service.sync(conn, body)
