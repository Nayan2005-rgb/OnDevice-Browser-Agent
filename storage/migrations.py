"""Idempotent schema migrations for the local SQLite store."""

from __future__ import annotations

import sqlite3
import time

from storage.schemas import CREATE_SCHEMA_SQL, CREATE_SCHEMA_V2_SQL, SCHEMA_VERSION


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply schema DDL idempotently. Returns current schema version.

    Existing v1 databases are upgraded in place — no destructive drops.
    """
    conn.executescript(CREATE_SCHEMA_SQL)
    conn.executescript(CREATE_SCHEMA_V2_SQL)

    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?", ("version",)
    ).fetchone()
    current = 0
    if row is not None:
        try:
            current = int(row[0] if not hasattr(row, "keys") else row["value"])
        except (TypeError, ValueError):
            current = 0

    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("version", str(SCHEMA_VERSION)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("migrated_at", str(time.time())),
        )
        return SCHEMA_VERSION

    if current == 0 and row is None:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES (?, ?)",
            ("version", str(SCHEMA_VERSION)),
        )
        return SCHEMA_VERSION

    return current if current else SCHEMA_VERSION
