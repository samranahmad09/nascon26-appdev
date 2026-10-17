"""Store schema, baseline catalogue and read helpers (FR-2.3)."""
from ...core.registry import register
from ...core.validate import is_id, utc_now

CURRENCY = "PKR"

SCHEMA = """
CREATE TABLE IF NOT EXISTS st_products (
    product_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, club_id TEXT, name TEXT NOT NULL,
    category TEXT NOT NULL, description TEXT NOT NULL, position INT NOT NULL);
CREATE TABLE IF NOT EXISTS st_variants (
    variant_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, size TEXT, color TEXT,
    price_minor BIGINT NOT NULL, currency TEXT NOT NULL,
    stock INT NOT NULL CHECK (stock >= 0), reserved INT NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    position INT NOT NULL);
CREATE TABLE IF NOT EXISTS st_price_schedule (
    variant_id TEXT NOT NULL, effective_at TEXT NOT NULL, price_minor BIGINT NOT NULL,
    PRIMARY KEY (variant_id, effective_at));
CREATE TABLE IF NOT EXISTS st_discount_codes (
    code TEXT PRIMARY KEY, percent_off INT NOT NULL CHECK (percent_off BETWEEN 0 AND 100));
CREATE TABLE IF NOT EXISTS st_orders (
    order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, customer_id TEXT NOT NULL, tenant_id TEXT,
    status TEXT NOT NULL, items JSONB NOT NULL, total_minor BIGINT NOT NULL,
    http_status INT, response JSONB, pdf BYTEA, authorization_id TEXT, capture_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS st_order_events (
    event_seq BIGSERIAL PRIMARY KEY, order_id TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    reason TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS st_authorizations (
    auth_seq BIGSERIAL PRIMARY KEY, order_id TEXT NOT NULL, amount_minor BIGINT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS st_captures (
    cap_seq BIGSERIAL PRIMARY KEY, auth_seq BIGINT NOT NULL, amount_minor BIGINT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS st_refunds (
    refund_seq BIGSERIAL PRIMARY KEY, cap_seq BIGINT NOT NULL, amount_minor BIGINT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS st_pins (
    match_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, pinned_by TEXT, pinned_at TEXT NOT NULL);
"""

TABLES = ["st_products", "st_variants", "st_price_schedule", "st_discount_codes", "st_orders", "st_order_events",
          "st_authorizations", "st_captures", "st_refunds", "st_pins"]

# (product_id, tenant_id, club_id, name, category, description,
#  [(variant_id, size, color, price_minor, default_stock)])
PRODUCTS = [
    ("prod-jersey-001", "tenant-alpha", "club-alpha-1", "Alpha United Home Jersey 2026", "apparel",
     "Official 2026 home jersey of Alpha United. Breathable match-grade fabric.",
     [("var-jersey-001-M", "M", "blue", 450000, 1), ("var-jersey-001-L", "L", "blue", 450000, 5)]),
    ("prod-cap-001", "tenant-alpha", "club-alpha-1", "Alpha United Supporters Cap", "accessories",
     "Adjustable cotton cap with the embroidered club crest.",
     [("var-cap-001-OS", "One size", "navy", 150000, 25)]),
    ("prod-scarf-001", "tenant-alpha", "club-alpha-2", "Alpha Rovers Knitted Scarf", "accessories",
     "Double-knit winter scarf in the club colours.",
     [("var-scarf-001-RED", "One size", "red", 200000, 30), ("var-scarf-001-WHITE", "One size", "white", 200000, 30)]),
    ("prod-wallpaper-001", "tenant-alpha", None, "Matchday Wallpaper Pack", "digital",
     "Ten high-resolution phone wallpapers from the 2026 season. Delivered digitally.",
     [("var-wallpaper-001-HD", "HD", None, 25000, 999)]),
    ("prod-beta-jersey-001", "tenant-beta", "club-beta-1", "Beta City Home Jersey 2026", "apparel",
     "Official 2026 home jersey of Beta City.",
     [("var-beta-jersey-001-M", "M", "green", 400000, 20)]),
]

# Scheduled price changes (variant_id, effective_at, price_minor): one already live, one in the future.
PRICE_SCHEDULE = [
    ("var-cap-001-OS", "2026-01-01T00:00:00Z", 120000),
    ("var-scarf-001-RED", "2027-01-01T00:00:00Z", 180000),
    ("var-scarf-001-WHITE", "2027-01-01T00:00:00Z", 180000),
]

DISCOUNT_CODES = [("WELCOME10", 10)]


def seed(conn, opts: dict) -> None:
    stock_overrides = opts.get("stock") or {}
    for pos, (pid, tenant, club, name, category, description, variants) in enumerate(PRODUCTS):
        conn.execute("INSERT INTO st_products VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (pid, tenant, club, name, category, description, pos))
        for vpos, (vid, size, color, price, default_stock) in enumerate(variants):
            conn.execute("INSERT INTO st_variants (variant_id, product_id, size, color, price_minor, currency, stock, "
                         "reserved, position) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)",
                         (vid, pid, size, color, price, CURRENCY, stock_overrides.get(vid, default_stock), vpos))
    for row in PRICE_SCHEDULE:
        conn.execute("INSERT INTO st_price_schedule VALUES (%s, %s, %s)", row)
    for row in DISCOUNT_CODES:
        conn.execute("INSERT INTO st_discount_codes VALUES (%s, %s)", row)


register(SCHEMA, TABLES, seed, order=50)

EFFECTIVE_PRICE = ("COALESCE((SELECT s.price_minor FROM st_price_schedule s WHERE s.variant_id = v.variant_id "
                   "AND s.effective_at <= %s ORDER BY s.effective_at DESC LIMIT 1), v.price_minor)")


def variants_by_id(conn, variant_ids: list[str]) -> dict[str, dict]:
    rows = conn.execute(
        f"SELECT v.variant_id, v.product_id, v.stock, v.reserved, {EFFECTIVE_PRICE} AS unit_price_minor, "
        "p.tenant_id, p.name FROM st_variants v JOIN st_products p ON p.product_id = v.product_id "
        "WHERE v.variant_id = ANY(%s)", (utc_now(), list(variant_ids))).fetchall()
    return {r["variant_id"]: r for r in rows}


def discount_percent(conn, code: str) -> int | None:
    row = conn.execute("SELECT percent_off FROM st_discount_codes WHERE code = %s", (code,)).fetchone()
    return row["percent_off"] if row else None


def _products(conn, where: str, params: tuple) -> list[dict]:
    products = conn.execute(
        f"SELECT product_id, tenant_id, club_id, name, category, description FROM st_products {where} "
        "ORDER BY position, product_id", params).fetchall()
    if not products:
        return []
    variants = conn.execute(
        f"SELECT v.product_id, v.variant_id, v.size, v.color, {EFFECTIVE_PRICE} AS price_minor, v.currency, v.stock "
        "FROM st_variants v WHERE v.product_id = ANY(%s) ORDER BY v.position, v.variant_id",
        (utc_now(), [p["product_id"] for p in products])).fetchall()
    by_product: dict[str, list] = {}
    for v in variants:
        by_product.setdefault(v.pop("product_id"), []).append(v)
    return [{"product_id": p["product_id"], "name": p["name"], "category": p["category"],
             "description": p["description"], "variants": by_product.get(p["product_id"], [])} for p in products]


def list_products(conn, tenant_id: str | None) -> list[dict]:
    if tenant_id is None:
        return _products(conn, "", ())
    return _products(conn, "WHERE tenant_id = %s", (tenant_id,))


def product_payload(conn, product_id: str) -> dict | None:
    if not is_id(product_id):
        return None
    found = _products(conn, "WHERE product_id = %s", (product_id,))
    return found[0] if found else None
