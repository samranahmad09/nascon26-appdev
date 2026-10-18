from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from . import db, locks, metrics, registry
from .config import DEFAULT_SEED
from .db import Jsonb
from .errors import error_response, not_found
from .validate import Validator, is_int, json_body

router = APIRouter()

DEFAULT_STOCK = {"var-jersey-001-M": 1, "var-jersey-001-L": 5}


def create_schema() -> None:
    with db.tx() as conn:
        for ddl in registry.SCHEMAS:
            conn.execute(ddl)


def is_seeded() -> bool:
    try:
        return db.fetchone("SELECT 1 AS ok FROM tenants LIMIT 1") is not None
    except Exception:
        return False


def reset_state(seed: int = DEFAULT_SEED, stock: dict | None = None) -> dict:
    final_stock = {**DEFAULT_STOCK, **(stock or {})}
    opts = {"seed": seed, "stock": final_stock}
    with locks.GLOBAL:
        with db.tx() as conn:
            if registry.TABLES:
                conn.execute("TRUNCATE " + ", ".join(registry.TABLES) + " RESTART IDENTITY CASCADE")
            for _, seeder in sorted(registry.SEEDERS, key=lambda s: s[0]):
                seeder(conn, opts)
            conn.execute("INSERT INTO app_state (key, value) VALUES ('last_seed', %s) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (Jsonb(seed),))
            sports = [r["sport_id"] for r in conn.execute("SELECT sport_id FROM sports ORDER BY created_seq").fetchall()]
            tenants = [r["tenant_id"] for r in conn.execute("SELECT tenant_id FROM tenants ORDER BY tenant_id").fetchall()]
        for hook in registry.RESET_HOOKS:
            hook()
    return {"status": "reset_complete", "seed": seed, "sports": sports, "tenants": tenants, "stock": final_stock}


def redis_ok() -> bool:
    try:
        return bool(_redis_client().ping())
    except Exception:
        return False


_redis = None


def _redis_client():
    global _redis
    if _redis is None:
        import redis
        from .config import REDIS_URL
        _redis = redis.Redis.from_url(REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
    return _redis


def media_ok() -> bool:
    """Media server reachable (skipped when MEDIA_HOST is unset, e.g. in unit-test runs)."""
    from .config import MEDIA_HOST
    if not MEDIA_HOST:
        return True
    import socket
    try:
        with socket.create_connection((MEDIA_HOST, 8888), timeout=1):
            return True
    except OSError:
        return False


@router.get("/health")
def health():
    checks = {"database_seeded": is_seeded(), "redis": redis_ok(), "media": media_ok()}
    if all(checks.values()):
        return {"status": "ok"}
    return error_response(503, "NOT_READY", "A dependency is not ready yet.",
                          {"failed": [name for name, ok in checks.items() if not ok]})


@router.post("/audit/v1/reset")
def reset(body=Depends(json_body)):
    v = Validator(body, allow_empty=True)
    seed = v.integer("seed", required=False)
    stock = v.obj("stock", required=False)
    if stock is not None and not all(isinstance(k, str) and is_int(q) and q >= 0 for k, q in stock.items()):
        v.bad("stock")
    v.check()
    for variant_id in (stock or {}):
        if variant_id not in DEFAULT_STOCK:
            raise not_found("VARIANT_NOT_FOUND", f"Variant {variant_id} does not exist.", {"variant_id": variant_id})
    result = reset_state(DEFAULT_SEED if seed is None else seed, stock)
    return JSONResponse(status_code=200, content=result)


@router.get("/metrics")
def prometheus_metrics():
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
