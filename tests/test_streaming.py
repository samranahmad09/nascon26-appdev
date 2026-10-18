import hashlib
import hmac
import json
import re
import threading

import httpx
import pytest

from app.core import config
from app.modules.streaming import media, service

URL = "/audit/v1/stream/key"
AUTH = "/internal/media/auth"
SPEC_KEY = "sk_live_9f83a1b2c3d4e5f6"


def raw_sig(entity_id, payload, salt=None):
    msg = entity_id + "|" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    key = (config.DAY2_SALT if salt is None else salt).encode("utf-8")
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def expected_sig(action, body):
    return raw_sig(body["stream_key"], {"action": action, "player_id": body["player_id"],
                                        "status": body["status"], "stream_key": body["stream_key"]})


@pytest.fixture
def severed(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "sever", lambda paths, background=True: calls.append(list(paths)))
    return calls


def post(client, **body):
    return client.post(URL, json=body)


def auth(client, path, action="publish"):
    return client.post(AUTH, json={"user": "", "password": "", "ip": "172.18.0.1", "action": action,
                                   "path": path, "protocol": "rtmp", "id": None, "query": ""})


def test_generate_returns_201_with_exact_contract(fresh, severed):
    r = post(fresh, action="generate", player_id="player-alpha-1")
    assert r.status_code == 201, r.text
    body = r.json()
    assert list(body) == ["player_id", "label", "stream_key", "status", "ingest_endpoints", "playback_url", "signature"]
    key = body["stream_key"]
    assert re.fullmatch(r"sk_live_[a-f0-9]{16}", key)
    assert body["player_id"] == "player-alpha-1"
    assert body["label"] == "default"
    assert body["status"] == "active"
    assert body["ingest_endpoints"] == {"rtmp": f"rtmp://localhost:1935/live/{key}",
                                        "webrtc": f"http://localhost:8889/live/{key}/whip"}
    assert body["playback_url"] == f"http://localhost:8888/live/{key}/index.m3u8"
    assert body["signature"] == expected_sig("generate", body)
    assert severed == [[]]


def test_rotate_spec_vector_uses_override_and_four_key_signature(fresh, severed):
    first = post(fresh, action="generate", player_id="player-alpha-1").json()["stream_key"]
    r = post(fresh, action="rotate", player_id="player-alpha-1", label="OBS-Main-Desk", stream_key_override=SPEC_KEY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stream_key"] == SPEC_KEY and body["label"] == "OBS-Main-Desk" and body["status"] == "active"
    assert body["ingest_endpoints"]["rtmp"] == "rtmp://localhost:1935/live/sk_live_9f83a1b2c3d4e5f6"
    assert body["ingest_endpoints"]["webrtc"] == "http://localhost:8889/live/sk_live_9f83a1b2c3d4e5f6/whip"
    assert body["playback_url"] == "http://localhost:8888/live/sk_live_9f83a1b2c3d4e5f6/index.m3u8"
    assert body["signature"] == raw_sig(SPEC_KEY, {"action": "rotate", "player_id": "player-alpha-1",
                                                   "status": "active", "stream_key": SPEC_KEY})
    assert severed[-1] == [f"live/{first}"]
    assert auth(fresh, f"live/{first}").status_code == 409
    assert auth(fresh, f"live/{SPEC_KEY}").status_code == 200


def test_appendix_a_vector_message_and_digest(monkeypatch):
    monkeypatch.setattr(config, "DAY2_SALT", "T01D27ad7ffff12e")
    body = service.key_body("rotate", "player-alpha-1", "OBS-Main-Desk", SPEC_KEY, "active")
    msg = ('sk_live_9f83a1b2c3d4e5f6|{"action":"rotate","player_id":"player-alpha-1","status":"active",'
           '"stream_key":"sk_live_9f83a1b2c3d4e5f6"}')
    assert body["signature"] == hmac.new(b"T01D27ad7ffff12e", msg.encode(), hashlib.sha256).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", body["signature"])


def test_rotate_without_prior_key_still_issues_one(fresh, severed):
    r = post(fresh, action="rotate", player_id="player-beta-2")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active" and re.fullmatch(r"sk_live_[a-f0-9]{16}", body["stream_key"])
    assert body["signature"] == expected_sig("rotate", body)
    assert severed == [[]]


def test_generate_replaces_the_active_key(fresh, severed):
    old = post(fresh, action="generate", player_id="player-alpha-2").json()["stream_key"]
    r = post(fresh, action="generate", player_id="player-alpha-2", label="Phone")
    assert r.status_code == 201
    new = r.json()["stream_key"]
    assert new != old and r.json()["label"] == "Phone"
    assert severed[-1] == [f"live/{old}"]
    assert auth(fresh, f"live/{old}").status_code == 409
    assert auth(fresh, f"live/{new}").status_code == 200


def test_revoke_returns_revoked_with_null_endpoints(fresh, severed):
    post(fresh, action="rotate", player_id="player-alpha-1", label="OBS-Main-Desk", stream_key_override=SPEC_KEY)
    r = post(fresh, action="revoke", player_id="player-alpha-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == ["player_id", "label", "stream_key", "status", "ingest_endpoints", "playback_url", "signature"]
    assert body["stream_key"] == SPEC_KEY and body["status"] == "revoked"
    assert body["ingest_endpoints"] is None and body["playback_url"] is None
    assert body["label"] == "OBS-Main-Desk"
    assert body["signature"] == raw_sig(SPEC_KEY, {"action": "revoke", "player_id": "player-alpha-1",
                                                   "status": "revoked", "stream_key": SPEC_KEY})
    assert severed[-1] == [f"live/{SPEC_KEY}"]
    denied = auth(fresh, f"live/{SPEC_KEY}")
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "KEY_REVOKED"


def test_revoke_twice_is_key_revoked(fresh, severed):
    post(fresh, action="generate", player_id="player-alpha-1")
    assert post(fresh, action="revoke", player_id="player-alpha-1").status_code == 200
    r = post(fresh, action="revoke", player_id="player-alpha-1")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "KEY_REVOKED"


def test_revoke_echoes_given_label(fresh, severed):
    post(fresh, action="generate", player_id="player-alpha-1", label="Desk")
    assert post(fresh, action="revoke", player_id="player-alpha-1", label="Gone").json()["label"] == "Gone"


def test_revoke_without_any_key_is_stream_not_found(fresh, severed):
    r = post(fresh, action="revoke", player_id="player-beta-1")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "STREAM_NOT_FOUND"
    assert r.json()["error"]["details"] == {"player_id": "player-beta-1"}


def test_revoke_with_override_targets_that_key(fresh, severed):
    old = post(fresh, action="generate", player_id="player-alpha-1").json()["stream_key"]
    post(fresh, action="rotate", player_id="player-alpha-1", stream_key_override=SPEC_KEY)
    missing = post(fresh, action="revoke", player_id="player-alpha-1", stream_key_override="sk_live_0000000000000000")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "STREAM_NOT_FOUND"
    rotated_away = post(fresh, action="revoke", player_id="player-alpha-1", stream_key_override=old)
    assert rotated_away.status_code == 409 and rotated_away.json()["error"]["code"] == "KEY_REVOKED"
    r = post(fresh, action="revoke", player_id="player-alpha-1", stream_key_override=SPEC_KEY)
    assert r.status_code == 200 and r.json()["stream_key"] == SPEC_KEY


def test_override_of_a_revoked_key_reactivates_it(fresh, severed):
    post(fresh, action="generate", player_id="player-alpha-1", stream_key_override=SPEC_KEY)
    post(fresh, action="revoke", player_id="player-alpha-1")
    r = post(fresh, action="generate", player_id="player-alpha-1", stream_key_override=SPEC_KEY)
    assert r.status_code == 201 and r.json()["status"] == "active"
    assert auth(fresh, f"live/{SPEC_KEY}").status_code == 200


def test_override_moves_a_key_between_players(fresh, severed):
    post(fresh, action="generate", player_id="player-alpha-1", stream_key_override=SPEC_KEY)
    r = post(fresh, action="generate", player_id="player-alpha-2", stream_key_override=SPEC_KEY)
    assert r.status_code == 201 and r.json()["player_id"] == "player-alpha-2"
    assert post(fresh, action="revoke", player_id="player-alpha-1").status_code == 404
    assert post(fresh, action="revoke", player_id="player-alpha-2").status_code == 200


def test_legacy_short_override_is_used_verbatim(fresh, severed):
    r = post(fresh, action="rotate", player_id="player-alpha-1", stream_key_override="sk_live_101")
    assert r.status_code == 200 and r.json()["stream_key"] == "sk_live_101"
    assert r.json()["signature"] == expected_sig("rotate", r.json())


def test_unknown_player_is_404(fresh, severed):
    for action in ("generate", "rotate", "revoke"):
        r = post(fresh, action=action, player_id="player-ghost")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "PLAYER_NOT_FOUND"
        assert r.json()["error"]["details"] == {"player_id": "player-ghost"}


@pytest.mark.parametrize("body,fields", [
    ({"action": "delete", "player_id": "player-alpha-1"}, ["action"]),
    ({"player_id": "player-alpha-1"}, ["action"]),
    ({"action": "generate"}, ["player_id"]),
    ({"action": "generate", "player_id": 7}, ["player_id"]),
    ({"action": "generate", "player_id": ""}, ["player_id"]),
    ({"action": "generate", "player_id": "player-alpha-1", "label": ""}, ["label"]),
    ({"action": "generate", "player_id": "player-alpha-1", "label": "x" * 65}, ["label"]),
    ({"action": "generate", "player_id": "player-alpha-1", "label": 5}, ["label"]),
    ({"action": "generate", "player_id": "player-alpha-1", "stream_key_override": 12}, ["stream_key_override"]),
    ({"action": "generate", "player_id": "player-alpha-1", "stream_key_override": ""}, ["stream_key_override"]),
    ({"action": "explode", "player_id": "player-ghost"}, ["action"]),
    ({"action": ["generate"], "player_id": None}, ["action", "player_id"]),
])
def test_schema_violations_are_400(fresh, severed, body, fields):
    r = fresh.post(URL, json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert r.json()["error"]["details"]["fields"] == fields


def test_label_limits_and_null_optionals(fresh, severed):
    r = post(fresh, action="generate", player_id="player-alpha-1", label="x" * 64, stream_key_override=None)
    assert r.status_code == 201 and r.json()["label"] == "x" * 64
    assert re.fullmatch(r"sk_live_[a-f0-9]{16}", r.json()["stream_key"])


def test_non_object_and_malformed_bodies_are_400(fresh, severed):
    assert fresh.post(URL, json=["generate"]).status_code == 400
    r = fresh.post(URL, content=b"{bad json", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_guest_token_cannot_manage_keys(fresh, severed):
    r = fresh.post(URL, json={"action": "generate", "player_id": "player-alpha-1"},
                   headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"


def test_reset_wipes_keys(fresh, severed, monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(service, "sever_all", lambda background=True: ran.set())
    key = post(fresh, action="generate", player_id="player-alpha-1").json()["stream_key"]
    assert fresh.post("/audit/v1/reset", json={}).status_code == 200
    assert ran.wait(2)
    assert post(fresh, action="revoke", player_id="player-alpha-1").status_code == 404
    assert auth(fresh, f"live/{key}").status_code == 401


# --- MediaMTX auth hook -------------------------------------------------------------------------

def test_auth_hook_allows_active_and_denies_unknown_keys(fresh, severed):
    key = post(fresh, action="generate", player_id="player-alpha-1").json()["stream_key"]
    for action in ("publish", "read", "playback"):
        assert auth(fresh, f"live/{key}", action).status_code == 200
    r = auth(fresh, "live/sk_live_ffffffffffffffff")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert auth(fresh, f"live/{key}/extra").status_code == 401
    assert auth(fresh, "other/path").status_code == 401
    assert auth(fresh, "costream/").status_code == 401


def test_auth_hook_open_actions_and_bad_requests(fresh):
    assert auth(fresh, "", action="api").status_code == 200
    assert auth(fresh, "live/x", action="dance").status_code == 401
    assert fresh.post(AUTH, json={"action": "publish", "path": 5}).status_code == 401
    assert fresh.post(AUTH, json=["publish"]).status_code == 401
    r = fresh.post(AUTH, content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 401


def test_auth_hook_costream_paths(fresh, severed):
    assert auth(fresh, "costream/cs-unknown", "read").status_code == 200
    body = {"session_id": "cs-solo", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"],
            "layout": "solo"}
    assert fresh.post("/audit/v1/costream/session", json=body).status_code == 201
    assert auth(fresh, "costream/cs-solo", "read").status_code == 200
    fresh.post("/audit/v1/costream/session", json={**body, "simulate_host_drop": True})
    r = auth(fresh, "costream/cs-solo", "read")
    assert r.status_code == 409 and r.json()["error"]["code"] == "STREAM_TERMINATED"


def test_auth_cache_is_dropped_when_a_key_changes(fresh, severed):
    key = post(fresh, action="generate", player_id="player-alpha-1").json()["stream_key"]
    assert auth(fresh, f"live/{key}").status_code == 200
    assert service.cached_auth({"action": "read", "path": f"live/{key}"}) is None
    post(fresh, action="revoke", player_id="player-alpha-1")
    assert service.cached_auth({"action": "read", "path": f"live/{key}"}) is service.MISS
    assert auth(fresh, f"live/{key}").status_code == 409
    assert service.cached_auth({"action": "read", "path": f"live/{key}"}).code == "KEY_REVOKED"


def test_auth_cache_skips_stale_lookups_and_bounds_size(fresh, monkeypatch):
    real_authorize = service.authorize

    def racing(conn, request):
        service.invalidate_auth()
        return real_authorize(conn, request)

    monkeypatch.setattr(service, "authorize", racing)
    assert service.check_auth({"action": "read", "path": "live/nope"}).status == 401
    assert service.cached_auth({"action": "read", "path": "live/nope"}) is service.MISS
    monkeypatch.setattr(service, "authorize", real_authorize)
    monkeypatch.setattr(service, "AUTH_CACHE_MAX", 2)
    for i in range(3):
        service.check_auth({"action": "read", "path": f"live/k{i}"})
    assert len(service._auth_cache) <= 2
    assert service.check_auth({"action": "api"}) is None


# --- MediaMTX control API -----------------------------------------------------------------------

class FakeMediaMTX:
    def __init__(self, conns=None, paths=None, fail=False):
        self.conns = conns or {}
        self.paths = paths or []
        self.fail = fail
        self.kicked = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("media down", request=request)
        parts = request.url.path.strip("/").split("/")
        if request.method == "GET" and parts == ["v3", "paths", "list"]:
            return httpx.Response(200, json={"itemCount": len(self.paths), "pageCount": 1, "items": self.paths})
        if request.method == "GET" and len(parts) == 3 and parts[2] == "list":
            if parts[1] not in self.conns:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"items": self.conns[parts[1]]})
        if request.method == "POST" and len(parts) == 4 and parts[2] == "kick":
            self.kicked.append((parts[1], parts[3]))
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def mediamtx(monkeypatch):
    def install(**kwargs):
        fake = FakeMediaMTX(**kwargs)
        monkeypatch.setattr(media, "transport", httpx.MockTransport(fake))
        monkeypatch.setattr(media, "SECOND_SWEEP_S", 0)
        media.reset_cache()
        return fake
    yield install
    media.reset_cache()


def test_kick_paths_kicks_publishers_and_readers_on_every_protocol(mediamtx):
    fake = mediamtx(conns={
        "rtmpconns": [{"id": "r1", "path": "live/a", "state": "publish"}, {"id": "r2", "path": "live/b"}],
        "webrtcsessions": [{"id": "w1", "path": "live/a", "state": "read"}],
        "srtconns": [],
    })
    assert media.kick_paths(["live/a"]) == 2
    assert fake.kicked == [("rtmpconns", "r1"), ("webrtcsessions", "w1")]


def test_sever_sweeps_twice_in_foreground_and_once_started_in_background(mediamtx, monkeypatch):
    fake = mediamtx(conns={"rtmpconns": [{"id": "r1", "path": "live/a"}]})
    media.sever(["live/a", "live/a", ""], background=False)
    assert fake.kicked == [("rtmpconns", "r1"), ("rtmpconns", "r1")]
    ran, done = [], threading.Event()
    monkeypatch.setattr(media, "_sweep", lambda paths: (ran.append(paths), done.set()))
    media.sever([])
    media.sever(["live/b"])
    assert done.wait(2) and ran == [["live/b"]]


def test_unreachable_media_is_harmless(mediamtx):
    mediamtx(fail=True)
    assert media.kick_paths(["live/a"]) == 0
    assert media.media_ready() is False
    assert media.active_streams() == 0 and media.bitrate_kbps() == 0
    assert media.api_post("/v3/rtmpconns/kick/x") is False


def test_media_ready_true_when_api_answers_and_cached(mediamtx):
    fake = mediamtx()
    assert service.media_ready() is True
    fake.fail = True
    assert media.media_ready() is True  # cached for READY_TTL_S
    media.reset_cache()
    assert media.media_ready() is False


def test_stream_metrics_from_paths(mediamtx, monkeypatch, client):
    monkeypatch.setattr(media, "METRICS_TTL_S", 0)
    fake = mediamtx(paths=[
        {"name": "live/a", "ready": True, "readyTime": "2026-10-18T05:00:00.123456789Z", "bytesReceived": 1_000_000},
        {"name": "live/b", "ready": True, "readyTime": None, "bytesReceived": 10},
        {"name": "live/c", "ready": False, "bytesReceived": 99},
    ])
    assert media.active_streams() == 2
    assert media.bitrate_kbps() >= 0
    fake.paths[0]["bytesReceived"] += 125_000  # 1 Mbit more
    media._stats["bytes_at"] -= 1.0
    assert 900 <= media.bitrate_kbps() <= 1000.5
    text = client.get("/metrics").text
    assert re.search(r"^nascon_active_streams_total 2$", text, re.M)
    assert re.search(r"^nascon_stream_bitrate_kbps \S+$", text, re.M)


def test_stream_metrics_average_since_ready_on_first_look(mediamtx, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(media, "METRICS_TTL_S", 0)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    mediamtx(paths=[{"name": "live/a", "ready": True, "readyTime": since, "bytesReceived": 1_250_000}])
    assert 900 <= media.bitrate_kbps() <= 1001
    assert media._parse_time("garbage") is None
    assert media._parse_time(None) is None


def test_stats_refresh_survives_bad_payloads(mediamtx, monkeypatch):
    monkeypatch.setattr(media, "METRICS_TTL_S", 0)
    mediamtx(paths=[{"name": "live/a", "ready": True, "bytesReceived": "lots"}])
    assert media.active_streams() == 0


def test_sever_all_kicks_live_and_costream_paths(mediamtx, monkeypatch):
    mediamtx(paths=[{"name": "live/a"}, {"name": "costream/cs-1"}, {"name": "other"}],
             conns={"rtmpconns": [{"id": "r1", "path": "live/a"}, {"id": "r2", "path": "costream/cs-1"},
                                  {"id": "r3", "path": "other"}]})
    seen = []
    monkeypatch.setattr(media, "sever", lambda paths, background=True: seen.append(paths))
    service.sever_all(background=False)
    assert seen == [["live/a", "costream/cs-1"]]
    media.reset_cache()
    mediamtx(fail=True)
    seen.clear()
    service.sever_all(background=False)
    assert seen == []


def test_no_media_host_means_no_network(monkeypatch):
    monkeypatch.setattr(media, "transport", None)
    monkeypatch.setattr(config, "MEDIA_HOST", "")
    media.reset_cache()
    assert media.api_get("/v3/paths/list") is None
    assert media.api_post("/v3/rtmpconns/kick/x") is False
    assert media.media_ready() is False
    media.reset_cache()
    monkeypatch.setattr(config, "MEDIA_HOST", "media.test")
    assert media._base_url() == "http://media.test:9997"


def test_media_ready_route(client, mediamtx):
    mediamtx()
    assert client.get("/internal/media/ready").json() == {"media_ready": True}


def test_ready_paths(mediamtx):
    mediamtx(paths=[{"name": "live/a", "ready": True}, {"name": "live/b", "ready": False}])
    assert media.ready_paths() == {"live/a"}
    media.reset_cache()
    mediamtx(fail=True)
    assert media.ready_paths() == set()


def test_active_keys(fresh, severed):
    from app.core import db
    key = post(fresh, action="generate", player_id="player-alpha-1").json()["stream_key"]
    post(fresh, action="generate", player_id="player-alpha-2")
    post(fresh, action="revoke", player_id="player-alpha-2")
    with db.tx() as conn:
        assert service.active_keys(conn, ["player-alpha-1", "player-alpha-2", "player-beta-1"]) == {
            "player-alpha-1": key}
