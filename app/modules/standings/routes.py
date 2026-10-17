from fastapi import APIRouter

from ...core import db, locks
from ...core.errors import not_found
from ...core.seed import get_last_seed
from ..sports import get_sport
from .logic import compute_standings

router = APIRouter()


@router.get("/audit/v1/standings/{league_id}")
def get_standings(league_id: str):
    from ..scoring import service  # imported lazily: the scoring module owns leagues and event logs

    with locks.GLOBAL, db.tx() as conn:
        league = service.get_league(conn, league_id)
        if league is None:
            raise not_found("LEAGUE_NOT_FOUND", f"League {league_id} does not exist.", {"league_id": league_id})
        if league.get("lots_seed") is None:
            league = {**league, "lots_seed": get_last_seed(conn)}
        sport = get_sport(conn, league["sport_id"]) or {}
        projections = service.league_projections(conn, league_id)
    return compute_standings(league, sport, projections)
