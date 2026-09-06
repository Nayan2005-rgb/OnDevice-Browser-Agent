"""Session repository — CRUD for agent_sessions rows."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from privacy.persistence_validator import assert_safe_metadata, validate_for_persistence
from storage.database import Database, StorageUnavailableError
from storage.schemas import ACTIVE_SESSION_STATUSES


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row)
    for key in ("metadata_json", "performance_json"):
        raw = d.pop(key, None)
        field = "metadata" if key.startswith("metadata") else "performance"
        try:
            d[field] = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            d[field] = {}
    d["operator_replan_approval"] = bool(d.get("operator_replan_approval"))
    d["cancel_requested"] = bool(d.get("cancel_requested"))
    return d


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, session: Dict[str, Any]) -> Dict[str, Any]:
        self.db.require_available()
        metadata = assert_safe_metadata(session.get("metadata") or {}, context="session.metadata")
        performance = assert_safe_metadata(
            session.get("performance") or {}, context="session.performance"
        )
        # Validate whole public-ish payload keys
        safe_check = {
            k: v
            for k, v in session.items()
            if k not in ("metadata", "performance") and not isinstance(v, (dict, list))
        }
        validate_for_persistence(safe_check, context="session")

        now = time.time()
        created_at = float(session.get("created_at") or now)
        updated_at = float(session.get("updated_at") or now)
        params = (
            session["session_id"],
            session.get("plan_id"),
            session["status"],
            int(session.get("current_step_index") or 0),
            int(session.get("plan_version") or 1),
            int(session.get("replan_count") or 0),
            session.get("tab_id"),
            session.get("window_id"),
            session.get("last_page_signature"),
            session.get("last_url_signature"),
            created_at,
            updated_at,
            session.get("pause_reason"),
            session.get("intervention_reason"),
            session.get("recovery_status"),
            1 if session.get("operator_replan_approval") else 0,
            1 if session.get("cancel_requested") else 0,
            (session.get("goal") or "")[:500],
            json.dumps(metadata, separators=(",", ":")),
            json.dumps(performance, separators=(",", ":")),
        )
        t0 = time.perf_counter()
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_sessions (
                        session_id, plan_id, status, current_step_index, plan_version,
                        replan_count, tab_id, window_id, last_page_signature,
                        last_url_signature, created_at, updated_at, pause_reason,
                        intervention_reason, recovery_status, operator_replan_approval,
                        cancel_requested, goal, metadata_json, performance_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        plan_id=excluded.plan_id,
                        status=excluded.status,
                        current_step_index=excluded.current_step_index,
                        plan_version=excluded.plan_version,
                        replan_count=excluded.replan_count,
                        tab_id=excluded.tab_id,
                        window_id=excluded.window_id,
                        last_page_signature=excluded.last_page_signature,
                        last_url_signature=excluded.last_url_signature,
                        updated_at=excluded.updated_at,
                        pause_reason=excluded.pause_reason,
                        intervention_reason=excluded.intervention_reason,
                        recovery_status=excluded.recovery_status,
                        operator_replan_approval=excluded.operator_replan_approval,
                        cancel_requested=excluded.cancel_requested,
                        goal=excluded.goal,
                        metadata_json=excluded.metadata_json,
                        performance_json=excluded.performance_json
                    """,
                    params,
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc
        write_ms = round((time.perf_counter() - t0) * 1000, 3)
        out = dict(session)
        out["metadata"] = metadata
        out["performance"] = {**performance, "database_write_ms": write_ms}
        out["created_at"] = created_at
        out["updated_at"] = updated_at
        return out

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        self.db.require_available()
        t0 = time.perf_counter()
        row = self.db.fetchone(
            "SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)
        )
        read_ms = round((time.perf_counter() - t0) * 1000, 3)
        if not row:
            return None
        d = _row_to_dict(row)
        d.setdefault("performance", {})["database_read_ms"] = read_ms
        return d

    def list_sessions(
        self,
        *,
        status: Optional[str] = None,
        tab_id: Optional[int] = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self.db.require_available()
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if tab_id is not None:
            clauses.append("tab_id = ?")
            params.append(tab_id)
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_SESSION_STATUSES)
            clauses.append(f"status IN ({placeholders})")
            params.extend(ACTIVE_SESSION_STATUSES)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM agent_sessions{where} "
            f"ORDER BY updated_at DESC LIMIT ?"
        )
        params.append(int(limit))
        rows = self.db.fetchall(sql, tuple(params))
        return [_row_to_dict(r) for r in rows]

    def delete(self, session_id: str) -> None:
        """Hard delete is discouraged; prefer status=cancelled/expired."""
        self.db.require_available()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
