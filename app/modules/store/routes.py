from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ...core import db, realtime
from ...core.auth import bearer_claims
from ...core.errors import ApiError, validation_error
from ...core.validate import is_id, json_body
from ..rbac.policy import authorize
from . import catalog, service

router = APIRouter()


@router.post("/audit/v1/checkout")
def checkout(body=Depends(json_body)):
    status, result = service.checkout(body)
    return JSONResponse(status_code=status, content=result)


@router.get("/audit/v1/inventory/{variant_id}")
def inventory(variant_id: str):
    return service.inventory(variant_id)


# ---- App-facing routes (/api/v1/*: Bearer token, rate limited) ----

@router.get("/api/v1/products")
def products(request: Request, category: str | None = None):
    claims = bearer_claims(request)
    with db.tx() as conn:
        found = catalog.list_products(conn, claims.get("tenant_id"))
    if category is not None:
        found = [p for p in found if p["category"] == category]
    return {"products": found}


@router.post("/api/v1/checkout")
def app_checkout(request: Request, body=Depends(json_body)):
    claims = bearer_claims(request)
    if isinstance(body, dict) and body.get("customer_id") is None:
        body = {**body, "customer_id": claims["sub"]}
    status, result = service.checkout(body)
    return JSONResponse(status_code=status, content=result)


@router.get("/api/v1/orders/{order_id}")
def order_detail(order_id: str, request: Request):
    authorize(request, "order.receipt.read", order_id)
    with db.tx() as conn:
        row = service.order_row(conn, order_id)
        history = service.order_history(conn, order_id)
    return {"order_id": row["order_id"], "customer_id": row["customer_id"], "status": row["status"],
            "items": row["items"], "total_minor": row["total_minor"], "currency": catalog.CURRENCY,
            "history": history}


@router.get("/api/v1/orders/{order_id}/receipt.pdf")
def receipt_pdf(order_id: str, request: Request):
    authorize(request, "order.receipt.read", order_id)
    with db.tx() as conn:
        row = service.order_row(conn, order_id)
    if row["pdf"] is None:
        raise ApiError(409, "RECEIPT_NOT_AVAILABLE", f"Order {order_id} has no receipt (it was not paid).",
                       {"order_id": order_id})
    return Response(content=bytes(row["pdf"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="receipt-{order_id}.pdf"'})


@router.post("/api/v1/orders/{order_id}/status")
def order_status(order_id: str, request: Request, body=Depends(json_body)):
    claims = authorize(request, "order.manage", order_id)
    to_status = body.get("status") if isinstance(body, dict) else None
    if to_status not in service.STATUSES:
        raise validation_error(["status"])
    return service.change_status(order_id, to_status, claims["sub"])


@router.get("/api/v1/matches/{match_id}/pinned-product")
def get_pinned(match_id: str, request: Request):
    bearer_claims(request)
    with db.tx() as conn:
        product = service.pinned_product(conn, match_id)
    return {"match_id": match_id, "product": product}


@router.post("/api/v1/matches/{match_id}/pinned-product")
def pin_product(match_id: str, request: Request, body=Depends(json_body)):
    claims = bearer_claims(request)
    if not isinstance(body, dict) or "product_id" not in body:
        raise validation_error(["product_id"])
    product_id = body["product_id"]
    if not is_id(match_id) or (product_id is not None and not isinstance(product_id, str)):
        raise validation_error(["match_id" if not is_id(match_id) else "product_id"])
    # Unpinning (product_id null) needs the same right over the product that is currently pinned.
    target = product_id
    if target is None:
        with db.tx() as conn:
            current = conn.execute("SELECT product_id FROM st_pins WHERE match_id = %s", (match_id,)).fetchone()
        target = current["product_id"] if current else None
    if target is not None:
        authorize(request, "product.pin", target)
    with db.tx() as conn:
        service.set_pin(conn, match_id, product_id, claims["sub"])
        product = service.pinned_product(conn, match_id)
    realtime.publish(f"match:{match_id}", {"type": "pinned_product", "product": product})
    return {"match_id": match_id, "product": product}
