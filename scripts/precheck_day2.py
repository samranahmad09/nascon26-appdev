"""Phase II Appendix C pre-submission checks against a running stack.

Run inside the API container so the real DAY2_SALT is used for signature checks:
    docker compose exec -T -e PYTHONPATH=/srv api python scripts/precheck_day2.py
"""
import concurrent.futures as cf
import sys

import httpx

from app.core.signing import sign_day2

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
GRID = "http://selenium-hub:4444"
TESTIDS = ["guest-video-player", "live-score-banner", "pinned-merch-card", "add-to-guest-cart-btn",
           "guest-cart-count", "chat-message-list", "chat-input-field", "chat-send-btn", "progressive-auth-modal"]
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=20)
    check("health 200", c.get("/health").status_code == 200)
    check("reset 200", c.post("/audit/v1/reset", json={}).status_code == 200)

    m1 = c.get("/audit/v1/score/match-m1").json()
    check("seed match-m1 in_progress 1-0", m1.get("status") == "in_progress"
          and (m1["home"]["score"], m1["away"]["score"]) == (1, 0), str(m1)[:120])

    r = c.post("/audit/v1/stream/key", json={"action": "rotate", "player_id": "player-alpha-1", "label": "OBS-Main-Desk",
                                             "stream_key_override": "sk_live_9f83a1b2c3d4e5f6"})
    b = r.json()
    sig = sign_day2(b.get("stream_key", ""), {"action": "rotate", "player_id": "player-alpha-1",
                                              "status": b.get("status"), "stream_key": b.get("stream_key")})
    check("stream key rotate 200 + signature", r.status_code == 200 and b.get("signature") == sig, r.text[:160])
    check("stream key keys", set(b) == {"player_id", "label", "stream_key", "status", "ingest_endpoints",
                                        "playback_url", "signature"}, str(sorted(b)))
    rv = c.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"}).json()
    check("revoke nulls endpoints", rv.get("status") == "revoked" and rv.get("ingest_endpoints") is None
          and rv.get("playback_url") is None, str(rv)[:160])
    check("unknown player 404", c.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "nope"}).status_code == 404)

    body = {"session_id": "cs-session-101", "host_player_id": "player-alpha-1",
            "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2"],
            "layout": "grid_4", "simulate_host_drop": True}
    r = c.post("/audit/v1/costream/session", json=body)
    b = r.json()
    sig = sign_day2("cs-session-101", {k: b.get(k) for k in ("active_host", "handoff_occurred", "layout",
                                                              "participant_count", "session_id", "status")})
    check("costream handoff 201 + signature", r.status_code == 201 and b.get("active_host") == "player-alpha-2"
          and b.get("participant_count") == 3 and b.get("signature") == sig, r.text[:160])
    check("costream replay 200", c.post("/audit/v1/costream/session", json=body).status_code == 200)
    check("costream > 4 is 409", c.post("/audit/v1/costream/session", json={**body, "session_id": "cs-x",
          "participants": body["participants"] + ["player-alpha-1b"]}).json()["error"]["code"] == "COSTREAM_SESSION_FULL")

    q = c.get("/audit/v1/qr/match-m1").json()
    sig = sign_day2("match-m1", {"entity_id": "match-m1", "short_url": q.get("short_url"), "target_route": q.get("target_route")})
    check("qr live route + signature", q.get("target_route") == "/web/matches/match-m1/live" and q.get("signature") == sig, str(q)[:160])
    loc = c.get("/q/m1-live", follow_redirects=False)
    check("short link 302", loc.status_code == 302 and loc.headers.get("location") == "/web/matches/match-m1/live")
    for fmt, magic in (("svg", b"<"), ("png", b"\x89PNG"), ("pdf", b"%PDF-")):
        e = c.get(f"/audit/v1/qr/match-m1/export.{fmt}")
        check(f"qr export {fmt}", e.status_code == 200 and e.content.lstrip().startswith(magic))
    c.post("/audit/v1/events", json={"event_id": "pc2-end", "match_id": "match-m1", "type": "match_end", "payload": {}})
    q2 = c.get("/audit/v1/qr/match-m1?inspect=true").json()
    check("qr retargets after match_end (edge 2)", q2.get("short_url") == q.get("short_url")
          and q2.get("target_route") == "/web/matches/match-m1/vod" and q2.get("signature") != q.get("signature"))
    check("qr unknown 404", c.get("/audit/v1/qr/match-zzz").json().get("error", {}).get("code") == "QR_NOT_FOUND")

    c.post("/audit/v1/reset", json={})
    merge = {"guest_token": "guest_anon_tok_7721", "registered_user_id": "player-alpha-1",
             "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 1}], "test_write_block": True}
    r1, r2 = c.post("/audit/v1/guest/cart-merge", json=merge), c.post("/audit/v1/guest/cart-merge", json=merge)
    b = r1.json()
    sig = sign_day2("guest_anon_tok_7721", {"cart_merged": b.get("cart_merged"), "guest_token": "guest_anon_tok_7721",
                                            "registered_user_id": "player-alpha-1", "total_items": 1,
                                            "write_rejection_code": 403})
    check("cart merge 200 + signature", r1.status_code == 200 and b.get("signature") == sig, r1.text[:160])
    check("cart merge replay total_items 1 (edge 4)", r2.status_code == 200 and r2.json()["active_cart"]["total_items"] == 1)
    check("merged token reuse 409", c.post("/audit/v1/guest/cart-merge", json={**merge, "registered_user_id": "player-alpha-2"})
          .json().get("error", {}).get("code") == "GUEST_TOKEN_ALREADY_MERGED")
    guest = {"Authorization": "Bearer guest_anon_tok_7721"}
    for path in ("/api/v1/chat", "/audit/v1/checkout", "/audit/v1/events"):
        r = c.post(path, json={}, headers=guest)
        check(f"guest write 403 {path}", r.status_code == 403 and r.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN")

    probe = {"chat_room_id": "chat-cs-session-101", "incoming_messages": [
        {"message_id": "msg-1", "sender": "user-guest-1", "text": "Clean sports commentary"},
        {"message_id": "msg-2", "sender": "user-spammer", "text": "Watch this illegal stream b@d_w0rd now!"},
        {"message_id": "msg-3", "sender": "user-flooder", "text": "GOAL! AAAA!!!"}], "check_dlq": True,
        "simulate_failed_notifications": 0}
    b = c.post("/audit/v1/moderation/test", json=probe).json()
    check("moderation example", [m["text"] for m in b["filtered_messages"]] == ["Clean sports commentary",
          "Watch this illegal stream *** now!"] and b["kicked_users"] == ["user-spammer"]
          and b["muted_users"] == ["user-flooder"] and b["dlq_status"]["queue_depth"] == 0)
    with cf.ThreadPoolExecutor(10) as pool:
        codes = list(pool.map(lambda _: httpx.post(BASE + "/audit/v1/moderation/test", json=probe, timeout=20).status_code, range(10)))
    check("10 concurrent moderation calls (edge 3)", codes == [200] * 10, str(codes))

    metrics = c.get("/metrics")
    names = ["nascon_active_ws_connections", "nascon_active_streams_total", "nascon_stream_bitrate_kbps",
             "nascon_dlq_depth", "nascon_qr_scans_total"]
    check("/metrics families", metrics.headers["content-type"].startswith("text/plain; version=0.0.4")
          and all(n in metrics.text for n in names))
    p = c.delete("/audit/v1/privacy/user/player-alpha-2")
    check("privacy erasure", p.status_code == 200 and p.json() == {"user_id": "player-alpha-2", "status": "anonymized",
                                                                    "pii_retained": False, "tokens_wiped": True}, p.text[:120])

    html = c.get("/web/matches/match-m1/live").text
    missing = [t for t in TESTIDS if f'data-testid="{t}"' not in html]
    check("guest player has all 9 data-testids", not missing, str(missing))
    try:
        g = httpx.get(GRID + "/status", timeout=5).json()["value"]
        check("grid ready with >= 3 nodes", g["ready"] and len(g["nodes"]) >= 3, str(len(g.get("nodes", []))))
    except Exception as exc:
        check("grid ready with >= 3 nodes", False, str(exc)[:80])

    c.post("/audit/v1/reset", json={})
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
