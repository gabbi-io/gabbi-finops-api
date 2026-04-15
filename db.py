from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


def _load_driver():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "O modo real requer a dependência 'psycopg'. "
            "Instale o projeto novamente com 'pip install -r requirements.txt'."
        ) from exc
    return psycopg, dict_row


def _conn_kwargs() -> dict[str, Any]:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", os.getenv("POSTGRES_DB", "gabbi")),
        "user": os.getenv("DB_USER", os.getenv("POSTGRES_USER", "gabbi")),
        "password": os.getenv("DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "gabbi")),
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
    }


@contextmanager
def get_conn() -> Iterator[Any]:
    psycopg, dict_row = _load_driver()
    with psycopg.connect(**_conn_kwargs(), row_factory=dict_row) as conn:
        yield conn


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return list(cur.fetchall())


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchone()


def execute(sql: str, params: dict[str, Any] | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        conn.commit()


def execute_returning(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        rows = list(cur.fetchall())
        conn.commit()
        return rows


def table_exists(schema: str, table: str) -> bool:
    row = fetch_one(
        """
        select exists (
          select 1
            from information_schema.tables
           where table_schema = %(schema)s
             and table_name = %(table)s
        ) as exists
        """,
        {"schema": schema, "table": table},
    )
    return bool(row and row["exists"])


def column_exists(schema: str, table: str, column: str) -> bool:
    row = fetch_one(
        """
        select exists (
          select 1
            from information_schema.columns
           where table_schema = %(schema)s
             and table_name = %(table)s
             and column_name = %(column)s
        ) as exists
        """,
        {"schema": schema, "table": table, "column": column},
    )
    return bool(row and row["exists"])
