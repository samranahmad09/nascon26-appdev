import hashlib
import hmac
import json

from app.core.metrics import REQUIRED, register, render
from app.core.signing import sign_day2


def _reference(salt, entity_id, payload):
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(salt.encode(), (entity_id + "|" + canon).encode(), hashlib.sha256).hexdigest()


def test_day2_signature_matches_appendix_a_reference():
    payload = {"action": "rotate", "player_id": "player-alpha-1", "status": "active",
               "stream_key": "sk_live_9f83a1b2c3d4e5f6"}
    got = sign_day2("sk_live_9f83a1b2c3d4e5f6", payload, salt="T01D27ad7ffff12e")
    assert got == _reference("T01D27ad7ffff12e", "sk_live_9f83a1b2c3d4e5f6", payload)
    assert len(got) == 64 and got == got.lower()
    short = {"action": "rotate", "player_id": "player-alpha-1"}
    assert sign_day2("sk_live_101", short, salt="T01D27ad7ffff12e") == _reference("T01D27ad7ffff12e", "sk_live_101", short)


def test_guest_token_writes_are_403_before_validation(fresh):
    headers = {"Authorization": "Bearer guest_anon_tok_7721"}
    for path in ("/api/v1/chat", "/audit/v1/checkout", "/audit/v1/events"):
        r = fresh.post(path, json={}, headers=headers)
        assert r.status_code == 403, path
        assert r.json()["error"] == {"code": "GUEST_WRITE_FORBIDDEN",
                                     "message": "Anonymous guest tokens cannot perform write mutations.",
                                     "details": {"guest_token": "guest_anon_tok_7721"}}
    # reads stay open to guests, and member tokens are unaffected
    assert fresh.get("/health", headers=headers).status_code == 200


def test_metrics_exposition(fresh):
    r = fresh.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    for name in REQUIRED:
        assert f"# TYPE {name} " in r.text
        assert any(line.startswith(name + " ") or line.startswith(name + "{") for line in r.text.splitlines())


def test_metrics_registry_labels_and_failures():
    register("nascon_test_gauge", lambda: [({"room": "a"}, 2), ({"room": "b"}, 3.5)], help_text="test")
    register("nascon_test_broken", lambda: 1 / 0)
    text = render()
    assert 'nascon_test_gauge{room="a"} 2' in text and 'nascon_test_gauge{room="b"} 3.5' in text
    assert "nascon_test_broken 0" in text


def test_day2_baseline_seed(fresh):
    r = fresh.get("/audit/v1/score/match-m1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress" and body["league_id"] == "league-alpha-1"
    assert (body["home"]["team_id"], body["away"]["team_id"]) == ("club-alpha-1", "club-alpha-2")
    assert (body["home"]["score"], body["away"]["score"]) == (1, 0)
    token = fresh.post("/api/v1/auth/login", json={"email": "admin@nascon.test", "password": "Nascon@26"}).json()["token"]
    roster = fresh.get("/api/v1/clubs/club-beta-1", headers={"Authorization": f"Bearer {token}"}).json()["roster"]
    assert any(p["player_id"] == "player-beta-2" for p in roster)


def test_score_event_reaches_match_websocket(fresh):
    with fresh.websocket_connect("/ws/matches/match-m1") as ws:
        r = fresh.post("/audit/v1/events", json={"event_id": "ws-e1", "match_id": "match-m1", "type": "score",
                                                 "payload": {"team_id": "club-alpha-2",
                                                             "event_type": fresh.get("/audit/v1/score/match-m1").json()["events"][2]["payload"]["event_type"]}})
        assert r.status_code == 201, r.text
        msg = ws.receive_json()
        assert msg["type"] == "score_update" and msg["match"]["away"]["score"] == 1


def test_health_reports_failed_dependencies(fresh, monkeypatch):
    from app.core import config, routes
    monkeypatch.setattr(config, "MEDIA_HOST", "127.0.0.1")  # nothing listens on 8888 inside the test container
    r = fresh.get("/health")
    assert r.status_code == 503 and r.json()["error"]["details"]["failed"] == ["media"]
    monkeypatch.setattr(config, "MEDIA_HOST", "")
    monkeypatch.setattr(routes, "redis_ok", lambda: False)
    assert fresh.get("/health").json()["error"]["details"]["failed"] == ["redis"]
