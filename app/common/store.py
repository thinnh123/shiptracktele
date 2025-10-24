import sqlite3
from typing import List, Optional
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).resolve().parents[1] / "shiptrack.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS shipments (
  id INTEGER PRIMARY KEY,
  label TEXT,
  carrier TEXT,
  tracking_code TEXT UNIQUE,
  last_status_code TEXT,
  last_status_text TEXT,
  last_checkpoint_time TEXT,
  last_location TEXT,
  auto_poll INTEGER DEFAULT 1,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS status_logs (
  id INTEGER PRIMARY KEY,
  shipment_id INTEGER,
  status_code TEXT,
  status_text TEXT,
  location TEXT,
  time TEXT,
  raw_json TEXT,
  FOREIGN KEY (shipment_id) REFERENCES shipments(id) ON DELETE CASCADE
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = connect()
    with con:
        con.executescript(SCHEMA)
    con.close()


def add_shipment(label: str, carrier: str, tracking_code: str, unified) -> int:
    con = connect()
    with con:
        cur = con.execute(
            """
            INSERT INTO shipments(label, carrier, tracking_code, last_status_code, last_status_text,
                                  last_checkpoint_time, last_location, auto_poll, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                label,
                carrier,
                tracking_code,
                unified.latest.code,
                unified.latest.text,
                unified.latest.time_iso,
                unified.latest.location,
                now_iso(),
                now_iso(),
            ),
        )
        shipment_id = cur.lastrowid
        con.execute(
            """
            INSERT INTO status_logs(shipment_id, status_code, status_text, location, time, raw_json)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                shipment_id,
                unified.latest.code,
                unified.latest.text,
                unified.latest.location,
                unified.latest.time_iso,
                str(unified.latest.raw),
            ),
        )
    con.close()
    return shipment_id


def list_shipments() -> List[sqlite3.Row]:
    con = connect()
    cur = con.execute(
        "SELECT * FROM shipments ORDER BY updated_at DESC, id DESC"
    )
    rows = cur.fetchall()
    con.close()
    return rows


def get_shipment_by_code(code: str) -> Optional[sqlite3.Row]:
    con = connect()
    cur = con.execute("SELECT * FROM shipments WHERE tracking_code = ?", (code,))
    row = cur.fetchone()
    con.close()
    return row


def update_shipment_from_unified(shipment_id: int, unified) -> bool:
    """Trả về True nếu có thay đổi (tạo log + cập nhật)."""
    con = connect()
    cur = con.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return False

    changed = (
        row["last_status_code"] != unified.latest.code
        or row["last_checkpoint_time"] != unified.latest.time_iso
    )

    if changed:
        with con:
            con.execute(
                """
                UPDATE shipments
                SET last_status_code=?, last_status_text=?, last_checkpoint_time=?,
                    last_location=?, updated_at=?
                WHERE id=?
                """,
                (
                    unified.latest.code,
                    unified.latest.text,
                    unified.latest.time_iso,
                    unified.latest.location,
                    now_iso(),
                    shipment_id,
                ),
            )
            con.execute(
                """
                INSERT INTO status_logs(shipment_id, status_code, status_text, location, time, raw_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    shipment_id,
                    unified.latest.code,
                    unified.latest.text,
                    unified.latest.location,
                    unified.latest.time_iso,
                    str(unified.latest.raw),
                ),
            )
    con.close()
    return changed


def delete_shipment(shipment_id: int):
    con = connect()
    with con:
        con.execute("DELETE FROM shipments WHERE id = ?", (shipment_id,))
    con.close()
