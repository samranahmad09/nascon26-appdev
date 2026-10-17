"""Production routes used by the mobile app (/api/v1/*, rate limited)."""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ...core import db, realtime
from ...core.auth import token_for_user, verify_password
from ...core.errors import ApiError
from ...core.seed import user_by_email
from ...core.validate import Validator, json_body

router = APIRouter()


@router.get("/api/v1/ping")
def ping():
    return {"pong": True}


@router.post("/api/v1/auth/login")
def login(body=Depends(json_body)):
    v = Validator(body)
    email = v.string("email", max_len=200)
    password = v.string("password", max_len=200)
    v.check()
    with db.tx() as conn:
        user = user_by_email(conn, email)
        tenant = None
        if user and user["tenant_id"]:
            tenant = conn.execute("SELECT tenant_id, name, branding FROM tenants WHERE tenant_id = %s",
                                  (user["tenant_id"],)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        raise ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    profile = {k: user[k] for k in ("user_id", "email", "display_name", "role", "tenant_id", "league_ids", "club_id", "player_id")}
    return {"token": token_for_user(user), "user": profile, "tenant": tenant}


@router.websocket("/api/v1/ws")
async def live(ws: WebSocket, topic: str = "*"):
    await ws.accept()
    realtime.subscribe(topic, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        realtime.unsubscribe(ws)
