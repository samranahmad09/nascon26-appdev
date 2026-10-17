"""Core baseline data: tenants, RBAC scenario (leagues, clubs, players) and the five demo users."""
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

TENANTS = [
    ("tenant-alpha", "Alpha Sports", {"logo_url": "/static/alpha.png", "primary_color": "#0B5FFF", "secondary_color": "#FFB800"}),
    ("tenant-beta", "Beta Athletics", {"logo_url": "/static/beta.png", "primary_color": "#0E9F6E", "secondary_color": "#1F2937"}),
]

TENANT_LEAGUES = [
    ("league-alpha-1", "tenant-alpha", "Alpha Premier League", "football", "2026", "user-organizer"),
    ("league-alpha-2", "tenant-alpha", "Alpha Hoops League", "basketball", "2026", None),
    ("league-beta-1", "tenant-beta", "Beta Champions League", "football", "2026", None),
]


def _club(name, venue, lat, lng, coach):
    return {
        "home_venue": {"name": venue, "lat": lat, "lng": lng},
        "socials": {"x": "@" + name.replace(" ", "").lower(), "instagram": name.replace(" ", "").lower()},
        "staff": [{"role": "coach", "name": coach}, {"role": "manager", "name": "Sana Iqbal"},
                  {"role": "physio", "name": "Dr. Omar Shah"}],
        "history": [{"season": "2024", "played": 18, "won": 9, "drawn": 5, "lost": 4},
                    {"season": "2025", "played": 18, "won": 11, "drawn": 3, "lost": 4}],
    }


CLUBS = [
    ("club-alpha-1", "tenant-alpha", "league-alpha-1", "Alpha United", _club("Alpha United", "Alpha Arena", 33.6844, 73.0479, "Imran Qureshi")),
    ("club-alpha-2", "tenant-alpha", "league-alpha-1", "Alpha Rovers", _club("Alpha Rovers", "Rovers Ground", 33.7215, 73.0433, "Faisal Mir")),
    ("club-beta-1", "tenant-beta", "league-beta-1", "Beta City", _club("Beta City", "Beta Stadium", 31.5204, 74.3587, "Kamran Ali")),
]

PLAYERS = [
    ("player-alpha-1", "tenant-alpha", "club-alpha-1", "Ahmed Raza", "fw", "captain", 9),
    ("player-alpha-1b", "tenant-alpha", "club-alpha-1", "Bilal Khan", "gk", "starter", 1),
    ("player-alpha-1c", "tenant-alpha", "club-alpha-1", "Usman Tariq", "df", "vice_captain", 4),
    ("player-alpha-1d", "tenant-alpha", "club-alpha-1", "Hamza Ali", "mf", "substitute", 8),
    ("player-alpha-2", "tenant-alpha", "club-alpha-2", "Zain Malik", "mf", "captain", 10),
    ("player-beta-1", "tenant-beta", "club-beta-1", "Danish Javed", "fw", "captain", 7),
]

USERS = [
    ("user-admin", "admin@nascon.test", "Platform Admin", "super_admin", None, [], None, None),
    ("user-organizer", "organizer@nascon.test", "League Organizer", "league_organizer", "tenant-alpha", ["league-alpha-1"], None, None),
    ("user-manager", "manager@nascon.test", "Club Manager", "club_manager", "tenant-alpha", [], "club-alpha-1", None),
    ("user-player", "player@nascon.test", "Ahmed Raza", "player", "tenant-alpha", [], "club-alpha-1", "player-alpha-1"),
    ("user-viewer", "viewer@nascon.test", "General Viewer", "viewer", "tenant-alpha", [], None, None),
]

_hash_cache: dict[str, str] = {}


def _hash(user_id: str) -> str:
    if user_id not in _hash_cache:
        _hash_cache[user_id] = hash_password(DEMO_PASSWORD, user_id)
    return _hash_cache[user_id]


def seed(conn, opts: dict) -> None:
    for tid, name, branding in TENANTS:
        conn.execute("INSERT INTO tenants VALUES (%s, %s, %s)", (tid, name, Jsonb(branding)))
    for row in TENANT_LEAGUES:
        conn.execute("INSERT INTO tenant_leagues (league_id, tenant_id, name, sport_id, season, organizer_user_id) "
                     "VALUES (%s, %s, %s, %s, %s, %s)", row)
    for cid, tid, lid, name, profile in CLUBS:
        conn.execute("INSERT INTO clubs VALUES (%s, %s, %s, %s, %s)", (cid, tid, lid, name, Jsonb(profile)))
    for row in PLAYERS:
        conn.execute("INSERT INTO players VALUES (%s, %s, %s, %s, %s, %s, %s)", row)
    for uid, email, display, role, tid, leagues, club, player in USERS:
        conn.execute("INSERT INTO users VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     (uid, email, _hash(uid), display, role, tid, Jsonb(leagues), club, player))


register(SCHEMA, ["app_state", "tenants", "tenant_leagues", "clubs", "players", "users"], seed, order=0)


def get_last_seed(conn) -> int:
    row = conn.execute("SELECT value FROM app_state WHERE key = 'last_seed'").fetchone()
    return int(row["value"]) if row else 42


def user_by_email(conn, email: str):
    return conn.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()
