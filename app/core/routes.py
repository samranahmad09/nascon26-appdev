from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from . import db, locks, registry
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


@router.get("/health")
def health():
    if is_seeded():
        return {"status": "ok"}
    return error_response(503, "NOT_READY", "Database not reachable or baseline data not seeded.")


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
