import json

import httpx
import pytest

from app.modules.costream import composite
from app.modules.streaming import media

URL = "/audit/v1/costream/session"
ADD = "/v3/config/paths/add/costream/"
DELETE = "/v3/config/paths/delete/costream/"


class ConfigAPI:
    """Records MediaMTX control-API calls; lists `paths` as configured path names."""

    def __init__(self):
        self.calls = []
        self.paths = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path == "/v3/config/paths/list":
            return httpx.Response(200, json={"items": [{"name": n} for n in self.paths]})
        return httpx.Response(200)

    def touched(self):
        return [(m, p) for m, p, _ in self.calls if p.startswith(("/v3/config/paths/add", "/v3/config/paths/delete"))]


@pytest.fixture
def mtx(monkeypatch):
    composite.drain()
    api = ConfigAPI()
    monkeypatch.setattr(media, "transport", httpx.MockTransport(api))
    monkeypatch.setattr(media, "sever", lambda paths, background=True: None)
    composite._running.clear()
    composite._healed.clear()
    yield api
    composite.drain()
    composite._running.clear()
    composite._healed.clear()


def test_start_adds_a_run_on_init_path(mtx):
    composite.start("cs-session-101")
    assert mtx.calls == [("POST", ADD + "cs-session-101",
                          {"runOnInit": "sh /costream.sh cs-session-101", "runOnInitRestart": True})]


def test_start_ignores_ids_unsafe_for_a_command_line(mtx):
    composite.start("cs bad; rm -rf /")
    composite.start("x" * 65)
    assert mtx.calls == []


def test_cap_evicts_the_least_recently_started(mtx, monkeypatch):
    monkeypatch.setattr(composite, "MAX_COMPOSITES", 2)
    for sid in ("cs-a", "cs-b", "cs-a", "cs-c"):
        composite.start(sid)
    assert mtx.touched() == [("POST", ADD + "cs-a"), ("POST", ADD + "cs-b"), ("POST", ADD + "cs-a"),
                             ("DELETE", DELETE + "cs-b"), ("POST", ADD + "cs-c")]
    assert list(composite._running) == ["cs-a", "cs-c"]


def test_stop_and_stop_all_delete_paths(mtx):
    composite.start("cs-a")
    composite.stop("cs-a")
    mtx.paths = ["costream/cs-x", "live/sk_live_1", "costream/cs-y"]
    composite.stop_all()
    assert mtx.touched() == [("POST", ADD + "cs-a"), ("DELETE", DELETE + "cs-a"),
                             ("DELETE", DELETE + "cs-x"), ("DELETE", DELETE + "cs-y")]
    assert not composite._running


def test_jobs_run_in_order_and_survive_failures(mtx):
    seen = []

    def boom():
        raise RuntimeError("media exploded")

    composite.submit(seen.append, 1)
    composite.submit(boom)
    composite.submit(seen.append, 2)
    composite.drain()
    assert seen == [1, 2]


def test_heal_is_rate_limited(mtx, monkeypatch):
    composite.heal("cs-a")
    composite.heal("cs-a")
    composite.drain()
    assert mtx.touched() == [("POST", ADD + "cs-a")]
    monkeypatch.setattr(composite, "HEAL_INTERVAL_S", 0)
    composite.heal("cs-a")
    composite.drain()
    assert mtx.touched() == [("POST", ADD + "cs-a")] * 2


def test_session_lifecycle_drives_the_composite(fresh, mtx):
    body = {"session_id": "cs-life", "host_player_id": "player-alpha-1",
            "participants": ["player-alpha-1", "player-alpha-2"], "layout": "grid_2"}
    assert fresh.post(URL, json=body).status_code == 201
    assert fresh.post(URL, json=body).status_code == 200  # replay changes nothing
    composite.drain()
    assert mtx.touched() == [("POST", ADD + "cs-life")]
    r = fresh.post(URL, json={**body, "participants": ["player-alpha-1"], "layout": "solo",
                              "simulate_host_drop": True})
    assert r.json()["status"] == "terminated"
    composite.drain()
    assert mtx.touched()[-1] == ("DELETE", DELETE + "cs-life")
    mtx.paths = ["costream/cs-life"]
    assert fresh.post("/audit/v1/reset", json={}).status_code == 200
    composite.drain()
    assert ("GET", "/v3/config/paths/list", None) in mtx.calls


def test_auth_hook_heals_an_active_sessions_composite(fresh, mtx):
    body = {"session_id": "cs-heal", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"]}
    fresh.post(URL, json=body)
    composite.drain()
    mtx.calls.clear()
    composite._healed.clear()
    r = fresh.post("/internal/media/auth", json={"action": "read", "path": "costream/cs-heal", "protocol": "hls"})
    assert r.status_code == 200
    composite.drain()
    assert mtx.touched() == [("POST", ADD + "cs-heal")]
    mtx.calls.clear()
    assert fresh.post("/internal/media/auth", json={"action": "read", "path": "costream/cs-unknown"}).status_code == 200
    composite.drain()
    assert mtx.touched() == []
