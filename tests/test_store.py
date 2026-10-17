import base64
import io
import re
import threading
import time

import pytest
from pypdf import PdfReader

from app.core import db
from app.core.signing import sign
from app.modules.store import gateway, receipt, service

M, L = "var-jersey-001-M", "var-jersey-001-L"
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def order(order_id, items=None, token="tok_success", discount=None, customer="cust-001"):
    return {"order_id": order_id, "customer_id": customer,
            "items": items if items is not None else [{"variant_id": M, "quantity": 1}],
            "discount_code": discount, "payment": {"token": token}}


def inventory(client, variant_id):
    r = client.get(f"/audit/v1/inventory/{variant_id}")
    assert r.status_code == 200, r.text
    return r.json()


def pdf_text(body):
    raw = base64.b64decode(body["receipt"]["pdf_base64"])
    assert raw.startswith(b"%PDF")
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["details"], dict)
    return body["error"]


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(service.queue, "emit", lambda event_type, data: captured.append((event_type, data)))
    return captured


def test_success_response_matches_section_5_12(fresh, events):
    r = fresh.post("/audit/v1/checkout", json=order("ord-001"))
    assert r.status_code == 201, r.text
    body = r.json()
    paid_at = body["receipt"]["payload"]["paid_at"]
    assert TS_RE.fullmatch(paid_at)
    line = {"variant_id": M, "quantity": 1, "unit_price_minor": 450000}
    assert body == {
        "order_id": "ord-001", "status": "paid", "currency": "PKR", "items": [line],
        "subtotal_minor": 450000, "discount_minor": 0, "total_minor": 450000,
        "payment": {"authorization_id": "auth_1", "capture_id": "cap_1", "state": "captured"},
        "receipt": {
            "payload": {"order_id": "ord-001", "customer_id": "cust-001", "items": [line],
                        "subtotal_minor": 450000, "discount_minor": 0, "total_minor": 450000, "currency": "PKR",
                        "paid_at": paid_at},
            "signature": body["receipt"]["signature"],
            "pdf_base64": body["receipt"]["pdf_base64"],
        },
    }
    assert body["receipt"]["signature"] == sign("ord-001", body["receipt"]["payload"], salt="test-salt")
    assert re.fullmatch(r"[0-9a-f]{64}", body["receipt"]["signature"])
    text = pdf_text(body)
    assert "ord-001" in text and "PKR 4500.00" in text
    assert inventory(fresh, M) == {"variant_id": M, "stock": 0, "reserved": 0}
    transitions = [(d["from_status"], d["to_status"]) for t, d in events if t == "order_status_changed"]
    assert transitions == [(None, "pending"), ("pending", "paid")]


def test_twenty_simultaneous_buyers_for_the_last_unit(fresh):
    assert fresh.post("/audit/v1/reset", json={"stock": {M: 1}}).status_code == 200
    barrier = threading.Barrier(20)
    results = [None] * 20

    def buy(i):
        barrier.wait()
        results[i] = fresh.post("/audit/v1/checkout", json=order(f"burst-{i:02d}", customer=f"cust-{i}"))

    threads = [threading.Thread(target=buy, args=(i,)) for i in range(20)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    elapsed = time.monotonic() - started
    assert elapsed < 10
    codes = [r.status_code for r in results]
    assert codes.count(201) == 1 and codes.count(409) == 19, codes
    for r in results:
        if r.status_code == 409:
            err = assert_error(r, 409, "OUT_OF_STOCK")
            assert err["details"] == {"variant_id": M}
    assert inventory(fresh, M) == {"variant_id": M, "stock": 0, "reserved": 0}
    winner = next(r for r in results if r.status_code == 201).json()
    assert winner["payment"] == {"authorization_id": "auth_1", "capture_id": "cap_1", "state": "captured"}
    # replaying the winner is answered from storage even though no stock is left
    i = int(winner["order_id"].split("-")[1])
    replay = fresh.post("/audit/v1/checkout", json=order(winner["order_id"], customer=f"cust-{i}"))
    assert replay.status_code == 200 and replay.json() == winner
    assert inventory(fresh, M) == {"variant_id": M, "stock": 0, "reserved": 0}


def test_burst_never_oversells_when_several_units_remain(fresh):
    assert fresh.post("/audit/v1/reset", json={"stock": {M: 3}}).status_code == 200
    barrier = threading.Barrier(12)
    codes = []

    def buy(i):
        barrier.wait()
        codes.append(fresh.post("/audit/v1/checkout", json=order(f"multi-{i}")).status_code)

    threads = [threading.Thread(target=buy, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(codes) == [201] * 3 + [409] * 9
    assert inventory(fresh, M) == {"variant_id": M, "stock": 0, "reserved": 0}


def test_declined_payment_releases_stock_which_is_purchasable_again(fresh, events):
    r = fresh.post("/audit/v1/checkout", json=order("ord-dec", token="tok_declined"))
    assert_error(r, 402, "PAYMENT_DECLINED")
    assert inventory(fresh, M) == {"variant_id": M, "stock": 1, "reserved": 0}
    r = fresh.post("/audit/v1/checkout", json=order("ord-after-dec"))
    assert r.status_code == 201
    assert r.json()["payment"]["authorization_id"] == "auth_1"
    assert inventory(fresh, M) == {"variant_id": M, "stock": 0, "reserved": 0}
    transitions = [(d["order_id"], d["to_status"]) for _, d in events]
    assert ("ord-dec", "cancelled") in transitions and ("ord-after-dec", "paid") in transitions


def test_capture_failure_voids_authorization_and_releases(fresh):
    r = fresh.post("/audit/v1/checkout", json=order("ord-cf", token="tok_capture_fail"))
    err = assert_error(r, 402, "PAYMENT_CAPTURE_FAILED")
    assert err["details"]["authorization_id"] == "auth_1"
    assert inventory(fresh, M) == {"variant_id": M, "stock": 1, "reserved": 0}
    assert db.fetchone("SELECT state FROM st_authorizations WHERE auth_seq = 1")["state"] == "voided"
    r = fresh.post("/audit/v1/checkout", json=order("ord-after-cf"))
    assert r.status_code == 201
    assert r.json()["payment"] == {"authorization_id": "auth_2", "capture_id": "cap_1", "state": "captured"}


def test_unknown_payment_token_is_declined(fresh):
    assert_error(fresh.post("/audit/v1/checkout", json=order("ord-tok", token="tok_unknown")), 402, "PAYMENT_DECLINED")
    assert inventory(fresh, M)["stock"] == 1


def test_welcome10_discount_maths(fresh):
    r = fresh.post("/audit/v1/checkout", json=order("ord-disc", items=[{"variant_id": L, "quantity": 3}],
                                                     discount="WELCOME10"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["subtotal_minor"], body["discount_minor"], body["total_minor"]) == (1350000, 135000, 1215000)
    payload = body["receipt"]["payload"]
    assert (payload["subtotal_minor"], payload["discount_minor"], payload["total_minor"]) == (1350000, 135000, 1215000)
    assert "PKR 12150.00" in pdf_text(body)
    assert inventory(fresh, L) == {"variant_id": L, "stock": 2, "reserved": 0}


def test_discount_is_floored(fresh):
    with db.tx() as conn:
        conn.execute("INSERT INTO st_products VALUES ('prod-odd', 'tenant-alpha', NULL, 'Odd', 'accessories', '', 99)")
        conn.execute("INSERT INTO st_variants (variant_id, product_id, size, color, price_minor, currency, stock, "
                     "reserved, position) VALUES ('var-odd', 'prod-odd', NULL, NULL, 33333, 'PKR', 5, 0, 0)")
    r = fresh.post("/audit/v1/checkout", json=order("ord-odd", items=[{"variant_id": "var-odd", "quantity": 1},
                                                                       {"variant_id": M, "quantity": 1}],
                                                    discount="WELCOME10"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["subtotal_minor"] == 483333
    assert body["discount_minor"] == 48333
    assert body["total_minor"] == 435000
    assert [i["variant_id"] for i in body["items"]] == ["var-odd", M]
    assert "PKR 4350.00" in pdf_text(body)


def test_idempotent_replay_and_duplicate_order_id(fresh, events):
    first = fresh.post("/audit/v1/checkout", json=order("ord-idem", items=[{"variant_id": L, "quantity": 1}]))
    assert first.status_code == 201
    replay = fresh.post("/audit/v1/checkout", json=order("ord-idem", items=[{"variant_id": L, "quantity": 1}]))
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert inventory(fresh, L) == {"variant_id": L, "stock": 4, "reserved": 0}
    # unknown fields are ignored, so this is still the identical request
    same = order("ord-idem", items=[{"variant_id": L, "quantity": 1}])
    same["note"] = "ignored"
    assert fresh.post("/audit/v1/checkout", json=same).status_code == 200
    dup = fresh.post("/audit/v1/checkout", json=order("ord-idem", items=[{"variant_id": L, "quantity": 2}]))
    err = assert_error(dup, 409, "DUPLICATE_ORDER_ID")
    assert err["details"] == {"order_id": "ord-idem"}
    assert inventory(fresh, L)["stock"] == 4
    assert sum(1 for _, d in events if d["to_status"] == "paid") == 1


def test_replay_of_a_declined_order_returns_the_stored_error(fresh):
    body = order("ord-dec2", token="tok_declined")
    assert_error(fresh.post("/audit/v1/checkout", json=body), 402, "PAYMENT_DECLINED")
    assert_error(fresh.post("/audit/v1/checkout", json=body), 402, "PAYMENT_DECLINED")
    assert db.fetchone("SELECT count(*) AS n FROM st_order_events WHERE order_id = 'ord-dec2'")["n"] == 2
    assert_error(fresh.post("/audit/v1/checkout", json=order("ord-dec2")), 409, "DUPLICATE_ORDER_ID")
    assert inventory(fresh, M) == {"variant_id": M, "stock": 1, "reserved": 0}


def test_out_of_stock_reserves_nothing(fresh):
    r = fresh.post("/audit/v1/checkout", json=order("ord-oos", items=[{"variant_id": L, "quantity": 2},
                                                                      {"variant_id": M, "quantity": 2}]))
    err = assert_error(r, 409, "OUT_OF_STOCK")
    assert err["details"] == {"variant_id": M}
    assert inventory(fresh, L) == {"variant_id": L, "stock": 5, "reserved": 0}
    assert inventory(fresh, M) == {"variant_id": M, "stock": 1, "reserved": 0}
    # the failed attempt left no order behind, so the id can still be used
    assert fresh.post("/audit/v1/checkout", json=order("ord-oos", items=[{"variant_id": L, "quantity": 2}])).status_code == 201


@pytest.mark.parametrize("mutate, field", [
    (lambda b: b.pop("order_id"), "order_id"),
    (lambda b: b.update(order_id="bad|id"), "order_id"),
    (lambda b: b.update(order_id="x" * 65), "order_id"),
    (lambda b: b.update(order_id=7), "order_id"),
    (lambda b: b.pop("customer_id"), "customer_id"),
    (lambda b: b.update(customer_id=""), "customer_id"),
    (lambda b: b.update(customer_id=12), "customer_id"),
    (lambda b: b.update(customer_id="nul\u0000byte"), "customer_id"),
    (lambda b: b.pop("items"), "items"),
    (lambda b: b.update(items=[]), "items"),
    (lambda b: b.update(items={"variant_id": M, "quantity": 1}), "items"),
    (lambda b: b.update(items=[{"variant_id": M, "quantity": 0}]), "items"),
    (lambda b: b.update(items=[{"variant_id": M, "quantity": 11}]), "items"),
    (lambda b: b.update(items=[{"variant_id": M, "quantity": 1.5}]), "items"),
    (lambda b: b.update(items=[{"variant_id": M, "quantity": True}]), "items"),
    (lambda b: b.update(items=[{"variant_id": M, "quantity": "1"}]), "items"),
    (lambda b: b.update(items=[{"variant_id": M}]), "items"),
    (lambda b: b.update(items=[{"quantity": 1}]), "items"),
    (lambda b: b.update(items=["var-jersey-001-M"]), "items"),
    (lambda b: b.update(items=[{"variant_id": M, "quantity": 1}, {"variant_id": M, "quantity": 2}]), "items"),
    (lambda b: b.update(items=[{"variant_id": f"v{i}", "quantity": 1} for i in range(11)]), "items"),
    (lambda b: b.update(discount_code=10), "discount_code"),
    (lambda b: b.update(discount_code=["WELCOME10"]), "discount_code"),
    (lambda b: b.pop("payment"), "payment"),
    (lambda b: b.update(payment="tok_success"), "payment"),
    (lambda b: b.update(payment={}), "payment"),
    (lambda b: b.update(payment={"token": 5}), "payment"),
])
def test_schema_violations_are_400(fresh, mutate, field):
    body = order("ord-bad")
    mutate(body)
    err = assert_error(fresh.post("/audit/v1/checkout", json=body), 400, "VALIDATION_ERROR")
    assert err["details"]["fields"] == [field]
    assert inventory(fresh, M)["stock"] == 1


def test_multiple_bad_fields_are_all_reported(fresh):
    err = assert_error(fresh.post("/audit/v1/checkout", json={"items": []}), 400, "VALIDATION_ERROR")
    assert err["details"]["fields"] == ["order_id", "customer_id", "items", "payment"]


def test_malformed_and_non_object_bodies(fresh):
    r = fresh.post("/audit/v1/checkout", content=b'{"order_id": ', headers={"content-type": "application/json"})
    assert assert_error(r, 400, "VALIDATION_ERROR")["details"]["fields"] == ["body"]
    assert assert_error(fresh.post("/audit/v1/checkout", json=[1]), 400, "VALIDATION_ERROR")["details"]["fields"] == ["body"]
    assert_error(fresh.post("/audit/v1/checkout"), 400, "VALIDATION_ERROR")


def test_missing_discount_code_key_means_no_discount(fresh):
    body = order("ord-nodisc")
    del body["discount_code"]
    r = fresh.post("/audit/v1/checkout", json=body)
    assert r.status_code == 201 and r.json()["discount_minor"] == 0


def test_validation_order_404_then_discount_400(fresh):
    r = fresh.post("/audit/v1/checkout", json=order("ord-404", items=[{"variant_id": "var-nope", "quantity": 1}],
                                                    discount="NOPE"))
    assert assert_error(r, 404, "VARIANT_NOT_FOUND")["details"] == {"variant_id": "var-nope"}
    for code in ("NOPE", "welcome10", ""):
        r = fresh.post("/audit/v1/checkout", json=order("ord-code", discount=code))
        assert_error(r, 400, "INVALID_DISCOUNT_CODE")
    # schema problems win over unknown variants
    r = fresh.post("/audit/v1/checkout", json=order("bad|id", items=[{"variant_id": "var-nope", "quantity": 1}]))
    assert_error(r, 400, "VALIDATION_ERROR")
    assert inventory(fresh, M) == {"variant_id": M, "stock": 1, "reserved": 0}


def test_inventory_unknown_variant(fresh):
    assert_error(fresh.get("/audit/v1/inventory/var-nope"), 404, "VARIANT_NOT_FOUND")
    assert_error(fresh.get("/audit/v1/inventory/bad%7Cid"), 404, "VARIANT_NOT_FOUND")
    assert inventory(fresh, L) == {"variant_id": L, "stock": 5, "reserved": 0}


@pytest.mark.parametrize("customer", [
    "cust\"'><script>alert(1)</script>",
    "x'; DROP TABLE st_orders;--",
    "عمر 😀 \\ \" \n tab\t",
])
def test_hostile_customer_id_is_stored_and_returned_as_plain_data(fresh, customer):
    r = fresh.post("/audit/v1/checkout", json=order("ord-xss", customer=customer))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["receipt"]["payload"]["customer_id"] == customer
    assert body["receipt"]["signature"] == sign("ord-xss", body["receipt"]["payload"], salt="test-salt")
    assert "ord-xss" in pdf_text(body)
    replay = fresh.post("/audit/v1/checkout", json=order("ord-xss", customer=customer))
    assert replay.status_code == 200 and replay.json() == body
    assert db.fetchone("SELECT customer_id FROM st_orders WHERE order_id = 'ord-xss'")["customer_id"] == customer


def test_unexpected_failure_after_capture_refunds_and_releases(fresh, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("pdf engine down")

    monkeypatch.setattr(receipt, "render", boom)
    with pytest.raises(RuntimeError):
        fresh.post("/audit/v1/checkout", json=order("ord-crash"))
    assert inventory(fresh, M) == {"variant_id": M, "stock": 1, "reserved": 0}
    assert db.fetchone("SELECT count(*) AS n FROM st_orders WHERE order_id = 'ord-crash'")["n"] == 0
    assert db.fetchone("SELECT count(*) AS n FROM st_refunds")["n"] == 1
    monkeypatch.undo()
    assert fresh.post("/audit/v1/checkout", json=order("ord-crash")).status_code == 201


def _slow_capture(monkeypatch, delay):
    original = gateway.capture

    def slow(*args, **kwargs):
        time.sleep(delay)
        return original(*args, **kwargs)

    monkeypatch.setattr(gateway, "capture", slow)


def test_identical_request_in_flight_waits_for_the_first_result(fresh, monkeypatch):
    _slow_capture(monkeypatch, 0.4)
    results = {}

    def first():
        results["first"] = fresh.post("/audit/v1/checkout", json=order("ord-race"))

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.15)
    second = fresh.post("/audit/v1/checkout", json=order("ord-race"))
    t.join(10)
    assert results["first"].status_code == 201
    assert second.status_code == 200 and second.json() == results["first"].json()
    assert inventory(fresh, M) == {"variant_id": M, "stock": 0, "reserved": 0}


def test_identical_request_in_flight_too_long_is_409(fresh, monkeypatch):
    _slow_capture(monkeypatch, 0.6)
    monkeypatch.setattr(service, "IN_FLIGHT_WAIT_S", 0.1)
    results = {}

    def first():
        results["first"] = fresh.post("/audit/v1/checkout", json=order("ord-slow"))

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.15)
    assert_error(fresh.post("/audit/v1/checkout", json=order("ord-slow")), 409, "DUPLICATE_ORDER_ID")
    t.join(10)
    assert results["first"].status_code == 201


def test_money_format():
    assert receipt.money(450000) == "PKR 4500.00"
    assert receipt.money(5) == "PKR 0.05"
    assert receipt.money(123456789) == "PKR 1234567.89"


def test_lone_surrogate_or_nul_text_is_400_not_500(fresh):
    raw = (rb'{"order_id": "ord-sur", "customer_id": "bad\ud800", "items": [{"variant_id": "var-jersey-001-M", '
           rb'"quantity": 1}], "discount_code": "\u0000", "payment": {"token": "tok_\udfff"}}')
    r = fresh.post("/audit/v1/checkout", content=raw, headers={"content-type": "application/json"})
    assert assert_error(r, 400, "VALIDATION_ERROR")["details"]["fields"] == ["customer_id", "discount_code", "payment"]


def test_catalog_lookup_of_unknown_products():
    from app.modules.store import catalog
    with db.tx() as conn:
        assert catalog.product_payload(conn, "prod-ghost") is None
        assert catalog.product_payload(conn, "bad|id") is None
        assert catalog.product_payload(conn, "prod-wallpaper-001")["variants"][0]["color"] is None


def test_longest_ids_stay_whole_in_the_pdf_text(fresh):
    wide_id = "W" * 64
    r = fresh.post("/audit/v1/checkout", json=order(wide_id, customer="M" * 512,
                                                    items=[{"variant_id": L, "quantity": 10}]))
    assert_error(r, 409, "OUT_OF_STOCK")
    r = fresh.post("/audit/v1/checkout", json=order(wide_id, customer="M" * 512))
    assert r.status_code == 201, r.text
    text = pdf_text(r.json())
    assert wide_id in text and "PKR 4500.00" in text
    mixed = "Ord_9.x:Y-" * 6 + "Zz-0"
    r = fresh.post("/audit/v1/checkout", json=order(mixed, items=[{"variant_id": L, "quantity": 1}]))
    assert mixed in pdf_text(r.json())
