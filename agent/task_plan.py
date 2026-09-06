"""Task plan model and explicit state machines for Milestone 4B.

Separates high-level goal decomposition from low-level action execution.
Plans store only safe metadata — never raw screenshots, DOM, or PII.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional


# ---------------------------------------------------------------------------
# Plan statuses
# ---------------------------------------------------------------------------
PLAN_CREATED = "created"
PLAN_PLANNING = "planning"
PLAN_RUNNING = "running"
PLAN_WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
PLAN_PAUSED = "paused"
PLAN_COMPLETED = "completed"
PLAN_FAILED = "failed"
PLAN_CANCELLED = "cancelled"
PLAN_UNSUPPORTED = "unsupported"

ALL_PLAN_STATUSES: FrozenSet[str] = frozenset(
    {
        PLAN_CREATED,
        PLAN_PLANNING,
        PLAN_RUNNING,
        PLAN_WAITING_FOR_CONFIRMATION,
        PLAN_PAUSED,
        PLAN_COMPLETED,
        PLAN_FAILED,
        PLAN_CANCELLED,
        PLAN_UNSUPPORTED,
    }
)

ALLOWED_PLAN_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    PLAN_CREATED: frozenset({PLAN_PLANNING, PLAN_CANCELLED, PLAN_UNSUPPORTED}),
    PLAN_PLANNING: frozenset(
        {PLAN_RUNNING, PLAN_FAILED, PLAN_CANCELLED, PLAN_UNSUPPORTED}
    ),
    PLAN_RUNNING: frozenset(
        {
            PLAN_WAITING_FOR_CONFIRMATION,
            PLAN_PAUSED,
            PLAN_COMPLETED,
            PLAN_FAILED,
            PLAN_CANCELLED,
        }
    ),
    PLAN_WAITING_FOR_CONFIRMATION: frozenset(
        {PLAN_RUNNING, PLAN_PAUSED, PLAN_CANCELLED, PLAN_FAILED}
    ),
    PLAN_PAUSED: frozenset({PLAN_RUNNING, PLAN_CANCELLED, PLAN_FAILED}),
    PLAN_COMPLETED: frozenset(),
    PLAN_FAILED: frozenset(),
    PLAN_CANCELLED: frozenset(),
    PLAN_UNSUPPORTED: frozenset(),
}

TERMINAL_PLAN_STATUSES: FrozenSet[str] = frozenset(
    {PLAN_COMPLETED, PLAN_FAILED, PLAN_CANCELLED, PLAN_UNSUPPORTED}
)

# ---------------------------------------------------------------------------
# Step statuses
# ---------------------------------------------------------------------------
STEP_PENDING = "pending"
STEP_READY = "ready"
STEP_RESOLVING = "resolving"
STEP_REQUIRES_CONFIRMATION = "requires_confirmation"
STEP_APPROVED = "approved"
STEP_EXECUTING = "executing"
STEP_VERIFYING = "verifying"
STEP_SUCCESS = "success"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_CANCELLED = "cancelled"
STEP_WAITING = "waiting"
STEP_RECOVERING = "recovering"

ALL_STEP_STATUSES: FrozenSet[str] = frozenset(
    {
        STEP_PENDING,
        STEP_READY,
        STEP_RESOLVING,
        STEP_REQUIRES_CONFIRMATION,
        STEP_APPROVED,
        STEP_EXECUTING,
        STEP_VERIFYING,
        STEP_SUCCESS,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_CANCELLED,
        STEP_WAITING,
        STEP_RECOVERING,
    }
)

ALLOWED_STEP_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    STEP_PENDING: frozenset(
        {
            STEP_READY,
            STEP_RESOLVING,
            STEP_WAITING,
            STEP_SKIPPED,
            STEP_CANCELLED,
            STEP_FAILED,
        }
    ),
    STEP_READY: frozenset(
        {
            STEP_RESOLVING,
            STEP_REQUIRES_CONFIRMATION,
            STEP_EXECUTING,
            STEP_WAITING,
            STEP_CANCELLED,
            STEP_FAILED,
            STEP_SUCCESS,
            STEP_RECOVERING,
        }
    ),
    STEP_RESOLVING: frozenset(
        {
            STEP_READY,
            STEP_REQUIRES_CONFIRMATION,
            STEP_EXECUTING,
            STEP_WAITING,
            STEP_FAILED,
            STEP_CANCELLED,
            STEP_SUCCESS,
            STEP_RECOVERING,
        }
    ),
    STEP_REQUIRES_CONFIRMATION: frozenset(
        {STEP_APPROVED, STEP_CANCELLED, STEP_FAILED}
    ),
    STEP_APPROVED: frozenset({STEP_EXECUTING, STEP_CANCELLED, STEP_FAILED}),
    STEP_EXECUTING: frozenset(
        {STEP_VERIFYING, STEP_FAILED, STEP_CANCELLED, STEP_SUCCESS, STEP_RECOVERING}
    ),
    STEP_WAITING: frozenset(
        {STEP_VERIFYING, STEP_SUCCESS, STEP_FAILED, STEP_CANCELLED}
    ),
    STEP_VERIFYING: frozenset(
        {STEP_SUCCESS, STEP_FAILED, STEP_RECOVERING, STEP_CANCELLED}
    ),
    STEP_RECOVERING: frozenset(
        {STEP_EXECUTING, STEP_RESOLVING, STEP_FAILED, STEP_CANCELLED, STEP_SUCCESS}
    ),
    STEP_SUCCESS: frozenset(),
    STEP_FAILED: frozenset(),
    STEP_SKIPPED: frozenset(),
    STEP_CANCELLED: frozenset(),
}

TERMINAL_STEP_STATUSES: FrozenSet[str] = frozenset(
    {STEP_SUCCESS, STEP_FAILED, STEP_SKIPPED, STEP_CANCELLED}
)

# Sensitive value patterns — never persist these as typed step values
_SENSITIVE_VALUE_MARKERS = (
    "password",
    "passwd",
    "credit card",
    "card number",
    "cvv",
    "ssn",
    "social security",
)


class InvalidPlanTransitionError(ValueError):
    """Raised when an invalid plan state transition is attempted."""

    def __init__(self, current: str, target: str, message: str | None = None):
        self.current = current
        self.target = target
        super().__init__(message or f"Invalid plan transition: {current} → {target}")


class InvalidStepTransitionError(ValueError):
    """Raised when an invalid step state transition is attempted."""

    def __init__(self, current: str, target: str, message: str | None = None):
        self.current = current
        self.target = target
        super().__init__(message or f"Invalid step transition: {current} → {target}")


def _safe_str(value: Any, max_len: int = 200) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def looks_sensitive_value(text: str) -> bool:
    """Heuristic: treat password-like / payment-like typed values as sensitive."""
    lowered = (text or "").lower()
    if any(m in lowered for m in _SENSITIVE_VALUE_MARKERS):
        return True
    # Long digit runs look like card/SSN material
    digits = "".join(c for c in (text or "") if c.isdigit())
    return len(digits) >= 12


@dataclass
class TaskStep:
    """One high-level semantic step in a task plan (no low-level selectors)."""

    step_id: str
    description: str
    action_type: str  # type | click | scroll | wait | navigate | no_action
    status: str = STEP_PENDING
    target_hint: Optional[str] = None
    value: Optional[str] = None
    condition: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    url: Optional[str] = None
    timeout_ms: Optional[int] = None
    value_redacted: bool = False
    lifecycle_id: Optional[str] = None
    confirmation_id: Optional[str] = None
    resolve_source: Optional[str] = None
    resolve_strategy: Optional[str] = None
    verification_status: Optional[str] = None
    recovery_attempts: int = 0
    error: Optional[str] = None
    performance: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, str]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def can_transition(self, target: str) -> bool:
        if target not in ALL_STEP_STATUSES:
            return False
        return target in ALLOWED_STEP_TRANSITIONS.get(self.status, frozenset())

    def transition(self, target: str, reason: str = "") -> str:
        if target not in ALL_STEP_STATUSES:
            raise InvalidStepTransitionError(
                self.status, target, f"Unknown step status: {target}"
            )
        if not self.can_transition(target):
            raise InvalidStepTransitionError(self.status, target)
        previous = self.status
        self.status = target
        self.history.append({"from": previous, "to": target, "reason": reason or ""})
        return self.status

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STEP_STATUSES

    def public_view(self, index: int = 0) -> Dict[str, Any]:
        """Safe API / dashboard projection — never exposes raw sensitive values."""
        value_out = None
        if self.value is not None:
            value_out = "[REDACTED]" if self.value_redacted else _safe_str(self.value, 80)
        return {
            "index": index + 1,
            "step_id": self.step_id,
            "description": _safe_str(self.description, 200),
            "action_type": self.action_type,
            "status": self.status,
            "target_hint": _safe_str(self.target_hint, 80) if self.target_hint else None,
            "value": value_out,
            "condition": self.condition,
            "resolve_source": self.resolve_source,
            "resolve_strategy": self.resolve_strategy,
            "verification_status": self.verification_status,
            "recovery_attempts": self.recovery_attempts,
            "error": _safe_str(self.error, 160) if self.error else None,
            "lifecycle_id": self.lifecycle_id,
            "confirmation_required": bool(self.confirmation_id)
            or self.status == STEP_REQUIRES_CONFIRMATION,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Internal serialization (still strips sensitive typed values)."""
        data = self.public_view()
        data.update(
            {
                "direction": self.direction,
                "amount": self.amount,
                "url": self.url,
                "timeout_ms": self.timeout_ms,
                "value_redacted": self.value_redacted,
                "confirmation_id": self.confirmation_id,
                "performance": dict(self.performance),
                "history": list(self.history),
            }
        )
        return data

    def serialize_for_persistence(self) -> Dict[str, Any]:
        """Full restore payload — never includes raw typed secrets or images."""
        value_out = None
        if self.value is not None:
            value_out = None if self.value_redacted else _safe_str(self.value, 200)
            if value_out and looks_sensitive_value(value_out):
                value_out = None
        # Strip coordinate caches — must never replay after restart
        meta = {
            k: v
            for k, v in (self.meta or {}).items()
            if k
            not in (
                "cached_coordinates",
                "active_coordinates",
                "coordinates",
                "raw_image",
                "screenshot",
            )
        }
        return {
            "step_id": self.step_id,
            "description": _safe_str(self.description, 300),
            "action_type": self.action_type,
            "status": self.status,
            "target_hint": _safe_str(self.target_hint, 120) if self.target_hint else None,
            "value": value_out,
            "condition": self.condition,
            "direction": self.direction,
            "amount": self.amount,
            "url": _safe_str(self.url, 300) if self.url else None,
            "timeout_ms": self.timeout_ms,
            "value_redacted": bool(self.value_redacted) or value_out is None and self.value is not None,
            "lifecycle_id": self.lifecycle_id,
            "confirmation_id": self.confirmation_id,
            "resolve_source": self.resolve_source,
            "resolve_strategy": self.resolve_strategy,
            "verification_status": self.verification_status,
            "recovery_attempts": int(self.recovery_attempts or 0),
            "error": _safe_str(self.error, 200) if self.error else None,
            "performance": {
                k: v
                for k, v in (self.performance or {}).items()
                if isinstance(v, (int, float, str, bool))
            },
            "history": list(self.history or []),
            "meta": meta,
        }

    @classmethod
    def from_persistence(cls, data: Dict[str, Any]) -> "TaskStep":
        return cls(
            step_id=str(data.get("step_id") or new_step_id(0)),
            description=_safe_str(data.get("description"), 300),
            action_type=str(data.get("action_type") or "no_action"),
            status=str(data.get("status") or STEP_PENDING),
            target_hint=data.get("target_hint"),
            value=None if data.get("value_redacted") else data.get("value"),
            condition=data.get("condition"),
            direction=data.get("direction"),
            amount=data.get("amount"),
            url=data.get("url"),
            timeout_ms=data.get("timeout_ms"),
            value_redacted=bool(data.get("value_redacted")),
            lifecycle_id=data.get("lifecycle_id"),
            confirmation_id=data.get("confirmation_id"),
            resolve_source=data.get("resolve_source"),
            resolve_strategy=data.get("resolve_strategy"),
            verification_status=data.get("verification_status"),
            recovery_attempts=int(data.get("recovery_attempts") or 0),
            error=data.get("error"),
            performance=dict(data.get("performance") or {}),
            history=list(data.get("history") or []),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class TaskPlan:
    """High-level multi-step plan for a user goal."""

    plan_id: str
    goal: str
    status: str = PLAN_CREATED
    current_step_index: int = 0
    steps: List[TaskStep] = field(default_factory=list)
    tab_id: Optional[int] = None
    window_id: Optional[int] = None
    unsupported_reason: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_perf: float = field(default_factory=time.perf_counter)
    performance: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, str]] = field(default_factory=list)
    active_stage: str = "plan"  # perceive|plan|resolve|execute|verify|next_step|replan
    cancel_requested: bool = False
    last_page_signature: Optional[str] = None
    perception_generation: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)
    # Milestone 4C — adaptive re-planning metadata
    plan_version: int = 1
    replan_count: int = 0
    last_replan_reason: Optional[str] = None
    last_page_change_level: Optional[str] = None
    last_page_change: Optional[Dict[str, Any]] = None
    requires_user_intervention: bool = False
    intervention_reason: Optional[str] = None
    revision_history: List[Dict[str, Any]] = field(default_factory=list)
    last_page_state: Optional[Dict[str, Any]] = None

    def can_transition(self, target: str) -> bool:
        if target not in ALL_PLAN_STATUSES:
            return False
        return target in ALLOWED_PLAN_TRANSITIONS.get(self.status, frozenset())

    def transition(self, target: str, reason: str = "") -> str:
        if target not in ALL_PLAN_STATUSES:
            raise InvalidPlanTransitionError(
                self.status, target, f"Unknown plan status: {target}"
            )
        if not self.can_transition(target):
            raise InvalidPlanTransitionError(self.status, target)
        previous = self.status
        self.status = target
        self.updated_at = time.time()
        self.history.append({"from": previous, "to": target, "reason": reason or ""})
        return self.status

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_PLAN_STATUSES

    def current_step(self) -> Optional[TaskStep]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def successful_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == STEP_SUCCESS)

    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == STEP_FAILED)

    def advance_after_success(self) -> bool:
        """Mark current step success already applied; move index forward.

        Returns True if there is another step to run.
        """
        nxt = self.current_step_index + 1
        if nxt >= len(self.steps):
            self.current_step_index = len(self.steps)
            if self.status == PLAN_RUNNING:
                self.transition(PLAN_COMPLETED, "All steps verified")
            return False
        self.current_step_index = nxt
        self.updated_at = time.time()
        return True

    def public_view(self) -> Dict[str, Any]:
        """Safe status payload for API / dashboard."""
        total_ms = round((time.perf_counter() - self.started_perf) * 1000, 3)
        steps_public = [s.public_view(i) for i, s in enumerate(self.steps)]
        current = self.current_step()
        metrics = {
            "total_steps": len(self.steps),
            "successful_steps": self.successful_steps(),
            "failed_steps": self.failed_steps(),
            "confirmation_count": int(self.metrics.get("confirmation_count") or 0),
            "recovery_count": int(self.metrics.get("recovery_count") or 0),
            **{
                k: v
                for k, v in (self.performance or {}).items()
                if isinstance(v, (int, float))
            },
            "plan_total_ms": total_ms,
        }
        if metrics["successful_steps"] > 0 and total_ms:
            metrics["average_step_ms"] = round(
                total_ms / max(metrics["successful_steps"], 1), 3
            )
        # 4C metrics (measured counters only)
        metrics["replan_count"] = int(self.replan_count or 0)
        metrics["page_changes_detected"] = int(self.metrics.get("page_changes_detected") or 0)
        metrics["successful_replans"] = int(self.metrics.get("successful_replans") or 0)
        metrics["failed_replans"] = int(self.metrics.get("failed_replans") or 0)
        metrics["user_interventions"] = int(self.metrics.get("user_interventions") or 0)
        metrics["max_replan_attempts"] = int(self.metrics.get("max_replan_attempts") or 2)

        page_change = None
        if self.last_page_change and isinstance(self.last_page_change, dict):
            page_change = {
                "level": self.last_page_change.get("level") or self.last_page_change_level,
                "score": self.last_page_change.get("score"),
                "reasons": list(self.last_page_change.get("reasons") or [])[:8],
            }

        revisions = []
        for rev in (self.revision_history or [])[-5:]:
            if not isinstance(rev, dict):
                continue
            revisions.append(
                {
                    "version": rev.get("version"),
                    "timestamp": rev.get("timestamp"),
                    "status": rev.get("status"),
                    "replan_reason": _safe_str(rev.get("replan_reason"), 120)
                    if rev.get("replan_reason")
                    else None,
                    "changes": list(rev.get("changes") or [])[:8],
                    "steps": [
                        {
                            "index": s.get("index"),
                            "description": _safe_str(s.get("description"), 120),
                            "status": s.get("status"),
                            "action_type": s.get("action_type"),
                        }
                        for s in (rev.get("steps") or [])[:20]
                        if isinstance(s, dict)
                    ],
                }
            )

        return {
            "plan_id": self.plan_id,
            "goal": _safe_str(self.goal, 300),
            "status": self.status,
            "version": int(self.plan_version or 1),
            "plan_version": int(self.plan_version or 1),
            "replan_count": int(self.replan_count or 0),
            "last_replan_reason": _safe_str(self.last_replan_reason, 160)
            if self.last_replan_reason
            else None,
            "last_page_change_level": self.last_page_change_level,
            "page_change": page_change,
            "requires_user_intervention": bool(self.requires_user_intervention),
            "intervention_reason": _safe_str(self.intervention_reason, 200)
            if self.intervention_reason
            else None,
            "revision_history": revisions,
            "current_step": self.current_step_index + 1 if current else self.current_step_index,
            "current_step_index": self.current_step_index,
            "total_steps": len(self.steps),
            "steps": steps_public,
            "active_stage": self.active_stage,
            "unsupported_reason": self.unsupported_reason,
            "error": _safe_str(self.error, 200) if self.error else None,
            "tab_id": self.tab_id,
            "metrics": metrics,
            "performance": dict(self.performance),
            "cancel_requested": self.cancel_requested,
            "perception_generation": self.perception_generation,
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.public_view()

    def serialize_for_persistence(self) -> Dict[str, Any]:
        """Full plan restore payload for SQLite — privacy-safe only."""
        return {
            "plan_id": self.plan_id,
            "goal": _safe_str(self.goal, 500),
            "status": self.status,
            "current_step_index": int(self.current_step_index),
            "steps": [s.serialize_for_persistence() for s in self.steps],
            "tab_id": self.tab_id,
            "window_id": self.window_id,
            "unsupported_reason": self.unsupported_reason,
            "error": _safe_str(self.error, 200) if self.error else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "performance": {
                k: v
                for k, v in (self.performance or {}).items()
                if isinstance(v, (int, float, str, bool))
            },
            "metrics": dict(self.metrics or {}),
            "history": list(self.history or []),
            "active_stage": self.active_stage,
            "cancel_requested": bool(self.cancel_requested),
            "last_page_signature": self.last_page_signature,
            "perception_generation": int(self.perception_generation or 0),
            "meta": {
                k: v
                for k, v in (self.meta or {}).items()
                if k
                not in (
                    "cached_coordinates",
                    "screenshot",
                    "raw_image",
                    "original_image",
                )
            },
            "plan_version": int(self.plan_version or 1),
            "replan_count": int(self.replan_count or 0),
            "last_replan_reason": self.last_replan_reason,
            "last_page_change_level": self.last_page_change_level,
            "last_page_change": self.last_page_change,
            "requires_user_intervention": bool(self.requires_user_intervention),
            "intervention_reason": self.intervention_reason,
            "revision_history": list(self.revision_history or []),
            "last_page_state": self.last_page_state,
            "session_id": (self.meta or {}).get("session_id"),
        }

    @classmethod
    def from_persistence(cls, data: Dict[str, Any]) -> "TaskPlan":
        steps_data = data.get("steps") or []
        steps = [
            TaskStep.from_persistence(s) if isinstance(s, dict) else s
            for s in steps_data
        ]
        plan = cls(
            plan_id=str(data.get("plan_id") or new_plan_id()),
            goal=_safe_str(data.get("goal"), 500),
            status=str(data.get("status") or PLAN_CREATED),
            current_step_index=int(data.get("current_step_index") or 0),
            steps=steps,
            tab_id=data.get("tab_id"),
            window_id=data.get("window_id"),
            unsupported_reason=data.get("unsupported_reason"),
            error=data.get("error"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            performance=dict(data.get("performance") or {}),
            metrics=dict(data.get("metrics") or {}),
            history=list(data.get("history") or []),
            active_stage=str(data.get("active_stage") or "plan"),
            cancel_requested=bool(data.get("cancel_requested")),
            last_page_signature=data.get("last_page_signature"),
            perception_generation=int(data.get("perception_generation") or 0),
            meta=dict(data.get("meta") or {}),
            plan_version=int(data.get("plan_version") or data.get("version") or 1),
            replan_count=int(data.get("replan_count") or 0),
            last_replan_reason=data.get("last_replan_reason"),
            last_page_change_level=data.get("last_page_change_level"),
            last_page_change=data.get("last_page_change"),
            requires_user_intervention=bool(data.get("requires_user_intervention")),
            intervention_reason=data.get("intervention_reason"),
            revision_history=list(data.get("revision_history") or []),
            last_page_state=data.get("last_page_state"),
        )
        if data.get("session_id"):
            plan.meta["session_id"] = data["session_id"]
        # Reset perf counter so wall-clock totals restart cleanly after restore
        plan.started_perf = time.perf_counter()
        return plan


class TaskPlanStatus:
    """Namespace for plan status constants (import-friendly)."""

    CREATED = PLAN_CREATED
    PLANNING = PLAN_PLANNING
    RUNNING = PLAN_RUNNING
    WAITING_FOR_CONFIRMATION = PLAN_WAITING_FOR_CONFIRMATION
    PAUSED = PLAN_PAUSED
    COMPLETED = PLAN_COMPLETED
    FAILED = PLAN_FAILED
    CANCELLED = PLAN_CANCELLED
    UNSUPPORTED = PLAN_UNSUPPORTED


class TaskStepStatus:
    """Namespace for step status constants."""

    PENDING = STEP_PENDING
    READY = STEP_READY
    RESOLVING = STEP_RESOLVING
    REQUIRES_CONFIRMATION = STEP_REQUIRES_CONFIRMATION
    APPROVED = STEP_APPROVED
    EXECUTING = STEP_EXECUTING
    VERIFYING = STEP_VERIFYING
    SUCCESS = STEP_SUCCESS
    FAILED = STEP_FAILED
    SKIPPED = STEP_SKIPPED
    CANCELLED = STEP_CANCELLED
    WAITING = STEP_WAITING
    RECOVERING = STEP_RECOVERING


def new_plan_id() -> str:
    return f"plan_{secrets.token_hex(8)}"


def new_step_id(index: int = 0) -> str:
    return f"step_{index + 1}_{secrets.token_hex(4)}"


def create_empty_plan(goal: str, *, tab_id: int | None = None, window_id: int | None = None) -> TaskPlan:
    return TaskPlan(
        plan_id=new_plan_id(),
        goal=_safe_str(goal, 500),
        tab_id=tab_id,
        window_id=window_id,
        metrics={
            "confirmation_count": 0,
            "recovery_count": 0,
            "successful_steps": 0,
            "failed_steps": 0,
            "page_changes_detected": 0,
            "successful_replans": 0,
            "failed_replans": 0,
            "user_interventions": 0,
            "max_replan_attempts": 2,
        },
    )
