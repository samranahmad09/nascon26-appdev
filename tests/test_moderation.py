import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import config, db
from app.modules.moderation import bus, rules, service

ROOM = "chat-cs-session-101"
EXAMPLE = {
    "chat_room_id": ROOM,
    "incoming_messages": [
        {"message_id": "msg-1", "sender": "user-guest-1", "text": "Clean sports commentary"},
        {"message_id": "msg-2", "sender": "user-spammer", "text": "Watch this illegal stream b@d_w0rd now!"},
        {"message_id": "msg-3", "sender": "user-flooder", "text": "GOAL! AAAA!!!"},
    ],
    "check_dlq": True,
    "simulate_failed_notifications": 0,
}


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(bus, "BACKOFF_BASE_S", 0.001)


def raw_signature(room, flagged_count, kicked, depth):
    payload = {"chat_room_id": room, "flagged_count": flagged_count, "kicked_users": kicked, "queue_depth": depth}
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(config.DAY2_SALT.encode("utf-8"), (room + "|" + canon).encode("utf-8"), hashlib.sha256).hexdigest()


def probe(client, messages, **extra):
    body = {"chat_room_id": extra.pop("room", ROOM), "incoming_messages": messages, **extra}
    r = client.post("/audit/v1/moderation/test", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_section_5_8_example_reproduces_exactly(fresh):
    r = fresh.post("/audit/v1/moderation/test", json=EXAMPLE)
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["chat_room_id", "filtered_messages", "kicked_users", "muted_users", "dlq_status", "signature"]
    assert body["chat_room_id"] == ROOM
    assert body["filtered_messages"] == [
        {"message_id": "msg-1", "sender": "user-guest-1", "text": "Clean sports commentary", "flagged": False,
         "reason": None},
        {"message_id": "msg-2", "sender": "user-spammer", "text": "Watch this illegal stream *** now!",
         "flagged": True, "reason": "profanity_leetspeak"},
    ]
    assert body["kicked_users"] == ["user-spammer"]
    assert body["muted_users"] == ["user-flooder"]
    assert body["dlq_status"] == {"queue_depth": 0, "undelivered_events": []}
    assert body["signature"] == raw_signature(ROOM, 1, ["user-spammer"], 0)
    assert len(body["signature"]) == 64 and body["signature"] == body["signature"].lower()


def test_example_replayed_gives_identical_body(fresh):
    first = fresh.post("/audit/v1/moderation/test", json=EXAMPLE).json()
    for _ in range(5):
        assert fresh.post("/audit/v1/moderation/test", json=EXAMPLE).json() == first


@pytest.mark.parametrize("text", ["b@d_w0rd", "bad_word", "badword", "p0rn", "p.0.r.n", "porn", "sc@m", "s!c@m",
                                  "scam", "fuck", "f*u*c*k", "shit", "sh!t", "bitch", "b1tch", "asshole", "a$$h0le",
                                  "idiot_spam", "1d10t_sp@m", "PORN", "b​ad​word", "ＦＵＣＫ"])
def test_every_dictionary_form_is_masked_and_kicked(fresh, text):
    body = probe(fresh, [{"message_id": "m1", "sender": "user-x", "text": f"well {text} there"}])
    assert body["filtered_messages"] == [{"message_id": "m1", "sender": "user-x", "text": "well *** there",
                                          "flagged": True, "reason": "profanity_leetspeak"}]
    assert body["kicked_users"] == ["user-x"] and body["muted_users"] == []
    assert body["signature"] == raw_signature(ROOM, 1, ["user-x"], 0)


@pytest.mark.parametrize("text,masked", [
    ("This is a sc@m!", "This is a ***!"),
    ("(b@d_w0rd), ok", "(***), ok"),
    ("f.u.c.k.i.n.g p0rn", "*** ***"),
    ("Sh1T  happens", "***  happens"),
])
def test_masking_keeps_whitespace_and_outer_punctuation(text, masked):
    assert rules.evaluate(text) == (rules.PROFANITY, masked)


@pytest.mark.parametrize("text", ["Clean sports commentary", "What a save?!", "1-0 at half time", "now!", "",
                                  "assassin class", "is hit", "Nice one!!", "Live reaction"])
def test_clean_text_passes_unchanged(text):
    assert rules.evaluate(text) == (None, text)


@pytest.mark.parametrize("text", ["GOAL! AAAA!!!", "aaaa", "aAaA", "Nooooo", "wow!!!", "what???", "eh?!?", "....",
                                  "sc@m!!!"])
def test_flood_is_dropped_and_muted(fresh, text):
    body = probe(fresh, [{"message_id": "f1", "sender": "user-flooder", "text": text},
                         {"message_id": "c1", "sender": "user-ok", "text": "fine"}])
    assert [m["message_id"] for m in body["filtered_messages"]] == ["c1"]
    assert body["muted_users"] == ["user-flooder"] and body["kicked_users"] == []
    assert body["signature"] == raw_signature(ROOM, 0, [], 0)


def test_lists_are_deduplicated_in_order_of_first_violation(fresh):
    body = probe(fresh, [
        {"message_id": "a", "sender": "user-b", "text": "p0rn"},
        {"message_id": "b", "sender": "user-a", "text": "sc@m"},
        {"message_id": "c", "sender": "user-b", "text": "shit"},
        {"message_id": "d", "sender": "user-z", "text": "!!!"},
        {"message_id": "e", "sender": "user-y", "text": "????"},
        {"message_id": "f", "sender": "user-z", "text": "ZZZZ"},
    ])
    assert body["kicked_users"] == ["user-b", "user-a"]
    assert body["muted_users"] == ["user-z", "user-y"]
    assert body["signature"] == raw_signature(ROOM, 3, ["user-b", "user-a"], 0)
    rows = db.fetchall("SELECT user_id, action FROM md_room_actions WHERE chat_room_id = %s ORDER BY 1, 2", (ROOM,))
    assert [(r["user_id"], r["action"]) for r in rows] == [
        ("user-a", "kick"), ("user-b", "kick"), ("user-y", "mute"), ("user-z", "mute")]


def test_rate_limit_blocks_fourth_distinct_message_within_window(fresh):
    msgs = [{"message_id": f"r{i}", "sender": "user-rapid", "text": f"hello {i}"} for i in range(5)]
    body = probe(fresh, msgs)
    assert [m["message_id"] for m in body["filtered_messages"]] == ["r0", "r1", "r2"]
    assert body["kicked_users"] == ["user-rapid"] and body["muted_users"] == ["user-rapid"]
    assert body["signature"] == raw_signature(ROOM, 0, ["user-rapid"], 0)
    # another room has its own window
    other = probe(fresh, msgs[:3], room="chat-other")
    assert other["kicked_users"] == [] and len(other["filtered_messages"]) == 3


def test_rate_limit_counts_across_requests_but_not_replays(fresh):
    one = [{"message_id": "x1", "sender": "user-r", "text": "hi"}]
    for _ in range(3):
        assert probe(fresh, one)["kicked_users"] == []
    for i in (2, 3):
        assert probe(fresh, [{"message_id": f"x{i}", "sender": "user-r", "text": "hi"}])["kicked_users"] == []
    blocked = probe(fresh, [{"message_id": "x4", "sender": "user-r", "text": "hi"}])
    assert blocked["filtered_messages"] == [] and blocked["kicked_users"] == ["user-r"]
    # profanity keeps its own outcome even when the window is exceeded
    prof = probe(fresh, [{"message_id": "x5", "sender": "user-r", "text": "sc@m"}])
    assert prof["filtered_messages"][0]["reason"] == "profanity_leetspeak" and prof["muted_users"] == []


def test_rate_window_slides():
    w = service.RateWindow(limit=3, window_s=2.0)
    assert [w.hit(("k",), f"m{i}", 0.5 * i) for i in range(4)] == [False, False, False, True]
    assert w.hit(("k",), "m9", 10.0) is False


def test_rate_window_sweeps_stale_senders():
    w = service.RateWindow(limit=3, window_s=2.0, sweep_every=3)
    w.hit(("old",), "a", 0.0)
    w.hit(("fresh",), "b", 9.5)
    w.hit(("fresh",), "c", 10.0)
    assert set(w._hits) == {("fresh",)}


def test_simulated_failures_go_to_dlq_after_three_attempts(fresh, monkeypatch):
    calls = []
    real = bus._attempt
    monkeypatch.setattr(bus, "_attempt", lambda note: (calls.append(note["event_id"]), real(note)))
    sleeps = []
    monkeypatch.setattr(bus, "_sleep", sleeps.append)
    body = probe(fresh, EXAMPLE["incoming_messages"], simulate_failed_notifications=3)
    assert len(calls) == 9 and len(set(calls)) == 3
    assert sleeps == [0.001, 0.002]
    status = body["dlq_status"]
    assert status["queue_depth"] == 3 and len(status["undelivered_events"]) == 3
    for event in status["undelivered_events"]:
        assert event["attempts"] == 3 and event["topic"] == f"chat:{ROOM}"
        assert event["event_type"] == "chat_notification" and event["last_error"] == "simulated downstream push failure"
        assert event["event_id"].startswith("evt-") and event["failed_at"].endswith("Z")
    assert body["signature"] == raw_signature(ROOM, 1, ["user-spammer"], 3)
    again = probe(fresh, [], simulate_failed_notifications=2)
    assert again["dlq_status"]["queue_depth"] == 5
    quiet = probe(fresh, [], check_dlq=False)
    assert quiet["dlq_status"] == {"queue_depth": 5, "undelivered_events": []}
    assert quiet["signature"] == raw_signature(ROOM, 0, [], 5)
    assert fresh.get("/audit/v1/dlq").json()["queue_depth"] == 5
    metrics = fresh.get("/metrics").text
    assert "nascon_dlq_depth 5" in metrics.splitlines()
    fresh.post("/audit/v1/reset", json={})
    assert probe(fresh, [])["dlq_status"] == {"queue_depth": 0, "undelivered_events": []}
    assert "nascon_dlq_depth 0" in fresh.get("/metrics").text.splitlines()


def test_ten_simultaneous_identical_requests(fresh):
    race = {**EXAMPLE, "simulate_failed_notifications": 2}

    def fire(_):
        return fresh.post("/audit/v1/moderation/test", json=race)

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(fire, range(10)))
    assert [r.status_code for r in responses] == [200] * 10
    depths = sorted(r.json()["dlq_status"]["queue_depth"] for r in responses)
    for r in responses:
        body = r.json()
        assert body["kicked_users"] == ["user-spammer"] and body["muted_users"] == ["user-flooder"]
        assert body["signature"] == raw_signature(ROOM, 1, ["user-spammer"], body["dlq_status"]["queue_depth"])
    assert depths[-1] == 20 and depths[0] >= 2
    assert bus.depth() == 20
    kicks = db.fetchall("SELECT user_id FROM md_room_actions WHERE chat_room_id = %s AND action = 'kick'", (ROOM,))
    assert [k["user_id"] for k in kicks] == ["user-spammer"]


def test_ten_threads_kicking_the_same_spammer_with_distinct_messages(fresh):
    def fire(i):
        return fresh.post("/audit/v1/moderation/test", json={
            "chat_room_id": ROOM, "simulate_failed_notifications": 1,
            "incoming_messages": [{"message_id": f"race-{i}", "sender": "user-spammer", "text": "b@d_w0rd"}]})

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(fire, range(10)))
    assert all(r.status_code == 200 and r.json()["kicked_users"] == ["user-spammer"] for r in responses)
    assert all(r.json()["filtered_messages"][0]["text"] == "***" for r in responses)
    assert bus.depth() == 10


@pytest.mark.parametrize("body,fields", [
    ({}, ["chat_room_id", "incoming_messages"]),
    ({"chat_room_id": "", "incoming_messages": []}, ["chat_room_id"]),
    ({"chat_room_id": ROOM, "incoming_messages": "nope"}, ["incoming_messages"]),
    ({"chat_room_id": ROOM, "incoming_messages": [{"message_id": "m", "sender": "s"}]}, ["incoming_messages"]),
    ({"chat_room_id": ROOM, "incoming_messages": [{"message_id": "m", "sender": "", "text": "x"}]}, ["incoming_messages"]),
    ({"chat_room_id": ROOM, "incoming_messages": [{"message_id": 1, "sender": "s", "text": "x"}]}, ["incoming_messages"]),
    ({"chat_room_id": ROOM, "incoming_messages": ["text"]}, ["incoming_messages"]),
    ({"chat_room_id": ROOM, "incoming_messages": [], "check_dlq": "yes"}, ["check_dlq"]),
    ({"chat_room_id": ROOM, "incoming_messages": [], "simulate_failed_notifications": -1},
     ["simulate_failed_notifications"]),
    ({"chat_room_id": ROOM, "incoming_messages": [], "simulate_failed_notifications": 1.5},
     ["simulate_failed_notifications"]),
    ({"chat_room_id": 7, "incoming_messages": []}, ["chat_room_id"]),
])
def test_probe_validation(fresh, body, fields):
    r = fresh.post("/audit/v1/moderation/test", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR" and r.json()["error"]["details"]["fields"] == fields


def test_probe_rejects_non_object_and_guest_tokens(fresh):
    assert fresh.post("/audit/v1/moderation/test", content=b"{bad").status_code == 400
    assert fresh.post("/audit/v1/moderation/test", json=[1]).json()["error"]["details"]["fields"] == ["body"]
    r = fresh.post("/audit/v1/moderation/test", json=EXAMPLE, headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"


def test_defaults_and_null_optionals(fresh):
    body = probe(fresh, [{"message_id": "m", "sender": "s", "text": "hi"}], check_dlq=None,
                 simulate_failed_notifications=None)
    assert body["dlq_status"] == {"queue_depth": 0, "undelivered_events": []}


def test_flagged_count_switch(fresh, monkeypatch):
    monkeypatch.setattr(service, "FLAGGED_COUNT_INCLUDES_DROPPED", True)
    body = fresh.post("/audit/v1/moderation/test", json=EXAMPLE).json()
    assert body["signature"] == raw_signature(ROOM, 2, ["user-spammer"], 0)


def test_audit_record_is_persisted_with_signature(fresh):
    body = fresh.post("/audit/v1/moderation/test", json=EXAMPLE).json()
    row = db.fetchone("SELECT * FROM md_audit ORDER BY audit_id DESC LIMIT 1")
    assert row["signature"] == body["signature"] and row["flagged_count"] == 1 and row["message_count"] == 3
    assert row["kicked_users"] == ["user-spammer"] and row["muted_users"] == ["user-flooder"]


def test_k6_style_single_message_posts(fresh):
    for i in range(3):
        r = fresh.post("/audit/v1/moderation/test", json={
            "chat_room_id": ROOM, "check_dlq": False,
            "incoming_messages": [{"message_id": f"k6-1-{i}", "sender": "user-1", "text": "Live reaction"}]})
        assert r.status_code == 200 and r.json()["filtered_messages"][0]["flagged"] is False
