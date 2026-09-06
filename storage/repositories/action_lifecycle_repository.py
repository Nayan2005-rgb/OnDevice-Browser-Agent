"""Durable action lifecycle repository (Milestone 5B)."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Dict, List, Optional, Sequence

from privacy.persistence_validator import assert_safe_metadata, validate_for_persistence
from storage.database import Database, StorageUnavailableError


def new_lifecycle_event_id() -> str:
    return f"ale_{secrets.token_hex(8)}"


class ActionLifecycleRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        self.db.require_available()
        lid = record.get("lifecycle_id")
        if not lid:
            raise ValueError("lifecycle_id required")

        payload = assert_safe_metadata(
            record.get("payload") or {}, context="lifecycle.payload"
        )
        timeline = record.get("timeline") or []
        # Timeline must be safe (state strings + reasons only)
        safe_timeline = []
        for t in timeline:
            if not isinstance(t, dict):
                continue
            safe_timeline.append(
                {
                    "state": str(t.get("state") or "")[:64],
                    "reason": str(t.get("reason") or "")[:200],
                    "ts": float(t["ts"]) if t.get("ts") is not None else None,
                }
            )
        validate_for_persistence(safe_timeline, context="lifecycle.timeline")
        performance = assert_safe_metadata(
            record.get("performance") or {}, context="lifecycle.performance"
        )
        validate_for_persistence(
            {
                "state": record.get("state"),
                "task": record.get("task"),
                "action_type": record.get("action_type"),
                "risk_level": record.get("risk_level"),
                "recovery_reason": record.get("recovery_reason"),
            },
            context="lifecycle.meta",
        )

        now = time.time()
        created_at = float(record.get("created_at") or now)
        updated_at = float(record.get("updated_at") or now)
        state = record.get("state") or "created"

        params = (
            lid,
            record.get("session_id"),
            record.get("plan_id"),
            record.get("step_id"),
            record.get("confirmation_id"),
            record.get("execution_id"),
            record.get("tab_id"),
            record.get("window_id"),
            state,
            (record.get("task") or "")[:300],
            record.get("action_type"),
            record.get("risk_level"),
            record.get("category"),
            (record.get("recovery_reason") or "")[:300] or None,
            record.get("verification_status"),
            created_at,
            updated_at,
            json.dumps(payload, separators=(",", ":")),
            json.dumps(safe_timeline, separators=(",", ":")),
            json.dumps(performance, separators=(",", ":")),
        )
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO action_lifecycles (
                        lifecycle_id, session_id, plan_id, step_id, confirmation_id,
                        execution_id, tab_id, window_id, state, task, action_type,
                        risk_level, category, recovery_reason, verification_status,
                        created_at, updated_at, payload_json, timeline_json,
                        performance_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(lifecycle_id) DO UPDATE SET
                        state=excluded.state,
                        confirmation_id=excluded.confirmation_id,
                        execution_id=excluded.execution_id,
                        recovery_reason=excluded.recovery_reason,
                        verification_status=excluded.verification_status,
                        timeline_json=excluded.timeline_json,
                        payload_json=excluded.payload_json,
                        performance_json=excluded.performance_json,
                        updated_at=excluded.updated_at,
                        risk_level=excluded.risk_level,
                        category=excluded.category
                    """,
                    params,
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc

        out = dict(record)
        out["state"] = state
        out["payload"] = payload
        out["timeline"] = safe_timeline
        out["performance"] = {
            **performance,
            "action_persistence_ms": round((time.perf_counter() - t0) * 1000, 3),
        }
        out["created_at"] = created_at
        out["updated_at"] = updated_at
        return out

    def append_event(
        self,
        *,
        lifecycle_id: str,
        to_state: str,
        from_state: Optional[str] = None,
        reason: str = "",
        session_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        action_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        confirmation_id: Optional[str] = None,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.db.require_available()
        meta = assert_safe_metadata(safe_metadata or {}, context="lifecycle.event")
        event_id = new_lifecycle_event_id()
        ts = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO action_lifecycle_events (
                    event_id, lifecycle_id, session_id, plan_id, step_id, action_id,
                    execution_id, confirmation_id, from_state, to_state, reason,
                    timestamp, safe_metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    lifecycle_id,
                    session_id,
                    plan_id,
                    step_id,
                    action_id,
                    execution_id,
                    confirmation_id,
                    from_state,
                    to_state,
                    (reason or "")[:300],
                    ts,
                    json.dumps(meta, separators=(",", ":")),
                ),
            )
        return {
            "event_id": event_id,
            "lifecycle_id": lifecycle_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
            "timestamp": ts,
            "safe_metadata": meta,
        }

    def get(self, lifecycle_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT * FROM action_lifecycles WHERE lifecycle_id = ?",
            (lifecycle_id,),
        )
        return self._row_to_record(row) if row else None

    def list_by_states(
        self, states: Sequence[str], *, limit: int = 200
    ) -> List[Dict[str, Any]]:
        self.db.require_available()
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        rows = self.db.fetchall(
            f"SELECT * FROM action_lifecycles WHERE state IN ({placeholders}) "
            f"ORDER BY updated_at DESC LIMIT ?",
            (*states, int(limit)),
        )
        return [self._row_to_record(r) for r in rows]

    def list_recovery_required(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        return self.list_by_states(["recovery_required"], limit=limit)

    def timeline(self, lifecycle_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        self.db.require_available()
        rows = self.db.fetchall(
            "SELECT * FROM action_lifecycle_events WHERE lifecycle_id = ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (lifecycle_id, int(limit)),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                meta = json.loads(r["safe_metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                meta = {}
            out.append(
                {
                    "event_id": r["event_id"],
                    "lifecycle_id": r["lifecycle_id"],
                    "session_id": r["session_id"],
                    "plan_id": r["plan_id"],
                    "step_id": r["step_id"],
                    "execution_id": r["execution_id"],
                    "confirmation_id": r["confirmation_id"],
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "reason": r["reason"],
                    "timestamp": r["timestamp"],
                    "safe_metadata": meta,
                }
            )
        return out

    def mark_recovery_required(
        self, lifecycle_id: str, *, reason: str
    ) -> Optional[Dict[str, Any]]:
        rec = self.get(lifecycle_id)
        if not rec:
            return None
        prev = rec.get("state")
        rec["state"] = "recovery_required"
        rec["recovery_reason"] = reason
        rec["updated_at"] = time.time()
        timeline = list(rec.get("timeline") or [])
        timeline.append(
            {"state": "recovery_required", "reason": reason, "ts": time.time()}
        )
        rec["timeline"] = timeline
        self.upsert(rec)
        self.append_event(
            lifecycle_id=lifecycle_id,
            from_state=prev,
            to_state="recovery_required",
            reason=reason,
            session_id=rec.get("session_id"),
            plan_id=rec.get("plan_id"),
            step_id=rec.get("step_id"),
            execution_id=rec.get("execution_id"),
            confirmation_id=rec.get("confirmation_id"),
        )
        return self.get(lifecycle_id)

    @staticmethod
    def _row_to_record(row) -> Dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        try:
            timeline = json.loads(row["timeline_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            timeline = []
        try:
            performance = json.loads(row["performance_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            performance = {}
        return {
            "lifecycle_id": row["lifecycle_id"],
            "session_id": row["session_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "confirmation_id": row["confirmation_id"],
            "execution_id": row["execution_id"],
            "tab_id": row["tab_id"],
            "window_id": row["window_id"],
            "state": row["state"],
            "task": row["task"],
            "action_type": row["action_type"],
            "risk_level": row["risk_level"],
            "category": row["category"],
            "recovery_reason": row["recovery_reason"],
            "verification_status": row["verification_status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "payload": payload,
            "timeline": timeline,
            "performance": performance,
        }
