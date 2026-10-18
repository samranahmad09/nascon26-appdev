import hashlib
import hmac
import itertools
import json
import re
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.core import config, db, realtime
from app.main import app
from app.modules.qr_guest import exports, service

BASE = "http://localhost:8080"
KEYS = ["entity_id", "entity_type", "entity_status", "short_url", "target_route", "deep_link_uri", "scan_count",
        "export_formats", "signature"]
MERGE_KEYS = ["guest_token", "write_block_verified", "write_rejection_code", "cart_merged", "active_cart", "signature"]
GUEST = "guest_anon_tok_7721"
L, M = "var-jersey-001-L", "var-jersey-001-M"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

_ips = itertools.count(1)


def ref_sign(entity_id, payload):
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(config.DAY2_SALT.encode(), (entity_id + "|" + canon).encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def api(fresh):
    """Client with its own address so the /api/v1 rate limiter never trips across tests."""
    n = next(_ips)
    address = (f"10.77.{n // 250}.{n % 250 + 1}", 50000)

    async def from_own_address(scope, receive, send):
        await app({**scope, "client": address} if scope["type"] == "http" else scope, receive, send)

    return TestClient(from_own_address)


def qr(client, entity_id, **params):
    return client.get(f"/audit/v1/qr/{entity_id}", params=params)


def event(client, event_id, match_id, kind, payload=None):
    body = {"event_id": event_id, "match_id": match_id, "type": kind, "payload": payload or {}}
    r = client.post("/audit/v1/events", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def login(client, email="player@nascon.test"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def merge_body(token=GUEST, user="player-alpha-1", cart=None, **extra):
    return {"guest_token": token, "registered_user_id": user,
            "guest_cart": cart if cart is not None else [{"variant_id": L, "quantity": 1}], **extra}


def error_of(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    return r.json()["error"]


# ---------------------------------------------------------------------------------------------
# GET /audit/v1/qr/{entity_id}
# ---------------------------------------------------------------------------------------------

def test_seeded_match_qr_matches_contract(fresh):
    r = qr(fresh, "match-m1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert list(body) == KEYS
    assert body == {
        "entity_id": "match-m1", "entity_type": "match", "entity_status": "in_progress",
        "short_url": f"{BASE}/q/m1-live", "target_route": "/web/matches/match-m1/live",
        "deep_link_uri": "nascon://entity/match-m1", "scan_count": 1,
        "export_formats": {"svg": f"{BASE}/audit/v1/qr/match-m1/export.svg",
                           "png": f"{BASE}/audit/v1/qr/match-m1/export.png",
                           "pdf": f"{BASE}/audit/v1/qr/match-m1/export.pdf"},
        "signature": ref_sign("match-m1", {"entity_id": "match-m1", "short_url": f"{BASE}/q/m1-live",
                                           "target_route": "/web/matches/match-m1/live"}),
    }
    assert re.fullmatch(r"[0-9a-f]{64}", body["signature"])


def test_scan_count_increments_unless_inspect(fresh):
    assert qr(fresh, "match-m1", inspect="true").json()["scan_count"] == 0
    assert qr(fresh, "match-m1").json()["scan_count"] == 1
    assert qr(fresh, "match-m1").json()["scan_count"] == 2
    for _ in range(3):
        assert qr(fresh, "match-m1", inspect="true").json()["scan_count"] == 2
    assert qr(fresh, "match-m1", inspect="1").json()["scan_count"] == 2
    assert qr(fresh, "match-m1", inspect="false").json()["scan_count"] == 3
    fresh.post("/audit/v1/reset", json={})
    assert qr(fresh, "match-m1", inspect="true").json()["scan_count"] == 0


def test_seeded_player_and_club_qr(fresh):
    player = qr(fresh, "player-alpha-1").json()
    assert (player["entity_type"], player["entity_status"]) == ("player", "active")
    assert player["short_url"] == f"{BASE}/q/p-alpha-1"
    assert player["target_route"] == "/web/players/player-alpha-1"
    club = qr(fresh, "club-alpha-1", inspect="true").json()
    assert (club["entity_type"], club["short_url"]) == ("club", f"{BASE}/q/c-alpha-1")
    assert club["target_route"] == "/web/clubs/club-alpha-1"
    assert club["scan_count"] == 0
    assert club["signature"] == ref_sign("club-alpha-1", {"entity_id": "club-alpha-1",
                                                          "short_url": f"{BASE}/q/c-alpha-1",
                                                          "target_route": "/web/clubs/club-alpha-1"})


def test_unknown_entities_and_codes_are_qr_not_found(fresh):
    for entity_id in ("match-nope", "player-nope", "club-nope", "cs-session-nope", "user-player", "x" * 80):
        err = error_of(qr(fresh, entity_id), 404, "QR_NOT_FOUND")
        assert err["details"] == {"entity_id": entity_id}
    error_of(qr(fresh, "bad id"), 404, "QR_NOT_FOUND")
    err = error_of(fresh.get("/q/nope-404", follow_redirects=False), 404, "QR_NOT_FOUND")
    assert err["details"] == {"short_code": "nope-404"}
    error_of(fresh.get("/q/bad code", follow_redirects=False), 404, "QR_NOT_FOUND")


def test_status_flip_updates_route_and_signature_but_not_short_url(fresh, monkeypatch):
    monkeypatch.setattr(service, "TRUST_S", 60.0)  # only the score announcement may invalidate the cache
    service._dirty_until.pop("match-m1", None)
    before = qr(fresh, "match-m1", inspect="true").json()
    assert before["target_route"] == "/web/matches/match-m1/live"
    assert service.inspect_cached("match-m1") is not None  # trusted, served from memory now
    assert qr(fresh, "match-m1", inspect="true").json() == before
    event(fresh, "qr-flip-end", "match-m1", "match_end")
    after = qr(fresh, "match-m1", inspect="true").json()
    assert after["entity_status"] == "completed"
    assert after["short_url"] == before["short_url"] == f"{BASE}/q/m1-live"
    assert after["target_route"] == "/web/matches/match-m1/vod"
    assert after["signature"] != before["signature"]
    assert after["signature"] == ref_sign("match-m1", {"entity_id": "match-m1", "short_url": f"{BASE}/q/m1-live",
                                                       "target_route": "/web/matches/match-m1/vod"})
    r = fresh.get("/q/m1-live", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/web/matches/match-m1/vod"


def test_new_match_gets_stable_generated_code_and_preview_route(fresh):
    sport_id = fresh.get("/audit/v1/score/match-m1").json()["sport_id"]
    event(fresh, "qr-new-init", "match-qr-new", "match_init",
          {"sport_id": sport_id, "home_team_id": "club-alpha-3", "away_team_id": "club-alpha-4"})
    first = qr(fresh, "match-qr-new").json()
    assert first["entity_type"] == "match" and first["entity_status"] == "upcoming"
    assert first["target_route"] == "/web/matches/match-qr-new/preview"
    assert re.fullmatch(rf"{BASE}/q/m-[0-9a-f]{{10}}", first["short_url"])
    assert qr(fresh, "match-qr-new").json()["short_url"] == first["short_url"]
    code = first["short_url"].rsplit("/", 1)[1]
    r = fresh.get(f"/q/{code}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/web/matches/match-qr-new/preview"
    event(fresh, "qr-new-start", "match-qr-new", "match_start")
    assert qr(fresh, "match-qr-new", inspect="true").json()["target_route"] == "/web/matches/match-qr-new/live"
    assert qr(fresh, "match-qr-new", inspect="true").json()["scan_count"] == 3


def test_generated_player_and_club_codes_are_stable_across_resets(fresh):
    player = qr(fresh, "player-alpha-2", inspect="true").json()
    assert re.fullmatch(rf"{BASE}/q/p-[0-9a-f]{{10}}", player["short_url"])
    assert player["target_route"] == "/web/players/player-alpha-2"
    club = qr(fresh, "club-beta-1").json()
    assert re.fullmatch(rf"{BASE}/q/c-[0-9a-f]{{10}}", club["short_url"]) and club["scan_count"] == 1
    fresh.post("/audit/v1/reset", json={})
    again = qr(fresh, "player-alpha-2", inspect="true").json()
    assert again["short_url"] == player["short_url"] and again["scan_count"] == 0


def test_target_route_rules():
    assert service.target_route("match", "m9", "upcoming") == "/web/matches/m9/preview"
    assert service.target_route("match", "m9", "in_progress") == "/web/matches/m9/live"
    assert service.target_route("match", "m9", "completed") == "/web/matches/m9/vod"
    for status in ("postponed", "cancelled", "abandoned"):
        assert service.target_route("match", "m9", status) == f"/web/matches/m9/notice?state={status}"
    assert service.target_route("player", "player-x", "active") == "/web/players/player-x"
    assert service.target_route("club", "club-x", "active") == "/web/clubs/club-x"
    assert service.target_route("costream", "cs-session-1", "live") == "/web/costream/cs-session-1"


def test_notice_route_when_projection_reports_postponed(fresh, monkeypatch):
    qr(fresh, "match-m1", inspect="true")
    real_project = service.scoring.project
    monkeypatch.setattr(service.scoring, "project", lambda conn, mid: {**real_project(conn, mid), "status": "postponed"})
    monkeypatch.setattr(service.scoring, "match_version", lambda conn, mid: 999)
    service._match_status.clear()
    body = qr(fresh, "match-m1", inspect="true").json()
    assert body["entity_status"] == "postponed"
    assert body["target_route"] == "/web/matches/match-m1/notice?state=postponed"
    assert body["signature"] == ref_sign("match-m1", {"entity_id": "match-m1", "short_url": f"{BASE}/q/m1-live",
                                                      "target_route": "/web/matches/match-m1/notice?state=postponed"})


def test_costream_sessions_resolve_through_costream_service(fresh, monkeypatch):
    fake = types.ModuleType("app.modules.costream.service")
    sessions = {"cs-session-qr1": {"session_id": "cs-session-qr1", "status": "live"},
                "cs-session-qr2": {"session_id": "cs-session-qr2"}}
    fake.get_session = lambda conn, sid: sessions.get(sid)
    monkeypatch.setitem(sys.modules, "app.modules.costream.service", fake)
    body = qr(fresh, "cs-session-qr1").json()
    assert body["entity_type"] == "costream" and body["entity_status"] == "live"
    assert body["target_route"] == "/web/costream/cs-session-qr1"
    assert re.fullmatch(rf"{BASE}/q/cs-[0-9a-f]{{10}}", body["short_url"])
    assert qr(fresh, "cs-session-qr1", inspect="true").json()["scan_count"] == 1
    assert qr(fresh, "cs-session-qr2").json()["entity_status"] == "active"
    sessions.pop("cs-session-qr1")
    error_of(qr(fresh, "cs-session-qr1", inspect="true"), 404, "QR_NOT_FOUND")
    error_of(qr(fresh, "cs-session-qr9"), 404, "QR_NOT_FOUND")


def test_costream_module_missing_or_without_getter_means_not_found(fresh, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.modules.costream.service", types.ModuleType("app.modules.costream.service"))
    error_of(qr(fresh, "cs-session-none"), 404, "QR_NOT_FOUND")
    monkeypatch.setitem(sys.modules, "app.modules.costream.service", None)
    error_of(qr(fresh, "cs-session-none"), 404, "QR_NOT_FOUND")

    def broken(conn, sid):
        raise service.ApiError(404, "STREAM_NOT_FOUND", "gone")
    fake = types.ModuleType("app.modules.costream.service")
    fake.get_session = broken
    monkeypatch.setitem(sys.modules, "app.modules.costream.service", fake)
    error_of(qr(fresh, "cs-session-none"), 404, "QR_NOT_FOUND")


# ---------------------------------------------------------------------------------------------
# In-memory fast path and its invalidation
# ---------------------------------------------------------------------------------------------

def test_inspect_is_served_from_memory_and_announcements_invalidate(fresh, monkeypatch):
    monkeypatch.setattr(service, "TRUST_S", 30.0)
    service._dirty_until.pop("match-m1", None)
    assert service.inspect_cached("match-m1") is None  # cold after reset
    qr(fresh, "match-m1", inspect="true")
    cached = service.inspect_cached("match-m1")
    assert cached is not None and json.loads(cached)["target_route"] == "/web/matches/match-m1/live"
    calls = []
    real_tx = service.db.tx
    monkeypatch.setattr(service.db, "tx", lambda: calls.append(1) or (_ for _ in ()).throw(AssertionError("db")))
    assert qr(fresh, "match-m1", inspect="true").status_code == 200 and calls == []
    monkeypatch.setattr(service.db, "tx", real_tx)
    realtime.publish("match:match-m1", {"type": "score_update"})
    assert service.inspect_cached("match-m1") is None  # announced: revalidate until the window closes
    service._dirty_until["match-m1"] = time.monotonic() - 5
    service._match_status["match-m1"].checked_at = time.monotonic() - 60
    assert service.inspect_cached("match-m1") is None  # older than TRUST_S
    qr(fresh, "match-m1", inspect="true")
    assert service.inspect_cached("match-m1") is not None
    assert service.inspect_cached("player-alpha-1") is None
    qr(fresh, "player-alpha-1", inspect="true")
    assert json.loads(service.inspect_cached("player-alpha-1"))["entity_status"] == "active"


def test_stale_check_never_overwrites_newer_one(fresh):
    qr(fresh, "match-m1", inspect="true")
    entry = service._match_status["match-m1"]
    entry.checked_at = time.monotonic() + 60
    service._dirty_until["match-m1"] = time.monotonic() + 120  # force the database path
    assert qr(fresh, "match-m1", inspect="true").json()["entity_status"] == "in_progress"
    assert service._match_status["match-m1"] is entry


def test_device_family_from_headers():
    assert service.device_family({"user-agent": IPHONE}) == "mobile"
    assert service.device_family({"user-agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) Mobile"}) == "mobile"
    assert service.device_family({"user-agent": DESKTOP}) == "desktop"
    assert service.device_family({"user-agent": DESKTOP, "sec-ch-ua-mobile": "?1"}) == "mobile"
    assert service.device_family({}) == "desktop"


# ---------------------------------------------------------------------------------------------
# GET /q/{short_code}, telemetry and metrics
# ---------------------------------------------------------------------------------------------

def test_short_link_redirects_and_counts_privacy_safe_scans(fresh, api):
    r = fresh.get("/q/m1-live", headers={"User-Agent": IPHONE}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/web/matches/match-m1/live"
    assert r.headers["cache-control"] == "no-store"
    fresh.get("/q/m1-live", headers={"User-Agent": DESKTOP}, follow_redirects=False)
    fresh.get("/q/p-alpha-1", headers={"User-Agent": IPHONE}, follow_redirects=False)
    assert qr(fresh, "match-m1", inspect="true").json()["scan_count"] == 2
    stats = api.get("/api/v1/qr/match-m1/analytics").json()
    assert stats["scan_count"] == 2 and stats["device_families"] == {"desktop": 1, "mobile": 1}
    assert stats["stored_fields"] == ["entity_id", "bucket", "device_family", "scans"]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:00:00Z", b["bucket"]) for b in stats["buckets"])
    assert "iPhone" not in json.dumps(stats)
    columns = {r["column_name"] for r in db.fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name IN ('qg_scan_stats', 'qg_qr_mappings')")}
    assert not columns & {"ip", "ip_address", "user_agent", "cookie", "fingerprint"}
    error_of(api.get("/api/v1/qr/match-nope/analytics"), 404, "QR_NOT_FOUND")
    error_of(api.get("/api/v1/qr/bad id/analytics"), 404, "QR_NOT_FOUND")
    metric = [line for line in fresh.get("/metrics").text.splitlines() if line.startswith("nascon_qr_scans_total ")]
    assert metric == ["nascon_qr_scans_total 3"]


def test_short_link_follows_live_state_after_memory_is_cold(fresh):
    fresh.get("/q/c-alpha-1", follow_redirects=False)
    service._by_code.clear()
    service._by_entity.clear()
    r = fresh.get("/q/c-alpha-1", follow_redirects=False)
    assert r.headers["location"] == "/web/clubs/club-alpha-1"
    assert qr(fresh, "club-alpha-1", inspect="true").json()["scan_count"] == 2


def test_browser_follows_short_link_to_web_route(fresh):
    r = fresh.get("/q/m1-live")
    assert r.history and r.history[0].status_code == 302
    assert r.url.path == "/web/matches/match-m1/live"


# ---------------------------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------------------------

def test_exports_svg_png_pdf(fresh):
    svg = fresh.get("/audit/v1/qr/match-m1/export.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.content.startswith(b"<svg") or svg.content.startswith(b"<?xml")
    assert f"{BASE}/q/m1-live".encode() in svg.content
    png = fresh.get("/audit/v1/qr/match-m1/export.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    pdf = fresh.get("/audit/v1/qr/match-m1/export.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert b"match-m1" in pdf.content and f"{BASE}/q/m1-live".encode() in pdf.content
    assert fresh.get("/audit/v1/qr/match-m1/export.pdf").content == pdf.content  # cached
    assert qr(fresh, "match-m1", inspect="true").json()["scan_count"] == 0  # exports are not scans


def test_pdf_poster_text_is_extractable(fresh):
    from pypdf import PdfReader
    import io
    content = fresh.get("/audit/v1/qr/player-alpha-1/export.pdf").content
    text = PdfReader(io.BytesIO(content)).pages[0].extract_text()
    assert "player-alpha-1" in text and f"{BASE}/q/p-alpha-1" in text


def test_exports_for_generated_entities_and_errors(fresh):
    r = fresh.get("/audit/v1/qr/club-alpha-2/export.svg")
    assert r.status_code == 200
    code = qr(fresh, "club-alpha-2", inspect="true").json()["short_url"]
    assert code.encode() in r.content
    err = error_of(fresh.get("/audit/v1/qr/match-m1/export.gif"), 400, "VALIDATION_ERROR")
    assert err["details"] == {"fields": ["fmt"]}
    error_of(fresh.get("/audit/v1/qr/match-nope/export.png"), 404, "QR_NOT_FOUND")
    error_of(fresh.get("/audit/v1/qr/bad id/export.png"), 404, "QR_NOT_FOUND")


def test_export_renderers_directly():
    assert exports.render("png", f"{BASE}/q/x", "x", "match")[:4] == b"\x89PNG"
    assert exports.render("svg", f"{BASE}/q/x", "x", "club").startswith(b"<svg")
    assert exports.render("pdf", f"{BASE}/q/x", "x", "unknown-kind").startswith(b"%PDF-")


# ---------------------------------------------------------------------------------------------
# Guest token and guest cart APIs
# ---------------------------------------------------------------------------------------------

def test_issue_guest_token(api):
    r = api.post("/api/v1/guest/token")
    assert r.status_code == 201
    body = r.json()
    assert list(body) == ["guest_token", "expires_at", "scope"]
    assert re.fullmatch(r"guest_anon_tok_[0-9a-f]{16}", body["guest_token"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", body["expires_at"])
    assert body["scope"] == ["stream:read", "score:subscribe", "cart:stage"]
    headers = {"Authorization": f"Bearer {body['guest_token']}"}
    assert api.get("/api/v1/guest/cart", headers=headers).json() == {
        "guest_token": body["guest_token"], "items": [], "count": 0}
    error_of(api.post("/api/v1/chat", json={}, headers=headers), 403, "GUEST_WRITE_FORBIDDEN")


def test_guest_cart_staging(api):
    headers = {"Authorization": f"Bearer {GUEST}"}
    r = api.post("/api/v1/guest/cart/items", json={"variant_id": L, "quantity": 1}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"guest_token": GUEST, "items": [{"variant_id": L, "quantity": 1, "unit_price_minor": 450000}],
                        "count": 1}
    r = api.post("/api/v1/guest/cart/items", json={"variant_id": L, "quantity": 2}, headers=headers)
    assert r.json()["count"] == 3 and r.json()["items"][0]["quantity"] == 3
    r = api.post("/api/v1/guest/cart/items", json={"variant_id": M, "quantity": 1}, headers=headers)
    assert [i["variant_id"] for i in r.json()["items"]] == [L, M] and r.json()["count"] == 4
    err = error_of(api.post("/api/v1/guest/cart/items", json={"variant_id": M, "quantity": 1}, headers=headers),
                   409, "OUT_OF_STOCK")
    assert err["details"] == {"variant_id": M}
    error_of(api.post("/api/v1/guest/cart/items", json={"variant_id": "var-nope", "quantity": 1}, headers=headers),
             404, "VARIANT_NOT_FOUND")
    err = error_of(api.post("/api/v1/guest/cart/items", json={"variant_id": L, "quantity": 0}, headers=headers),
                   400, "VALIDATION_ERROR")
    assert err["details"] == {"fields": ["quantity"]}
    error_of(api.post("/api/v1/guest/cart/items", content=b"{oops", headers=headers), 400, "VALIDATION_ERROR")
    assert api.get("/api/v1/guest/cart", headers=headers).json()["count"] == 4


def test_guest_cart_requires_a_live_guest_token(api):
    error_of(api.get("/api/v1/guest/cart"), 401, "UNAUTHENTICATED")
    error_of(api.post("/api/v1/guest/cart/items", json={"variant_id": L, "quantity": 1}), 401, "UNAUTHENTICATED")
    error_of(api.get("/api/v1/guest/cart", headers={"Authorization": "Bearer guest_anon_tok_unknown"}),
             401, "UNAUTHENTICATED")
    error_of(api.get("/api/v1/guest/cart", headers={"Authorization": "Bearer guest_" + "x" * 300}),
             401, "UNAUTHENTICATED")
    token = api.post("/api/v1/guest/token").json()["guest_token"]
    db.execute("UPDATE qg_guest_tokens SET expires_at = '2000-01-01T00:00:00Z' WHERE guest_token = %s", (token,))
    error_of(api.get("/api/v1/guest/cart", headers={"Authorization": f"Bearer {token}"}), 401, "UNAUTHENTICATED")


# ---------------------------------------------------------------------------------------------
# Member carts and the web merge flow
# ---------------------------------------------------------------------------------------------

def test_member_cart_starts_empty_and_requires_member_token(api):
    member = login(api)
    assert api.get("/api/v1/cart", headers=member).json() == {
        "user_id": "player-alpha-1", "currency": "PKR", "total_items": 0, "subtotal_minor": 0, "items": []}
    viewer = api.get("/api/v1/cart", headers=login(api, "viewer@nascon.test")).json()
    assert viewer["user_id"] == "user-viewer" and viewer["items"] == []
    error_of(api.get("/api/v1/cart"), 401, "UNAUTHENTICATED")
    error_of(api.get("/api/v1/cart", headers={"Authorization": f"Bearer {GUEST}"}), 401, "UNAUTHENTICATED")


def test_web_merge_flow(api):
    guest = {"Authorization": f"Bearer {GUEST}"}
    api.post("/api/v1/guest/cart/items", json={"variant_id": L, "quantity": 2}, headers=guest)
    member = login(api)
    r = api.post("/api/v1/guest/merge", json={"guest_token": GUEST}, headers=member)
    assert r.status_code == 200
    expected = {"user_id": "player-alpha-1", "currency": "PKR", "total_items": 2, "subtotal_minor": 900000,
                "items": [{"variant_id": L, "quantity": 2, "unit_price_minor": 450000}]}
    assert r.json() == expected
    assert api.post("/api/v1/guest/merge", json={"guest_token": GUEST}, headers=member).json() == expected
    assert api.get("/api/v1/cart", headers=member).json() == expected
    assert api.get("/api/v1/guest/cart", headers=guest).json()["count"] == 0
    error_of(api.post("/api/v1/guest/cart/items", json={"variant_id": L, "quantity": 1}, headers=guest),
             409, "GUEST_TOKEN_ALREADY_MERGED")
    other = login(api, "viewer@nascon.test")
    error_of(api.post("/api/v1/guest/merge", json={"guest_token": GUEST}, headers=other),
             409, "GUEST_TOKEN_ALREADY_MERGED")
    fresh_token = api.post("/api/v1/guest/token").json()["guest_token"]
    r = api.post("/api/v1/guest/merge", json={"guest_token": fresh_token}, headers=other)
    assert r.status_code == 200 and r.json()["items"] == []


def test_web_merge_errors(api):
    member = login(api)
    error_of(api.post("/api/v1/guest/merge", json={"guest_token": GUEST}), 401, "UNAUTHENTICATED")
    err = error_of(api.post("/api/v1/guest/merge", json={"guest_token": GUEST}, headers={"Authorization": f"Bearer {GUEST}"}),
                   403, "GUEST_WRITE_FORBIDDEN")
    assert err["details"] == {"guest_token": GUEST}
    err = error_of(api.post("/api/v1/guest/merge", json={"guest_token": "member_tok"}, headers=member),
                   400, "VALIDATION_ERROR")
    assert err["details"] == {"fields": ["guest_token"]}
    error_of(api.post("/api/v1/guest/merge", json={"guest_token": "guest_never_issued"}, headers=member),
             404, "GUEST_TOKEN_NOT_FOUND")
    # stock moved since staging: merge refuses and leaves everything as it was
    api.post("/api/v1/guest/cart/items", json={"variant_id": M, "quantity": 1}, headers={"Authorization": f"Bearer {GUEST}"})
    db.execute("UPDATE st_variants SET stock = 0 WHERE variant_id = %s", (M,))
    err = error_of(api.post("/api/v1/guest/merge", json={"guest_token": GUEST}, headers=member), 409, "OUT_OF_STOCK")
    assert err["details"] == {"variant_id": M}
    assert api.get("/api/v1/guest/cart", headers={"Authorization": f"Bearer {GUEST}"}).json()["count"] == 1


# ---------------------------------------------------------------------------------------------
# POST /audit/v1/guest/cart-merge
# ---------------------------------------------------------------------------------------------

def expected_merge(token, user, total, items, subtotal, verified=True, code=403):
    return {"guest_token": token, "write_block_verified": verified, "write_rejection_code": code, "cart_merged": True,
            "active_cart": {"user_id": user, "currency": "PKR", "total_items": total, "subtotal_minor": subtotal,
                            "items": items},
            "signature": ref_sign(token, {"cart_merged": True, "guest_token": token, "registered_user_id": user,
                                          "total_items": total, "write_rejection_code": code})}


def test_cart_merge_example_and_idempotent_replay(fresh, api):
    body = merge_body(test_write_block=True)
    first = fresh.post("/audit/v1/guest/cart-merge", json=body)
    assert first.status_code == 200, first.text
    assert list(first.json()) == MERGE_KEYS
    assert list(first.json()["active_cart"]) == ["user_id", "currency", "total_items", "subtotal_minor", "items"]
    want = expected_merge(GUEST, "player-alpha-1", 1, [{"variant_id": L, "quantity": 1, "unit_price_minor": 450000}],
                          450000)
    assert first.json() == want
    replay = fresh.post("/audit/v1/guest/cart-merge", json=body)
    assert replay.status_code == 200 and replay.json() == want
    assert api.get("/api/v1/cart", headers=login(api)).json()["total_items"] == 1
    stock = fresh.get(f"/audit/v1/inventory/{L}")
    if stock.status_code == 200:
        assert stock.json()["reserved"] == 0  # merges never reserve inventory
    err = error_of(fresh.post("/audit/v1/guest/cart-merge", json=merge_body(user="player-alpha-2")),
                   409, "GUEST_TOKEN_ALREADY_MERGED")
    assert err["details"] == {"guest_token": GUEST}
    error_of(fresh.post("/audit/v1/guest/cart-merge", json=merge_body(cart=[{"variant_id": L, "quantity": 2}])),
             409, "GUEST_TOKEN_ALREADY_MERGED")
    reordered = merge_body(cart=[{"variant_id": L, "quantity": 1}], test_write_block=False)
    assert fresh.post("/audit/v1/guest/cart-merge", json=reordered).status_code == 200


def test_cart_merge_defaults_and_unissued_tokens(fresh):
    r = fresh.post("/audit/v1/guest/cart-merge",
                   json=merge_body(token="guest_probe_42", user="user-viewer",
                                   cart=[{"variant_id": M, "quantity": 1}, {"variant_id": L, "quantity": 2}]))
    assert r.status_code == 200, r.text
    assert r.json() == expected_merge(
        "guest_probe_42", "user-viewer", 3,
        [{"variant_id": M, "quantity": 1, "unit_price_minor": 450000},
         {"variant_id": L, "quantity": 2, "unit_price_minor": 450000}], 1350000)


def test_cart_merge_without_write_block_probe(fresh):
    r = fresh.post("/audit/v1/guest/cart-merge", json=merge_body(test_write_block=False))
    assert r.status_code == 200
    assert r.json() == expected_merge(GUEST, "player-alpha-1", 1,
                                      [{"variant_id": L, "quantity": 1, "unit_price_minor": 450000}], 450000,
                                      verified=False, code=None)


def test_cart_merge_sums_into_existing_cart_and_duplicate_lines(fresh):
    fresh.post("/audit/v1/guest/cart-merge", json=merge_body(token="guest_a"))
    r = fresh.post("/audit/v1/guest/cart-merge",
                   json=merge_body(token="guest_b", cart=[{"variant_id": L, "quantity": 1}, {"variant_id": L, "quantity": 2}]))
    assert r.json()["active_cart"]["items"] == [{"variant_id": L, "quantity": 4, "unit_price_minor": 450000}]
    assert r.json()["active_cart"]["total_items"] == 4
    same_cart_other_alias = fresh.post("/audit/v1/guest/cart-merge", json=merge_body(token="guest_c", user="user-player"))
    assert same_cart_other_alias.json()["active_cart"]["user_id"] == "user-player"
    assert same_cart_other_alias.json()["active_cart"]["total_items"] == 5
    err = error_of(fresh.post("/audit/v1/guest/cart-merge", json=merge_body(token="guest_d")), 409, "OUT_OF_STOCK")
    assert err["details"] == {"variant_id": L}


def test_cart_merge_out_of_stock_leaves_no_trace_and_replay_wins_over_stock(fresh, api):
    err = error_of(fresh.post("/audit/v1/guest/cart-merge", json=merge_body(cart=[{"variant_id": M, "quantity": 2}])),
                   409, "OUT_OF_STOCK")
    assert err["details"] == {"variant_id": M}
    assert api.get("/api/v1/cart", headers=login(api)).json()["items"] == []
    one_m = merge_body(cart=[{"variant_id": M, "quantity": 1}])
    assert fresh.post("/audit/v1/guest/cart-merge", json=one_m).status_code == 200  # same token, now fits
    replay = fresh.post("/audit/v1/guest/cart-merge", json=one_m)
    assert replay.status_code == 200 and replay.json()["active_cart"]["total_items"] == 1
    error_of(fresh.post("/audit/v1/guest/cart-merge", json=merge_body(token="guest_other", cart=[{"variant_id": M, "quantity": 1}])),
             409, "OUT_OF_STOCK")
    huge = merge_body(token="guest_huge", cart=[{"variant_id": L, "quantity": 10 ** 30}])
    error_of(fresh.post("/audit/v1/guest/cart-merge", json=huge), 409, "OUT_OF_STOCK")


@pytest.mark.parametrize("body, fields", [
    ({}, ["guest_token", "registered_user_id", "guest_cart"]),
    (merge_body(token="anon_tok_1"), ["guest_token"]),
    (merge_body(token=""), ["guest_token"]),
    (merge_body(token=7721), ["guest_token"]),
    (merge_body(user=""), ["registered_user_id"]),
    (merge_body(user=5), ["registered_user_id"]),
    (merge_body(cart=[]), ["guest_cart"]),
    (merge_body(cart=[{"variant_id": L, "quantity": 1}] * 11), ["guest_cart"]),
    (merge_body(cart=[{"variant_id": L, "quantity": 0}]), ["guest_cart"]),
    (merge_body(cart=[{"variant_id": L, "quantity": "1"}]), ["guest_cart"]),
    (merge_body(cart=[{"variant_id": L, "quantity": True}]), ["guest_cart"]),
    (merge_body(cart=[{"variant_id": L, "quantity": 1.0}]), ["guest_cart"]),
    (merge_body(cart=[{"variant_id": "", "quantity": 1}]), ["guest_cart"]),
    (merge_body(cart=[{"quantity": 1}]), ["guest_cart"]),
    (merge_body(cart=["var-jersey-001-L"]), ["guest_cart"]),
    (merge_body(cart={"variant_id": L, "quantity": 1}), ["guest_cart"]),
    (merge_body(test_write_block="yes"), ["test_write_block"]),
])
def test_cart_merge_schema_errors(fresh, body, fields):
    err = error_of(fresh.post("/audit/v1/guest/cart-merge", json=body), 400, "VALIDATION_ERROR")
    assert err["details"] == {"fields": fields}


def test_cart_merge_malformed_json_and_non_object(fresh):
    error_of(fresh.post("/audit/v1/guest/cart-merge", content=b"{not json"), 400, "VALIDATION_ERROR")
    err = error_of(fresh.post("/audit/v1/guest/cart-merge", json=[1, 2]), 400, "VALIDATION_ERROR")
    assert err["details"] == {"fields": ["body"]}
    error_of(fresh.post("/audit/v1/guest/cart-merge"), 400, "VALIDATION_ERROR")


def test_cart_merge_not_found_order(fresh):
    err = error_of(fresh.post("/audit/v1/guest/cart-merge",
                              json=merge_body(user="player-ghost", cart=[{"variant_id": "var-ghost", "quantity": 1}])),
                   404, "PLAYER_NOT_FOUND")
    assert err["details"] == {"registered_user_id": "player-ghost"}
    err = error_of(fresh.post("/audit/v1/guest/cart-merge",
                              json=merge_body(cart=[{"variant_id": L, "quantity": 1}, {"variant_id": "var-ghost", "quantity": 1}])),
                   404, "VARIANT_NOT_FOUND")
    assert err["details"] == {"variant_id": "var-ghost"}
    # a rejected request does not consume the token
    assert fresh.post("/audit/v1/guest/cart-merge", json=merge_body()).status_code == 200


def test_cart_merge_with_guest_bearer_on_the_request_itself(fresh):
    r = fresh.post("/audit/v1/guest/cart-merge", json=merge_body(), headers={"Authorization": f"Bearer {GUEST}"})
    assert r.status_code == 200 and r.json()["write_block_verified"] is True


def test_concurrent_identical_merges_count_once(fresh):
    req = service.parse_cart_merge(merge_body(cart=[{"variant_id": L, "quantity": 2}]))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.cart_merge(req, True, 403), range(4)))
    assert {r["active_cart"]["total_items"] for r in results} == {2}
    assert len({r["signature"] for r in results}) == 1
    row = db.fetchone("SELECT quantity FROM qg_member_cart_items WHERE user_id = 'player-alpha-1' AND variant_id = %s", (L,))
    assert row["quantity"] == 2


def test_write_block_probe_uses_real_guard():
    import asyncio
    from app.modules.qr_guest.routes import probe_write_block
    assert asyncio.run(probe_write_block("guest_anon_tok_7721")) == (True, 403)
    assert asyncio.run(probe_write_block("guest_ünïcode")) == (True, 403)


# ---------------------------------------------------------------------------------------------
# Privacy erasure hook
# ---------------------------------------------------------------------------------------------

def test_wipe_user_tokens_unlinks_tokens_and_carts(fresh, api):
    fresh.post("/audit/v1/guest/cart-merge", json=merge_body())
    fresh.post("/audit/v1/guest/cart-merge", json=merge_body(token="guest_second", cart=[{"variant_id": M, "quantity": 1}]))
    fresh.post("/audit/v1/guest/cart-merge", json=merge_body(token="guest_viewer", user="user-viewer"))
    with db.tx() as conn:
        assert service.wipe_user_tokens(conn, "user-player") == 3  # two tokens + one cart, via the alias
    rows = db.fetchall("SELECT guest_token, merged_user_id, revoked FROM qg_guest_tokens WHERE guest_token IN "
                       "('guest_anon_tok_7721', 'guest_second') ORDER BY guest_token")
    assert all(r["merged_user_id"] is None and r["revoked"] for r in rows) and len(rows) == 2
    assert api.get("/api/v1/cart", headers=login(api)).json()["items"] == []
    assert api.get("/api/v1/cart", headers=login(api, "viewer@nascon.test")).json()["total_items"] == 1
    error_of(fresh.post("/audit/v1/guest/cart-merge", json=merge_body()), 409, "GUEST_TOKEN_ALREADY_MERGED")
    error_of(api.get("/api/v1/guest/cart", headers={"Authorization": f"Bearer {GUEST}"}), 401, "UNAUTHENTICATED")
    with db.tx() as conn:
        assert service.wipe_user_tokens(conn, "player-nobody") == 0
