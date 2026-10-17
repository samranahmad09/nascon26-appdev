"""Each module registers its DDL, the tables /audit/v1/reset must wipe, and an optional seeder."""

SCHEMAS: list[str] = []
TABLES: list[str] = []
SEEDERS: list[tuple[int, object]] = []
RESET_HOOKS: list[object] = []


def register(schema: str = "", tables=(), seeder=None, order: int = 100, on_reset=None) -> None:
    if schema:
        SCHEMAS.append(schema)
    for t in tables:
        if t not in TABLES:
            TABLES.append(t)
    if seeder is not None:
        SEEDERS.append((order, seeder))
    if on_reset is not None:
        RESET_HOOKS.append(on_reset)
