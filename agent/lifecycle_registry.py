"""Action lifecycle registry with optional SQLite durability (Milestone 4A / 5B)."""

from __future__ import annotations

import secrets
import time
from typing import Any, Dict, List, Optional

from agent.action_state import (
    RECOVERY_REQUIRED,
    ActionLifecycle,
    ActionStateMachine,
    InvalidTransitionError,
)
from agent.recovery_engine import RecoveryEngine
from storage.database import Database


class LifecycleRegistry:
    def __init__(self, db: Optional[Database] = None) -> None:
        self._items: Dict[str, Dict[str, Any]] = {}
        self._latest_id: Optional[str] = None
        self._db = db
        self._repo = None
        if db is not None:
            from storage.repositories.action_lifecycle_repository import (
                ActionLifecycleRepository,
            )

            self._repo = ActionLifecycleRepository(db)

    def create(
        self,
        task: str = "",
        *,
        session_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> ActionLifecycle:
        lifecycle_id = f"life_{secrets.token_hex(8)}"
        life = ActionStateMachine.create(lifecycle_id, task=task)
        now = time.time()
        self._items[lifecycle_id] = {
            "lifecycle": life,
            "created_at": now,
            "timeline": [{"state": "created", "ts": now}],
            "safety": None,
            "confirmation": None,
            "verification": None,
            "recovery_attempts": [],
            "recovery_engine": RecoveryEngine(),
            "pre_action_state": None,
            "action": None,
            "strategy": None,
            "source": None,
            "confidence": None,
            "performance": {},
            "started_perf": time.perf_counter(),
            "session_id": session_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "recovery_reason": None,
        }
        self._latest_id = lifecycle_id
        self._persist(lifecycle_id)
        return life

    def get(self, lifecycle_id: str) -> Optional[Dict[str, Any]]:
        item = self._items.get(lifecycle_id)
        if item:
            return item
        if self._repo is not None:
            row = self._repo.get(lifecycle_id)
            if row:
                return self._hydrate_item(row)
        return None

    def latest(self) -> Optional[Dict[str, Any]]:
        if self._latest_id:
            return self.get(self._latest_id)
        return None

    def transition(self, lifecycle_id: str, state: str, reason: str = "") -> None:
        item = self.get(lifecycle_id)
        if not item:
            return
        life: ActionLifecycle = item["lifecycle"]
        prev = life.state
        life.transition(state, reason=reason)
        item["timeline"].append({"state": state, "ts": time.time(), "reason": reason})
        if state == RECOVERY_REQUIRED:
            item["recovery_reason"] = reason
        self._persist(lifecycle_id)
        if self._repo is not None:
            try:
                self._repo.append_event(
                    lifecycle_id=lifecycle_id,
                    from_state=prev,
                    to_state=state,
                    reason=reason,
                    session_id=item.get("session_id"),
                    plan_id=item.get("plan_id"),
                    step_id=item.get("step_id"),
                    execution_id=item.get("execution_id"),
                    confirmation_id=(item.get("confirmation") or {}).get("id")
                    if isinstance(item.get("confirmation"), dict)
                    else item.get("confirmation_id"),
                )
            except Exception:
                pass

    def mark_recovery_required(self, lifecycle_id: str, reason: str) -> bool:
        item = self.get(lifecycle_id)
        if not item:
            return False
        life: ActionLifecycle = item["lifecycle"]
        if life.state == RECOVERY_REQUIRED:
            item["recovery_reason"] = reason
            self._persist(lifecycle_id)
            return True
        try:
            self.transition(lifecycle_id, RECOVERY_REQUIRED, reason)
            return True
        except InvalidTransitionError:
            # Force via direct state set only when allowed by durable recovery rules —
            # never silently mutate: record failure
            return False

    def list_recovery_required(self) -> List[Dict[str, Any]]:
        out = []
        for lid, item in list(self._items.items()):
            life: ActionLifecycle = item["lifecycle"]
            if life.state == RECOVERY_REQUIRED:
                view = self.public_view(lid)
                if view:
                    out.append(view)
        if self._repo is not None:
            for row in self._repo.list_recovery_required():
                if row["lifecycle_id"] not in self._items:
                    self._hydrate_item(row)
                    view = self.public_view(row["lifecycle_id"])
                    if view:
                        out.append(view)
        return out

    def public_view(self, lifecycle_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        item = self.get(lifecycle_id) if lifecycle_id else self.latest()
        if not item:
            return None
        life: ActionLifecycle = item["lifecycle"]
        total_ms = round((time.perf_counter() - item["started_perf"]) * 1000, 3)
        return {
            "lifecycle_id": life.lifecycle_id,
            "state": life.state,
            "task": life.task,
            "timeline": [
                {"state": t["state"], "reason": t.get("reason") or ""}
                for t in item["timeline"]
            ],
            "strategy": item.get("strategy"),
            "source": item.get("source"),
            "confidence": item.get("confidence"),
            "safety": item.get("safety"),
            "confirmation": item.get("confirmation"),
            "verification": item.get("verification"),
            "recovery_attempts": list(item.get("recovery_attempts") or []),
            "recovery_reason": item.get("recovery_reason"),
            "execution_id": item.get("execution_id"),
            "execution_status": item.get("execution_status") or life.state,
            "session_id": item.get("session_id"),
            "plan_id": item.get("plan_id"),
            "step_id": item.get("step_id"),
            "tab_id": item.get("tab_id"),
            "action_type": (item.get("action") or {}).get("type")
            if isinstance(item.get("action"), dict)
            else None,
            "risk_level": (item.get("safety") or {}).get("level")
            if isinstance(item.get("safety"), dict)
            else item.get("risk_level"),
            "performance": {
                **(item.get("performance") or {}),
                "total_action_lifecycle_ms": total_ms,
            },
            "history": life.history,
        }

    def durable_timeline(self, lifecycle_id: str) -> List[Dict[str, Any]]:
        if self._repo is None:
            item = self.get(lifecycle_id)
            if not item:
                return []
            return [
                {
                    "to_state": t["state"],
                    "reason": t.get("reason") or "",
                    "timestamp": t.get("ts"),
                }
                for t in item.get("timeline") or []
            ]
        return self._repo.timeline(lifecycle_id)

    def hydrate(self) -> Dict[str, Any]:
        """Restore unfinished lifecycles after server restart."""
        t0 = time.perf_counter()
        restored = 0
        recovery_marked = 0
        if self._repo is None:
            return {"restored": 0, "recovery_marked": 0, "crash_recovery_ms": 0.0}
        from storage.schemas import INTERRUPTIBLE_LIFECYCLE_STATES

        rows = self._repo.list_by_states(INTERRUPTIBLE_LIFECYCLE_STATES, limit=500)
        for row in rows:
            state = row.get("state")
            if state in ("claimed", "executing"):
                reason = (
                    "server_restart_during_execution"
                    if state == "executing"
                    else "server_restart_during_claim"
                )
                row["state"] = RECOVERY_REQUIRED
                row["recovery_reason"] = reason
                timeline = list(row.get("timeline") or [])
                timeline.append(
                    {"state": RECOVERY_REQUIRED, "reason": reason, "ts": time.time()}
                )
                row["timeline"] = timeline
                self._repo.upsert(row)
                self._repo.append_event(
                    lifecycle_id=row["lifecycle_id"],
                    from_state=state,
                    to_state=RECOVERY_REQUIRED,
                    reason=reason,
                    session_id=row.get("session_id"),
                    plan_id=row.get("plan_id"),
                    execution_id=row.get("execution_id"),
                    confirmation_id=row.get("confirmation_id"),
                )
                recovery_marked += 1
            self._hydrate_item(row)
            restored += 1
            self._latest_id = row["lifecycle_id"]
        return {
            "restored": restored,
            "recovery_marked": recovery_marked,
            "crash_recovery_ms": round((time.perf_counter() - t0) * 1000, 3),
        }

    def clear(self) -> None:
        self._items.clear()
        self._latest_id = None
        if self._repo is not None and self._db is not None:
            try:
                with self._db.transaction() as conn:
                    conn.execute("DELETE FROM action_lifecycles")
                    conn.execute("DELETE FROM action_lifecycle_events")
            except Exception:
                pass

    def _persist(self, lifecycle_id: str) -> None:
        if self._repo is None:
            return
        item = self._items.get(lifecycle_id)
        if not item:
            return
        life: ActionLifecycle = item["lifecycle"]
        conf = item.get("confirmation")
        confirmation_id = None
        if isinstance(conf, dict):
            confirmation_id = conf.get("id")
        elif item.get("confirmation_id"):
            confirmation_id = item.get("confirmation_id")
        # Never persist raw action selectors with secrets — store safe summary only
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        safe_payload = {
            "action_type": (action or {}).get("type"),
            "has_selector": bool((action or {}).get("selector")),
            "has_coordinates": (action or {}).get("x") is not None,
            # Explicitly omit coordinates from durable trust — recovery must re-resolve
        }
        try:
            self._repo.upsert(
                {
                    "lifecycle_id": lifecycle_id,
                    "session_id": item.get("session_id"),
                    "plan_id": item.get("plan_id"),
                    "step_id": item.get("step_id"),
                    "confirmation_id": confirmation_id,
                    "execution_id": item.get("execution_id"),
                    "tab_id": item.get("tab_id"),
                    "window_id": item.get("window_id"),
                    "state": life.state,
                    "task": life.task,
                    "action_type": (action or {}).get("type"),
                    "risk_level": (item.get("safety") or {}).get("level")
                    if isinstance(item.get("safety"), dict)
                    else item.get("risk_level"),
                    "category": (item.get("safety") or {}).get("category")
                    if isinstance(item.get("safety"), dict)
                    else item.get("category"),
                    "recovery_reason": item.get("recovery_reason"),
                    "verification_status": (item.get("verification") or {}).get("status")
                    if isinstance(item.get("verification"), dict)
                    else None,
                    "created_at": item.get("created_at"),
                    "updated_at": time.time(),
                    "payload": safe_payload,
                    "timeline": item.get("timeline") or [],
                    "performance": item.get("performance") or {},
                }
            )
        except Exception:
            raise

    def _hydrate_item(self, row: Dict[str, Any]) -> Dict[str, Any]:
        lid = row["lifecycle_id"]
        life = ActionLifecycle(
            lifecycle_id=lid,
            state=row.get("state") or "created",
            task=row.get("task") or "",
        )
        item = {
            "lifecycle": life,
            "created_at": row.get("created_at") or time.time(),
            "timeline": list(row.get("timeline") or []),
            "safety": {
                "level": row.get("risk_level"),
                "category": row.get("category"),
            }
            if row.get("risk_level") or row.get("category")
            else None,
            "confirmation": {"id": row.get("confirmation_id")}
            if row.get("confirmation_id")
            else None,
            "verification": {"status": row.get("verification_status")}
            if row.get("verification_status")
            else None,
            "recovery_attempts": [],
            "recovery_engine": RecoveryEngine(),
            "pre_action_state": None,
            "action": {"type": row.get("action_type")} if row.get("action_type") else None,
            "strategy": None,
            "source": None,
            "confidence": None,
            "performance": dict(row.get("performance") or {}),
            "started_perf": time.perf_counter(),
            "session_id": row.get("session_id"),
            "plan_id": row.get("plan_id"),
            "step_id": row.get("step_id"),
            "execution_id": row.get("execution_id"),
            "confirmation_id": row.get("confirmation_id"),
            "tab_id": row.get("tab_id"),
            "window_id": row.get("window_id"),
            "recovery_reason": row.get("recovery_reason"),
            "risk_level": row.get("risk_level"),
            "category": row.get("category"),
            "execution_status": row.get("state"),
        }
        self._items[lid] = item
        return item


_registry: Optional[LifecycleRegistry] = None


def get_lifecycle_registry() -> LifecycleRegistry:
    global _registry
    if _registry is None:
        _registry = LifecycleRegistry()
    return _registry


def reset_lifecycle_registry(db: Optional[Database] = None) -> LifecycleRegistry:
    global _registry
    _registry = LifecycleRegistry(db=db)
    return _registry


def configure_lifecycle_registry(db: Database) -> LifecycleRegistry:
    global _registry
    _registry = LifecycleRegistry(db=db)
    return _registry
