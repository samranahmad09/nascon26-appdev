import time

import pytest

from app.core import db
from app.core.middleware import RateLimit
from app.modules.moderation import bus, service

ROOM = "chat-cs-session-101"


def _limiter(client):
    node = client.app.middleware_stack
    while node is not None and not isinstance(node, RateLimit):
        node = getattr(node, "app", None)
    return node


@pytest.fixture(autouse=True)
def fast_backoff_and_clean_rate_limit(client, monkeypatch):
    monkeypatch.setattr(bus, "BACKOFF_BASE_S", 0.001)
    client.get("/health")
    _limiter(client)._hits.clear()
    yield
    _limiter(client)._hits.clear()


def wait_for_subscriber(topic):
    deadline = time.monotonic() + 3
    while not bus.has_subscribers(topic):
        assert time.monotonic() < deadline, topic
        time.sleep(0.01)


def token(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def post(client, headers, text, room=ROOM, **extra):
    return client.post("/api/v1/chat", json={"chat_room_id": room, "text": text, **extra}, headers=headers)


def test_chat_requires_member_token(fresh):
    r = fresh.post("/api/v1/chat", json={"chat_room_id": ROOM, "text": "hi"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    r = fresh.post("/api/v1/chat", content=b"{bad")
    assert r.status_code == 401
    r = fresh.post("/api/v1/chat", json={"chat_room_id": ROOM, "text": "hi"}, headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 401
    r = fresh.post("/api/v1/chat", json={"chat_room_id": ROOM, "text": "hi"},
                   headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"


def test_hosts_and_viewers_share_one_monotonic_sequence(fresh):
    player, viewer = token(fresh, "player@nascon.test"), token(fresh, "viewer@nascon.test")
    seqs = []
    for i, who in enumerate([player, viewer, player, viewer]):
        r = post(fresh, who, f"message {i}")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["chat_room_id"] == ROOM and body["blocked"] is False
        msg = body["message"]
        assert set(msg) == {"sequence", "message_id", "sender", "sender_name", "text", "flagged", "reason", "sent_at"}
        assert msg["flagged"] is False and msg["reason"] is None and msg["sent_at"].endswith("Z")
        seqs.append(msg["sequence"])
    assert seqs == [1, 2, 3, 4]
    other = post(fresh, viewer, "elsewhere", room="chat-cs-session-202").json()["message"]
    assert other["sequence"] == 1
    listing = fresh.get(f"/api/v1/chat/{ROOM}/messages").json()
    assert listing["chat_room_id"] == ROOM
    assert [m["sequence"] for m in listing["messages"]] == [1, 2, 3, 4]
    assert [m["sender"] for m in listing["messages"]] == ["user-player", "user-viewer", "user-player", "user-viewer"]
    assert listing["messages"][0]["sender_name"] == "Ahmed Raza"
    assert [m["sequence"] for m in fresh.get(f"/api/v1/chat/{ROOM}/messages?after=2").json()["messages"]] == [3, 4]
    assert [m["sequence"] for m in fresh.get(f"/api/v1/chat/{ROOM}/messages?limit=2").json()["messages"]] == [3, 4]
    guest = {"Authorization": "Bearer guest_anon_tok_7721"}
    assert fresh.get(f"/api/v1/chat/{ROOM}/messages", headers=guest).status_code == 200


def test_chat_message_is_broadcast_on_room_topic(fresh):
    headers = token(fresh, "player@nascon.test")
    with fresh.websocket_connect(f"/api/v1/ws?topic=chat:{ROOM}") as ws:
        wait_for_subscriber(f"chat:{ROOM}")
        r = post(fresh, headers, "what a goal")
        assert r.status_code == 201
        msg = ws.receive_json()
        assert msg["topic"] == f"chat:{ROOM}" and msg["type"] == "chat_message"
        assert msg["message"] == r.json()["message"]


def test_profanity_is_masked_stored_and_sender_kicked(fresh):
    headers = token(fresh, "player@nascon.test")
    r = post(fresh, headers, "that ref is a sc@m!")
    assert r.status_code == 201
    msg = r.json()["message"]
    assert msg["text"] == "that ref is a ***!" and msg["flagged"] is True and msg["reason"] == "profanity_leetspeak"
    row = db.fetchone("SELECT action, reason FROM md_room_actions WHERE chat_room_id = %s AND user_id = 'user-player'",
                      (ROOM,))
    assert row == {"action": "kick", "reason": "profanity_leetspeak"}


def test_flood_is_blocked_and_sender_muted(fresh):
    headers = token(fresh, "player@nascon.test")
    r = post(fresh, headers, "GOAL! AAAA!!!")
    assert r.status_code == 200
    assert r.json() == {"chat_room_id": ROOM, "blocked": True, "reason": "flood_character_repeat", "muted": True,
                        "kicked": False, "message": None}
    r = post(fresh, headers, "sorry")
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert r.json()["error"]["details"]["reason"] == "muted"
    assert fresh.get(f"/api/v1/chat/{ROOM}/messages").json()["messages"] == []
    # another member is unaffected
    assert post(fresh, token(fresh, "viewer@nascon.test"), "fine").status_code == 201


def test_fourth_message_in_two_seconds_is_rate_limited(fresh):
    headers = token(fresh, "viewer@nascon.test")
    assert [post(fresh, headers, f"m{i}").status_code for i in range(3)] == [201, 201, 201]
    r = post(fresh, headers, "m3")
    assert r.status_code == 429
    assert r.json()["error"]["details"]["reason"] == "rate_limit_exceeded"
    actions = db.fetchall("SELECT action FROM md_room_actions WHERE user_id = 'user-viewer' ORDER BY action")
    assert [a["action"] for a in actions] == ["kick", "mute"]


def test_client_message_id_makes_posts_idempotent(fresh):
    headers = token(fresh, "player@nascon.test")
    first = post(fresh, headers, "hello", message_id="client-1")
    assert first.status_code == 201
    again = post(fresh, headers, "hello", message_id="client-1")
    assert again.status_code == 200 and again.json()["message"] == first.json()["message"]
    assert len(fresh.get(f"/api/v1/chat/{ROOM}/messages").json()["messages"]) == 1


@pytest.mark.parametrize("body,fields", [
    ({"chat_room_id": ROOM}, ["text"]),
    ({"chat_room_id": ROOM, "text": "   "}, ["text"]),
    ({"chat_room_id": ROOM, "text": "x" * 501}, ["text"]),
    ({"chat_room_id": "bad room", "text": "hi"}, ["chat_room_id"]),
    ({"text": "hi"}, ["chat_room_id"]),
    ({"chat_room_id": ROOM, "text": "hi", "message_id": "has space"}, ["message_id"]),
])
def test_chat_validation(fresh, body, fields):
    r = fresh.post("/api/v1/chat", json=body, headers=token(fresh, "player@nascon.test"))
    assert r.status_code == 400 and r.json()["error"]["details"]["fields"] == fields


def test_listing_validation(fresh):
    assert fresh.get("/api/v1/chat/bad room/messages").status_code == 400
    assert fresh.get(f"/api/v1/chat/{ROOM}/messages?after=-1").status_code == 400
    assert fresh.get(f"/api/v1/chat/{ROOM}/messages?limit=0").status_code == 400
    assert fresh.get("/api/v1/chat/chat-empty/messages").json() == {"chat_room_id": "chat-empty", "messages": []}


def test_racing_identical_message_ids_resolve_to_one_row(fresh, monkeypatch):
    import psycopg

    headers = token(fresh, "player@nascon.test")
    real = service.post_chat
    calls = []

    def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            real(*args)
            raise psycopg.errors.UniqueViolation("duplicate key")
        return real(*args)

    monkeypatch.setattr(service, "post_chat", flaky)
    r = post(fresh, headers, "dup", message_id="dup-1")
    assert r.status_code == 200 and r.json()["message"]["message_id"] == "dup-1"
    assert len(calls) == 2


def test_notification_bus_delivery_and_dead_letters(fresh):
    with fresh.websocket_connect("/api/v1/ws?topic=orders") as ws:
        wait_for_subscriber("orders")
        notes = bus.handle_domain_event("order_status_changed", {"order_id": "ord-mod-1", "to_status": "paid"})
        assert [n["delivered"] for n in notes] == [True, True] and [n["attempts"] for n in notes] == [1, 1]
        assert [n["topic"] for n in notes] == ["orders", "order:ord-mod-1"]
        for _ in range(50):
            msg = ws.receive_json()
            if msg["data"].get("order_id") == "ord-mod-1":
                break
        assert msg["type"] == "order_status_changed" and msg["topic"] == "orders"
    assert bus.handle_domain_event("something_else", {}) == []
    assert bus.handle_domain_event(None, {}) == []
    invites = bus.notify_costream_invite("cs-session-101", "player-alpha-2", {"token": "inv"}, simulate_dlq=True)
    assert [n["attempts"] for n in invites] == [3, 3] and bus.depth() == 2
    # a targeted delivery to a disconnected subscriber also dead-letters after three attempts
    lost = bus.notify("user:nobody", "costream_invite", {"x": 1}, require_subscriber=True)
    assert lost["delivered"] is False and lost["last_error"] == "target subscriber disconnected"
    assert bus.depth() == 3
    with fresh.websocket_connect("/api/v1/ws?topic=user:somebody") as ws:
        wait_for_subscriber("user:somebody")
        ok = bus.notify("user:somebody", "costream_invite", {"x": 2}, require_subscriber=True)
        assert ok["delivered"] is True and ws.receive_json()["x"] == 2


def test_dispatch_probe_and_dlq_listing(fresh):
    r = fresh.post("/audit/v1/notifications/dispatch", json={"topic": "orders", "simulate_dlq": True,
                                                             "payload": {"order_id": "o-9"}})
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] is False and body["attempts"] == 3 and body["dlq_depth"] == 1
    ok = fresh.post("/audit/v1/notifications/dispatch", json={"topic": "orders", "event_type": "ping"}).json()
    assert ok["delivered"] is True and ok["attempts"] == 1 and ok["dlq_depth"] == 1
    listing = fresh.get("/audit/v1/dlq").json()
    assert listing["queue_depth"] == 1 and listing["undelivered_events"][0]["payload"] == {"order_id": "o-9"}
    bad = fresh.post("/audit/v1/notifications/dispatch", json={"simulate_dlq": "x"})
    assert bad.status_code == 400 and bad.json()["error"]["details"]["fields"] == ["topic", "simulate_dlq"]
