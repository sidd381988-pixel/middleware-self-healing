"""
SQLite-backed incident state store.

Tracks per-incident-type occurrence counts and timestamps so the agent
can implement logic like "if OOM has happened > 2 times → notify admins".
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

_lock = threading.Lock()
_conn: sqlite3.Connection = None


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            detail      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actions_taken (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL,
            action      TEXT NOT NULL,
            taken_at    TEXT NOT NULL,
            result      TEXT
        )
    """)
    conn.commit()
    return conn


def init(db_path: str):
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect(db_path)


def _require_conn():
    if _conn is None:
        raise RuntimeError("state_manager.init() must be called before use")


def record_incident(incident_type: str, detail: str = None):
    """Insert a new incident occurrence."""
    _require_conn()
    with _lock:
        _conn.execute(
            "INSERT INTO incidents (incident_type, occurred_at, detail) VALUES (?, ?, ?)",
            (incident_type, _now(), detail),
        )
        _conn.commit()


def count_incidents(incident_type: str, since_seconds: int = None) -> int:
    """Return how many times incident_type occurred (optionally within a time window)."""
    _require_conn()
    with _lock:
        if since_seconds is None:
            row = _conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE incident_type = ?",
                (incident_type,),
            ).fetchone()
        else:
            cutoff = _iso_offset(since_seconds)
            row = _conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE incident_type = ? AND occurred_at >= ?",
                (incident_type, cutoff),
            ).fetchone()
        return row[0]


def record_action(incident_type: str, action: str, result: str = None):
    """Log a remediation action that was executed."""
    _require_conn()
    with _lock:
        _conn.execute(
            "INSERT INTO actions_taken (incident_type, action, taken_at, result) VALUES (?, ?, ?, ?)",
            (incident_type, action, _now(), result),
        )
        _conn.commit()


def last_action_time(incident_type: str, action: str) -> Optional[str]:
    """Return ISO timestamp of the most recent time action was taken for incident_type."""
    _require_conn()
    with _lock:
        row = _conn.execute(
            "SELECT taken_at FROM actions_taken WHERE incident_type = ? AND action = ? "
            "ORDER BY taken_at DESC LIMIT 1",
            (incident_type, action),
        ).fetchone()
        return row["taken_at"] if row else None


def seconds_since_last_action(incident_type: str, action: str) -> Optional[float]:
    """Returns elapsed seconds since the last action, or None if never taken."""
    ts = last_action_time(incident_type, action)
    if ts is None:
        return None
    last = datetime.fromisoformat(ts)
    now = datetime.now(timezone.utc)
    return (now - last).total_seconds()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_offset(seconds: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
