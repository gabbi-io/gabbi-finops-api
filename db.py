# db.py
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


def _dsn() -> str:
    host = os.getenv("DB_HOST", "192.168.230.108")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "gabbi-io")
    user = os.getenv("DB_USER", "gabbi_io")
    pwd = "lrc2An*gvNP%00SkW%bY5cFLQV6S0o5v7^",
    if not pwd:
        raise RuntimeError("DB_PASSWORD não configurada no ambiente")
    return f"host={host} port={port} dbname={name} user={user} password={pwd}"


@contextmanager
def get_conn():
    conn = psycopg2.connect(_dsn())
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def execute(sql: str, params: tuple | dict | None = None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()

def execute_returning(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Executes a statement and returns rows (for INSERT ... RETURNING)."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            try:
                rows = cur.fetchall()
            except Exception:
                rows = []
        conn.commit()
        return [dict(r) for r in rows]


def table_exists(schema: str, table: str) -> bool:
    row = fetch_one(
        """
        select 1
        from information_schema.tables
        where table_schema=%(s)s and table_name=%(t)s
        limit 1
        """,
        {"s": schema, "t": table},
    )
    return bool(row)
