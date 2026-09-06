"""Durable approved-action delivery repository (Milestone 5B).

Supports lease-based claiming and crash-safe status transitions.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

from privacy.persistence_validator import (
    PersistencePrivacyError,
    assert_safe_metadata,
    sanitize_for_persistence,
    validate_for_persistence,
)
from storage.database import Database, StorageUnavailableError


def _safe_action_payload(action: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = dict(action or {})
    if raw.get("type") == "type":
        text = str(raw.get("text") or "")
        if raw.get("sensitive") or "password" in str(raw.get("label") or "").lower():
            raw["text"] = ""
        elif len("".join(c for c in text if c.isdigit())) >= 12:
            raw["text"] = ""
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
        validate_for_persistence(raw, context="delivery.action")
        return sanitize_for_persistence(raw, reject=True, context="delivery.action")
    except PersistencePrivacyError:
        safe = {"type": raw.get("type")}
        if raw.get("selector") and not raw.get("sensitive"):
            safe["selector"] = raw.get("selector")
        return assert_safe_metadata(safe, context="delivery.action.fallback")


class ActionDeliveryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        self.db.require_available()
        eid = record.get("execution_id")
        if not eid:
            raise ValueError("execution_id required")

        action_payload = _safe_action_payload(record.get("action") or {})
        performance = assert_safe_metadata(
            record.get("performance") or {}, context="delivery.performance"
        )
        result = assert_safe_metadata(
            record.get("result") or {}, context="delivery.result"
        )
        validate_for_persistence(
            {
                "status": record.get("status"),
                "task": record.get("task"),
                "category": record.get("category"),
                "recovery_reason": record.get("recovery_reason"),
            },
            context="delivery.meta",
        )

        now = time.time()
        created_at = float(record.get("created_at") or now)
        updated_at = float(record.get("updated_at") or now)
        expires_at = float(record.get("expires_at") or (now + 60))
        status = record.get("status") or "approved"

        params = (
            eid,
            record.get("confirmation_id"),
            record.get("lifecycle_id"),
            record.get("session_id"),
            record.get("plan_id"),
            record.get("step_id"),
            record.get("tab_id"),
            record.get("window_id"),
            status,
            action_payload.get("type") or record.get("action_type"),
            (record.get("task") or "")[:300],
            (record.get("category") or "")[:80],
            json.dumps(action_payload, separators=(",", ":")),
            created_at,
            expires_at,
            record.get("claimed_at"),
            record.get("lease_until"),
            record.get("executed_at"),
            (record.get("recovery_reason") or "")[:300] or None,
            json.dumps(result, separators=(",", ":")),
            json.dumps(performance, separators=(",", ":")),
            record.get("idempotency_key"),
            updated_at,
        )
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO action_deliveries (
                        execution_id, confirmation_id, lifecycle_id, session_id,
                        plan_id, step_id, tab_id, window_id, status, action_type,
                        task, category, action_payload_json, created_at, expires_at,
                        claimed_at, lease_until, executed_at, recovery_reason,
                        result_json, performance_json, idempotency_key, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        status=excluded.status,
                        claimed_at=excluded.claimed_at,
                        lease_until=excluded.lease_until,
                        executed_at=excluded.executed_at,
                        recovery_reason=excluded.recovery_reason,
                        result_json=excluded.result_json,
                        performance_json=excluded.performance_json,
                        action_payload_json=excluded.action_payload_json,
                        updated_at=excluded.updated_at,
                        expires_at=excluded.expires_at
                    """,
                    params,
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc

        out = dict(record)
        out["action"] = action_payload
        out["performance"] = {
            **performance,
            "action_persistence_ms": round((time.perf_counter() - t0) * 1000, 3),
        }
        out["result"] = result
        out["created_at"] = created_at
        out["updated_at"] = updated_at
        out["expires_at"] = expires_at
        out["status"] = status
        return out

    def get(self, execution_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT * FROM action_deliveries WHERE execution_id = ?",
            (execution_id,),
        )
        return self._row_to_record(row) if row else None

    def get_by_confirmation(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT * FROM action_deliveries WHERE confirmation_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (confirmation_id,),
        )
        return self._row_to_record(row) if row else None

    def list_by_status(
        self, statuses: Sequence[str], *, limit: int = 200
    ) -> List[Dict[str, Any]]:
        self.db.require_available()
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT * FROM action_deliveries WHERE status IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT ?",
            (*statuses, int(limit)),
        )
        return [self._row_to_record(r) for r in rows]

    def list_for_tab(self, tab_id: int, *, statuses: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        self.db.require_available()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.db.fetchall(
                f"SELECT * FROM action_deliveries WHERE tab_id = ? "
                f"AND status IN ({placeholders}) ORDER BY created_at DESC",
                (int(tab_id), *statuses),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM action_deliveries WHERE tab_id = ? "
                "ORDER BY created_at DESC LIMIT 50",
                (int(tab_id),),
            )
        return [self._row_to_record(r) for r in rows]

    def claim_atomic(
        self,
        *,
        tab_id: int,
        lease_seconds: int,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Atomically claim one approved delivery for tab. Returns claimed record or None."""
        t0 = time.perf_counter()
        self.db.require_available()
        ts = now if now is not None else time.time()
        lease_until = ts + int(lease_seconds)
        try:
            with self.db.transaction() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM action_deliveries
                    WHERE tab_id = ?
                      AND status IN ('approved', 'waiting_for_browser')
                      AND expires_at > ?
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (int(tab_id), ts),
                ).fetchone()
                if row is None:
                    return None
                eid = row["execution_id"]
                # Re-check status inside transaction for exactly-once
                cur = conn.execute(
                    """
                    UPDATE action_deliveries
                    SET status = 'claimed',
                        claimed_at = ?,
                        lease_until = ?,
                        updated_at = ?
                    WHERE execution_id = ?
                      AND status IN ('approved', 'waiting_for_browser')
                      AND expires_at > ?
                    """,
                    (ts, lease_until, ts, eid, ts),
                )
                if cur.rowcount != 1:
                    return None
                # Merge claim performance
                try:
                    perf = json.loads(row["performance_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    perf = {}
                claim_ms = round((time.perf_counter() - t0) * 1000, 3)
                perf["action_claim_ms"] = claim_ms
                if row["created_at"]:
                    perf["approved_action_wait_ms"] = round(
                        (ts - float(row["created_at"])) * 1000, 3
                    )
                conn.execute(
                    "UPDATE action_deliveries SET performance_json = ? WHERE execution_id = ?",
                    (json.dumps(perf, separators=(",", ":")), eid),
                )
                updated = conn.execute(
                    "SELECT * FROM action_deliveries WHERE execution_id = ?",
                    (eid,),
                ).fetchone()
                return self._row_to_record(updated) if updated else None
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc

    def expire_leases(
        self, *, now: Optional[float] = None, reason: str = "lease_expired"
    ) -> List[Dict[str, Any]]:
        """Mark claimed actions with expired leases as recovery_required."""
        self.db.require_available()
        ts = now if now is not None else time.time()
        changed: List[Dict[str, Any]] = []
        try:
            with self.db.transaction() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM action_deliveries
                    WHERE status = 'claimed'
                      AND lease_until IS NOT NULL
                      AND lease_until < ?
                    """,
                    (ts,),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        """
                        UPDATE action_deliveries
                        SET status = 'recovery_required',
                            recovery_reason = ?,
                            updated_at = ?
                        WHERE execution_id = ? AND status = 'claimed'
                        """,
                        (reason, ts, row["execution_id"]),
                    )
                    rec = self._row_to_record(row)
                    rec["status"] = "recovery_required"
                    rec["recovery_reason"] = reason
                    changed.append(rec)
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc
        return changed

    def mark_recovery_required(
        self, execution_id: str, *, reason: str
    ) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE action_deliveries
                SET status = 'recovery_required',
                    recovery_reason = ?,
                    updated_at = ?
                WHERE execution_id = ?
                  AND status IN ('approved', 'waiting_for_browser', 'claimed', 'executing')
                """,
                ((reason or "")[:300], now, execution_id),
            )
        return self.get(execution_id)

    @staticmethod
    def _row_to_record(row) -> Dict[str, Any]:
        try:
            action = json.loads(row["action_payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            action = {}
        try:
            performance = json.loads(row["performance_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            performance = {}
        try:
            result = json.loads(row["result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            result = {}
        return {
            "execution_id": row["execution_id"],
            "confirmation_id": row["confirmation_id"],
            "lifecycle_id": row["lifecycle_id"],
            "session_id": row["session_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "tab_id": row["tab_id"],
            "window_id": row["window_id"],
            "status": row["status"],
            "action_type": row["action_type"],
            "task": row["task"],
            "category": row["category"],
            "action": action,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "claimed_at": row["claimed_at"],
            "lease_until": row["lease_until"],
            "executed_at": row["executed_at"],
            "recovery_reason": row["recovery_reason"],
            "result": result or None,
            "performance": performance,
            "idempotency_key": row["idempotency_key"],
            "updated_at": row["updated_at"],
        }
