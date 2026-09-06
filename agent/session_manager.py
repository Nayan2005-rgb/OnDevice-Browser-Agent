"""Session manager — authoritative persistent session lifecycle (Milestone 5A)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from agent.session import (
    EVENT_CONFIRMATION_APPROVED,
    EVENT_CONFIRMATION_CANCELLED,
    EVENT_CONFIRMATION_REQUESTED,
    EVENT_PAGE_CHANGED,
    EVENT_PAUSED,
    EVENT_PLAN_CREATED,
    EVENT_RECOVERY_COMPLETED,
    EVENT_RECOVERY_STARTED,
    EVENT_REPLAN_APPROVED,
    EVENT_REPLAN_CREATED,
    EVENT_REPLAN_REJECTED,
    EVENT_RESUMED,
    EVENT_SESSION_CANCELLED,
    EVENT_SESSION_COMPLETED,
    EVENT_SESSION_CREATED,
    EVENT_SESSION_FAILED,
    EVENT_STEP_COMPLETED,
    EVENT_STEP_FAILED,
    EVENT_STEP_STARTED,
    EVENT_USER_INTERVENTION_REQUIRED,
    RECOVERY_WAITING_FOR_BROWSER,
    SESSION_CANCELLED,
    SESSION_COMPLETED,
    SESSION_CREATED,
    SESSION_FAILED,
    SESSION_PAUSED,
    SESSION_RECOVERING,
    SESSION_REQUIRES_USER_INTERVENTION,
    SESSION_RUNNING,
    SESSION_WAITING_FOR_BROWSER,
    SESSION_WAITING_FOR_CONFIRMATION,
    AgentSession,
    InvalidSessionTransitionError,
    new_session_id,
    to_public_event,
    to_public_session,
)
from agent.task_plan import TaskPlan
from agent.task_plan_registry import TaskPlanRegistry, get_task_plan_registry
from privacy.persistence_validator import PersistencePrivacyError, validate_for_persistence
from storage.database import Database, StorageUnavailableError, get_database
from storage.repositories.action_repository import ActionRepository
from storage.repositories.event_repository import EventRepository
from storage.repositories.plan_repository import PlanRepository
from storage.repositories.session_repository import SessionRepository


class SessionManager:
    """Owns durable AgentSession records and write-through plan persistence."""

    def __init__(
        self,
        db: Optional[Database] = None,
        *,
        plan_registry: Optional[TaskPlanRegistry] = None,
    ) -> None:
        self.db = db if db is not None else get_database()
        self.sessions = SessionRepository(self.db)
        self.plans = PlanRepository(self.db)
        self.events = EventRepository(self.db)
        self.actions = ActionRepository(self.db)
        self.plan_registry = plan_registry or get_task_plan_registry()
        self._lock = threading.RLock()
        self._cache: Dict[str, AgentSession] = {}
        self._plan_to_session: Dict[str, str] = {}
        self._storage_failed = False

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    def storage_available(self) -> bool:
        return bool(self.db.available) and not self._storage_failed

    def _fail_closed(self, exc: Exception) -> None:
        self._storage_failed = True
        try:
            self.db.mark_unavailable(str(exc))
        except Exception:
            pass

    def require_storage(self) -> None:
        if not self.storage_available():
            raise StorageUnavailableError(
                self.db.last_error or "Persistent storage unavailable"
            )

    # ------------------------------------------------------------------
    # Create / load
    # ------------------------------------------------------------------
    def create_session(
        self,
        *,
        goal: str = "",
        plan: Optional[TaskPlan] = None,
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
        operator_replan_approval: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        t0 = time.perf_counter()
        self.require_storage()
        session = AgentSession(
            session_id=new_session_id(),
            status=SESSION_CREATED,
            goal=(goal or (plan.goal if plan else ""))[:500],
            tab_id=tab_id if tab_id is not None else (plan.tab_id if plan else None),
            window_id=window_id
            if window_id is not None
            else (plan.window_id if plan else None),
            operator_replan_approval=bool(operator_replan_approval),
            metadata=dict(metadata or {}),
        )
        if plan is not None:
            session.plan_id = plan.plan_id
            session.current_step_index = plan.current_step_index
            session.plan_version = int(plan.plan_version or 1)
            session.replan_count = int(plan.replan_count or 0)
            session.last_page_signature = plan.last_page_signature
            plan.meta["session_id"] = session.session_id
            self.plan_registry.put(plan)
        session.performance["session_creation_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        try:
            t_p = time.perf_counter()
            self.sessions.upsert(session.to_record())
            if plan is not None:
                self.persist_plan(plan, session_id=session.session_id)
            self.append_event(
                session.session_id,
                EVENT_SESSION_CREATED,
                plan_version=session.plan_version,
                safe_metadata={"goal": session.goal[:120]},
            )
            if plan is not None:
                self.append_event(
                    session.session_id,
                    EVENT_PLAN_CREATED,
                    plan_version=session.plan_version,
                    safe_metadata={"plan_id": plan.plan_id},
                )
            session.performance["session_persistence_ms"] = round(
                (time.perf_counter() - t_p) * 1000, 3
            )
            self.sessions.upsert(session.to_record())
        except (StorageUnavailableError, PersistencePrivacyError) as exc:
            self._fail_closed(exc)
            raise
        with self._lock:
            self._cache[session.session_id] = session
            if session.plan_id:
                self._plan_to_session[session.plan_id] = session.session_id
        return session

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        with self._lock:
            if session_id in self._cache:
                return self._cache[session_id]
        self.require_storage()
        t0 = time.perf_counter()
        row = self.sessions.get(session_id)
        if not row:
            return None
        session = AgentSession.from_record(row)
        session.performance["session_load_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        with self._lock:
            self._cache[session_id] = session
            if session.plan_id:
                self._plan_to_session[session.plan_id] = session_id
        return session

    def get_session_for_plan(self, plan_id: str) -> Optional[AgentSession]:
        with self._lock:
            sid = self._plan_to_session.get(plan_id)
        if sid:
            return self.get_session(sid)
        self.require_storage()
        # Scan active sessions (small local store)
        for row in self.sessions.list_sessions(active_only=False, limit=200):
            if row.get("plan_id") == plan_id:
                return self.get_session(row["session_id"])
        return None

    def get_active_for_tab(self, tab_id: int) -> Optional[AgentSession]:
        self.require_storage()
        rows = self.sessions.list_sessions(tab_id=tab_id, active_only=True, limit=5)
        if not rows:
            return None
        return self.get_session(rows[0]["session_id"])

    def list_sessions(
        self, *, active_only: bool = False, limit: int = 50
    ) -> List[AgentSession]:
        self.require_storage()
        rows = self.sessions.list_sessions(active_only=active_only, limit=limit)
        return [AgentSession.from_record(r) for r in rows]

    def persist_session(self, session: AgentSession) -> AgentSession:
        self.require_storage()
        session.updated_at = time.time()
        try:
            self.sessions.upsert(session.to_record())
        except (StorageUnavailableError, PersistencePrivacyError) as exc:
            self._fail_closed(exc)
            raise
        with self._lock:
            self._cache[session.session_id] = session
            if session.plan_id:
                self._plan_to_session[session.plan_id] = session.session_id
        return session

    def persist_plan(self, plan: TaskPlan, *, session_id: Optional[str] = None) -> None:
        self.require_storage()
        sid = session_id or (plan.meta or {}).get("session_id")
        payload = plan.serialize_for_persistence()
        if sid:
            payload["session_id"] = sid
        validate_for_persistence(payload, context="task_plan")
        try:
            self.plans.upsert(payload, session_id=sid)
        except (StorageUnavailableError, PersistencePrivacyError) as exc:
            self._fail_closed(exc)
            raise
        self.plan_registry.put(plan)

    def load_plan_into_registry(self, plan_id: str) -> Optional[TaskPlan]:
        existing = self.plan_registry.get(plan_id)
        if existing:
            return existing
        self.require_storage()
        payload = self.plans.get(plan_id)
        if not payload:
            return None
        plan = TaskPlan.from_persistence(payload)
        self.plan_registry.put(plan)
        return plan

    def sync_from_plan(self, plan: TaskPlan) -> Optional[AgentSession]:
        """Update session fields from an in-memory plan and persist both."""
        session = self.get_session_for_plan(plan.plan_id)
        if not session:
            return None
        session.current_step_index = plan.current_step_index
        session.plan_version = int(plan.plan_version or 1)
        session.replan_count = int(plan.replan_count or 0)
        session.last_page_signature = plan.last_page_signature
        session.goal = plan.goal or session.goal
        session.tab_id = plan.tab_id if plan.tab_id is not None else session.tab_id
        session.window_id = (
            plan.window_id if plan.window_id is not None else session.window_id
        )
        if plan.requires_user_intervention:
            session.intervention_reason = plan.intervention_reason
        # Map plan status → session status (conservative)
        mapped = self._map_plan_status(plan, session)
        if mapped and mapped != session.status and session.can_transition(mapped):
            try:
                session.transition(mapped, f"Synced from plan status {plan.status}")
            except InvalidSessionTransitionError:
                pass
        elif mapped == session.status:
            session.updated_at = time.time()
        self.persist_plan(plan, session_id=session.session_id)
        self.persist_session(session)
        return session

    def _map_plan_status(self, plan: TaskPlan, session: AgentSession) -> Optional[str]:
        from agent.task_plan import (
            PLAN_CANCELLED,
            PLAN_COMPLETED,
            PLAN_FAILED,
            PLAN_PAUSED,
            PLAN_RUNNING,
            PLAN_WAITING_FOR_CONFIRMATION,
        )

        if plan.cancel_requested or plan.status == PLAN_CANCELLED:
            return SESSION_CANCELLED
        if plan.status == PLAN_COMPLETED:
            return SESSION_COMPLETED
        if plan.status == PLAN_FAILED:
            return SESSION_FAILED
        if plan.requires_user_intervention:
            return SESSION_REQUIRES_USER_INTERVENTION
        if plan.status == PLAN_PAUSED:
            return SESSION_PAUSED
        if plan.status == PLAN_WAITING_FOR_CONFIRMATION:
            return SESSION_WAITING_FOR_CONFIRMATION
        if plan.status == PLAN_RUNNING:
            if session.status == SESSION_WAITING_FOR_BROWSER:
                return SESSION_WAITING_FOR_BROWSER
            if session.status == SESSION_RECOVERING:
                return SESSION_RECOVERING
            return SESSION_RUNNING
        return None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def append_event(
        self,
        session_id: str,
        event_type: str,
        *,
        plan_version: Optional[int] = None,
        step_index: Optional[int] = None,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.require_storage()
        try:
            return self.events.append(
                session_id=session_id,
                event_type=event_type,
                plan_version=plan_version,
                step_index=step_index,
                safe_metadata=safe_metadata or {},
            )
        except (StorageUnavailableError, PersistencePrivacyError) as exc:
            self._fail_closed(exc)
            raise

    def timeline(self, session_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
        self.require_storage()
        return [to_public_event(e) for e in self.events.list_for_session(session_id, limit=limit)]

    # ------------------------------------------------------------------
    # Replan preview persistence
    # ------------------------------------------------------------------
    def save_replan_preview(self, session_id: str, preview: Dict[str, Any]) -> Dict[str, Any]:
        self.require_storage()
        validate_for_persistence(preview, context="replan_preview")
        preview_id = preview.get("preview_id") or f"rpv_{session_id[-8:]}"
        payload = dict(preview)
        payload["preview_id"] = preview_id
        payload["session_id"] = session_id
        payload.setdefault("status", "pending")
        payload.setdefault("created_at", time.time())
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO replan_previews (
                    preview_id, session_id, plan_id, plan_version, proposed_version,
                    reason, page_change_level, created_at, status, payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    preview_id=excluded.preview_id,
                    plan_id=excluded.plan_id,
                    plan_version=excluded.plan_version,
                    proposed_version=excluded.proposed_version,
                    reason=excluded.reason,
                    page_change_level=excluded.page_change_level,
                    created_at=excluded.created_at,
                    status=excluded.status,
                    payload_json=excluded.payload_json
                """,
                (
                    preview_id,
                    session_id,
                    payload.get("plan_id"),
                    int(payload.get("plan_version") or 1),
                    int(payload.get("proposed_version") or 2),
                    (payload.get("reason") or "")[:200],
                    payload.get("page_change_level"),
                    float(payload.get("created_at") or time.time()),
                    payload.get("status") or "pending",
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
        self.append_event(
            session_id,
            EVENT_REPLAN_CREATED,
            plan_version=int(payload.get("plan_version") or 1),
            safe_metadata={
                "reason": (payload.get("reason") or "")[:120],
                "page_change_level": payload.get("page_change_level"),
                "proposed_version": payload.get("proposed_version"),
            },
        )
        return payload

    def get_replan_preview(self, session_id: str) -> Optional[Dict[str, Any]]:
        self.require_storage()
        row = self.db.fetchone(
            "SELECT payload_json, status FROM replan_previews WHERE session_id = ?",
            (session_id,),
        )
        if not row:
            return None
        try:
            data = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        data["status"] = row["status"]
        return data

    def clear_replan_preview(self, session_id: str, *, status: str = "cleared") -> None:
        self.require_storage()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE replan_previews SET status = ? WHERE session_id = ?",
                (status, session_id),
            )
            if status in ("approved", "rejected", "cleared"):
                conn.execute(
                    "DELETE FROM replan_previews WHERE session_id = ?",
                    (session_id,),
                )

    # ------------------------------------------------------------------
    # Hydration after process start
    # ------------------------------------------------------------------
    def hydrate_active_sessions(self) -> List[AgentSession]:
        """Load active sessions into memory after server restart.

        Does NOT auto-execute. Marks sessions waiting_for_browser / recovering.
        """
        self.require_storage()
        t0 = time.perf_counter()
        restored: List[AgentSession] = []
        for row in self.sessions.list_sessions(active_only=True, limit=100):
            session = AgentSession.from_record(row)
            if session.plan_id:
                self.load_plan_into_registry(session.plan_id)
                self._plan_to_session[session.plan_id] = session.session_id
            # Never auto-run after restart — wait for fresh perception
            if session.status in (
                SESSION_RUNNING,
                SESSION_WAITING_FOR_CONFIRMATION,
                SESSION_RECOVERING,
            ):
                try:
                    if session.can_transition(SESSION_WAITING_FOR_BROWSER):
                        session.transition(
                            SESSION_WAITING_FOR_BROWSER, "Server restart — await browser"
                        )
                    session.recovery_status = RECOVERY_WAITING_FOR_BROWSER
                    self.persist_session(session)
                    self.append_event(
                        session.session_id,
                        EVENT_RECOVERY_STARTED,
                        plan_version=session.plan_version,
                        safe_metadata={"reason": "server_restart"},
                    )
                except InvalidSessionTransitionError:
                    pass
            self._cache[session.session_id] = session
            restored.append(session)
        ms = round((time.perf_counter() - t0) * 1000, 3)
        for s in restored:
            s.performance["session_recovery_ms"] = ms
        return restored

    def public_session_bundle(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.get_session(session_id)
        if not session:
            return None
        plan = None
        if session.plan_id:
            plan_obj = self.load_plan_into_registry(session.plan_id)
            if plan_obj:
                plan = plan_obj.public_view()
        preview = None
        try:
            preview = self.get_replan_preview(session_id)
        except StorageUnavailableError:
            preview = None
        return {
            "session": to_public_session(session),
            "plan": plan,
            "replan_preview": preview,
            "timeline": self.timeline(session_id, limit=200),
        }


_manager: Optional[SessionManager] = None
_manager_lock = threading.Lock()


def get_session_manager() -> SessionManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SessionManager()
        return _manager


def reset_session_manager(
    db: Optional[Database] = None,
    *,
    plan_registry: Optional[TaskPlanRegistry] = None,
) -> SessionManager:
    global _manager
    with _manager_lock:
        _manager = SessionManager(db=db, plan_registry=plan_registry)
        return _manager
