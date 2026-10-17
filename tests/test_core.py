from app.core.middleware import RateLimit
from app.core.signing import canonical, lots_key, sign


def test_signature_matches_appendix_a_vector_1():
    payload = {"team_id": "t1", "event_type": "goal"}
    assert canonical(payload) == '{"event_type":"goal","team_id":"t1"}'
    assert sign("evt-1", payload, salt="demo-salt") == "972a8b558690b19121cc5bea84978f85e8eb15a3735018db36616bab1d7ec501"


def test_lots_key_matches_appendix_a_vector_2():
    assert lots_key(42, "lg-1", "t1") == "0613316991ce0ac31e88d6003e3ff4d5e21bb072ac5f81b42c0cdc6598453293"
    assert lots_key(42, "lg-1", "t2") == "e3e3a14e22727f936e1f288cf5d02a8a9362b1da13c5432725fc10b2c7955dd9"
    assert lots_key(42, "lg-1", "t3") == "c8f28e3c2f1eb6c4f636b4f57050e3362a2b39c07fda9b61742a26dd20397028"


def test_canonical_keeps_non_ascii_and_sorts_recursively():
    assert canonical({"b": {"y": 1, "x": "ü"}, "a": [3, 1]}) == '{"a":[3,1],"b":{"x":"ü","y":1}}'


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_reset_defaults_and_is_repeatable(client):
    first = client.post("/audit/v1/reset").json()
    second = client.post("/audit/v1/reset", json={}).json()
    assert first == second == {
        "status": "reset_complete", "seed": 42,
        "sports": ["football", "basketball", "cricket", "esports"],
        "tenants": ["tenant-alpha", "tenant-beta"],
        "stock": {"var-jersey-001-M": 1, "var-jersey-001-L": 5},
    }


def test_reset_with_seed_and_stock(client):
    r = client.post("/audit/v1/reset", json={"seed": 7, "stock": {"var-jersey-001-M": 3}})
    assert r.status_code == 200
    assert r.json()["seed"] == 7
    assert r.json()["stock"] == {"var-jersey-001-M": 3, "var-jersey-001-L": 5}


def test_reset_rejects_bad_stock(client):
    r = client.post("/audit/v1/reset", json={"stock": {"var-jersey-001-M": -1}})
    assert r.status_code == 400
    assert r.json()["error"]["details"]["fields"] == ["stock"]
    r = client.post("/audit/v1/reset", json={"stock": {"nope": 1}})
    assert r.status_code == 404


def test_malformed_json_is_400(client):
    r = client.post("/audit/v1/sports", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_body_over_1mb_is_413(client):
    r = client.post("/audit/v1/sports", content=b"x" * (1024 * 1024 + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_unknown_route_uses_error_body(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_rate_limiter_window():
    limiter = RateLimit(app=None)
    results = [limiter.check("1.2.3.4", 0.0 + i * 0.01) for i in range(150)]
    assert results[0] is None
    assert sum(r is not None for r in results) == 50
    assert limiter.check("1.2.3.4", 11.0 + 5) is None


def test_ping_and_rate_limit(client):
    assert client.get("/api/v1/ping").json() == {"pong": True}


def test_login_demo_accounts(fresh):
    for email in ("admin@nascon.test", "organizer@nascon.test", "manager@nascon.test",
                  "player@nascon.test", "viewer@nascon.test"):
        r = fresh.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})
        assert r.status_code == 200, email
        assert r.json()["token"]
    r = fresh.post("/api/v1/auth/login", json={"email": "admin@nascon.test", "password": "wrong"})
    assert r.status_code == 401


def test_noncanonical_jwt_signature_is_rejected():
    import base64

    import pytest

    from app.core.auth import decode_token, issue_token
    from app.core.errors import ApiError

    token = issue_token({"sub": "u1", "role": "viewer"})
    assert decode_token(token)["sub"] == "u1"
    head, payload, sig = token.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    idx = alphabet.index(sig[-1])
    sibling = alphabet[idx ^ 1]
    raw_a = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    raw_b = base64.urlsafe_b64decode(sig[:-1] + sibling + "=" * (-len(sig) % 4))
    assert raw_a == raw_b  # same bytes, different text: PyJWT alone would accept it
    with pytest.raises(ApiError) as err:
        decode_token(f"{head}.{payload}.{sig[:-1]}{sibling}")
    assert err.value.status == 401
    with pytest.raises(ApiError):
        decode_token("not-a-jwt")


def test_nul_and_lone_surrogate_text_is_400(client):
    for raw in (rb'{"sport_id": "a\u0000b"}', rb'{"sport_id": "\ud800"}'):
        r = client.post("/audit/v1/sports", content=raw, headers={"content-type": "application/json"})
        assert r.status_code == 400, raw
        assert r.json()["error"]["details"]["fields"] == ["sport_id"]
    r = client.post("/audit/v1/sports", content=rb'["\u0000"]', headers={"content-type": "application/json"})
    assert r.json()["error"]["details"]["fields"] == ["body"]
    r = client.post("/audit/v1/sports", content=rb'{"\ud800": 1}', headers={"content-type": "application/json"})
    assert r.json()["error"]["details"]["fields"] == ["body"]
