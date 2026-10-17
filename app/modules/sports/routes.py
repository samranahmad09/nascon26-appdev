from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...core import db, locks
from ...core.db import Jsonb
from ...core.errors import conflict
from ...core.registry import register
from ...core.validate import json_body
from .logic import load_seed_sports, validate_sport

router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sports (
    sport_id TEXT PRIMARY KEY, config JSONB NOT NULL, seeded BOOLEAN NOT NULL DEFAULT FALSE,
    created_seq BIGSERIAL);
"""


def seed(conn, opts: dict) -> None:
    for sport in load_seed_sports():
        config = validate_sport(sport)
        conn.execute("INSERT INTO sports (sport_id, config, seeded) VALUES (%s, %s, TRUE)",
                     (config["sport_id"], Jsonb(config)))


register(SCHEMA, ["sports"], seed, order=10)


def get_sport(conn, sport_id: str) -> dict | None:
    row = conn.execute("SELECT config FROM sports WHERE sport_id = %s", (sport_id,)).fetchone()
    return row["config"] if row else None


def list_sports(conn) -> list[dict]:
    return [r["config"] for r in conn.execute("SELECT config FROM sports ORDER BY created_seq").fetchall()]


@router.post("/audit/v1/sports")
def create_sport(body=Depends(json_body)):
    config = validate_sport(body)
    with locks.GLOBAL, db.tx() as conn:
        if get_sport(conn, config["sport_id"]) is not None:
            raise conflict("SPORT_EXISTS", f"Sport {config['sport_id']} already exists.", {"sport_id": config["sport_id"]})
        conn.execute("INSERT INTO sports (sport_id, config) VALUES (%s, %s)", (config["sport_id"], Jsonb(config)))
    return JSONResponse(status_code=201, content={"sport_id": config["sport_id"], "status": "created", "config": config})
