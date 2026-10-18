import sys
import time
import types

import pytest

from app.core import db
from app.core.metrics import REQUIRED
from app.core.middleware import RateLimit

EXPECTED = {"status": "anonymized", "pii_retained": False, "tokens_wiped": True}


def _limiter(client):
    node = client.app.middleware_stack
    while node is not None and not isinstance(node, RateLimit):
        node = getattr(node, "app", None)
    return node


@pytest.fixture(autouse=True)
def clean_rate_limit(client):
    client.get("/health")
    _limiter(client)._hits.clear()
    yield
    _limiter(client)._hits.clear()


def login(client, email):
    return client.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})


def test_privacy_erasure_of_player_and_linked_account(fresh):
    token = login(fresh, "player@nascon.test").json()["token"]
    posted = fresh.post("/api/v1/chat", json={"chat_room_id": "chat-cs-session-101", "text": "hello"},
                        headers={"Authorization": f"Bearer {token}"})
    assert posted.status_code == 201
    started = time.perf_counter()
    r = fresh.delete("/audit/v1/privacy/user/player-alpha-1")
    assert time.perf_counter() - started < 1.0
    assert r.status_code == 200
    assert r.json() == {"user_id": "player-alpha-1", **EXPECTED}
    assert list(r.json()) == ["user_id", "status", "pii_retained", "tokens_wiped"]
    assert db.fetchone("SELECT name FROM players WHERE player_id = 'player-alpha-1'")["name"] == "Anonymized Player"
    account = db.fetchone("SELECT email, display_name FROM users WHERE user_id = 'user-player'")
    assert account["display_name"] == "Anonymized User" and account["email"].endswith("@erased.invalid")
    assert login(fresh, "player@nascon.test").status_code != 200
    msgs = fresh.get("/api/v1/chat/chat-cs-session-101/messages").json()["messages"]
    assert [(m["sender"], m["sender_name"], m["text"]) for m in msgs] == [("anonymized-user", "Anonymized User", "hello")]
    # idempotent
    again = fresh.delete("/audit/v1/privacy/user/player-alpha-1")
    assert again.status_code == 200 and again.json() == r.json()


def test_privacy_erasure_by_login_user_id(fresh):
    r = fresh.delete("/audit/v1/privacy/user/user-viewer")
    assert r.status_code == 200 and r.json() == {"user_id": "user-viewer", **EXPECTED}
    assert db.fetchone("SELECT display_name FROM users WHERE user_id = 'user-viewer'")["display_name"] == "Anonymized User"


@pytest.mark.parametrize("user_id", ["player-nobody", "guest_anon_tok_7721", "x" * 80, "bad id"])
def test_privacy_erasure_unknown_user_is_404(fresh, user_id):
    r = fresh.delete(f"/audit/v1/privacy/user/{user_id}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PLAYER_NOT_FOUND" and r.json()["error"]["details"] == {"user_id": user_id}


def test_privacy_erasure_blocked_for_guest_tokens(fresh):
    r = fresh.delete("/audit/v1/privacy/user/player-alpha-1", headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"


def test_privacy_erasure_wipes_guest_tokens_through_qr_module(fresh, monkeypatch):
    import app.modules.qr_guest as qr_pkg

    calls = []
    fake = types.ModuleType("app.modules.qr_guest.service")
    fake.wipe_user_tokens = lambda conn, user_id: calls.append(user_id)
    monkeypatch.setitem(sys.modules, "app.modules.qr_guest.service", fake)
    monkeypatch.setattr(qr_pkg, "service", fake, raising=False)
    assert fresh.delete("/audit/v1/privacy/user/player-alpha-1").status_code == 200
    assert calls == ["player-alpha-1", "user-player"]

    def broken(conn, user_id):
        conn.execute("SELECT 1/0")

    fake.wipe_user_tokens = broken
    r = fresh.delete("/audit/v1/privacy/user/player-alpha-2")
    assert r.status_code == 200 and r.json()["tokens_wiped"] is True
    assert db.fetchone("SELECT name FROM players WHERE player_id = 'player-alpha-2'")["name"] == "Anonymized Player"

    del fake.wipe_user_tokens
    assert fresh.delete("/audit/v1/privacy/user/player-beta-1").status_code == 200


def test_privacy_erasure_without_qr_module(fresh, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.modules.qr_guest.service", None)
    import app.modules.qr_guest as qr_pkg
    monkeypatch.delattr(qr_pkg, "service", raising=False)
    assert fresh.delete("/audit/v1/privacy/user/player-beta-2").status_code == 200


def test_metrics_families_and_dlq_depth(fresh):
    r = fresh.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    lines = r.text.splitlines()
    for name, (kind, _) in REQUIRED.items():
        assert f"# TYPE {name} {kind}" in lines
    assert "nascon_dlq_depth 0" in lines
    fresh.post("/audit/v1/moderation/test", json={"chat_room_id": "chat-m", "incoming_messages": [],
                                                  "simulate_failed_notifications": 4})
    assert "nascon_dlq_depth 4" in fresh.get("/metrics").text.splitlines()
