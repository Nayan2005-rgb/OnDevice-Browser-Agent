"""Persistent AgentSession model and explicit state machine (Milestone 5A)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional


# ---------------------------------------------------------------------------
# Session statuses
# ---------------------------------------------------------------------------
SESSION_CREATED = "created"
SESSION_RUNNING = "running"
SESSION_PAUSED = "paused"
SESSION_WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
SESSION_WAITING_FOR_BROWSER = "waiting_for_browser"
SESSION_REQUIRES_USER_INTERVENTION = "requires_user_intervention"
SESSION_RECOVERING = "recovering"
SESSION_COMPLETED = "completed"
SESSION_FAILED = "failed"
SESSION_CANCELLED = "cancelled"
SESSION_EXPIRED = "expired"

ALL_SESSION_STATUSES: FrozenSet[str] = frozenset(
    {
        SESSION_CREATED,
        SESSION_RUNNING,
        SESSION_PAUSED,
        SESSION_WAITING_FOR_CONFIRMATION,
        SESSION_WAITING_FOR_BROWSER,
        SESSION_REQUIRES_USER_INTERVENTION,
        SESSION_RECOVERING,
        SESSION_COMPLETED,
        SESSION_FAILED,
        SESSION_CANCELLED,
        SESSION_EXPIRED,
    }
)

ALLOWED_SESSION_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    SESSION_CREATED: frozenset(
        {
            SESSION_RUNNING,
            SESSION_WAITING_FOR_BROWSER,
            SESSION_PAUSED,
            SESSION_CANCELLED,
            SESSION_FAILED,
        }
    ),
    SESSION_RUNNING: frozenset(
        {
            SESSION_PAUSED,
            SESSION_WAITING_FOR_CONFIRMATION,
            SESSION_WAITING_FOR_BROWSER,
            SESSION_REQUIRES_USER_INTERVENTION,
            SESSION_RECOVERING,
            SESSION_COMPLETED,
            SESSION_FAILED,
            SESSION_CANCELLED,
        }
    ),
    SESSION_PAUSED: frozenset(
        {
            SESSION_RUNNING,
            SESSION_WAITING_FOR_BROWSER,
            SESSION_RECOVERING,
            SESSION_REQUIRES_USER_INTERVENTION,
            SESSION_CANCELLED,
            SESSION_FAILED,
            SESSION_EXPIRED,
        }
    ),
    SESSION_WAITING_FOR_CONFIRMATION: frozenset(
        {
            SESSION_RUNNING,
            SESSION_PAUSED,
            SESSION_CANCELLED,
            SESSION_FAILED,
            SESSION_WAITING_FOR_BROWSER,
        }
    ),
    SESSION_WAITING_FOR_BROWSER: frozenset(
        {
            SESSION_RECOVERING,
            SESSION_RUNNING,
            SESSION_PAUSED,
            SESSION_CANCELLED,
            SESSION_FAILED,
            SESSION_EXPIRED,
        }
    ),
    SESSION_REQUIRES_USER_INTERVENTION: frozenset(
        {
            SESSION_RUNNING,
            SESSION_PAUSED,
            SESSION_CANCELLED,
            SESSION_FAILED,
            SESSION_WAITING_FOR_BROWSER,
        }
    ),
    SESSION_RECOVERING: frozenset(
        {
            SESSION_RUNNING,
            SESSION_PAUSED,
            SESSION_REQUIRES_USER_INTERVENTION,
            SESSION_WAITING_FOR_BROWSER,
            SESSION_CANCELLED,
            SESSION_FAILED,
        }
    ),
    SESSION_COMPLETED: frozenset(),
    SESSION_FAILED: frozenset(),
    SESSION_CANCELLED: frozenset(),
    SESSION_EXPIRED: frozenset(),
}

TERMINAL_SESSION_STATUSES: FrozenSet[str] = frozenset(
    {SESSION_COMPLETED, SESSION_FAILED, SESSION_CANCELLED, SESSION_EXPIRED}
)

ACTIVE_SESSION_STATUSES: FrozenSet[str] = frozenset(
    ALL_SESSION_STATUSES - TERMINAL_SESSION_STATUSES
)

# Recovery visualization statuses
RECOVERY_WAITING_FOR_BROWSER = "waiting_for_browser"
RECOVERY_RECOVERING = "recovering"
RECOVERY_VALIDATING = "validating"
RECOVERY_READY = "ready"
RECOVERY_REPLANNING = "replanning"
RECOVERY_REQUIRES_INTERVENTION = "requires_user_intervention"
RECOVERY_FAILED = "failed"

# Event types (append-only timeline)
EVENT_SESSION_CREATED = "session_created"
EVENT_PLAN_CREATED = "plan_created"
EVENT_STEP_STARTED = "step_started"
EVENT_STEP_COMPLETED = "step_completed"
EVENT_STEP_FAILED = "step_failed"
EVENT_PAGE_CHANGED = "page_changed"
EVENT_REPLAN_CREATED = "replan_created"
EVENT_REPLAN_APPROVED = "replan_approved"
EVENT_REPLAN_REJECTED = "replan_rejected"
EVENT_CONFIRMATION_REQUESTED = "confirmation_requested"
EVENT_CONFIRMATION_APPROVED = "confirmation_approved"
EVENT_CONFIRMATION_CANCELLED = "confirmation_cancelled"
EVENT_PAUSED = "paused"
EVENT_RESUMED = "resumed"
EVENT_RECOVERY_STARTED = "recovery_started"
EVENT_RECOVERY_COMPLETED = "recovery_completed"
EVENT_USER_INTERVENTION_REQUIRED = "user_intervention_required"
EVENT_SESSION_COMPLETED = "session_completed"
EVENT_SESSION_FAILED = "session_failed"
EVENT_SESSION_CANCELLED = "session_cancelled"


class InvalidSessionTransitionError(ValueError):
    def __init__(self, current: str, target: str, message: str | None = None):
        self.current = current
        self.target = target
        super().__init__(message or f"Invalid session transition: {current} → {target}")


def new_session_id() -> str:
    return f"sess_{secrets.token_hex(6)}"


def short_session_label(session_id: str) -> str:
    """Human-friendly short id for dashboard (e.g. #A91F23)."""
    raw = (session_id or "").replace("sess_", "")[:6].upper()
    return f"#{raw}" if raw else "#??????"


@dataclass
class AgentSession:
    """Privacy-safe persistent agent session."""

    session_id: str
    status: str = SESSION_CREATED
    plan_id: Optional[str] = None
    current_step_index: int = 0
    plan_version: int = 1
    replan_count: int = 0
    tab_id: Optional[int] = None
    window_id: Optional[int] = None
    last_page_signature: Optional[str] = None
    last_url_signature: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    pause_reason: Optional[str] = None
    intervention_reason: Optional[str] = None
    recovery_status: Optional[str] = None
    operator_replan_approval: bool = False
    cancel_requested: bool = False
    goal: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    performance: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, str]] = field(default_factory=list)

    def can_transition(self, target: str) -> bool:
        if target not in ALL_SESSION_STATUSES:
            return False
        return target in ALLOWED_SESSION_TRANSITIONS.get(self.status, frozenset())

    def transition(self, target: str, reason: str = "") -> str:
        if target not in ALL_SESSION_STATUSES:
            raise InvalidSessionTransitionError(
                self.status, target, f"Unknown session status: {target}"
            )
        if not self.can_transition(target):
            raise InvalidSessionTransitionError(self.status, target)
        previous = self.status
        self.status = target
        self.updated_at = time.time()
        self.history.append({"from": previous, "to": target, "reason": reason or ""})
        return self.status

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_SESSION_STATUSES

    def is_active(self) -> bool:
        return self.status in ACTIVE_SESSION_STATUSES

    def to_record(self) -> Dict[str, Any]:
        """Internal persistence record (still privacy-safe)."""
        return {
            "session_id": self.session_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "current_step_index": self.current_step_index,
            "plan_version": self.plan_version,
            "replan_count": self.replan_count,
            "tab_id": self.tab_id,
            "window_id": self.window_id,
            "last_page_signature": self.last_page_signature,
            "last_url_signature": self.last_url_signature,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pause_reason": self.pause_reason,
            "intervention_reason": self.intervention_reason,
            "recovery_status": self.recovery_status,
            "operator_replan_approval": self.operator_replan_approval,
            "cancel_requested": self.cancel_requested,
            "goal": (self.goal or "")[:500],
            "metadata": dict(self.metadata or {}),
            "performance": dict(self.performance or {}),
        }

    def public_view(self) -> Dict[str, Any]:
        """API / dashboard projection — no internal paths or secrets."""
        return {
            "session_id": self.session_id,
            "session_label": short_session_label(self.session_id),
            "plan_id": self.plan_id,
            "status": self.status,
            "current_step_index": self.current_step_index,
            "plan_version": self.plan_version,
            "replan_count": self.replan_count,
            "tab_id": self.tab_id,
            "window_id": self.window_id,
            "last_page_signature": self.last_page_signature,
            "last_url_signature": self.last_url_signature,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pause_reason": self.pause_reason,
            "intervention_reason": self.intervention_reason,
            "recovery_status": self.recovery_status,
            "operator_replan_approval": bool(self.operator_replan_approval),
            "cancel_requested": bool(self.cancel_requested),
            "goal": (self.goal or "")[:300],
            "performance": {
                k: v
                for k, v in (self.performance or {}).items()
                if isinstance(v, (int, float, str, bool)) and "path" not in k.lower()
            },
            "is_terminal": self.is_terminal(),
            "is_active": self.is_active(),
        }

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "AgentSession":
        return cls(
            session_id=record["session_id"],
            status=record.get("status") or SESSION_CREATED,
            plan_id=record.get("plan_id"),
            current_step_index=int(record.get("current_step_index") or 0),
            plan_version=int(record.get("plan_version") or 1),
            replan_count=int(record.get("replan_count") or 0),
            tab_id=record.get("tab_id"),
            window_id=record.get("window_id"),
            last_page_signature=record.get("last_page_signature"),
            last_url_signature=record.get("last_url_signature"),
            created_at=float(record.get("created_at") or time.time()),
            updated_at=float(record.get("updated_at") or time.time()),
            pause_reason=record.get("pause_reason"),
            intervention_reason=record.get("intervention_reason"),
            recovery_status=record.get("recovery_status"),
            operator_replan_approval=bool(record.get("operator_replan_approval")),
            cancel_requested=bool(record.get("cancel_requested")),
            goal=record.get("goal") or "",
            metadata=dict(record.get("metadata") or {}),
            performance=dict(record.get("performance") or {}),
        )


def to_public_session(session: AgentSession | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(session, AgentSession):
        return session.public_view()
    return AgentSession.from_record(session).public_view()


def to_public_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "session_id": event.get("session_id"),
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "plan_version": event.get("plan_version"),
        "step_index": event.get("step_index"),
        "safe_metadata": dict(event.get("safe_metadata") or {}),
    }


def to_public_plan(plan_view: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure plan payloads destined for API stay within public_view shape."""
    # Already expected to be TaskPlan.public_view(); strip accidental internals
    blocked = {"last_page_state", "meta", "started_perf"}
    return {k: v for k, v in plan_view.items() if k not in blocked}
