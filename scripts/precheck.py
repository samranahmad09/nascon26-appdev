"""Appendix C pre-submission checks against a running stack.

Run inside the API container so the real DAY1_SALT is used for signature checks:
    docker compose exec -T api python scripts/precheck.py
"""
import concurrent.futures as cf
import json
import sys
import threading
import time

import httpx

from app.core.signing import sign

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=15)
    r = c.get("/health")
    check("health 200 ok", r.status_code == 200 and r.json() == {"status": "ok"}, r.text)

    a = c.post("/audit/v1/reset", json={}).json()
    b = c.post("/audit/v1/reset").json()
    check("reset repeatable", a == b and a.get("status") == "reset_complete", json.dumps(a))

    for email in ("admin", "organizer", "manager", "player", "viewer"):
        r = c.post("/api/v1/auth/login", json={"email": f"{email}@nascon.test", "password": "Nascon@26"})
        check(f"demo login {email}", r.status_code == 200, r.text[:120])

    c.post("/audit/v1/reset")
    body = {"event_id": "pc-e1", "match_id": "pc-m1", "type": "match_init",
            "payload": {"sport_id": "football", "home_team_id": "pc-h", "away_team_id": "pc-a"}}
    r = c.post("/audit/v1/events", json=body)
    check("event signature", r.status_code == 201 and r.json().get("signature") == sign("pc-e1", body["payload"]), r.text[:200])
    r2 = c.post("/audit/v1/events", json=body)
    check("event idempotent replay 200", r2.status_code == 200 and r2.json() == r.json(), r2.text[:200])

    c.post("/audit/v1/reset", json={"stock": {"var-jersey-001-M": 1}})
    barrier = threading.Barrier(20)

    def buy(i):
        with httpx.Client(base_url=BASE, timeout=15) as cc:
            barrier.wait()
            return cc.post("/audit/v1/checkout", json={
                "order_id": f"pc-ord-{i}", "customer_id": "cust-1",
                "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}],
                "discount_code": None, "payment": {"token": "tok_success"}})

    t0 = time.time()
    with cf.ThreadPoolExecutor(20) as pool:
        responses = list(pool.map(buy, range(20)))
    elapsed = time.time() - t0
    codes = sorted(r.status_code for r in responses)
    inv = c.get("/audit/v1/inventory/var-jersey-001-M").json()
    check("20 parallel checkouts -> one 201, nineteen 409", codes == [201] + [409] * 19, str(codes))
    check("inventory stock 0 reserved 0", inv == {"variant_id": "var-jersey-001-M", "stock": 0, "reserved": 0}, json.dumps(inv))
    check("burst under 10 s", elapsed < 10, f"{elapsed:.1f}s")
    ok = next((r for r in responses if r.status_code == 201), None)
    if ok is not None:
        rec = ok.json()["receipt"]
        check("receipt signature", rec["signature"] == sign(ok.json()["order_id"], rec["payload"]))
        check("receipt pdf", rec["pdf_base64"].startswith("JVBER"))

    m = c.post("/audit/v1/rbac-matrix").json()
    t = m.get("tokens", {})
    r = c.get("/audit/v1/rbac/platform/config", headers={"Authorization": "Bearer " + t.get("super_admin", "")})
    check("rbac super admin P7 200", r.status_code == 200, r.text[:120])
    r = c.get("/audit/v1/rbac/platform/config", headers={"Authorization": "Bearer " + t.get("viewer", "")})
    check("rbac viewer P7 403", r.status_code == 403, r.text[:120])
    check("rbac no token 401", c.get("/audit/v1/rbac/platform/config").status_code == 401)

    r = c.post("/audit/v1/sports", content=b"x" * (1024 * 1024 + 10), headers={"content-type": "application/json"})
    check("413 over 1 MB", r.status_code == 413)
    r = c.post("/audit/v1/sports", json={"sport_id": "x'; DROP TABLE sports;--"})
    check("sql-ish sport id -> 400", r.status_code == 400)

    pings = [c.get("/api/v1/ping") for _ in range(150)]
    limited = [p for p in pings if p.status_code == 429]
    check("rate limit first 200", pings[0].status_code == 200)
    check("rate limit >= 40 x 429 with Retry-After", len(limited) >= 40 and all("retry-after" in p.headers for p in limited),
          str(len(limited)))
    health = [c.get("/health").status_code for _ in range(150)]
    check("/health never rate limited", all(code == 200 for code in health))

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
