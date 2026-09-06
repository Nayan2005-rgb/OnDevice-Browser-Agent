"""Plan repository — persists full privacy-safe TaskPlan payloads."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from privacy.persistence_validator import assert_safe_metadata, validate_for_persistence
from storage.database import Database, StorageUnavailableError


class PlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, plan_payload: Dict[str, Any], *, session_id: Optional[str] = None) -> Dict[str, Any]:
        self.db.require_available()
        validate_for_persistence(plan_payload, context="task_plan")
        safe = assert_safe_metadata(plan_payload, context="task_plan")
        plan_id = safe.get("plan_id")
        if not plan_id:
            raise ValueError("plan_id required")
        now = time.time()
        params = (
            plan_id,
            session_id or safe.get("session_id"),
            (safe.get("goal") or "")[:500],
            safe.get("status") or "created",
            int(safe.get("current_step_index") or 0),
            int(safe.get("plan_version") or safe.get("version") or 1),
            int(safe.get("replan_count") or 0),
            float(safe.get("created_at") or now),
            float(safe.get("updated_at") or now),
            safe.get("tab_id"),
            safe.get("window_id"),
            json.dumps(safe, separators=(",", ":")),
        )
        t0 = time.perf_counter()
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO task_plans (
                        plan_id, session_id, goal, status, current_step_index,
                        plan_version, replan_count, created_at, updated_at,
                        tab_id, window_id, payload_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                        session_id=excluded.session_id,
                        goal=excluded.goal,
                        status=excluded.status,
                        current_step_index=excluded.current_step_index,
                        plan_version=excluded.plan_version,
                        replan_count=excluded.replan_count,
                        updated_at=excluded.updated_at,
                        tab_id=excluded.tab_id,
                        window_id=excluded.window_id,
                        payload_json=excluded.payload_json
                    """,
                    params,
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc
        safe.setdefault("performance", {})["database_write_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        return safe

    def get(self, plan_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT payload_json, session_id FROM task_plans WHERE plan_id = ?",
            (plan_id,),
        )
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if row["session_id"] and "session_id" not in payload:
            payload["session_id"] = row["session_id"]
        return payload

    def get_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        row = self.db.fetchone(
            "SELECT payload_json FROM task_plans WHERE session_id = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        )
        if not row:
            return None
        try:
            return json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return None

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        self.db.require_available()
        rows = self.db.fetchall(
            "SELECT payload_json FROM task_plans ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r["payload_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
        return out
