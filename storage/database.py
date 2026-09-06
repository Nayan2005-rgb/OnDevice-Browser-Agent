"""SQLite database connection manager with DI-friendly configuration.

Default path: data/ondevice_agent.db (relative to repo root).
Tests inject a temporary path via Database(path=...) or configure_database().
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterator, Optional

from storage.migrations import apply_migrations
from storage.schemas import SCHEMA_VERSION


DEFAULT_DB_RELATIVE = Path("data") / "ondevice_agent.db"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_database_path() -> Path:
    env = os.environ.get("ONDEVICE_AGENT_DB")
    if env:
        return Path(env)
    return _repo_root() / DEFAULT_DB_RELATIVE


class StorageUnavailableError(RuntimeError):
    """Raised when the persistent store cannot be used — fail closed."""


class Database:
    """Thread-safe SQLite wrapper with automatic schema init."""

    def __init__(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self._lock = threading.RLock()
        self._available = True
        self._last_error: Optional[str] = None
        self._ensure_parent()
        self._initialize()

    def _ensure_parent(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._available = False
            self._last_error = str(exc)
            raise StorageUnavailableError(f"Cannot create database directory: {exc}") from exc

    def _initialize(self) -> None:
        try:
            with self.connect() as conn:
                apply_migrations(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("version", str(SCHEMA_VERSION)),
                )
                conn.commit()
            self._available = True
            self._last_error = None
        except Exception as exc:  # noqa: BLE001 — fail closed
            self._available = False
            self._last_error = str(exc)
            raise StorageUnavailableError(f"Database initialization failed: {exc}") from exc

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def mark_unavailable(self, reason: str) -> None:
        self._available = False
        self._last_error = reason

    def require_available(self) -> None:
        if not self._available:
            raise StorageUnavailableError(
                self._last_error or "Persistent storage unavailable"
            )

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        if not self._available:
            raise StorageUnavailableError(
                self._last_error or "Persistent storage unavailable"
            )
        try:
            conn = sqlite3.connect(
                str(self.path),
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error:
                pass
        except Exception as exc:  # noqa: BLE001
            self.mark_unavailable(str(exc))
            raise StorageUnavailableError(f"Cannot open database: {exc}") from exc
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            self.require_available()
            with self.connect() as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    yield conn
                    conn.commit()
                except StorageUnavailableError:
                    raise
                except Exception as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    # Disk / integrity failures → fail closed
                    msg = str(exc).lower()
                    if any(
                        tok in msg
                        for tok in (
                            "disk",
                            "readonly",
                            "locked",
                            "corrupt",
                            "unable to open",
                            "no such table",
                        )
                    ):
                        self.mark_unavailable(str(exc))
                        raise StorageUnavailableError(str(exc)) from exc
                    raise

    def execute(
        self, sql: str, params: tuple[Any, ...] | list[Any] = ()
    ) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def executemany(
        self, sql: str, seq: Iterator[tuple[Any, ...]] | list[tuple[Any, ...]]
    ) -> None:
        with self.transaction() as conn:
            conn.executemany(sql, list(seq))

    def fetchone(
        self, sql: str, params: tuple[Any, ...] | list[Any] = ()
    ) -> Optional[sqlite3.Row]:
        with self._lock:
            self.require_available()
            with self.connect() as conn:
                cur = conn.execute(sql, params)
                return cur.fetchone()

    def fetchall(
        self, sql: str, params: tuple[Any, ...] | list[Any] = ()
    ) -> list[sqlite3.Row]:
        with self._lock:
            self.require_available()
            with self.connect() as conn:
                cur = conn.execute(sql, params)
                return list(cur.fetchall())


_db: Optional[Database] = None
_db_lock = threading.Lock()
_configured_path: Optional[Path] = None


def configure_database(path: Optional[os.PathLike[str] | str] = None) -> Database:
    """Configure (or reconfigure) the process-wide database instance."""
    global _db, _configured_path
    with _db_lock:
        _configured_path = Path(path) if path is not None else default_database_path()
        _db = Database(_configured_path)
        return _db


def get_database() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            _db = Database(_configured_path or default_database_path())
        return _db


def reset_database(path: Optional[os.PathLike[str] | str] = None) -> Database:
    """Reset singleton — used by tests with a temp path."""
    global _db, _configured_path
    with _db_lock:
        _configured_path = Path(path) if path is not None else None
        _db = None
        if path is not None:
            _db = Database(path)
            return _db
        return get_database()
