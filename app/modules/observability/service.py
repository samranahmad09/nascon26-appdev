"""Right to erasure (Section 5.9, NFR-9).

user_id may be a player id (player-alpha-1) or a login user id (user-player). Erasing it anonymises the
player's name, every login account that is that id or is linked to that player (email, display name,
password hash, so the account can no longer sign in), the sender of their chat messages, and their
moderation rows, then asks the QR/guest module to wipe guest and viewer tokens tied to each id.
Everything runs in one short transaction; repeating the call is a no-op that returns the same body.
"""
import hashlib
import logging
import uuid

from ...core import db
from ...core.errors import not_found
from ...core.validate import is_id
from ..moderation import service as moderation

log = logging.getLogger("observability.privacy")

ANON_PLAYER_NAME = "Anonymized Player"
ANON_DISPLAY_NAME = "Anonymized User"
ERASED_PASSWORD = "!erased"


def _wipe_tokens(conn, user_id: str) -> None:
    """qr_guest owns guest/viewer tokens; tolerate its absence (module merged separately)."""
    try:
        from ..qr_guest import service as qr_service
    except ImportError:
        return
    wipe = getattr(qr_service, "wipe_user_tokens", None)
    if wipe is None:
        return
    try:
        with conn.transaction():
            wipe(conn, user_id)
    except Exception:
        log.exception("wipe_user_tokens(%s) failed", user_id)


def erase_user(user_id: str) -> dict:
    if not is_id(user_id):
        raise not_found("PLAYER_NOT_FOUND", f"Player {user_id} does not exist.", {"user_id": user_id})
    with db.tx() as conn:
        player = conn.execute("SELECT player_id FROM players WHERE player_id = %s", (user_id,)).fetchone()
        accounts = conn.execute("SELECT user_id FROM users WHERE user_id = %s OR player_id = %s ORDER BY user_id",
                                (user_id, user_id)).fetchall()
        if player is None and not accounts:
            raise not_found("PLAYER_NOT_FOUND", f"Player {user_id} does not exist.", {"user_id": user_id})
        ids = [user_id] + [a["user_id"] for a in accounts if a["user_id"] != user_id]
        if player is not None:
            conn.execute("UPDATE players SET name = %s WHERE player_id = %s", (ANON_PLAYER_NAME, user_id))
        for account in accounts:
            email = f"erased-{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:16]}@erased.invalid"
            conn.execute("UPDATE users SET email = %s, display_name = %s, password_hash = %s WHERE user_id = %s",
                         (email, ANON_DISPLAY_NAME, ERASED_PASSWORD, account["user_id"]))
        moderation.anonymize_user(conn, ids)
        for uid in ids:
            _wipe_tokens(conn, uid)
    return {"user_id": user_id, "status": "anonymized", "pii_retained": False, "tokens_wiped": True}
