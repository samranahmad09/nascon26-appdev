from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import service

router = APIRouter()


@router.delete("/audit/v1/privacy/user/{user_id}")
def privacy_erase(user_id: str):
    return JSONResponse(status_code=200, content=service.erase_user(user_id))
