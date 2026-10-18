import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi import WebSocketDisconnect

from app.core import config, db, realtime
from app.modules.costream import routes, service
from app.modules.streaming import media

URL = "/audit/v1/costream/session"
FOUR = ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2"]
SPEC = {"session_id": "cs-session-101", "host_player_id": "player-alpha-1", "participants": FOUR,
        "layout": "grid_4", "simulate_host_drop": True}
KEYS = ["session_id", "status", "active_host", "handoff_occurred", "participant_count", "active_participants",
        "layout", "composite_playback_url", "chat_room_id", "signature"]


def raw_sig(body):
    payload = {k: body[k] for k in ("active_host", "handoff_occurred", "layout", "participant_count",
                                    "session_id", "status")}
    msg = body["session_id"] + "|" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(config.DAY2_SALT.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest.fixture
def severed(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "sever", lambda paths, background=True: calls.append(list(paths)))
    return calls


@pytest.fixture
def published(monkeypatch):
    calls = []
    monkeypatch.setattr(realtime, "publish", lambda topic, message: calls.append((topic, message)))
    return calls


def post(client, **overrides):
    body = {**SPEC, **overrides}
    return client.post(URL, json={k: v for k, v in body.items() if v is not ...})


def error(r):
    return r.json()["error"]


def test_spec_example_hands_off_to_next_participant(fresh, severed, published):
    r = fresh.post(URL, json=SPEC)
    assert r.status_code == 201, r.text
    body = r.json()
    assert list(body) == KEYS
    assert {k: v for k, v in body.items() if k != "signature"} == {
        "session_id": "cs-session-101",
        "status": "active",
        "active_host": "player-alpha-2",
        "handoff_occurred": True,
        "participant_count": 3,
        "active_participants": ["player-alpha-2", "player-beta-1", "player-beta-2"],
        "layout": "grid_4",
        "composite_playback_url": "http://localhost:8888/costream/cs-session-101/index.m3u8",
        "chat_room_id": "chat-cs-session-101",
    }
    assert body["signature"] == raw_sig(body)
    assert published == [("costream:cs-session-101", {"type": "costream_update", "session": body})]
    assert severed == []


def test_signature_matches_reference_salt(fresh, monkeypatch):
    monkeypatch.setattr(config, "DAY2_SALT", "T01D27ad7ffff12e")
    with db.tx() as conn:
        body = service.evaluate(conn, service.parse(SPEC))
    msg = ('cs-session-101|{"active_host":"player-alpha-2","handoff_occurred":true,"layout":"grid_4",'
           '"participant_count":3,"session_id":"cs-session-101","status":"active"}')
    assert body["signature"] == hmac.new(b"T01D27ad7ffff12e", msg.encode(), hashlib.sha256).hexdigest()
    terminated = {"session_id": "cs-x", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"],
                  "simulate_host_drop": True}
    with db.tx() as conn:
        body = service.evaluate(conn, service.parse(terminated))
    msg = ('cs-x|{"active_host":null,"handoff_occurred":false,"layout":"grid_4","participant_count":0,'
           '"session_id":"cs-x","status":"terminated"}')
    assert body["signature"] == hmac.new(b"T01D27ad7ffff12e", msg.encode(), hashlib.sha256).hexdigest()


def test_identical_replay_is_200_with_same_body(fresh, severed, published):
    first = fresh.post(URL, json=SPEC)
    again = fresh.post(URL, json=SPEC)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json() == first.json()
    assert list(again.json()) == KEYS
    assert len(published) == 1


def test_omitted_defaults_replay_explicit_defaults(fresh, severed, published):
    r1 = post(fresh, session_id="cs-defaults", layout=..., simulate_host_drop=...)
    assert r1.status_code == 201
    assert r1.json()["layout"] == "grid_4" and r1.json()["handoff_occurred"] is False
    r2 = post(fresh, session_id="cs-defaults", layout="grid_4", simulate_host_drop=False)
    assert r2.status_code == 200 and r2.json() == r1.json()
    r3 = post(fresh, session_id="cs-defaults", layout=None, simulate_host_drop=None, extra="ignored")
    assert r3.status_code == 200


def test_no_drop_keeps_host_and_everyone(fresh, severed, published):
    r = post(fresh, session_id="cs-live", simulate_host_drop=False)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "active" and body["active_host"] == "player-alpha-1"
    assert body["handoff_occurred"] is False and body["participant_count"] == 4
    assert body["active_participants"] == FOUR
    assert body["signature"] == raw_sig(body)


def test_later_drop_on_same_session_hands_off_and_keeps_chat_room(fresh, severed, published):
    created = post(fresh, session_id="cs-session-101", simulate_host_drop=False).json()
    r = fresh.post(URL, json=SPEC)
    assert r.status_code == 201
    dropped = r.json()
    assert dropped["active_host"] == "player-alpha-2" and dropped["participant_count"] == 3
    assert dropped["chat_room_id"] == created["chat_room_id"] == "chat-cs-session-101"
    assert dropped["composite_playback_url"] == created["composite_playback_url"]
    with db.tx() as conn:
        assert service.get_session(conn, "cs-session-101") == dropped
        assert list(service.get_session(conn, "cs-session-101")) == KEYS
        assert service.get_session(conn, "cs-nope") is None
        assert service.session_status(conn, "cs-session-101") == "active"
        revision = conn.execute("SELECT revision FROM cs_sessions WHERE session_id = 'cs-session-101'").fetchone()
    assert revision["revision"] == 2


def test_two_participants_drop_leaves_one_host(fresh, severed, published):
    r = post(fresh, session_id="cs-two", participants=["player-alpha-1", "player-beta-1"], layout="grid_2")
    assert r.status_code == 201
    body = r.json()
    assert body["active_host"] == "player-beta-1" and body["participant_count"] == 1
    assert body["active_participants"] == ["player-beta-1"] and body["handoff_occurred"] is True
    assert body["signature"] == raw_sig(body)


def test_host_not_first_hands_off_to_first_remaining(fresh, severed, published):
    r = post(fresh, session_id="cs-mid", host_player_id="player-beta-1")
    body = r.json()
    assert body["active_host"] == "player-alpha-1"
    assert body["active_participants"] == ["player-alpha-1", "player-alpha-2", "player-beta-2"]


def test_sole_host_drop_terminates(fresh, severed, published):
    r = post(fresh, session_id="cs-solo", participants=["player-alpha-1"], layout="solo")
    assert r.status_code == 201
    body = r.json()
    assert list(body) == KEYS
    assert body["status"] == "terminated" and body["active_host"] is None
    assert body["handoff_occurred"] is False and body["participant_count"] == 0
    assert body["active_participants"] == []
    assert body["chat_room_id"] == "chat-cs-solo"
    assert body["signature"] == raw_sig(body)
    assert severed == [["costream/cs-solo"]]


def test_sole_host_without_drop_is_active_solo(fresh, severed, published):
    r = post(fresh, session_id="cs-solo2", participants=["player-alpha-1"], layout="solo", simulate_host_drop=False)
    assert r.status_code == 201
    assert r.json()["participant_count"] == 1 and r.json()["active_host"] == "player-alpha-1"


@pytest.mark.parametrize("overrides,fields", [
    ({"session_id": ...}, ["session_id"]),
    ({"session_id": ""}, ["session_id"]),
    ({"session_id": 101}, ["session_id"]),
    ({"host_player_id": ...}, ["host_player_id"]),
    ({"host_player_id": ["player-alpha-1"]}, ["host_player_id"]),
    ({"participants": ...}, ["participants"]),
    ({"participants": []}, ["participants"]),
    ({"participants": "player-alpha-1"}, ["participants"]),
    ({"participants": ["player-alpha-1", "player-alpha-1"]}, ["participants"]),
    ({"participants": ["player-alpha-1", 2]}, ["participants"]),
    ({"participants": ["player-alpha-1", ""]}, ["participants"]),
    ({"layout": "grid_9"}, ["layout"]),
    ({"layout": 4}, ["layout"]),
    ({"simulate_host_drop": "yes"}, ["simulate_host_drop"]),
    ({"simulate_host_drop": 1}, ["simulate_host_drop"]),
    ({"participants": FOUR + ["player-alpha-1"], "layout": "solo"}, ["participants"]),
])
def test_schema_violations_are_400(fresh, severed, published, overrides, fields):
    r = post(fresh, **overrides)
    assert r.status_code == 400, r.text
    assert error(r)["code"] == "VALIDATION_ERROR"
    assert error(r)["details"]["fields"] == fields


def test_non_object_bodies_are_400(fresh):
    assert fresh.post(URL, json=["cs-1"]).status_code == 400
    r = fresh.post(URL, content=b"{", headers={"content-type": "application/json"})
    assert r.status_code == 400 and error(r)["code"] == "VALIDATION_ERROR"


def test_unknown_participant_is_404(fresh, severed, published):
    r = post(fresh, participants=["player-alpha-1", "player-ghost"])
    assert r.status_code == 404
    assert error(r)["code"] == "PLAYER_NOT_FOUND" and error(r)["details"] == {"player_id": "player-ghost"}


def test_unknown_host_is_404_before_membership_check(fresh, severed, published):
    r = post(fresh, host_player_id="player-ghost")
    assert r.status_code == 404 and error(r)["details"] == {"player_id": "player-ghost"}


def test_host_outside_participants_is_400(fresh, severed, published):
    r = post(fresh, host_player_id="player-alpha-3", participants=["player-alpha-1", "player-alpha-2"])
    assert r.status_code == 400
    assert error(r)["code"] == "VALIDATION_ERROR" and error(r)["details"]["fields"] == ["host_player_id"]


def test_more_than_four_is_session_full(fresh, severed, published):
    five = FOUR + ["player-alpha-3"]
    r = post(fresh, participants=five, layout="solo")
    assert r.status_code == 409
    assert error(r)["code"] == "COSTREAM_SESSION_FULL"
    assert error(r)["details"]["participant_count"] == 5


def test_unknown_player_beats_session_full(fresh, severed, published):
    r = post(fresh, participants=FOUR + ["player-ghost"])
    assert r.status_code == 404 and error(r)["code"] == "PLAYER_NOT_FOUND"


def test_host_outside_beats_session_full(fresh, severed, published):
    r = post(fresh, host_player_id="player-alpha-4", participants=FOUR + ["player-alpha-3"])
    assert r.status_code == 400 and error(r)["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("layout,participants,drop,status", [
    ("solo", FOUR[:2], False, 400),
    ("solo", FOUR[:2], True, 201),
    ("solo", FOUR[:3], True, 400),
    ("grid_2", FOUR[:3], False, 400),
    ("grid_2", FOUR[:3], True, 201),
    ("grid_2", FOUR[:2], False, 201),
    ("grid_2", FOUR, True, 400),
    ("grid_4", FOUR, False, 201),
    ("spotlight", FOUR, False, 201),
    ("spotlight", FOUR[:1], False, 201),
])
def test_layout_capacity_uses_effective_count(fresh, severed, published, layout, participants, drop, status):
    r = post(fresh, session_id=f"cs-{layout}-{len(participants)}-{drop}", participants=participants,
             layout=layout, simulate_host_drop=drop)
    assert r.status_code == status, r.text
    if status == 400:
        assert error(r)["code"] == "UNSUPPORTED_LAYOUT"
        assert error(r)["details"]["layout"] == layout
    else:
        assert r.json()["layout"] == layout


def test_failed_requests_store_nothing(fresh, severed, published):
    post(fresh, session_id="cs-bad", layout="solo", simulate_host_drop=False)
    with db.tx() as conn:
        assert service.get_session(conn, "cs-bad") is None
    assert published == []


def test_get_costream_route(fresh, severed, published):
    created = fresh.post(URL, json=SPEC).json()
    r = fresh.get("/api/v1/costream/cs-session-101")
    assert r.status_code == 200 and r.json() == created
    missing = fresh.get("/api/v1/costream/cs-nope")
    assert missing.status_code == 404 and error(missing)["code"] == "STREAM_NOT_FOUND"


def test_reset_wipes_sessions(fresh, severed, published):
    fresh.post(URL, json=SPEC)
    fresh.post("/audit/v1/reset", json={})
    assert fresh.get("/api/v1/costream/cs-session-101").status_code == 404
    assert fresh.post(URL, json=SPEC).status_code == 201


def test_composite_inputs_lists_live_participants_in_order(fresh, severed, published, monkeypatch):
    keys = {"player-alpha-2": "sk_live_aaaaaaaaaaaaaaaa", "player-beta-1": "sk_live_bbbbbbbbbbbbbbbb",
            "player-beta-2": "sk_live_cccccccccccccccc"}
    for player_id, key in keys.items():
        r = fresh.post("/audit/v1/stream/key", json={"action": "generate", "player_id": player_id,
                                                     "stream_key_override": key})
        assert r.status_code == 201
    fresh.post(URL, json=SPEC)
    monkeypatch.setattr(media, "ready_paths", lambda: {"live/sk_live_cccccccccccccccc", "live/sk_live_aaaaaaaaaaaaaaaa"})
    r = fresh.get("/internal/costream/cs-session-101/inputs")
    assert r.status_code == 200
    assert r.text == "live/sk_live_aaaaaaaaaaaaaaaa\nlive/sk_live_cccccccccccccccc"
    assert fresh.get("/internal/costream/cs-nope/inputs").text == ""


def stored(session_id):
    with db.tx() as conn:
        return service.get_session(conn, session_id)


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.05)
    return predicate()


HOST_WS = "/ws/costream/cs-session-101?player_id=player-alpha-1&role=host"


def test_host_socket_disconnect_hands_off_and_notifies_viewers(fresh, severed):
    fresh.post(URL, json={**SPEC, "simulate_host_drop": False})
    with fresh.websocket_connect("/ws/costream/cs-session-101") as viewer:
        first = viewer.receive_json()
        assert first["type"] == "costream_state" and first["session"]["active_host"] == "player-alpha-1"
        with fresh.websocket_connect(HOST_WS) as host:
            assert host.receive_json()["session"]["participant_count"] == 4
            host.send_text("still here")
        update = viewer.receive_json()
    assert update["topic"] == "costream:cs-session-101" and update["type"] == "costream_update"
    session = update["session"]
    assert session["active_host"] == "player-alpha-2" and session["handoff_occurred"] is True
    assert session["participant_count"] == 3
    assert session["active_participants"] == ["player-alpha-2", "player-beta-1", "player-beta-2"]
    assert session["chat_room_id"] == "chat-cs-session-101"
    assert session["signature"] == raw_sig(session)
    assert stored("cs-session-101") == session


def test_only_the_active_hosts_last_socket_hands_off(fresh, severed):
    fresh.post(URL, json={**SPEC, "simulate_host_drop": False})
    with fresh.websocket_connect(HOST_WS) as first:
        first.receive_json()
        with fresh.websocket_connect(HOST_WS) as second:
            second.receive_json()
        with fresh.websocket_connect("/ws/costream/cs-session-101?player_id=player-alpha-1") as viewer:
            viewer.receive_json()
        with fresh.websocket_connect("/ws/costream/cs-session-101?player_id=player-beta-1&role=host") as other:
            other.receive_json()
        time.sleep(0.3)
        assert stored("cs-session-101")["active_host"] == "player-alpha-1"
    assert wait_for(lambda: stored("cs-session-101")["active_host"] == "player-alpha-2")


def test_sole_host_socket_disconnect_terminates(fresh, severed):
    fresh.post(URL, json={"session_id": "cs-one", "host_player_id": "player-alpha-1",
                          "participants": ["player-alpha-1"], "layout": "solo"})
    with fresh.websocket_connect("/ws/costream/cs-one?player_id=player-alpha-1&role=host") as host:
        host.receive_json()
    assert wait_for(lambda: stored("cs-one")["status"] == "terminated")
    assert wait_for(lambda: severed == [["costream/cs-one"]])


def test_socket_for_unknown_session(fresh, severed):
    with fresh.websocket_connect("/ws/costream/cs-nope?player_id=player-alpha-1&role=host") as ws:
        assert ws.receive_json() == {"topic": "costream:cs-nope", "type": "costream_state", "session": None}
    with db.tx() as conn:
        assert service.host_left(conn, "cs-nope", "player-alpha-1") is None


class DroppedSocket:
    query_params = {"player_id": "player-alpha-1", "role": "host"}

    async def accept(self):
        pass

    async def send_json(self, data):
        raise WebSocketDisconnect(1006)

    async def receive(self):
        return {"type": "websocket.disconnect"}


def test_host_dropping_before_first_frame_still_hands_off(fresh, severed):
    fresh.post(URL, json={**SPEC, "simulate_host_drop": False})
    asyncio.run(routes.costream_socket(DroppedSocket(), "cs-session-101"))
    assert wait_for(lambda: stored("cs-session-101")["active_host"] == "player-alpha-2")


def test_guest_token_cannot_create_sessions(fresh):
    r = fresh.post(URL, json=SPEC, headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert r.status_code == 403 and error(r)["code"] == "GUEST_WRITE_FORBIDDEN"
