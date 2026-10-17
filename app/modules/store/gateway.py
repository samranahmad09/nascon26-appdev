"""Mock payment gateway: authorization, capture, void and refund, recorded in a small ledger.

Test tokens: tok_success (authorize + capture succeed), tok_declined (authorization fails),
tok_capture_fail (authorization succeeds, capture fails). Any other token is declined.
Ids are sequential per reset (auth_1, cap_1, ref_1) because the ledger tables restart their identities.
"""
from ...core import db
from ...core.validate import utc_now

# token -> (authorization succeeds, capture succeeds)
TOKENS = {
    "tok_success": (True, True),
    "tok_declined": (False, False),
    "tok_capture_fail": (True, False),
}


class PaymentDeclined(Exception):
    pass


class CaptureFailed(Exception):
    pass


def authorize(token: str, order_id: str, amount_minor: int) -> str:
    can_authorize, _ = TOKENS.get(token, (False, False))
    if not can_authorize:
        raise PaymentDeclined(token)
    with db.tx() as conn:
        row = conn.execute("INSERT INTO st_authorizations (order_id, amount_minor, state, created_at) "
                           "VALUES (%s, %s, 'authorized', %s) RETURNING auth_seq",
                           (order_id, amount_minor, utc_now())).fetchone()
    return f"auth_{row['auth_seq']}"


def _seq(prefixed_id: str) -> int:
    return int(prefixed_id.split("_", 1)[1])


def capture(token: str, authorization_id: str, amount_minor: int) -> str:
    _, can_capture = TOKENS.get(token, (False, False))
    if not can_capture:
        raise CaptureFailed(token)
    with db.tx() as conn:
        conn.execute("UPDATE st_authorizations SET state = 'captured' WHERE auth_seq = %s", (_seq(authorization_id),))
        row = conn.execute("INSERT INTO st_captures (auth_seq, amount_minor, created_at) VALUES (%s, %s, %s) "
                           "RETURNING cap_seq", (_seq(authorization_id), amount_minor, utc_now())).fetchone()
    return f"cap_{row['cap_seq']}"


def void(authorization_id: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE st_authorizations SET state = 'voided' WHERE auth_seq = %s", (_seq(authorization_id),))


def refund(capture_id: str, amount_minor: int) -> str:
    with db.tx() as conn:
        row = conn.execute("INSERT INTO st_refunds (cap_seq, amount_minor, created_at) VALUES (%s, %s, %s) "
                           "RETURNING refund_seq", (_seq(capture_id), amount_minor, utc_now())).fetchone()
    return f"ref_{row['refund_seq']}"
