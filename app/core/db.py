import time
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

pool: ConnectionPool | None = None

__all__ = ["Jsonb", "init", "close", "tx", "fetchone", "fetchall", "execute"]


def init(timeout_s: float = 90.0) -> None:
    global pool
    if pool is not None:
        return
    pool = ConnectionPool(
        config.DATABASE_URL,
        min_size=2,
        max_size=24,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    deadline = time.time() + timeout_s
    while True:
        try:
            pool.open(wait=True, timeout=5)
            with pool.connection() as conn:
                conn.execute("SELECT 1")
            return
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(1)


def close() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


@contextmanager
def tx():
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def fetchone(sql: str, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def fetchall(sql: str, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params=()) -> None:
    with pool.connection() as conn:
        conn.execute(sql, params)
