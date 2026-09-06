"""Durable confirmation store adapters (Milestone 5B).

Implements ConfirmationStore protocol backed by SQLite while preserving
the in-memory store for unit tests that do not request persistence.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from storage.database import Database
from storage.repositories.confirmation_repository import ConfirmationRepository


class SqliteConfirmationStore:
    """ConfirmationStore backed by ConfirmationRepository."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = ConfirmationRepository(db)
        self._cache: Dict[str, Dict[str, Any]] = {}

    def put(self, confirmation_id: str, record: Dict[str, Any]) -> None:
        data = dict(record)
        data["id"] = confirmation_id
        data["confirmation_id"] = confirmation_id
        if "state" in data and "status" not in data:
            data["status"] = data["state"]
        if "status" in data and "state" not in data:
            data["state"] = data["status"]
        data["updated_at"] = time.time()
        saved = self.repo.upsert(data)
        # Merge persistence timing into cached performance
        perf = dict(data.get("performance") or {})
        perf.update(saved.get("performance") or {})
        saved["performance"] = perf
        # Keep full record shape expected by PendingConfirmation
        self._cache[confirmation_id] = {
            **data,
            **{k: saved[k] for k in ("action", "target", "performance") if k in saved},
            "id": confirmation_id,
            "confirmation_id": confirmation_id,
            "state": saved.get("status") or data.get("state"),
            "status": saved.get("status") or data.get("status"),
        }

    def get(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        if confirmation_id in self._cache:
            # Refresh from DB for durability consistency
            pass
        row = self.repo.get(confirmation_id)
        if not row:
            self._cache.pop(confirmation_id, None)
            return None
        # Prefer action/target from DB; overlay any in-memory-only fields
        cached = self._cache.get(confirmation_id) or {}
        merged = {**cached, **row}
        merged["id"] = confirmation_id
        merged["state"] = row.get("status") or row.get("state")
        self._cache[confirmation_id] = merged
        return merged

    def delete(self, confirmation_id: str) -> None:
        self.repo.delete(confirmation_id)
        self._cache.pop(confirmation_id, None)

    def values(self) -> list:
        rows = self.repo.list_all(limit=1000)
        out: List[Dict[str, Any]] = []
        for row in rows:
            cid = row["confirmation_id"]
            cached = self._cache.get(cid) or {}
            merged = {**cached, **row}
            merged["id"] = cid
            merged["state"] = row.get("status")
            self._cache[cid] = merged
            out.append(merged)
        return out

    def clear(self) -> None:
        # Tests may clear — wipe cache; leave DB rows (temp DB is ephemeral)
        self._cache.clear()
        # Also delete all rows in the temp DB for isolation
        try:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM confirmations")
        except Exception:
            pass

    def hydrate(self) -> int:
        """Load unfinished confirmations into cache. Returns count."""
        t0 = time.perf_counter()
        rows = self.repo.list_by_status(
            ["pending", "approved", "consumed"], limit=500
        )
        for row in rows:
            self._cache[row["confirmation_id"]] = row
        restore_ms = round((time.perf_counter() - t0) * 1000, 3)
        for row in rows:
            perf = dict(row.get("performance") or {})
            perf["confirmation_restore_ms"] = restore_ms
            row["performance"] = perf
        return len(rows)
