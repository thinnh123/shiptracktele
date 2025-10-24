# app/common/store.py
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# =========================
# Helpers
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()

# =========================
# Postgres compatibility layer
# =========================
_PG_AVAILABLE = False
try:
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        from urllib.parse import urlparse

        _PG_AVAILABLE = True
except Exception:
    _PG_AVAILABLE = False


def _is_pg() -> bool:
    return bool(DATABASE_URL and _PG_AVAILABLE)


# --- Small wrapper so the rest of the app can keep using sqlite-like API ---
class _PGCursorWrap:
    def __init__(self, cur: "psycopg2.extensions.cursor"):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _PGConnWrap:
    """
    Provide a subset of sqlite3.Connection API:
      - execute(sql, params) -> cursor-like with fetchone()/fetchall()
      - executescript(sql_script)
      - commit(), close()
      - context manager 'with con:'
    It also converts '?' placeholders to '%s' automatically.
    """
    def __init__(self, real_conn: "psycopg2.extensions.connection"):
        self._conn = real_conn

    def execute(self, sql: str, params: Iterable[Any] = ()):
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, tuple(params))
        return _PGCursorWrap(cur)

    def executescript(self, script: str):
        # Split on semicolons, ignore empties
        for stmt in [s.strip() for s in script.split(";") if s.strip()]:
            self.execute(stmt)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


# =========================
# Connect
# =========================
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "shiptrack.db")

def connect():
    """
    Returns a connection-like object:
      - sqlite3.Connection (row_factory = sqlite3.Row) if no DATABASE_URL
      - _PGConnWrap for Postgres (rows are dicts)
    """
    if _is_pg():
        # Parse DATABASE_URL: postgresql://user:pass@host:port/dbname
        from urllib.parse import urlparse
        url = urlparse(DATABASE_URL)
        import psycopg2
        real = psycopg2.connect(
            dbname=url.path.lstrip("/"),
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
        )
        return _PGConnWrap(real)
    else:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        return con


# =========================
# Schema & init
# =========================
SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    label TEXT NOT NULL,
    carrier TEXT NOT NULL,
    tracking_code TEXT NOT NULL UNIQUE,
    last_status_code TEXT DEFAULT '',
    last_status_text TEXT DEFAULT '',
    last_checkpoint_time TEXT DEFAULT '',
    last_location TEXT DEFAULT '',
    auto_poll INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

def init_db():
    con = connect()
    try:
        if _is_pg():
            # Postgres: adjust "IDENTITY" for compat with sqlite's INTEGER PK
            pg_schema = SCHEMA.replace(
                "INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY",
                "BIGSERIAL PRIMARY KEY"
            )
            # 'auto_poll' as boolean could be nicer, but keep INTEGER for compat
            con.executescript(pg_schema)
            con.commit()
        else:
            con.executescript(SCHEMA)
            con.commit()
    finally:
        con.close()


# =========================
# CRUD helpers used by app
# =========================
def list_shipments():
    con = connect()
    try:
        rows = con.execute("SELECT * FROM shipments ORDER BY id DESC").fetchall()
        return rows
    finally:
        con.close()


def add_shipment(label: str, carrier: str, code: str, unified) -> None:
    con = connect()
    try:
        with con:
            con.execute(
                """
                INSERT INTO shipments(
                    label, carrier, tracking_code,
                    last_status_code, last_status_text, last_checkpoint_time,
                    last_location, auto_poll, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tracking_code) DO NOTHING
                """,
                (
                    label or "(Không tên)",
                    carrier,
                    code,
                    getattr(unified.latest, "code", "") if hasattr(unified, "latest") else "",
                    getattr(unified.latest, "text", "") if hasattr(unified, "latest") else "",
                    getattr(unified.latest, "time_iso", "") if hasattr(unified, "latest") else "",
                    getattr(unified.latest, "location", "") if hasattr(unified, "latest") else "",
                    1,
                    now_iso(),
                    now_iso(),
                ),
            )
    finally:
        con.close()


def update_shipment_from_unified(shipment_id: int, unified) -> bool:
    """
    Returns True if the last_* fields changed.
    """
    con = connect()
    try:
        before = con.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
        if not before:
            return False

        new_code = getattr(unified.latest, "code", "")
        new_text = getattr(unified.latest, "text", "")
        new_time = getattr(unified.latest, "time_iso", "")
        new_loc  = getattr(unified.latest, "location", "")

        changed = (
            (before["last_status_code"] != new_code)
            or (before["last_status_text"] != new_text)
            or (before["last_checkpoint_time"] != new_time)
            or (before["last_location"] != new_loc)
        )

        with con:
            con.execute(
                """
                UPDATE shipments
                SET last_status_code=?, last_status_text=?, last_checkpoint_time=?,
                    last_location=?, updated_at=?
                WHERE id=?
                """,
                (new_code, new_text, new_time, new_loc, now_iso(), shipment_id),
            )
        return changed
    finally:
        con.close()


def delete_shipment(shipment_id: int) -> None:
    con = connect()
    try:
        with con:
            con.execute("DELETE FROM shipments WHERE id=?", (shipment_id,))
    finally:
        con.close()
