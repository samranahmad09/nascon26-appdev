import concurrent.futures as cf


def _token(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Nascon@26"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_follow_unfollow_idempotent_and_guest_blocked(fresh):
    me = _token(fresh, "manager@nascon.test")
    r = fresh.post("/api/v1/athletes/player-alpha-2/follow", headers=me)
    assert r.status_code == 200 and r.json()["following"] is True and r.json()["followers"] == 1
    assert fresh.post("/api/v1/athletes/player-alpha-2/follow", headers=me).json()["followers"] == 1
    r = fresh.delete("/api/v1/athletes/player-alpha-2/follow", headers=me)
    assert r.json() == {**r.json(), "following": False, "followers": 0}
    assert fresh.delete("/api/v1/athletes/player-alpha-2/follow", headers=me).json()["followers"] == 0
    g = fresh.post("/api/v1/athletes/player-alpha-2/follow", headers={"Authorization": "Bearer guest_anon_tok_7721"})
    assert g.status_code == 403 and g.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"
    assert fresh.post("/api/v1/athletes/player-alpha-2/follow").status_code == 401
    assert fresh.post("/api/v1/athletes/nope/follow", headers=me).status_code == 404


def test_concurrent_toggle_burst_has_no_drift(fresh):
    users = ["admin", "organizer", "manager", "player", "viewer"]
    headers = {u: _token(fresh, f"{u}@nascon.test") for u in users}

    def toggle(i):
        h = headers[users[i % len(users)]]
        return (fresh.post if i % 2 == 0 else fresh.delete)("/api/v1/athletes/player-alpha-1/follow", headers=h).status_code

    with cf.ThreadPoolExecutor(10) as pool:
        assert set(pool.map(toggle, range(40))) == {200}
    followers = fresh.get("/api/v1/athletes/player-alpha-1").json()["followers"]
    following = sum(fresh.post("/api/v1/athletes/player-alpha-1/follow", headers=h).json()["following"] for h in headers.values())
    assert 0 <= followers <= len(users) and following == len(users)
    assert fresh.get("/api/v1/athletes/player-alpha-1").json()["followers"] == len(users)


def test_public_athlete_profile(fresh):
    r = fresh.get("/api/v1/athletes/player-alpha-1")
    body = r.json()
    assert r.status_code == 200 and body["club"]["club_id"] == "club-alpha-1"
    assert body["career"]["played"] >= 2 and isinstance(body["vod_archive"], list)
    assert any(f["match_id"] for f in body["upcoming_fixtures"]) or body["upcoming_fixtures"] == []
    assert fresh.get("/api/v1/athletes/nope").status_code == 404
