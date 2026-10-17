"""Production routes used by the mobile app (/api/v1/*, rate limited). Graded writes go through /audit/v1/*."""
import io

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ...core import db, realtime
from ...core.auth import bearer_claims, token_for_user, verify_password
from ...core.errors import ApiError, not_found
from ...core.seed import user_by_email
from ...core.validate import Validator, json_body
from ..sports import get_sport
from .demo import optional_module

router = APIRouter()

PROFILE_FIELDS = ("user_id", "email", "display_name", "role", "tenant_id", "league_ids", "club_id", "player_id")


def current_claims(request: Request) -> dict:
    return bearer_claims(request)


def _forbidden():
    return ApiError(403, "FORBIDDEN", "You do not have access to this organisation's data.")


def _can_see(claims: dict, tenant_id: str | None) -> bool:
    return claims.get("role") == "super_admin" or (tenant_id is not None and claims.get("tenant_id") == tenant_id)


def _session(conn, user: dict) -> dict:
    tenant = None
    if user["tenant_id"]:
        tenant = conn.execute("SELECT tenant_id, name, branding FROM tenants WHERE tenant_id = %s",
                              (user["tenant_id"],)).fetchone()
    return {"user": {k: user[k] for k in PROFILE_FIELDS}, "tenant": tenant}


def _team_names(conn) -> dict:
    return {r["club_id"]: r["name"] for r in conn.execute("SELECT club_id, name FROM clubs").fetchall()}


def _league_row(conn, claims: dict, league_id: str) -> dict:
    row = conn.execute("SELECT * FROM tenant_leagues WHERE league_id = %s", (league_id,)).fetchone()
    if row is None:
        if claims.get("role") != "super_admin":
            raise not_found("LEAGUE_NOT_FOUND", f"League {league_id} does not exist.", {"league_id": league_id})
        return {"league_id": league_id, "tenant_id": None}
    if not _can_see(claims, row["tenant_id"]):
        raise _forbidden()
    return row


def _meta(conn, match_ids) -> dict:
    rows = conn.execute("SELECT * FROM app_fixture_meta WHERE match_id = ANY(%s)", (list(match_ids),)).fetchall()
    return {r["match_id"]: r for r in rows}


def _summary(p: dict, names: dict, meta: dict | None) -> dict:
    home, away = p["home"], p["away"]
    return {
        "match_id": p["match_id"],
        "round": meta["round"] if meta else None,
        "home": {"team_id": home["team_id"], "name": names.get(home["team_id"], home["team_id"])},
        "away": {"team_id": away["team_id"], "name": names.get(away["team_id"], away["team_id"])},
        "status": p["status"],
        "outcome": p["outcome"],
        "scheduled_at": meta["scheduled_at"] if meta else None,
        "venue": meta["venue"] if meta else None,
        "home_score": home["score"],
        "away_score": away["score"],
    }


@router.get("/api/v1/ping")
def ping():
    return {"pong": True}


@router.post("/api/v1/auth/login")
def login(body=Depends(json_body)):
    v = Validator(body)
    email = v.string("email", max_len=200)
    password = v.string("password", max_len=200)
    v.check()
    with db.tx() as conn:
        user = user_by_email(conn, email)
        if not user or not verify_password(password, user["password_hash"]):
            raise ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
        return {"token": token_for_user(user), **_session(conn, user)}


@router.get("/api/v1/me")
def me(claims=Depends(current_claims)):
    with db.tx() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = %s", (claims["sub"],)).fetchone()
        if user is None:
            raise ApiError(401, "UNAUTHENTICATED", "This account no longer exists. Please sign in again.")
        return _session(conn, user)


@router.get("/api/v1/leagues")
def leagues(claims=Depends(current_claims)):
    rows = db.fetchall("SELECT league_id, name, sport_id, season, tenant_id FROM tenant_leagues ORDER BY league_id")
    return {"leagues": [r for r in rows if _can_see(claims, r["tenant_id"])]}


@router.get("/api/v1/leagues/{league_id}/fixtures")
def league_fixtures(league_id: str, claims=Depends(current_claims)):
    scoring = optional_module("app.modules.scoring.service")
    with db.tx() as conn:
        _league_row(conn, claims, league_id)
        league = scoring.get_league(conn, league_id) if scoring else None
        if league is None:
            return {"league_id": league_id, "matches": []}
        projections = scoring.league_projections(conn, league_id)
        names = _team_names(conn)
        meta = _meta(conn, [p["match_id"] for p in projections])
    matches = [_summary(p, names, meta.get(p["match_id"])) for p in projections]
    matches.sort(key=lambda m: (m["round"] or 0, m["scheduled_at"] or ""))
    return {"league_id": league_id, "matches": matches}


@router.get("/api/v1/matches/{match_id}")
def match_detail(match_id: str, claims=Depends(current_claims)):
    scoring = optional_module("app.modules.scoring.service")
    if scoring is None:
        raise ApiError(503, "NOT_READY", "Live scores are not available yet.")
    store = optional_module("app.modules.store.service")
    with db.tx() as conn:
        proj = scoring.project(conn, match_id)
        if proj is None:
            raise not_found("MATCH_NOT_FOUND", f"Match {match_id} does not exist.", {"match_id": match_id})
        if proj.get("league_id"):
            _league_row(conn, claims, proj["league_id"])
        sport = get_sport(conn, proj["sport_id"]) or {}
        names = _team_names(conn)
        meta = _meta(conn, [match_id]).get(match_id)
        pinned = store.pinned_product(conn, match_id) if store is not None and hasattr(store, "pinned_product") else None
    return {
        **proj,
        "home_name": names.get(proj["home"]["team_id"], proj["home"]["team_id"]),
        "away_name": names.get(proj["away"]["team_id"], proj["away"]["team_id"]),
        "round": meta["round"] if meta else None,
        "scheduled_at": meta["scheduled_at"] if meta else None,
        "venue": meta["venue"] if meta else None,
        "scoring_model": sport.get("scoring_model"),
        "scoring_events": sport.get("scoring_events", {}),
        "pinned_product": pinned,
    }


def compute_league_standings(conn, league_id: str) -> dict | None:
    scoring = optional_module("app.modules.scoring.service")
    logic = optional_module("app.modules.standings.logic")
    if scoring is None or logic is None or not hasattr(logic, "compute_standings"):
        return None
    league = scoring.get_league(conn, league_id)
    if league is None:
        return None
    return logic.compute_standings(league, get_sport(conn, league["sport_id"]), scoring.league_projections(conn, league_id))


@router.get("/api/v1/leagues/{league_id}/standings")
def league_standings(league_id: str, claims=Depends(current_claims)):
    with db.tx() as conn:
        _league_row(conn, claims, league_id)
        body = compute_league_standings(conn, league_id)
        names = _team_names(conn)
    if body is None:
        return {"league_id": league_id, "qualification_spots": 0, "table": [], "h2h": {}, "team_names": {}}
    body["team_names"] = {row["team_id"]: names.get(row["team_id"], row["team_id"]) for row in body["table"]}
    return body


@router.get("/api/v1/clubs")
def clubs(claims=Depends(current_claims)):
    rows = db.fetchall("SELECT club_id, name, tenant_id, league_id FROM clubs ORDER BY club_id")
    return {"clubs": [r for r in rows if _can_see(claims, r["tenant_id"])]}


def club_roster(conn, club: dict) -> list[dict]:
    roster = optional_module("app.modules.roster.service")
    if roster is not None and hasattr(roster, "squad_members"):
        members = roster.squad_members(conn, club["league_id"], club["club_id"])
        if members is not None:
            return members
    rows = conn.execute("SELECT player_id, name, position, role, shirt_number FROM players WHERE club_id = %s "
                        "ORDER BY shirt_number", (club["club_id"],)).fetchall()
    return [{**r, "squad_id": club["club_id"]} for r in rows]


@router.get("/api/v1/clubs/{club_id}")
def club_detail(club_id: str, claims=Depends(current_claims)):
    with db.tx() as conn:
        club = conn.execute("SELECT * FROM clubs WHERE club_id = %s", (club_id,)).fetchone()
        if club is None:
            raise not_found("CLUB_NOT_FOUND", f"Club {club_id} does not exist.", {"club_id": club_id})
        if not _can_see(claims, club["tenant_id"]):
            raise _forbidden()
        return {**club, "roster": club_roster(conn, club)}


def logo_png(name: str, branding: dict) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=branding.get("primary_color", "#0B5FFF"))
    draw.ellipse((22, 22, size - 22, size - 22), outline=branding.get("secondary_color", "#FFFFFF"), width=10)
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    font = ImageFont.load_default(size=96)
    box = draw.textbbox((0, 0), initials, font=font)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
              initials, font=font, fill="#FFFFFF")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@router.get("/api/v1/tenants/{tenant_id}/logo.png")
def tenant_logo(tenant_id: str):
    row = db.fetchone("SELECT name, branding FROM tenants WHERE tenant_id = %s", (tenant_id,))
    if row is None:
        raise not_found("TENANT_NOT_FOUND", f"Tenant {tenant_id} does not exist.", {"tenant_id": tenant_id})
    return Response(content=logo_png(row["name"], row["branding"]), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.websocket("/api/v1/ws")
async def live(ws: WebSocket, topic: str = "*"):
    await ws.accept()
    realtime.subscribe(topic, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        realtime.unsubscribe(ws)
