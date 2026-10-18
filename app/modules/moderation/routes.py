import re

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ...core.auth import bearer_claims
from ...core.errors import validation_error
from ...core.validate import Validator, json_body
from . import bus, service

router = APIRouter()

ROOM_RE = r"[A-Za-z0-9_.:\-]{1,128}"
MESSAGE_ID_RE = r"[A-Za-z0-9_.:\-]{1,64}"
MAX_CHAT_TEXT = 500
MAX_PROBE_MESSAGES = 1000
MAX_SIMULATED_FAILURES = 10000


def _short_text(value, max_len: int = 128) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= max_len


def parse_probe(body) -> dict:
    v = Validator(body)
    room = v.string("chat_room_id", max_len=128)
    messages = v.array("incoming_messages", max_len=MAX_PROBE_MESSAGES)
    check_dlq = v.boolean("check_dlq", required=False)
    failed = v.integer("simulate_failed_notifications", required=False, minimum=0, maximum=MAX_SIMULATED_FAILURES)
    clean = []
    for m in messages or []:
        if not (isinstance(m, dict) and _short_text(m.get("message_id")) and _short_text(m.get("sender"))
                and isinstance(m.get("text"), str)):
            v.bad("incoming_messages")
            break
        clean.append({"message_id": m["message_id"], "sender": m["sender"], "text": m["text"]})
    v.check()
    return {"chat_room_id": room, "incoming_messages": clean, "check_dlq": True if check_dlq is None else check_dlq,
            "simulate_failed_notifications": failed or 0}


@router.post("/audit/v1/moderation/test")
def moderation_test(body=Depends(json_body)):
    return JSONResponse(status_code=200, content=service.run_probe(parse_probe(body)))


@router.get("/audit/v1/dlq")
def dlq_status():
    return {"queue_depth": bus.depth(), "undelivered_events": bus.undelivered()}


@router.post("/audit/v1/notifications/dispatch")
def dispatch(body=Depends(json_body)):
    """Desk/viva probe for FR-5.2: push one event through the bus (simulate_dlq forces the retry + DLQ path)."""
    v = Validator(body)
    topic = v.string("topic", max_len=160)
    event_type = v.string("event_type", required=False, max_len=64)
    payload = v.obj("payload", required=False)
    simulate = v.boolean("simulate_dlq", required=False)
    require = v.boolean("require_subscriber", required=False)
    v.check()
    note = bus.notify(topic, event_type or "notification", payload or {}, bool(simulate), bool(require))
    return {"event_id": note["event_id"], "topic": topic, "delivered": note["delivered"], "attempts": note["attempts"],
            "dlq_depth": bus.depth()}


def member_claims(request: Request) -> dict:
    return bearer_claims(request)


@router.post("/api/v1/chat")
def chat_post(claims=Depends(member_claims), body=Depends(json_body)):
    v = Validator(body)
    room = v.string("chat_room_id", regex=ROOM_RE)
    text = v.string("text", max_len=MAX_CHAT_TEXT)
    message_id = v.string("message_id", required=False, regex=MESSAGE_ID_RE)
    if text is not None and not text.strip():
        v.bad("text")
    v.check()
    try:
        status, payload = service.post_chat(room, claims["sub"], text, message_id)
    except psycopg.errors.UniqueViolation:
        # the same client message_id raced itself; the winner is stored, so this is a replay
        status, payload = service.post_chat(room, claims["sub"], text, message_id)
    return JSONResponse(status_code=status, content=payload)


@router.get("/api/v1/chat/{room}/messages")
def chat_messages(room: str, after: int | None = Query(None, ge=0), limit: int = Query(100, ge=1, le=500)):
    if not re.fullmatch(ROOM_RE, room):
        raise validation_error(["chat_room_id"])
    return service.list_chat(room, after, limit)
