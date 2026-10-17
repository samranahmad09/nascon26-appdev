import base64
import json
import time

import jwt
import pytest

from app.core import auth, config
from app.core.middleware import RATE_LIMIT, RateLimit
from app.modules.rbac import policy
from app.modules.rbac.routes import CONTEXT

ROLES = ["super_admin", "league_organizer", "club_manager", "player", "viewer"]


def limiter(client):
    node = client.app.middleware_stack
    while node is not None and not isinstance(node, RateLimit):
        node = getattr(node, "app", None)
    return node


@pytest.fixture(autouse=True)
def clean_rate_limit(client):
    client.get("/health")
    limiter(client)._hits.clear()
    yield
    limiter(client)._hits.clear()


@pytest.fixture(scope="module")
def matrix(client):
    r = client.post("/audit/v1/rbac-matrix")
    assert r.status_code == 200, r.text
    return r.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_matrix_shape(matrix):
    assert set(matrix) == {"tokens", "context"}
    assert set(matrix["tokens"]) == set(ROLES)
    assert matrix["context"] == {
        "tenant_id": "tenant-alpha", "foreign_tenant_id": "tenant-beta",
        "own_league_id": "league-alpha-1", "other_league_id": "league-alpha-2", "foreign_league_id": "league-beta-1",
        "own_club_id": "club-alpha-1", "other_club_id": "club-alpha-2", "foreign_club_id": "club-beta-1",
        "own_player_id": "player-alpha-1", "other_player_id": "player-alpha-2", "foreign_player_id": "player-beta-1",
    }
    for role, token in matrix["tokens"].items():
        claims = auth.decode_token(token)
        assert claims["role"] == role
        assert claims["exp"] - claims["iat"] >= 3600
        assert claims["tenant_id"] == (None if role == "super_admin" else "tenant-alpha")


def test_matrix_ignores_any_body(client):
    r = client.post("/audit/v1/rbac-matrix", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 200 and set(r.json()["tokens"]) == set(ROLES)


A, D = 200, 403
# (method, path template, context key or None, expected per role in ROLES order) - Section 5.13 matrix
MATRIX = [
    ("POST", "/audit/v1/rbac/tenants", None, (A, D, D, D, D)),
    ("POST", "/audit/v1/rbac/leagues/{}/fixtures", "own_league_id", (A, A, D, D, D)),
    ("POST", "/audit/v1/rbac/leagues/{}/fixtures", "other_league_id", (A, D, D, D, D)),
    ("POST", "/audit/v1/rbac/leagues/{}/fixtures", "foreign_league_id", (A, D, D, D, D)),
    ("POST", "/audit/v1/rbac/clubs/{}/roster", "own_club_id", (A, D, A, D, D)),
    ("POST", "/audit/v1/rbac/clubs/{}/roster", "other_club_id", (A, D, D, D, D)),
    ("POST", "/audit/v1/rbac/clubs/{}/roster", "foreign_club_id", (A, D, D, D, D)),
    ("POST", "/audit/v1/rbac/clubs/{}/products", "own_club_id", (A, D, A, D, D)),
    ("POST", "/audit/v1/rbac/clubs/{}/products", "other_club_id", (A, D, D, D, D)),
    ("POST", "/audit/v1/rbac/clubs/{}/products", "foreign_club_id", (A, D, D, D, D)),
    ("PATCH", "/audit/v1/rbac/players/{}", "own_player_id", (A, D, A, A, D)),
    ("PATCH", "/audit/v1/rbac/players/{}", "other_player_id", (A, D, D, D, D)),
    ("PATCH", "/audit/v1/rbac/players/{}", "foreign_player_id", (A, D, D, D, D)),
    ("GET", "/audit/v1/rbac/clubs/{}", "own_club_id", (A, A, A, A, A)),
    ("GET", "/audit/v1/rbac/clubs/{}", "other_club_id", (A, A, A, A, A)),
    ("GET", "/audit/v1/rbac/clubs/{}", "foreign_club_id", (A, D, D, D, D)),
    ("GET", "/audit/v1/rbac/platform/config", None, (A, D, D, D, D)),
]
CASES = [(m, p, k, role, exp[i]) for m, p, k, exp in MATRIX for i, role in enumerate(ROLES)]


@pytest.mark.parametrize("method, template, key, role, expected", CASES,
                         ids=[f"{m} {p.format(k or '')} {r}" for m, p, k, r, _ in CASES])
def test_probe_matrix(client, matrix, method, template, key, role, expected):
    path = template.format(CONTEXT[key]) if key else template
    kwargs = {"json": {}} if method != "GET" else {}
    r = client.request(method, path, headers=bearer(matrix["tokens"][role]), **kwargs)
    assert r.status_code == expected, r.text
    if expected == 200:
        assert r.json() == {"authorized": True}
    else:
        body = r.json()
        assert body["error"]["code"] == "FORBIDDEN" and isinstance(body["error"]["details"], dict)


def test_player_may_patch_only_themself_and_manager_only_own_club_players(client, matrix):
    # a second player of club-alpha-1 (seeded) is the manager's but not the player's
    path = "/audit/v1/rbac/players/player-alpha-1b"
    assert client.patch(path, json={}, headers=bearer(matrix["tokens"]["club_manager"])).status_code == 200
    assert client.patch(path, json={}, headers=bearer(matrix["tokens"]["player"])).status_code == 403


PROBES = [
    ("POST", "/audit/v1/rbac/tenants"),
    ("POST", "/audit/v1/rbac/leagues/league-alpha-1/fixtures"),
    ("POST", "/audit/v1/rbac/clubs/club-alpha-1/roster"),
    ("POST", "/audit/v1/rbac/clubs/club-alpha-1/products"),
    ("PATCH", "/audit/v1/rbac/players/player-alpha-1"),
    ("GET", "/audit/v1/rbac/clubs/club-alpha-1"),
    ("GET", "/audit/v1/rbac/platform/config"),
]


def _b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _forge(claims, header=None, secret=None):
    if header is not None:
        return f"{_b64(header)}.{_b64(claims)}."
    return jwt.encode(claims, secret, algorithm="HS256")


def bad_tokens(matrix):
    good = matrix["tokens"]["super_admin"]
    claims = auth.decode_token(good)
    head, body, sig = good.split(".")
    viewer_body = matrix["tokens"]["viewer"].split(".")[1]
    flipped = sig[:10] + ("A" if sig[10] != "A" else "B") + sig[11:]
    return {
        "tampered_signature": f"{head}.{body}.{flipped}",
        "tampered_payload": f"{head}.{_b64({**claims, 'sub': 'someone-else'})}.{sig}",
        "swapped_payload": f"{head}.{viewer_body}.{sig}",
        "alg_none": _forge(claims, header={"alg": "none", "typ": "JWT"}),
        "alg_none_upper": _forge(claims, header={"alg": "None", "typ": "JWT"}),
        "wrong_secret": _forge(claims, secret="not-the-server-secret-but-long-enough-0123456789"),
        "expired": auth.issue_token({k: v for k, v in claims.items() if k not in ("iat", "exp")}, ttl_s=-30),
        "no_exp": _forge({k: v for k, v in claims.items() if k != "exp"}, secret=config.JWT_SECRET),
        "garbage": "not.a.jwt",
        "empty_segments": "..",
    }


@pytest.mark.parametrize("method, path", PROBES)
def test_probes_reject_missing_and_invalid_tokens_with_401(client, matrix, method, path):
    kwargs = {"json": {}} if method != "GET" else {}
    assert client.request(method, path, **kwargs).json()["error"]["code"] == "UNAUTHENTICATED"
    for name, token in bad_tokens(matrix).items():
        r = client.request(method, path, headers=bearer(token), **kwargs)
        assert r.status_code == 401, (name, r.text)
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    for header in ({"Authorization": "Bearer"}, {"Authorization": "Bearer   "},
                   {"Authorization": f"Basic {matrix['tokens']['super_admin']}"},
                   {"Authorization": matrix["tokens"]["super_admin"]}):
        r = client.request(method, path, headers=header, **kwargs)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_p7_security_probes(client, matrix):
    r = client.get("/audit/v1/rbac/platform/config")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    tokens = bad_tokens(matrix)
    for name in ("tampered_signature", "alg_none", "expired"):
        assert client.get("/audit/v1/rbac/platform/config", headers=bearer(tokens[name])).status_code == 401
    assert client.get("/audit/v1/rbac/platform/config", headers=bearer(matrix["tokens"]["super_admin"])).json() == \
        {"authorized": True}
    assert client.get("/audit/v1/rbac/platform/config",
                      headers={"authorization": "bearer " + matrix["tokens"]["super_admin"]}).status_code == 200


@pytest.mark.parametrize("method, path, code", [
    ("POST", "/audit/v1/rbac/leagues/league-ghost/fixtures", "LEAGUE_NOT_FOUND"),
    ("POST", "/audit/v1/rbac/clubs/club-ghost/roster", "CLUB_NOT_FOUND"),
    ("POST", "/audit/v1/rbac/clubs/club-ghost/products", "CLUB_NOT_FOUND"),
    ("PATCH", "/audit/v1/rbac/players/player-ghost", "PLAYER_NOT_FOUND"),
    ("GET", "/audit/v1/rbac/clubs/club-ghost", "CLUB_NOT_FOUND"),
    ("GET", "/audit/v1/rbac/clubs/bad%7Cid", "CLUB_NOT_FOUND"),
])
def test_unknown_ids_are_404_for_super_admin_and_403_for_everyone_else(client, matrix, method, path, code):
    kwargs = {"json": {}} if method != "GET" else {}
    r = client.request(method, path, headers=bearer(matrix["tokens"]["super_admin"]), **kwargs)
    assert r.status_code == 404 and r.json()["error"]["code"] == code
    for role in ROLES[1:]:
        r = client.request(method, path, headers=bearer(matrix["tokens"][role]), **kwargs)
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("method, path", [p for p in PROBES if p[0] != "GET"])
def test_probes_do_not_parse_or_validate_the_body(client, matrix, method, path):
    for content in (b"{broken", b"[]", b'{"tenant_id": 5}', b""):
        r = client.request(method, path, content=content, headers={**bearer(matrix["tokens"]["super_admin"]),
                                                                   "content-type": "application/json"})
        assert r.status_code == 200 and r.json() == {"authorized": True}
        r = client.request(method, path, content=content, headers={**bearer(matrix["tokens"]["viewer"]),
                                                                   "content-type": "application/json"})
        assert r.status_code == 403


def test_authentication_is_checked_before_existence(client):
    r = client.patch("/audit/v1/rbac/players/player-ghost", json={})
    assert r.status_code == 401


def test_cross_tenant_never_404_even_for_beta_ids(client, matrix):
    for role in ROLES[1:]:
        for path in ("/audit/v1/rbac/clubs/club-beta-1", "/audit/v1/rbac/players/player-beta-1"):
            method = "GET" if "clubs" in path else "PATCH"
            r = client.request(method, path, headers=bearer(matrix["tokens"][role]))
            assert r.status_code == 403


def test_policy_table_is_complete_and_deny_by_default():
    for action, (kind, grants) in policy.POLICY.items():
        assert kind is None or kind in policy.LOADERS
        assert set(grants) <= set(policy.ROLES)
        assert all(scope == "any" or scope in policy.SCOPES for scope in grants.values())
    claims = {"role": "intruder", "tenant_id": "tenant-alpha", "sub": "u"}
    assert not any(policy.is_allowed(claims, a, {"tenant_id": "tenant-alpha"}) for a in policy.POLICY)
    viewer = {"role": "viewer", "tenant_id": "tenant-alpha", "sub": "u"}
    assert not policy.is_allowed(viewer, "club.read", None)
    assert not policy.is_allowed(viewer, "club.read", {"club_id": "c", "tenant_id": "tenant-beta"})
    assert policy.is_allowed(viewer, "club.read", {"club_id": "c", "tenant_id": "tenant-alpha"})
    tenantless = {"role": "viewer", "tenant_id": None, "sub": "u"}
    assert not policy.is_allowed(tenantless, "club.read", {"club_id": "c", "tenant_id": None})
    organizer = {"role": "league_organizer", "tenant_id": "tenant-alpha", "league_ids": ["lg-x"], "sub": "u"}
    assert not policy.is_allowed(organizer, "league.fixtures.write", {"league_id": "lg-x", "tenant_id": "tenant-beta"})
    assert policy.is_allowed(organizer, "league.fixtures.write", {"league_id": "lg-x", "tenant_id": "tenant-alpha"})


# ---- rate limiting (NFR-5) ----

def test_ping_rate_limit_150_requests(client):
    started = time.monotonic()
    responses = [client.get("/api/v1/ping") for _ in range(150)]
    assert time.monotonic() - started < 5
    assert responses[0].status_code == 200 and responses[0].json() == {"pong": True}
    limited = [r for r in responses if r.status_code == 429]
    assert len(limited) >= 40
    assert [r.status_code for r in responses[:RATE_LIMIT]] == [200] * RATE_LIMIT
    for r in limited:
        assert r.json()["error"]["code"] == "RATE_LIMITED"
        assert int(r.headers["Retry-After"]) >= 1
        assert r.json()["error"]["details"]["retry_after_seconds"] == int(r.headers["Retry-After"])
    # 11 s later the client is welcome again (simulated clock instead of sleeping)
    lim = limiter(client)
    assert lim.check("testclient", time.monotonic() + 11) is None


def test_rate_limiter_is_per_ip_and_rolling():
    lim = RateLimit(app=None)
    for i in range(RATE_LIMIT):
        assert lim.check("10.0.0.1", 100.0 + i * 0.01) is None
    retry = lim.check("10.0.0.1", 101.5)
    assert retry == 9
    assert lim.check("10.0.0.2", 101.5) is None
    assert lim.check("10.0.0.1", 110.0) is None  # the first hit (t=100.0) has left the 10 s window
    assert lim.check("10.0.0.1", 110.001) is not None


def test_audit_routes_and_health_are_not_rate_limited(client):
    for _ in range(RATE_LIMIT + 20):
        assert client.get("/audit/v1/inventory/var-jersey-001-L").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/ping").status_code == 200


# ---- body size (413) ----

BIG = b"{" + b" " * (1024 * 1024) + b"}"


@pytest.mark.parametrize("path", ["/audit/v1/checkout", "/audit/v1/rbac-matrix", "/audit/v1/rbac/tenants",
                                  "/audit/v1/reset", "/api/v1/checkout"])
def test_body_over_1mb_is_413(client, path):
    r = client.post(path, content=BIG, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_chunked_body_over_1mb_is_413(client):
    def chunks():
        for _ in range(17):
            yield b" " * 65536

    r = client.post("/audit/v1/checkout", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_body_of_exactly_1mb_is_accepted_by_the_size_limit(client):
    body = json.dumps({"pad": "x" * (1024 * 1024 - 20)}).encode()
    assert len(body) <= 1024 * 1024
    r = client.post("/audit/v1/checkout", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 400  # reaches validation, not 413


# ---- password hashing / token helpers ----

def test_password_hashing():
    stored = auth.hash_password("Nascon@26", "user-x")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("Nascon@26", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("Nascon@26", "garbage")


def test_token_round_trip_and_demo_login(client):
    token = auth.token_for_user({"user_id": "u1", "role": "viewer", "tenant_id": "tenant-alpha"})
    claims = auth.decode_token(token)
    assert claims["sub"] == "u1" and claims["league_ids"] == [] and claims["club_id"] is None
    r = client.post("/api/v1/auth/login", json={"email": "manager@nascon.test", "password": "Nascon@26"})
    assert r.status_code == 200
    assert auth.decode_token(r.json()["token"])["club_id"] == "club-alpha-1"
