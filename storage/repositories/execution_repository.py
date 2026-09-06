"""Persistent execution idempotency records (Milestone 5B)."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from privacy.persistence_validator import assert_safe_metadata
from storage.database import Database, StorageUnavailableError


class ExecutionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, execution_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT * FROM execution_records WHERE execution_id = ?",
            (execution_id,),
        )
        return self._row_to_record(row) if row else None

    def try_accept(
        self,
        *,
        execution_id: str,
        confirmation_id: Optional[str] = None,
        lifecycle_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tab_id: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        report_status: Optional[str] = None,
        safe_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Atomically accept an execution report exactly once.

        Returns:
            {"status": "accepted", "record": ...} on first accept
            {"status": "duplicate_execution", "record": ...} on replay
        """
        t0 = time.perf_counter()
        self.db.require_available()
        result = assert_safe_metadata(safe_result or {}, context="execution.result")
        now = time.time()
        try:
            with self.db.transaction() as conn:
                existing = conn.execute(
                    "SELECT * FROM execution_records WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                check_ms = round((time.perf_counter() - t0) * 1000, 3)
                if existing is not None:
                    rec = self._row_to_record(existing)
                    rec.setdefault("performance", {})["idempotency_check_ms"] = check_ms
                    return {"status": "duplicate_execution", "record": rec}

                if idempotency_key:
                    by_key = conn.execute(
                        "SELECT * FROM execution_records WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    if by_key is not None:
                        rec = self._row_to_record(by_key)
                        rec.setdefault("performance", {})[
                            "idempotency_check_ms"
                        ] = check_ms
                        return {"status": "duplicate_execution", "record": rec}

                conn.execute(
                    """
                    INSERT INTO execution_records (
                        execution_id, confirmation_id, lifecycle_id, session_id,
                        tab_id, status, idempotency_key, report_status,
                        created_at, updated_at, safe_result_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        execution_id,
                        confirmation_id,
                        lifecycle_id,
                        session_id,
                        tab_id,
                        "accepted",
                        idempotency_key,
                        report_status,
                        now,
                        now,
                        json.dumps(result, separators=(",", ":")),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM execution_records WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                rec = self._row_to_record(row)
                rec["performance"] = {"idempotency_check_ms": check_ms}
                return {"status": "accepted", "record": rec}
        except StorageUnavailableError:
            raise
        except Exception as exc:
            # Unique constraint race → treat as duplicate
            msg = str(exc).lower()
            if "unique" in msg or "constraint" in msg:
                existing = self.get(execution_id)
                return {
                    "status": "duplicate_execution",
                    "record": existing or {"execution_id": execution_id},
                }
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc

    @staticmethod
    def _row_to_record(row) -> Dict[str, Any]:
        try:
            result = json.loads(row["safe_result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            result = {}
        return {
            "execution_id": row["execution_id"],
            "confirmation_id": row["confirmation_id"],
            "lifecycle_id": row["lifecycle_id"],
            "session_id": row["session_id"],
            "tab_id": row["tab_id"],
            "status": row["status"],
            "idempotency_key": row["idempotency_key"],
            "report_status": row["report_status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "safe_result": result,
        }
