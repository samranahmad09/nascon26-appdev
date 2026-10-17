"""Policy-as-code RBAC (FR-5.1, NFR-5, Section 5.13).

POLICY is the single source of truth: action -> (resource kind, {role: scope}). A role missing from an
action's grants is denied. Scopes are evaluated only against token claims plus the stored owner/tenant of
the resource, so tenant isolation is absolute: every non-"any" scope first requires the same tenant.
Authorization runs before anything else about a request; an id that does not exist cannot belong to the
caller, so non-super-admins get 403 for it and only a platform-wide grant can reach the 404.
"""
import base64
import binascii

from fastapi import Request

from ...core import db
from ...core.auth import bearer_claims
from ...core.errors import ApiError, not_found
from ...core.validate import is_id

ROLES = ("super_admin", "league_organizer", "club_manager", "player", "viewer")
TENANT_READERS = {role: "tenant" for role in ROLES if role != "super_admin"}

POLICY: dict[str, tuple[str | None, dict[str, str]]] = {
    # Section 5.13 probe actions (P1-P7)
    "tenant.create": (None, {"super_admin": "any"}),
    "league.fixtures.write": ("league", {"super_admin": "any", "league_organizer": "own_league"}),
    "club.roster.write": ("club", {"super_admin": "any", "club_manager": "own_club"}),
    "club.products.write": ("club", {"super_admin": "any", "club_manager": "own_club"}),
    "player.update": ("player", {"super_admin": "any", "club_manager": "own_club", "player": "self"}),
    "club.read": ("club", {"super_admin": "any", **TENANT_READERS}),
    "platform.config.read": (None, {"super_admin": "any"}),
    # Application actions
    "product.pin": ("product", {"super_admin": "any", "league_organizer": "tenant", "club_manager": "own_club"}),
    "order.manage": ("order", {"super_admin": "any", "club_manager": "tenant"}),
    "order.receipt.read": ("order", {"super_admin": "any", **{role: "customer" for role in TENANT_READERS}}),
}


def _same_tenant(claims: dict, res: dict) -> bool:
    return claims.get("tenant_id") is not None and res.get("tenant_id") == claims.get("tenant_id")


SCOPES = {
    "tenant": _same_tenant,
    "own_league": lambda c, r: _same_tenant(c, r) and r.get("league_id") in (c.get("league_ids") or []),
    "own_club": lambda c, r: _same_tenant(c, r) and c.get("club_id") is not None and r.get("club_id") == c.get("club_id"),
    "self": lambda c, r: _same_tenant(c, r) and c.get("player_id") is not None and r.get("player_id") == c.get("player_id"),
    "customer": lambda c, r: r.get("customer_id") == c.get("sub"),
}

LOADERS = {
    "league": ("SELECT league_id, tenant_id FROM tenant_leagues WHERE league_id = %s", "LEAGUE_NOT_FOUND"),
    "club": ("SELECT club_id, tenant_id FROM clubs WHERE club_id = %s", "CLUB_NOT_FOUND"),
    "player": ("SELECT player_id, tenant_id, club_id FROM players WHERE player_id = %s", "PLAYER_NOT_FOUND"),
    "product": ("SELECT product_id, tenant_id, club_id FROM st_products WHERE product_id = %s", "PRODUCT_NOT_FOUND"),
    "order": ("SELECT order_id, tenant_id, customer_id FROM st_orders WHERE order_id = %s", "ORDER_NOT_FOUND"),
}


def is_allowed(claims: dict, action: str, resource: dict | None) -> bool:
    _, grants = POLICY[action]
    scope = grants.get(claims.get("role"))
    if scope is None:
        return False
    if scope == "any":
        return True
    return resource is not None and SCOPES[scope](claims, resource)


def load_resource(kind: str, resource_id) -> dict | None:
    if not is_id(resource_id):
        return None
    return db.fetchone(LOADERS[kind][0], (resource_id,))


def _canonical_b64url(segment: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        return False
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == segment


def claims_of(request: Request) -> dict:
    """bearer_claims plus a strict encoding check: base64 decoders ignore the spare low bits of the last
    character, so a signature altered only there would otherwise still verify."""
    _, _, token = request.headers.get("authorization", "").partition(" ")
    segments = token.strip().split(".")
    if len(segments) == 3 and not all(_canonical_b64url(s) for s in segments):
        raise ApiError(401, "UNAUTHENTICATED", "Token is missing, invalid or expired.")
    return bearer_claims(request)


def authorize(request: Request, action: str, resource_id: str | None = None) -> dict:
    """401 for a bad token, 403 when the policy denies, 404 only for a missing id behind an "any" grant."""
    claims = claims_of(request)
    kind, _ = POLICY[action]
    resource = load_resource(kind, resource_id) if kind else None
    if not is_allowed(claims, action, resource):
        raise ApiError(403, "FORBIDDEN", "You are not allowed to perform this action.", {"action": action})
    if kind and resource is None:
        raise not_found(LOADERS[kind][1], f"{kind.capitalize()} {resource_id} does not exist.", {f"{kind}_id": resource_id})
    return claims
