"""Safe action execution record repository (metadata only)."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Dict, List, Optional

from privacy.persistence_validator import assert_safe_metadata, validate_for_persistence
from storage.database import Database, StorageUnavailableError


def new_action_record_id() -> str:
    return f"act_{secrets.token_hex(8)}"


class ActionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        self.db.require_available()
        payload = assert_safe_metadata(record.get("payload") or {}, context="action.payload")
        validate_for_persistence(
            {
                "status": record.get("status"),
                "action_type": record.get("action_type"),
                "safe_description": record.get("safe_description"),
            },
            context="action_record",
        )
        rid = record.get("record_id") or new_action_record_id()
        now = time.time()
        created_at = float(record.get("created_at") or now)
        updated_at = float(record.get("updated_at") or now)
        params = (
            rid,
            record.get("session_id"),
            record.get("plan_id"),
            record.get("step_id"),
            record.get("confirmation_id"),
            record.get("lifecycle_id"),
            record.get("status") or "unknown",
            record.get("action_type"),
            (record.get("safe_description") or "")[:300],
            created_at,
            updated_at,
            json.dumps(payload, separators=(",", ":")),
        )
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO action_records (
                        record_id, session_id, plan_id, step_id, confirmation_id,
                        lifecycle_id, status, action_type, safe_description,
                        created_at, updated_at, payload_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(record_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        payload_json=excluded.payload_json,
                        safe_description=excluded.safe_description
                    """,
                    params,
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc
        out = dict(record)
        out["record_id"] = rid
        out["payload"] = payload
        out["created_at"] = created_at
        out["updated_at"] = updated_at
        return out

    def list_for_session(self, session_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        self.db.require_available()
        rows = self.db.fetchall(
            "SELECT * FROM action_records WHERE session_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (session_id, int(limit)),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            out.append(
                {
                    "record_id": r["record_id"],
                    "session_id": r["session_id"],
                    "plan_id": r["plan_id"],
                    "step_id": r["step_id"],
                    "confirmation_id": r["confirmation_id"],
                    "lifecycle_id": r["lifecycle_id"],
                    "status": r["status"],
                    "action_type": r["action_type"],
                    "safe_description": r["safe_description"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "payload": payload,
                }
            )
        return out
