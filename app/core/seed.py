"""Core baseline data: tenants, RBAC scenario (leagues, clubs, players) and the five demo users.
The data itself lives in seed_data.json so no sport or entity name is hard-coded in Python."""
import json
from pathlib import Path

from .auth import hash_password
from .db import Jsonb
from .registry import register

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY, name TEXT NOT NULL, branding JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS tenant_leagues (
    league_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, sport_id TEXT NOT NULL,
    season TEXT NOT NULL, organizer_user_id TEXT);
CREATE TABLE IF NOT EXISTS clubs (
    club_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, league_id TEXT, name TEXT NOT NULL, profile JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, club_id TEXT, name TEXT NOT NULL,
    position TEXT, role TEXT, shirt_number INT);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
    role TEXT NOT NULL, tenant_id TEXT, league_ids JSONB NOT NULL DEFAULT '[]'::jsonb, club_id TEXT, player_id TEXT);
"""

DEMO_PASSWORD = "Nascon@26"
DATA = json.loads(Path(__file__).with_name("seed_data.json").read_text(encoding="utf-8"))

_hash_cache: dict[str, str] = {}


def _hash(user_id: str) -> str:
    if user_id not in _hash_cache:
        _hash_cache[user_id] = hash_password(DEMO_PASSWORD, user_id)
    return _hash_cache[user_id]


def club_profile(club: dict) -> dict:
    handle = club["name"].replace(" ", "").lower()
    return {
        "home_venue": {"name": club["venue"], "lat": club["lat"], "lng": club["lng"]},
        "socials": {"x": "@" + handle, "instagram": handle},
        "staff": [{"role": "coach", "name": club["coach"]}, {"role": "manager", "name": "Sana Iqbal"},
                  {"role": "physio", "name": "Dr. Omar Shah"}],
        "history": [{"season": "2024", "played": 18, "won": 9, "drawn": 5, "lost": 4},
                    {"season": "2025", "played": 18, "won": 11, "drawn": 3, "lost": 4}],
    }


# Tuple views kept for modules written against the earlier constants.
TENANTS = [(t["tenant_id"], t["name"], t["branding"]) for t in DATA["tenants"]]
TENANT_LEAGUES = [(lg["league_id"], lg["tenant_id"], lg["name"], lg["sport_id"], lg["season"], lg["organizer_user_id"])
                  for lg in DATA["tenant_leagues"]]
CLUBS = [(c["club_id"], c["tenant_id"], c["league_id"], c["name"], club_profile(c)) for c in DATA["clubs"]]
PLAYERS = [(p["player_id"], p["tenant_id"], p["club_id"], p["name"], p["position"], p["role"], p["shirt_number"])
           for p in DATA["players"]]
USERS = [(u["user_id"], u["email"], u["display_name"], u["role"], u["tenant_id"], u["league_ids"], u["club_id"],
          u["player_id"]) for u in DATA["users"]]


def seed(conn, opts: dict) -> None:
    for t in DATA["tenants"]:
        conn.execute("INSERT INTO tenants VALUES (%s, %s, %s)", (t["tenant_id"], t["name"], Jsonb(t["branding"])))
    for lg in DATA["tenant_leagues"]:
        conn.execute("INSERT INTO tenant_leagues (league_id, tenant_id, name, sport_id, season, organizer_user_id) "
                     "VALUES (%s, %s, %s, %s, %s, %s)",
                     (lg["league_id"], lg["tenant_id"], lg["name"], lg["sport_id"], lg["season"], lg["organizer_user_id"]))
    for c in DATA["clubs"]:
        conn.execute("INSERT INTO clubs VALUES (%s, %s, %s, %s, %s)",
                     (c["club_id"], c["tenant_id"], c["league_id"], c["name"], Jsonb(club_profile(c))))
    for p in DATA["players"]:
        conn.execute("INSERT INTO players VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (p["player_id"], p["tenant_id"], p["club_id"], p["name"], p["position"], p["role"], p["shirt_number"]))
    for u in DATA["users"]:
        conn.execute("INSERT INTO users VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     (u["user_id"], u["email"], _hash(u["user_id"]), u["display_name"], u["role"], u["tenant_id"],
                      Jsonb(u["league_ids"]), u["club_id"], u["player_id"]))


register(SCHEMA, ["app_state", "tenants", "tenant_leagues", "clubs", "players", "users"], seed, order=0)


def get_last_seed(conn) -> int:
    row = conn.execute("SELECT value FROM app_state WHERE key = 'last_seed'").fetchone()
    return int(row["value"]) if row else 42


def user_by_email(conn, email: str):
    return conn.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()
