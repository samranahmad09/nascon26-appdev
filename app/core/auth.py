import base64
import hashlib
import hmac
import time

import jwt
from fastapi import Request

from . import config
from .errors import ApiError

ALGORITHM = "HS256"
TOKEN_TTL_S = 12 * 3600
PBKDF2_ROUNDS = 20000


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, expected = stored.split("$", 3)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def issue_token(claims: dict, ttl_s: int = TOKEN_TTL_S) -> str:
    now = int(time.time())
    return jwt.encode({**claims, "iat": now, "exp": now + ttl_s}, config.JWT_SECRET, algorithm=ALGORITHM)


def token_for_user(user: dict) -> str:
    return issue_token({
        "sub": user["user_id"],
        "role": user["role"],
        "tenant_id": user.get("tenant_id"),
        "league_ids": user.get("league_ids") or [],
        "club_id": user.get("club_id"),
        "player_id": user.get("player_id"),
    })


def _canonical_segments(token: str) -> bool:
    """PyJWT tolerates non-canonical base64url (spare low bits in the last character), so a
    tampered signature can still verify. Require every segment to re-encode to itself."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        for seg in parts:
            raw = base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))
            if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != seg:
                return False
    except (ValueError, UnicodeError):
        return False
    return True


def decode_token(token: str) -> dict:
    if not _canonical_segments(token):
        raise ApiError(401, "UNAUTHENTICATED", "Token is missing, invalid or expired.")
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise ApiError(401, "UNAUTHENTICATED", "Token is missing, invalid or expired.")


def bearer_claims(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "UNAUTHENTICATED", "Authorization: Bearer <token> is required.")
    return decode_token(token.strip())
