"""Durable confirmation repository (Milestone 5B).

Persists only privacy-safe confirmation metadata and sanitized action payloads
needed for post-restart resume. Never stores screenshots, passwords, OTP, etc.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from privacy.persistence_validator import (
    PersistencePrivacyError,
    assert_safe_metadata,
    sanitize_for_persistence,
    validate_for_persistence,
)
from storage.database import Database, StorageUnavailableError


def _safe_action_payload(action: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize executable action for durable storage (fail closed on forbidden keys)."""
    raw = dict(action or {})
    # Never persist password / OTP typed values
    if raw.get("type") == "type":
        text = str(raw.get("text") or "")
        if raw.get("sensitive") or "password" in str(raw.get("label") or "").lower():
            raw["text"] = ""
        elif len("".join(c for c in text if c.isdigit())) >= 12:
            raw["text"] = ""
    # Drop nested forbidden fields before validate
    for k in list(raw.keys()):
        kl = str(k).lower()
        if any(
            tok in kl
            for tok in (
                "password",
                "screenshot",
                "otp",
                "ssn",
                "credit_card",
                "embedding",
                "face",
            )
        ):
            raw.pop(k, None)
    try:
        validate_for_persistence(raw, context="confirmation.action")
        return sanitize_for_persistence(raw, reject=True, context="confirmation.action")
    except PersistencePrivacyError:
        # Fail closed: keep only non-sensitive structural fields
        safe = {
            "type": raw.get("type"),
            "selector": raw.get("selector") if not raw.get("sensitive") else None,
        }
        if raw.get("x") is not None and not raw.get("sensitive"):
            safe["x"] = raw.get("x")
            safe["y"] = raw.get("y")
        return assert_safe_metadata(safe, context="confirmation.action.fallback")


class ConfirmationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        self.db.require_available()
        cid = record.get("confirmation_id") or record.get("id")
        if not cid:
            raise ValueError("confirmation_id required")

        safe_target = assert_safe_metadata(
            record.get("target") or record.get("safe_target") or {},
            context="confirmation.target",
        )
        action_payload = _safe_action_payload(record.get("action") or {})
        performance = assert_safe_metadata(
            record.get("performance") or {}, context="confirmation.performance"
        )
        validate_for_persistence(
            {
                "status": record.get("status") or record.get("state"),
                "category": record.get("category"),
                "task": record.get("task"),
                "safe_description": record.get("safe_description"),
            },
            context="confirmation.meta",
        )

        now = time.time()
        created_at = float(record.get("created_at") or now)
        updated_at = float(record.get("updated_at") or now)
        expires_at = float(record.get("expires_at") or (now + 60))
        status = record.get("status") or record.get("state") or "pending"
        action_type = action_payload.get("type") or record.get("action_type")
        safe_description = (
            record.get("safe_description")
            or record.get("task")
            or record.get("reason")
            or ""
        )[:300]

        params = (
            cid,
            record.get("session_id"),
            record.get("plan_id"),
            record.get("step_id"),
            record.get("lifecycle_id"),
            record.get("tab_id"),
            record.get("window_id"),
            status,
            record.get("category"),
            (record.get("reason") or "")[:300],
            (record.get("task") or "")[:300],
            action_type,
            safe_description,
            json.dumps(safe_target, separators=(",", ":")),
            json.dumps(action_payload, separators=(",", ":")),
            created_at,
            expires_at,
            updated_at,
            1 if record.get("consumed") else 0,
            record.get("execution_id"),
            json.dumps(performance, separators=(",", ":")),
        )
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO confirmations (
                        confirmation_id, session_id, plan_id, step_id, lifecycle_id,
                        tab_id, window_id, status, category, reason, task, action_type,
                        safe_description, safe_target_json, action_payload_json,
                        created_at, expires_at, updated_at, consumed, execution_id,
                        performance_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(confirmation_id) DO UPDATE SET
                        status=excluded.status,
                        consumed=excluded.consumed,
                        execution_id=excluded.execution_id,
                        updated_at=excluded.updated_at,
                        expires_at=excluded.expires_at,
                        action_payload_json=excluded.action_payload_json,
                        safe_target_json=excluded.safe_target_json,
                        performance_json=excluded.performance_json,
                        reason=excluded.reason
                    """,
                    params,
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc

        out = dict(record)
        out["confirmation_id"] = cid
        out["id"] = cid
        out["status"] = status
        out["state"] = status
        out["action"] = action_payload
        out["target"] = safe_target
        out["performance"] = {
            **performance,
            "action_persistence_ms": round((time.perf_counter() - t0) * 1000, 3),
        }
        out["created_at"] = created_at
        out["updated_at"] = updated_at
        out["expires_at"] = expires_at
        return out

    def get(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT * FROM confirmations WHERE confirmation_id = ?",
            (confirmation_id,),
        )
        return self._row_to_record(row) if row else None

    def delete(self, confirmation_id: str) -> None:
        self.db.require_available()
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM confirmations WHERE confirmation_id = ?",
                (confirmation_id,),
            )

    def list_by_status(self, statuses: List[str], *, limit: int = 200) -> List[Dict[str, Any]]:
        self.db.require_available()
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT * FROM confirmations WHERE status IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT ?",
            (*statuses, int(limit)),
        )
        return [self._row_to_record(r) for r in rows]

    def list_pending(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return self.list_by_status(["pending"], limit=limit)

    def list_all(self, *, limit: int = 500) -> List[Dict[str, Any]]:
        self.db.require_available()
        rows = self.db.fetchall(
            "SELECT * FROM confirmations ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        return [self._row_to_record(r) for r in rows]

    def list_for_session(self, session_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        self.db.require_available()
        rows = self.db.fetchall(
            "SELECT * FROM confirmations WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, int(limit)),
        )
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row) -> Dict[str, Any]:
        try:
            target = json.loads(row["safe_target_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            target = {}
        try:
            action = json.loads(row["action_payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            action = {}
        try:
            performance = json.loads(row["performance_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            performance = {}
        status = row["status"]
        return {
            "id": row["confirmation_id"],
            "confirmation_id": row["confirmation_id"],
            "session_id": row["session_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "lifecycle_id": row["lifecycle_id"],
            "tab_id": row["tab_id"],
            "window_id": row["window_id"],
            "status": status,
            "state": status,
            "category": row["category"],
            "reason": row["reason"],
            "task": row["task"],
            "action_type": row["action_type"],
            "safe_description": row["safe_description"],
            "target": target,
            "action": action,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "updated_at": row["updated_at"],
            "consumed": bool(row["consumed"]),
            "execution_id": row["execution_id"],
            "performance": performance,
        }
