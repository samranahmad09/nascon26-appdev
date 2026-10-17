"""Checkout, inventory, order lifecycle and pinned products (FR-2.3, NFR-3, Section 5.12).

Concurrency (NFR-3): an order is claimed and its stock reserved in ONE short transaction that locks the
variant rows (in variant_id order, so multi-item orders cannot deadlock) and decrements stock with a
conditional UPDATE. Either every line is reserved or the transaction rolls back and nothing is. The mock
gateway runs outside that transaction; a second short transaction then commits (reserved goes back down)
or releases (stock goes back up). The global app lock is never used here.
"""
import base64
import hashlib
import time

from ...core import db, queue
from ...core.db import Jsonb
from ...core.errors import ApiError, conflict, not_found, validation_error
from ...core.signing import canonical, sign
from ...core.validate import is_id, is_int, utc_now
from . import catalog, gateway, receipt

STATUSES = ("pending", "paid", "packed", "shipped", "delivered", "cancelled", "refunded")
# Allowed manual transitions after payment (pending -> paid / cancelled happen inside checkout).
TRANSITIONS = {
    "paid": ("packed", "refunded"),
    "packed": ("shipped", "refunded"),
    "shipped": ("delivered",),
    "delivered": ("refunded",),
}
MAX_TEXT = 512
IN_FLIGHT_WAIT_S = 4.0


def _clean_text(value) -> bool:
    """Free text must survive UTF-8 encoding and Postgres TEXT (no NUL, no lone surrogates)."""
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_checkout(body) -> dict:
    if not isinstance(body, dict):
        raise validation_error(["body"], "Request body must be a JSON object.")
    bad: list[str] = []

    order_id = body.get("order_id")
    if not is_id(order_id):
        bad.append("order_id")
    customer_id = body.get("customer_id")
    if not (_clean_text(customer_id) and 1 <= len(customer_id) <= MAX_TEXT):
        bad.append("customer_id")

    items = body.get("items")
    norm_items = []
    if isinstance(items, list) and 1 <= len(items) <= 10:
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                break
            vid, qty = item.get("variant_id"), item.get("quantity")
            if not is_id(vid) or vid in seen or not is_int(qty) or not 1 <= qty <= 10:
                break
            seen.add(vid)
            norm_items.append({"variant_id": vid, "quantity": qty})
    if not isinstance(items, list) or len(norm_items) != len(items) or not norm_items:
        bad.append("items")

    discount_code = body.get("discount_code")
    if discount_code is not None and not (_clean_text(discount_code) and len(discount_code) <= MAX_TEXT):
        bad.append("discount_code")

    payment = body.get("payment")
    token = payment.get("token") if isinstance(payment, dict) else None
    if not (_clean_text(token) and 1 <= len(token) <= MAX_TEXT):
        bad.append("payment")

    if bad:
        raise validation_error(bad)
    return {"order_id": order_id, "customer_id": customer_id, "items": norm_items,
            "discount_code": discount_code, "token": token}


def _fingerprint(req: dict) -> str:
    return hashlib.sha256(canonical(req).encode("utf-8")).hexdigest()


def _emit(order_id: str, from_status, to_status: str, at: str, reason: str, extra: dict | None = None) -> None:
    queue.emit("order_status_changed", {"order_id": order_id, "from_status": from_status, "to_status": to_status,
                                        "reason": reason, "at": at, **(extra or {})})


def _record_event(conn, order_id: str, from_status, to_status: str, reason: str, at: str) -> None:
    conn.execute("INSERT INTO st_order_events (order_id, from_status, to_status, reason, at) VALUES (%s, %s, %s, %s, %s)",
                 (order_id, from_status, to_status, reason, at))


def _price(req: dict) -> dict:
    """Step 1 after schema validation: variants must exist (404), then the discount code (400)."""
    with db.tx() as conn:
        variants = catalog.variants_by_id(conn, [i["variant_id"] for i in req["items"]])
        for item in req["items"]:
            if item["variant_id"] not in variants:
                raise not_found("VARIANT_NOT_FOUND", f"Variant {item['variant_id']} does not exist.",
                                {"variant_id": item["variant_id"]})
        percent = 0
        if req["discount_code"] is not None:
            percent = catalog.discount_percent(conn, req["discount_code"])
            if percent is None:
                raise ApiError(400, "INVALID_DISCOUNT_CODE", "Unknown discount code.",
                               {"discount_code": req["discount_code"]})
    lines = [{"variant_id": i["variant_id"], "quantity": i["quantity"],
              "unit_price_minor": int(variants[i["variant_id"]]["unit_price_minor"])} for i in req["items"]]
    subtotal = sum(line["unit_price_minor"] * line["quantity"] for line in lines)
    discount = subtotal * percent // 100
    return {"lines": lines, "subtotal": subtotal, "discount": discount, "total": subtotal - discount,
            "tenant_id": variants[req["items"][0]["variant_id"]]["tenant_id"],
            "names": {vid: v["name"] for vid, v in variants.items()}}


def _replay(row: dict, fingerprint: str, order_id: str):
    if row["fingerprint"] != fingerprint:
        raise conflict("DUPLICATE_ORDER_ID", f"Order {order_id} already exists with a different request body.",
                       {"order_id": order_id})
    if row["http_status"] is not None and row["http_status"] >= 400:
        err = row["response"]["error"]
        raise ApiError(row["http_status"], err["code"], err["message"], err["details"])
    return 200, row["response"]


def _claim_and_reserve(req: dict, priced: dict, fingerprint: str):
    """One transaction: claim the order id, then reserve every line or nothing. Returns a stored row on conflict."""
    now = utc_now()
    with db.tx() as conn:
        claimed = conn.execute(
            "INSERT INTO st_orders (order_id, fingerprint, customer_id, tenant_id, status, items, total_minor, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s) "
            "ON CONFLICT (order_id) DO NOTHING RETURNING order_id",
            (req["order_id"], fingerprint, req["customer_id"], priced["tenant_id"], Jsonb(priced["lines"]),
             priced["total"], now, now)).fetchone()
        if claimed is None:
            return conn.execute("SELECT fingerprint, status, http_status, response FROM st_orders WHERE order_id = %s",
                                (req["order_id"],)).fetchone()
        wanted = {line["variant_id"]: line["quantity"] for line in priced["lines"]}
        locked = conn.execute("SELECT variant_id, stock FROM st_variants WHERE variant_id = ANY(%s) "
                              "ORDER BY variant_id FOR UPDATE", (sorted(wanted),)).fetchall()
        stock = {r["variant_id"]: r["stock"] for r in locked}
        for line in priced["lines"]:
            if stock.get(line["variant_id"], 0) < line["quantity"]:
                raise conflict("OUT_OF_STOCK", f"Variant {line['variant_id']} has no stock left.",
                               {"variant_id": line["variant_id"]})
        for vid in sorted(wanted):
            done = conn.execute("UPDATE st_variants SET stock = stock - %s, reserved = reserved + %s "
                                "WHERE variant_id = %s AND stock >= %s RETURNING stock",
                                (wanted[vid], wanted[vid], vid, wanted[vid])).fetchone()
            if done is None:  # cannot happen under the row lock; kept as a hard guard against overselling
                raise conflict("OUT_OF_STOCK", f"Variant {vid} has no stock left.", {"variant_id": vid})
        _record_event(conn, req["order_id"], None, "pending", "stock_reserved", now)
    return None


def _settle(order_id: str, lines: list, *, paid: bool, http_status: int, response: dict, reason: str,
            pdf: bytes | None = None, payment: dict | None = None) -> str:
    """Second transaction: commit (paid) or release (cancelled) the reservation."""
    now = utc_now()
    to_status = "paid" if paid else "cancelled"
    with db.tx() as conn:
        for line in sorted(lines, key=lambda x: x["variant_id"]):
            if paid:
                conn.execute("UPDATE st_variants SET reserved = reserved - %s WHERE variant_id = %s",
                             (line["quantity"], line["variant_id"]))
            else:
                conn.execute("UPDATE st_variants SET stock = stock + %s, reserved = reserved - %s WHERE variant_id = %s",
                             (line["quantity"], line["quantity"], line["variant_id"]))
        conn.execute("UPDATE st_orders SET status = %s, http_status = %s, response = %s, pdf = %s, "
                     "authorization_id = %s, capture_id = %s, updated_at = %s WHERE order_id = %s",
                     (to_status, http_status, Jsonb(response), pdf, (payment or {}).get("authorization_id"),
                      (payment or {}).get("capture_id"), now, order_id))
        _record_event(conn, order_id, "pending", to_status, reason, now)
    return now


def _release_after_crash(order_id: str, lines: list) -> None:
    with db.tx() as conn:
        for line in sorted(lines, key=lambda x: x["variant_id"]):
            conn.execute("UPDATE st_variants SET stock = stock + %s, reserved = reserved - %s WHERE variant_id = %s",
                         (line["quantity"], line["quantity"], line["variant_id"]))
        conn.execute("DELETE FROM st_orders WHERE order_id = %s", (order_id,))
        _record_event(conn, order_id, "pending", "cancelled", "internal_error", utc_now())


def _fail(req: dict, priced: dict, status: int, code: str, message: str, reason: str, details: dict) -> ApiError:
    body = {"error": {"code": code, "message": message, "details": details}}
    at = _settle(req["order_id"], priced["lines"], paid=False, http_status=status, response=body, reason=reason)
    _emit(req["order_id"], "pending", "cancelled", at, reason)
    return ApiError(status, code, message, details)


def _pay_and_receipt(req: dict, priced: dict) -> dict:
    order_id = req["order_id"]
    try:
        auth_id = gateway.authorize(req["token"], order_id, priced["total"])
    except gateway.PaymentDeclined:
        raise _fail(req, priced, 402, "PAYMENT_DECLINED", "The payment was declined.", "payment_declined",
                    {"order_id": order_id})
    try:
        cap_id = gateway.capture(req["token"], auth_id, priced["total"])
    except gateway.CaptureFailed:
        gateway.void(auth_id)
        raise _fail(req, priced, 402, "PAYMENT_CAPTURE_FAILED", "The payment could not be captured; the "
                    "authorization was voided.", "capture_failed", {"order_id": order_id, "authorization_id": auth_id})

    payment = {"authorization_id": auth_id, "capture_id": cap_id, "state": "captured"}
    try:
        payload = {"order_id": order_id, "customer_id": req["customer_id"],
                   "items": [dict(line) for line in priced["lines"]],
                   "subtotal_minor": priced["subtotal"], "discount_minor": priced["discount"],
                   "total_minor": priced["total"], "currency": catalog.CURRENCY, "paid_at": utc_now()}
        signature = sign(order_id, payload)
        pdf = receipt.render(payload, priced["names"], signature, payment)
        response = {
            "order_id": order_id, "status": "paid", "currency": catalog.CURRENCY,
            "items": [dict(line) for line in priced["lines"]],
            "subtotal_minor": priced["subtotal"], "discount_minor": priced["discount"],
            "total_minor": priced["total"],
            "payment": payment,
            "receipt": {"payload": payload, "signature": signature,
                        "pdf_base64": base64.b64encode(pdf).decode("ascii")},
        }
        at = _settle(order_id, priced["lines"], paid=True, http_status=201, response=response,
                     reason="payment_captured", pdf=pdf, payment=payment)
    except Exception:
        gateway.refund(cap_id, priced["total"])  # never keep the money for an order we could not record
        raise
    _emit(order_id, "pending", "paid", at, "payment_captured", {"total_minor": priced["total"]})
    return response


def checkout(body) -> tuple[int, dict]:
    req = parse_checkout(body)
    priced = _price(req)
    fingerprint = _fingerprint(req)
    deadline = time.monotonic() + IN_FLIGHT_WAIT_S
    while True:
        existing = _claim_and_reserve(req, priced, fingerprint)
        if existing is None:
            break
        if existing["status"] != "pending" or existing["fingerprint"] != fingerprint:
            return _replay(existing, fingerprint, req["order_id"])
        # The identical request is still in flight: wait for its outcome instead of reserving twice.
        if time.monotonic() > deadline:
            raise conflict("DUPLICATE_ORDER_ID", f"Order {req['order_id']} is still being processed.",
                           {"order_id": req["order_id"]})
        time.sleep(0.05)
    _emit(req["order_id"], None, "pending", utc_now(), "stock_reserved")
    try:
        return 201, _pay_and_receipt(req, priced)
    except ApiError:
        raise
    except Exception:
        _release_after_crash(req["order_id"], priced["lines"])
        raise


def inventory(variant_id: str) -> dict:
    row = None
    if is_id(variant_id):
        row = db.fetchone("SELECT variant_id, stock, reserved FROM st_variants WHERE variant_id = %s", (variant_id,))
    if row is None:
        raise not_found("VARIANT_NOT_FOUND", f"Variant {variant_id} does not exist.", {"variant_id": variant_id})
    return {"variant_id": row["variant_id"], "stock": row["stock"], "reserved": row["reserved"]}


def order_row(conn, order_id) -> dict | None:
    if not is_id(order_id):
        return None
    return conn.execute("SELECT order_id, customer_id, tenant_id, status, items, total_minor, capture_id, pdf "
                        "FROM st_orders WHERE order_id = %s", (order_id,)).fetchone()


def order_history(conn, order_id: str) -> list[dict]:
    return conn.execute("SELECT from_status, to_status, reason, at FROM st_order_events WHERE order_id = %s "
                        "ORDER BY event_seq", (order_id,)).fetchall()


def change_status(order_id: str, to_status: str, actor: str) -> dict:
    """Manual lifecycle after payment: paid -> packed -> shipped -> delivered, refund from any paid state."""
    with db.tx() as conn:
        row = conn.execute("SELECT status, items, total_minor, capture_id FROM st_orders WHERE order_id = %s FOR UPDATE",
                           (order_id,)).fetchone()
        if to_status not in TRANSITIONS.get(row["status"], ()):
            raise conflict("ILLEGAL_TRANSITION", f"Order {order_id} cannot move from {row['status']} to {to_status}.",
                           {"order_id": order_id, "from_status": row["status"], "to_status": to_status})
        refund_id = None
        if to_status == "refunded":
            refund_id = gateway.refund(row["capture_id"], row["total_minor"])
            for line in sorted(row["items"], key=lambda x: x["variant_id"]):
                conn.execute("UPDATE st_variants SET stock = stock + %s WHERE variant_id = %s",
                             (line["quantity"], line["variant_id"]))
        now = utc_now()
        conn.execute("UPDATE st_orders SET status = %s, updated_at = %s WHERE order_id = %s", (to_status, now, order_id))
        _record_event(conn, order_id, row["status"], to_status, f"manual:{actor}", now)
    _emit(order_id, row["status"], to_status, now, f"manual:{actor}", {"refund_id": refund_id} if refund_id else None)
    return {"order_id": order_id, "status": to_status, "previous_status": row["status"], "refund_id": refund_id,
            "updated_at": now}


def pinned_product(conn, match_id: str) -> dict | None:
    """Product pinned to a match page, in the /api/v1/products shape, or None."""
    if not is_id(match_id):
        return None
    row = conn.execute("SELECT product_id FROM st_pins WHERE match_id = %s", (match_id,)).fetchone()
    return catalog.product_payload(conn, row["product_id"]) if row else None


def set_pin(conn, match_id: str, product_id: str | None, actor: str) -> None:
    if product_id is None:
        conn.execute("DELETE FROM st_pins WHERE match_id = %s", (match_id,))
        return
    conn.execute("INSERT INTO st_pins (match_id, product_id, pinned_by, pinned_at) VALUES (%s, %s, %s, %s) "
                 "ON CONFLICT (match_id) DO UPDATE SET product_id = EXCLUDED.product_id, "
                 "pinned_by = EXCLUDED.pinned_by, pinned_at = EXCLUDED.pinned_at",
                 (match_id, product_id, actor, utc_now()))
