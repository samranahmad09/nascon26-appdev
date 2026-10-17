import pytest

from app.core import db
from app.core.middleware import RateLimit
from app.modules.store import routes as store_routes
from app.modules.store import service

M, L = "var-jersey-001-M", "var-jersey-001-L"


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


@pytest.fixture
def tokens(fresh):
    return fresh.post("/audit/v1/rbac-matrix").json()["tokens"]


def h(tokens, role):
    return {"Authorization": f"Bearer {tokens[role]}"}


def app_order(order_id, variant=L, token="tok_success", qty=1):
    return {"order_id": order_id, "items": [{"variant_id": variant, "quantity": qty}], "discount_code": None,
            "payment": {"token": token}}


def test_products_are_tenant_scoped_and_use_scheduled_prices(fresh, tokens):
    assert fresh.get("/api/v1/products").status_code == 401
    alpha = fresh.get("/api/v1/products", headers=h(tokens, "viewer")).json()["products"]
    assert [p["product_id"] for p in alpha] == ["prod-jersey-001", "prod-cap-001", "prod-scarf-001", "prod-wallpaper-001"]
    jersey = alpha[0]
    assert set(jersey) == {"product_id", "name", "category", "description", "variants"}
    assert jersey["category"] == "apparel"
    assert jersey["variants"] == [
        {"variant_id": M, "size": "M", "color": "blue", "price_minor": 450000, "currency": "PKR", "stock": 1},
        {"variant_id": L, "size": "L", "color": "blue", "price_minor": 450000, "currency": "PKR", "stock": 5},
    ]
    prices = {v["variant_id"]: v["price_minor"] for p in alpha for v in p["variants"]}
    assert prices["var-cap-001-OS"] == 120000          # scheduled change already effective
    assert prices["var-scarf-001-RED"] == 200000       # scheduled change still in the future
    assert all(v["stock"] >= 20 for p in alpha[1:] for v in p["variants"])
    everything = fresh.get("/api/v1/products", headers=h(tokens, "super_admin")).json()["products"]
    assert len(everything) == 5 and everything[-1]["product_id"] == "prod-beta-jersey-001"
    digital = fresh.get("/api/v1/products?category=digital", headers=h(tokens, "player")).json()["products"]
    assert [p["product_id"] for p in digital] == ["prod-wallpaper-001"]


def test_scheduled_price_is_charged_at_checkout(fresh):
    body = {"order_id": "ord-cap", "customer_id": "c", "items": [{"variant_id": "var-cap-001-OS", "quantity": 2}],
            "discount_code": "WELCOME10", "payment": {"token": "tok_success"}}
    r = fresh.post("/audit/v1/checkout", json=body)
    assert r.status_code == 201
    assert r.json()["items"][0]["unit_price_minor"] == 120000
    assert r.json()["total_minor"] == 216000


def test_app_checkout_receipt_and_order_detail(fresh, tokens):
    assert fresh.post("/api/v1/checkout", json=app_order("app-1")).status_code == 401
    r = fresh.post("/api/v1/checkout", json=app_order("app-1"), headers=h(tokens, "player"))
    assert r.status_code == 201, r.text
    assert r.json()["receipt"]["payload"]["customer_id"] == "user-player"

    pdf = fresh.get("/api/v1/orders/app-1/receipt.pdf", headers=h(tokens, "player"))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert fresh.get("/api/v1/orders/app-1/receipt.pdf", headers=h(tokens, "super_admin")).status_code == 200
    assert fresh.get("/api/v1/orders/app-1/receipt.pdf", headers=h(tokens, "viewer")).status_code == 403
    assert fresh.get("/api/v1/orders/app-1/receipt.pdf").status_code == 401
    missing = fresh.get("/api/v1/orders/nope/receipt.pdf", headers=h(tokens, "super_admin"))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
    assert fresh.get("/api/v1/orders/nope/receipt.pdf", headers=h(tokens, "player")).status_code == 403

    detail = fresh.get("/api/v1/orders/app-1", headers=h(tokens, "player")).json()
    assert detail["status"] == "paid" and detail["customer_id"] == "user-player"
    assert [(e["from_status"], e["to_status"]) for e in detail["history"]] == [(None, "pending"), ("pending", "paid")]

    # an explicit customer_id is kept as sent
    r = fresh.post("/api/v1/checkout", json={**app_order("app-2"), "customer_id": "cust-explicit"},
                   headers=h(tokens, "player"))
    assert r.json()["receipt"]["payload"]["customer_id"] == "cust-explicit"
    assert fresh.post("/api/v1/checkout", json=[], headers=h(tokens, "player")).status_code == 400


def test_declined_order_has_no_receipt(fresh, tokens):
    r = fresh.post("/api/v1/checkout", json=app_order("app-dec", token="tok_declined"), headers=h(tokens, "player"))
    assert r.status_code == 402
    pdf = fresh.get("/api/v1/orders/app-dec/receipt.pdf", headers=h(tokens, "player"))
    assert pdf.status_code == 409 and pdf.json()["error"]["code"] == "RECEIPT_NOT_AVAILABLE"
    assert fresh.get("/api/v1/orders/app-dec", headers=h(tokens, "player")).json()["status"] == "cancelled"


def test_order_lifecycle_transitions_emit_events_and_refund_restocks(fresh, tokens, monkeypatch):
    events = []
    monkeypatch.setattr(service.queue, "emit", lambda t, d: events.append(d))
    assert fresh.post("/api/v1/checkout", json=app_order("app-life", qty=2), headers=h(tokens, "player")).status_code == 201
    assert fresh.get(f"/audit/v1/inventory/{L}").json()["stock"] == 3

    url = "/api/v1/orders/app-life/status"
    assert fresh.post(url, json={"status": "packed"}, headers=h(tokens, "viewer")).status_code == 403
    assert fresh.post(url, json={"status": "packed"}, headers=h(tokens, "player")).status_code == 403
    bad = fresh.post(url, json={"status": "lost"}, headers=h(tokens, "club_manager"))
    assert bad.status_code == 400 and bad.json()["error"]["details"]["fields"] == ["status"]
    assert fresh.post(url, json=None, headers=h(tokens, "club_manager")).status_code == 400
    illegal = fresh.post(url, json={"status": "delivered"}, headers=h(tokens, "club_manager"))
    assert illegal.status_code == 409 and illegal.json()["error"]["code"] == "ILLEGAL_TRANSITION"

    for status in ("packed", "shipped", "delivered"):
        r = fresh.post(url, json={"status": status}, headers=h(tokens, "club_manager"))
        assert r.status_code == 200 and r.json()["status"] == status
    r = fresh.post(url, json={"status": "refunded"}, headers=h(tokens, "super_admin"))
    assert r.status_code == 200 and r.json()["refund_id"] == "ref_1"
    assert fresh.get(f"/audit/v1/inventory/{L}").json() == {"variant_id": L, "stock": 5, "reserved": 0}
    again = fresh.post(url, json={"status": "refunded"}, headers=h(tokens, "super_admin"))
    assert again.status_code == 409
    assert [e["to_status"] for e in events] == ["pending", "paid", "packed", "shipped", "delivered", "refunded"]
    assert set(service.STATUSES) >= {e["to_status"] for e in events}
    history = db.fetchall("SELECT to_status FROM st_order_events WHERE order_id = 'app-life' ORDER BY event_seq")
    assert [row["to_status"] for row in history] == ["pending", "paid", "packed", "shipped", "delivered", "refunded"]


def test_beta_manager_cannot_manage_alpha_orders(fresh, tokens):
    from app.core.auth import issue_token
    beta_manager = issue_token({"sub": "beta-mgr", "role": "club_manager", "tenant_id": "tenant-beta",
                                "league_ids": [], "club_id": "club-beta-1", "player_id": None})
    assert fresh.post("/api/v1/checkout", json=app_order("app-x"), headers=h(tokens, "player")).status_code == 201
    r = fresh.post("/api/v1/orders/app-x/status", json={"status": "packed"},
                   headers={"Authorization": f"Bearer {beta_manager}"})
    assert r.status_code == 403


def test_pinned_product_showcase(fresh, tokens, monkeypatch):
    published = []
    monkeypatch.setattr(store_routes.realtime, "publish", lambda topic, msg: published.append((topic, msg)))
    url = "/api/v1/matches/m-final/pinned-product"
    assert fresh.get(url).status_code == 401
    assert fresh.get(url, headers=h(tokens, "viewer")).json() == {"match_id": "m-final", "product": None}

    r = fresh.post(url, json={"product_id": "prod-jersey-001"}, headers=h(tokens, "league_organizer"))
    assert r.status_code == 200, r.text
    product = r.json()["product"]
    assert product["product_id"] == "prod-jersey-001" and len(product["variants"]) == 2
    assert published == [("match:m-final", {"type": "pinned_product", "product": product})]
    assert fresh.get(url, headers=h(tokens, "viewer")).json() == {"match_id": "m-final", "product": product}
    with db.tx() as conn:
        assert service.pinned_product(conn, "m-final") == product
        assert service.pinned_product(conn, "bad|id") is None

    assert fresh.post(url, json={"product_id": "prod-cap-001"}, headers=h(tokens, "player")).status_code == 403
    assert fresh.post(url, json={"product_id": "prod-scarf-001"}, headers=h(tokens, "club_manager")).status_code == 403
    assert fresh.post(url, json={"product_id": "prod-beta-jersey-001"},
                      headers=h(tokens, "league_organizer")).status_code == 403
    assert fresh.post(url, json={"product_id": "prod-ghost"}, headers=h(tokens, "league_organizer")).status_code == 403
    ghost = fresh.post(url, json={"product_id": "prod-ghost"}, headers=h(tokens, "super_admin"))
    assert ghost.status_code == 404 and ghost.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
    r = fresh.post(url, json={"product_id": "prod-cap-001"}, headers=h(tokens, "club_manager"))
    assert r.status_code == 200 and r.json()["product"]["product_id"] == "prod-cap-001"

    assert fresh.post(url, json={"product_id": None}, headers=h(tokens, "viewer")).status_code == 403
    r = fresh.post(url, json={"product_id": None}, headers=h(tokens, "club_manager"))
    assert r.status_code == 200 and r.json()["product"] is None
    assert published[-1] == ("match:m-final", {"type": "pinned_product", "product": None})
    assert fresh.post(url, json={"product_id": None}, headers=h(tokens, "viewer")).json()["product"] is None

    for body, field in (({}, "product_id"), ([], "product_id"), ({"product_id": 5}, "product_id")):
        r = fresh.post(url, json=body, headers=h(tokens, "super_admin"))
        assert r.status_code == 400 and r.json()["error"]["details"]["fields"] == [field]
    r = fresh.post("/api/v1/matches/bad%7Cid/pinned-product", json={"product_id": "prod-cap-001"},
                   headers=h(tokens, "super_admin"))
    assert r.status_code == 400 and r.json()["error"]["details"]["fields"] == ["match_id"]
