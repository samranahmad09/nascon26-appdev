"""FR-3.1: public athlete identity and a concurrency-safe follow graph.

Follower counts are always COUNT(*) over the (follower_id, athlete_id) primary key, so simultaneous
follow/unfollow bursts can never drift a stored counter: each pair resolves idempotently."""
from fastapi import APIRouter, Depends, Request

from ...core import db
from ...core.auth import bearer_claims
from ...core.errors import not_found
from ...core.registry import register
from ...core.validate import utc_now

router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS so_follows (
    follower_id TEXT NOT NULL, athlete_id TEXT NOT NULL, followed_at TEXT NOT NULL,
    PRIMARY KEY (follower_id, athlete_id));
CREATE INDEX IF NOT EXISTS so_follows_athlete ON so_follows (athlete_id);
"""

register(SCHEMA, ["so_follows"])


def member_claims(request: Request) -> dict:
    return bearer_claims(request)


def _athlete(conn, athlete_id: str) -> dict:
    row = conn.execute("SELECT p.*, c.name AS club_name FROM players p LEFT JOIN clubs c ON c.club_id = p.club_id "
                       "WHERE p.player_id = %s", (athlete_id,)).fetchone()
    if row is None:
        raise not_found("PLAYER_NOT_FOUND", f"Player {athlete_id} does not exist.", {"player_id": athlete_id})
    return row


def _followers(conn, athlete_id: str) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM so_follows WHERE athlete_id = %s", (athlete_id,)).fetchone()["n"]


def _follow_state(conn, follower_id: str, athlete_id: str) -> dict:
    following = conn.execute("SELECT 1 FROM so_follows WHERE follower_id = %s AND athlete_id = %s",
                             (follower_id, athlete_id)).fetchone() is not None
    return {"athlete_id": athlete_id, "follower_id": follower_id, "following": following,
            "followers": _followers(conn, athlete_id)}


def _follower_id(claims: dict) -> str:
    return claims.get("player_id") or claims["sub"]


@router.post("/api/v1/athletes/{athlete_id}/follow")
def follow(athlete_id: str, claims=Depends(member_claims)):
    with db.tx() as conn:
        _athlete(conn, athlete_id)
        follower = _follower_id(claims)
        conn.execute("INSERT INTO so_follows (follower_id, athlete_id, followed_at) VALUES (%s, %s, %s) "
                     "ON CONFLICT (follower_id, athlete_id) DO NOTHING", (follower, athlete_id, utc_now()))
        return _follow_state(conn, follower, athlete_id)


@router.delete("/api/v1/athletes/{athlete_id}/follow")
def unfollow(athlete_id: str, claims=Depends(member_claims)):
    with db.tx() as conn:
        _athlete(conn, athlete_id)
        follower = _follower_id(claims)
        conn.execute("DELETE FROM so_follows WHERE follower_id = %s AND athlete_id = %s", (follower, athlete_id))
        return _follow_state(conn, follower, athlete_id)


def _career(conn, club_id: str | None) -> tuple[dict, list, list]:
    record = {"played": 0, "won": 0, "drawn": 0, "lost": 0}
    upcoming, vods = [], []
    if not club_id:
        return record, upcoming, vods
    from ..scoring import service as scoring

    rows = conn.execute("SELECT match_id FROM scoring_matches WHERE home_team_id = %s OR away_team_id = %s "
                        "ORDER BY created_seq", (club_id, club_id)).fetchall()
    for row in rows:
        p = scoring.project(conn, row["match_id"])
        if p is None:
            continue
        if p["status"] == "upcoming":
            upcoming.append({"match_id": p["match_id"], "home": p["home"]["team_id"], "away": p["away"]["team_id"]})
        if p["status"] != "completed" or p["outcome"] == "undecided":
            continue
        record["played"] += 1
        if p["winner_team_id"] is None:
            record["drawn"] += 1
        elif p["winner_team_id"] == club_id:
            record["won"] += 1
        else:
            record["lost"] += 1
        vods.append({"match_id": p["match_id"], "vod_route": f"/web/matches/{p['match_id']}/vod"})
    return record, upcoming, vods


@router.get("/api/v1/athletes/{athlete_id}")
def athlete_profile(athlete_id: str):
    """Public read: guests may view athlete pages without an account."""
    with db.tx() as conn:
        a = _athlete(conn, athlete_id)
        record, upcoming, vods = _career(conn, a["club_id"])
        return {
            "athlete_id": a["player_id"], "name": a["name"], "position": a["position"], "role": a["role"],
            "shirt_number": a["shirt_number"],
            "club": {"club_id": a["club_id"], "name": a["club_name"]} if a["club_id"] else None,
            "career": record, "upcoming_fixtures": upcoming, "vod_archive": vods,
            "followers": _followers(conn, athlete_id),
        }
