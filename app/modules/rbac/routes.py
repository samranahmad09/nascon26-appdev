"""Section 5.13: the RBAC matrix route and the seven authorization probes.

Probes only authorize: no body parsing, no validation, no mutation. Every decision goes through POLICY.
"""
from fastapi import APIRouter, Request

from ...core import db
from ...core.auth import token_for_user
from .policy import authorize

router = APIRouter()

ROLE_USERS = {
    "super_admin": "user-admin",
    "league_organizer": "user-organizer",
    "club_manager": "user-manager",
    "player": "user-player",
    "viewer": "user-viewer",
}

CONTEXT = {
    "tenant_id": "tenant-alpha", "foreign_tenant_id": "tenant-beta",
    "own_league_id": "league-alpha-1", "other_league_id": "league-alpha-2", "foreign_league_id": "league-beta-1",
    "own_club_id": "club-alpha-1", "other_club_id": "club-alpha-2", "foreign_club_id": "club-beta-1",
    "own_player_id": "player-alpha-1", "other_player_id": "player-alpha-2", "foreign_player_id": "player-beta-1",
}

ALLOWED = {"authorized": True}


@router.post("/audit/v1/rbac-matrix")
def rbac_matrix():
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM users WHERE user_id = ANY(%s)", (list(ROLE_USERS.values()),)).fetchall()
    by_id = {r["user_id"]: r for r in rows}
    return {"tokens": {role: token_for_user(by_id[uid]) for role, uid in ROLE_USERS.items()}, "context": dict(CONTEXT)}


@router.post("/audit/v1/rbac/tenants")
def probe_create_tenant(request: Request):
    authorize(request, "tenant.create")
    return ALLOWED


@router.post("/audit/v1/rbac/leagues/{league_id}/fixtures")
def probe_league_fixtures(league_id: str, request: Request):
    authorize(request, "league.fixtures.write", league_id)
    return ALLOWED


@router.post("/audit/v1/rbac/clubs/{club_id}/roster")
def probe_club_roster(club_id: str, request: Request):
    authorize(request, "club.roster.write", club_id)
    return ALLOWED


@router.post("/audit/v1/rbac/clubs/{club_id}/products")
def probe_club_products(club_id: str, request: Request):
    authorize(request, "club.products.write", club_id)
    return ALLOWED


@router.patch("/audit/v1/rbac/players/{player_id}")
def probe_update_player(player_id: str, request: Request):
    authorize(request, "player.update", player_id)
    return ALLOWED


@router.get("/audit/v1/rbac/clubs/{club_id}")
def probe_read_club(club_id: str, request: Request):
    authorize(request, "club.read", club_id)
    return ALLOWED


@router.get("/audit/v1/rbac/platform/config")
def probe_platform_config(request: Request):
    authorize(request, "platform.config.read")
    return ALLOWED
