"""Guest Web Player (FR-4.2, 6.2.2, NFR-8): pages, static assets and the read-only /web/api helpers."""
import json
import re
from pathlib import Path

import httpx
import pytest

from app.core import db
from app.modules.store import service as store
from app.modules.web import routes
from app.modules.web import service as web

TESTIDS = {"guest-video-player", "live-score-banner", "pinned-merch-card", "add-to-guest-cart-btn", "guest-cart-count",
           "chat-message-list", "chat-input-field", "chat-send-btn", "progressive-auth-modal"}
CSS = (Path(web.__file__).with_name("static") / "player.css").read_text(encoding="utf-8")


def found_testids(page: str) -> list[str]:
    return re.findall(r'data-testid="([^"]+)"', page)


def element(page: str, testid: str) -> str:
    m = re.search(r'<[a-z]+[^>]*data-testid="%s"[^>]*>' % re.escape(testid), page, re.S)
    assert m, testid
    return m.group(0)


def text_of(page: str, testid: str) -> str:
    m = re.search(r'data-testid="%s"[^>]*>([^<]*)<' % re.escape(testid), page, re.S)
    assert m, testid
    return m.group(1)


def boot(page: str) -> dict:
    m = re.search(r'<script type="application/json" id="boot">(.*?)</script>', page, re.S)
    assert m
    return json.loads(m.group(1))


def score_event(client, match_id: str, team_id: str, event_id: str):
    """Repeats the kind of event that scored for this match, read from its own log (no event name in the test)."""
    score = client.get(f"/audit/v1/score/{match_id}").json()
    seed = next(e for e in score["events"] if "event_type" in e["payload"] and "team_id" in e["payload"])
    body = {"event_id": event_id, "match_id": match_id, "type": seed["type"],
            "payload": {**seed["payload"], "team_id": team_id}}
    return client.post("/audit/v1/events", json=body)


# ---------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------

def test_live_page_exposes_exactly_the_nine_testids(fresh):
    r = fresh.get("/web/matches/match-m1/live")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    ids = found_testids(r.text)
    assert sorted(ids) == sorted(TESTIDS)


def test_live_page_first_paint_has_score_teams_and_pinned_jersey(fresh):
    page = fresh.get("/web/matches/match-m1/live").text
    assert text_of(page, "live-score-banner") == "1 - 0"
    assert text_of(page, "guest-cart-count") == "0"
    assert "Alpha United" in page and "Alpha Rovers" in page
    card = element(page, "pinned-merch-card")
    assert 'data-product-id="prod-jersey-001"' in card and 'data-variant-id="var-jersey-001-L"' in card
    assert "prod-jersey-001 / var-jersey-001-L" in page
    assert "PKR 4,500" in page
    b = boot(page)
    assert b["match_id"] == "match-m1" and b["chat_room_id"] == "chat-match-m1" and b["kind"] == "live"
    assert b["product"]["cart_variant_id"] == "var-jersey-001-L"


def test_live_page_has_no_login_wall_or_install_prompt(fresh):
    page = fresh.get("/web/matches/match-m1/live").text
    modal = element(page, "progressive-auth-modal")
    assert " hidden" in modal and 'role="dialog"' in modal and 'aria-modal="true"' in modal
    assert 'rel="manifest"' not in page and "beforeinstallprompt" not in page


def test_elements_use_the_contract_tags(fresh):
    page = fresh.get("/web/matches/match-m1/live").text
    tags = {t: element(page, t).split()[0][1:] for t in TESTIDS}
    assert tags["guest-video-player"] == "video"
    assert tags["add-to-guest-cart-btn"] == "button" and tags["chat-send-btn"] == "button"
    assert tags["chat-input-field"] == "input" and tags["guest-cart-count"] == "span"
    assert tags["chat-message-list"] in ("ul", "div")
    assert tags["live-score-banner"] == "div" and tags["pinned-merch-card"] == "div"
    assert tags["progressive-auth-modal"] == "div"


def test_page_loads_only_local_scripts(fresh):
    page = fresh.get("/web/matches/match-m1/live").text
    srcs = re.findall(r'<script[^>]*src="([^"]+)"', page)
    assert srcs and all(s.startswith("/web/static/") for s in srcs)
    assert "/web/static/hls.min.js?v=" in page  # loaded on demand from the boot config


@pytest.mark.parametrize("kind", ["preview", "vod"])
def test_preview_and_vod_reuse_the_player(fresh, kind):
    r = fresh.get(f"/web/matches/match-m1/{kind}")
    assert r.status_code == 200
    assert sorted(found_testids(r.text)) == sorted(TESTIDS)
    assert boot(r.text)["kind"] == kind


def test_costream_page_has_the_nine_testids_even_before_the_session_exists(fresh):
    r = fresh.get("/web/costream/cs-session-101")
    assert r.status_code == 200
    assert sorted(found_testids(r.text)) == sorted(TESTIDS)
    b = boot(r.text)
    assert b["chat_room_id"] == "chat-cs-session-101" and b["kind"] == "costream"
    assert b["match_id"] == "match-m1"
    assert text_of(r.text, "live-score-banner") == "1 - 0"


def test_notice_page_shows_the_state(fresh):
    r = fresh.get("/web/matches/match-m1/notice", params={"state": "postponed"})
    assert r.status_code == 200
    assert '<strong id="notice-state">postponed</strong>' in r.text
    other = fresh.get("/web/matches/match-m1/notice", params={"state": "<script>x</script>"})
    assert '<strong id="notice-state">in_progress</strong>' in other.text
    assert "<script>x" not in other.text


def test_bare_match_link_redirects_by_status(fresh):
    r = fresh.get("/web/matches/match-m1", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/web/matches/match-m1/live"


def test_unknown_match_is_a_404_page(fresh):
    r = fresh.get("/web/matches/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E/live")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "<img src=x" not in r.text


def test_player_and_club_pages(fresh):
    p = fresh.get("/web/players/player-alpha-1")
    assert p.status_code == 200 and "Ahmed Raza" in p.text and 'href="/web/clubs/club-alpha-1"' in p.text
    c = fresh.get("/web/clubs/club-alpha-1")
    assert c.status_code == 200 and "Alpha United" in c.text and 'href="/web/players/player-alpha-1"' in c.text
    assert fresh.get("/web/players/player-nobody").status_code == 404
    assert fresh.get("/web/clubs/club-nobody").status_code == 404


def test_pages_render_for_a_guest_token(fresh):
    r = fresh.get("/web/matches/match-m1/live", headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------------------------
# Read-only JSON helpers
# ---------------------------------------------------------------------------------------------

def test_match_overlay_shape(fresh):
    r = fresh.get("/web/api/matches/match-m1/overlay")
    assert r.status_code == 200
    o = r.json()
    assert o["match_id"] == "match-m1" and o["status"] == "in_progress"
    assert o["route"] == "/web/matches/match-m1/live"
    assert o["home"] == {"team_id": "club-alpha-1", "name": "Alpha United", "score": 1}
    assert o["away"] == {"team_id": "club-alpha-2", "name": "Alpha Rovers", "score": 0}
    assert o["score_text"] == "1 - 0"
    assert o["pinned_product"]["product_id"] == "prod-jersey-001"
    assert o["pinned_product"]["cart_variant_id"] == "var-jersey-001-L"
    assert o["pinned_product"]["price_label"] == "PKR 4,500"
    assert o["playback_url"] is None
    assert o["chat_room_id"] == "chat-match-m1" and o["ws_path"] == "/ws/matches/match-m1"


def test_overlay_follows_new_score_events(fresh):
    r = score_event(fresh, "match-m1", "club-alpha-1", "web-test-ev-1")
    assert r.status_code == 201, r.text
    assert fresh.get("/web/api/matches/match-m1/overlay").json()["score_text"] == "2 - 0"
    page = fresh.get("/web/matches/match-m1/live").text
    assert text_of(page, "live-score-banner") == "2 - 0"


def test_overlay_unknown_match(fresh):
    r = fresh.get("/web/api/matches/match-nope/overlay")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "MATCH_NOT_FOUND"


def test_overlay_needs_no_token(fresh):
    assert fresh.get("/web/api/matches/match-m1/overlay").status_code == 200
    assert fresh.get("/web/api/matches/match-m1/overlay",
                     headers={"Authorization": "Bearer guest_anon_tok_7721"}).status_code == 200


def test_costream_overlay_without_a_session(fresh):
    o = fresh.get("/web/api/costream/cs-session-404").json()
    assert o["session_id"] == "cs-session-404" and o["exists"] is False and o["status"] == "waiting"
    assert o["chat_room_id"] == "chat-cs-session-404" and o["playback_url"] is None
    assert o["match"]["match_id"] == "match-m1"
    assert o["pinned_product"]["product_id"] == "prod-jersey-001"


def test_chat_history_passes_moderation_backlog_through(fresh, monkeypatch):
    calls = []

    class Moderation:
        @staticmethod
        def list_chat(room, after, limit):
            calls.append((room, after, limit))
            return {"chat_room_id": room, "messages": [{"sequence": 1, "text": "***"}]}

    real = web.optional
    monkeypatch.setattr(web, "optional", lambda path: Moderation if path.endswith("moderation.service") else real(path))
    r = fresh.get("/web/api/chat/chat-match-m1/messages", params={"after": 3, "limit": 50})
    assert r.status_code == 200
    assert r.json() == {"chat_room_id": "chat-match-m1", "messages": [{"sequence": 1, "text": "***"}]}
    assert calls == [("chat-match-m1", 3, 50)]
    assert fresh.get("/web/api/chat/chat-match-m1/messages").status_code == 200
    assert calls[-1] == ("chat-match-m1", None, 100)


def test_chat_history_validation_and_missing_module(fresh, monkeypatch):
    assert fresh.get("/web/api/chat/bad%20room/messages").json()["error"]["details"]["fields"] == ["chat_room_id"]
    assert fresh.get("/web/api/chat/chat-x/messages", params={"limit": 0}).status_code == 400
    assert fresh.get("/web/api/chat/chat-x/messages", params={"after": -1}).status_code == 400
    real = web.optional
    monkeypatch.setattr(web, "optional", lambda path: None if path.endswith("moderation.service") else real(path))
    r = fresh.get("/web/api/chat/chat-x/messages")
    assert r.status_code == 503 and r.json()["error"]["code"] == "CHAT_UNAVAILABLE"


def test_player_and_club_api(fresh):
    p = fresh.get("/web/api/players/player-alpha-1").json()
    assert p["name"] == "Ahmed Raza" and p["club_id"] == "club-alpha-1" and p["club_name"] == "Alpha United"
    c = fresh.get("/web/api/clubs/club-alpha-1").json()
    assert c["name"] == "Alpha United" and any(x["player_id"] == "player-alpha-1" for x in c["players"])
    assert fresh.get("/web/api/players/nobody").json()["error"]["code"] == "PLAYER_NOT_FOUND"
    assert fresh.get("/web/api/clubs/nobody").json()["error"]["code"] == "CLUB_NOT_FOUND"


# ---------------------------------------------------------------------------------------------
# Seeded pin and fallback
# ---------------------------------------------------------------------------------------------

def test_reset_pins_the_jersey_on_match_m1(fresh):
    with db.tx() as conn:
        assert store.pinned_product(conn, "match-m1")["product_id"] == "prod-jersey-001"


def test_another_pin_wins_and_unpinning_falls_back_to_the_jersey(fresh):
    with db.tx() as conn:
        store.set_pin(conn, "match-m1", "prod-cap-001", "test")
    assert fresh.get("/web/api/matches/match-m1/overlay").json()["pinned_product"]["product_id"] == "prod-cap-001"
    with db.tx() as conn:
        store.set_pin(conn, "match-m1", None, "test")
    o = fresh.get("/web/api/matches/match-m1/overlay").json()
    assert o["pinned_product"]["product_id"] == "prod-jersey-001"
    assert o["pinned_product"]["cart_variant_id"] == "var-jersey-001-L"


def test_seeder_keeps_an_existing_pin(fresh):
    with db.tx() as conn:
        store.set_pin(conn, "match-m1", "prod-cap-001", "test")
        web.seed_pin(conn, {})
        assert store.pinned_product(conn, "match-m1")["product_id"] == "prod-cap-001"


def test_cart_variant_rule():
    assert web.cart_variant(None) is None
    assert web.cart_variant({"variants": []}) is None
    assert web.cart_variant({"variants": [{"variant_id": "a", "size": "M"}, {"variant_id": "b", "size": "l"}]})["variant_id"] == "b"
    assert web.cart_variant({"variants": [{"variant_id": "a", "size": None}]})["variant_id"] == "a"
    assert web.price_label({"price_minor": 450000, "currency": "PKR"}) == "PKR 4,500"
    assert web.price_label({"price_minor": 12345, "currency": "PKR"}) == "PKR 123.45"
    assert web.price_label(None) == ""


def test_match_route_rule():
    assert web.match_route("m", "upcoming") == "/web/matches/m/preview"
    assert web.match_route("m", "in_progress") == "/web/matches/m/live"
    assert web.match_route("m", "completed") == "/web/matches/m/vod"
    assert web.match_route("m", "postponed") == "/web/matches/m/notice?state=postponed"


# ---------------------------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------------------------

def test_static_assets_are_served_from_versioned_urls(fresh):
    page = fresh.get("/web/matches/match-m1/live").text
    for url in re.findall(r'(?:src|href)="(/web/static/[^"]+)"', page):
        r = fresh.get(url)
        assert r.status_code == 200, url
        assert "immutable" in r.headers["cache-control"]
    js = fresh.get("/web/static/player.js")
    assert js.headers["content-type"].startswith("text/javascript")
    hls = fresh.get("/web/static/hls.min.js")
    assert hls.status_code == 200 and len(hls.content) > 100_000
    assert fresh.get("/web/static/nope.js").json()["error"]["code"] == "ASSET_NOT_FOUND"
    assert fresh.get("/web/static/..%2Froutes.py").status_code == 404


# ---------------------------------------------------------------------------------------------
# HLS proxy for viewers that cannot reach the media server port
# ---------------------------------------------------------------------------------------------

def test_hls_proxy_only_serves_stream_files(fresh):
    r = fresh.get("/web/hls/live/abc/evil.sh")
    assert r.status_code == 404 and r.json()["error"]["code"] == "STREAM_NOT_FOUND"
    assert fresh.get("/web/hls/other/abc/index.m3u8").status_code == 404


def test_hls_proxy_passes_playlists_through(fresh, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text="#EXTM3U\n", headers={"content-type": "application/vnd.apple.mpegurl"})

    monkeypatch.setattr(routes, "_hls_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = fresh.get("/web/hls/live/abc/index.m3u8", params={"_HLS_msn": "3"})
    assert r.status_code == 200 and r.text.startswith("#EXTM3U")
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert seen["url"] == f"{routes.MEDIA_HLS}/live/abc/index.m3u8?_HLS_msn=3"


def test_hls_proxy_reports_an_unreachable_media_server(fresh, monkeypatch):
    monkeypatch.setattr(routes, "MEDIA_HLS", "http://127.0.0.1:9")
    monkeypatch.setattr(routes, "_hls_client", None)
    r = fresh.get("/web/hls/costream/cs-session-101/index.m3u8")
    assert r.status_code == 502 and r.json()["error"]["code"] == "MEDIA_UNAVAILABLE"


def test_match_video_comes_from_a_club_players_live_key(fresh, monkeypatch):
    class Streaming:
        @staticmethod
        def active_keys(conn, player_ids):
            return {"player-alpha-2": "key-a2"} if "player-alpha-2" in player_ids else {}

        @staticmethod
        def playback_url(key):
            return f"http://localhost:8888/live/{key}/index.m3u8"

    real = web.optional
    monkeypatch.setattr(web, "optional", lambda path: Streaming if path.endswith("streaming.service") else real(path))
    o = fresh.get("/web/api/matches/match-m1/overlay").json()
    assert o["playback_url"] == "http://localhost:8888/live/key-a2/index.m3u8"


# ---------------------------------------------------------------------------------------------
# Accessibility (NFR-8): static checks the Selenium DOM inspection relies on
# ---------------------------------------------------------------------------------------------

def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_colour_pairs_meet_wcag_aa():
    var = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})", CSS))
    pairs = [("text", "bg"), ("text", "surface"), ("text", "surface-2"), ("muted", "bg"), ("muted", "surface"),
             ("muted", "surface-2"), ("accent-ink", "accent"), ("link", "bg"), ("error", "surface")]
    for fg, bg in pairs:
        assert contrast(var[fg], var[bg]) >= 4.5, (fg, bg)
    for chip in ("live", "upcoming", "done"):
        assert contrast("#ffffff", var[chip]) >= 4.5, chip
    assert contrast("#a3b1c6", var["surface-2"]) >= 4.5  # placeholder text
    assert contrast("#fde68a", var["surface-2"]) >= 4.5  # chat sender names
    assert contrast("#e2e8f0", "#334155") >= 4.5  # disabled buttons


def test_touch_targets_are_at_least_48px():
    for selector in (".btn", ".ctl", ".input", ".cart-pill", ".team", ".skip-link"):
        block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
        assert block and "min-height: 48px" in block.group(1), selector
    assert "min-width: 48px" in re.search(r"\.ctl\s*\{([^}]*)\}", CSS).group(1)


def test_controls_have_accessible_names(fresh):
    page = fresh.get("/web/matches/match-m1/live").text
    assert '<html lang="en">' in page and 'name="viewport"' in page
    assert 'aria-label="Match video: Alpha United vs Alpha Rovers"' in element(page, "guest-video-player")
    for btn in re.findall(r"<button[^>]*>.*?</button>", page, re.S):
        named = "aria-label=" in btn or re.sub(r"<[^>]+>", "", btn).strip()
        assert named, btn
    for field in re.findall(r"<input[^>]*>", page):
        field_id = re.search(r'id="([^"]+)"', field).group(1)
        assert "aria-label=" in field or f'for="{field_id}"' in page, field
    assert 'role="group" aria-label="Video controls"' in page
