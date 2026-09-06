"""Explicit action lifecycle state machine for Milestone 4A / 4A.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


# Canonical states
CREATED = "created"
RESOLVED = "resolved"
REQUIRES_CONFIRMATION = "requires_confirmation"
PENDING_CONFIRMATION = "pending_confirmation"  # alias-friendly durable name
APPROVED = "approved"
WAITING_FOR_EXTENSION = "waiting_for_extension"
WAITING_FOR_BROWSER = "waiting_for_browser"  # durable synonym
CLAIMED = "claimed"
EXECUTING = "executing"
EXECUTED = "executed"
VERIFYING = "verifying"
VERIFICATION_PENDING = "verification_pending"  # durable synonym
SUCCESS = "success"
FAILED = "failed"
RECOVERING = "recovering"
RECOVERY_REQUIRED = "recovery_required"
CANCELLED = "cancelled"
EXPIRED = "expired"
BLOCKED = "blocked"
UNCLEAR = "unclear"

ALL_STATES: FrozenSet[str] = frozenset(
    {
        CREATED,
        RESOLVED,
        REQUIRES_CONFIRMATION,
        PENDING_CONFIRMATION,
        APPROVED,
        WAITING_FOR_EXTENSION,
        WAITING_FOR_BROWSER,
        CLAIMED,
        EXECUTING,
        EXECUTED,
        VERIFYING,
        VERIFICATION_PENDING,
        SUCCESS,
        FAILED,
        RECOVERING,
        RECOVERY_REQUIRED,
        CANCELLED,
        EXPIRED,
        BLOCKED,
        UNCLEAR,
    }
)

# Explicit allowed transitions
ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    CREATED: frozenset({RESOLVED, BLOCKED, CANCELLED, FAILED}),
    RESOLVED: frozenset(
        {
            REQUIRES_CONFIRMATION,
            PENDING_CONFIRMATION,
            APPROVED,
            EXECUTING,
            BLOCKED,
            CANCELLED,
            FAILED,
        }
    ),
    REQUIRES_CONFIRMATION: frozenset(
        {APPROVED, CANCELLED, EXPIRED, FAILED, RECOVERY_REQUIRED}
    ),
    PENDING_CONFIRMATION: frozenset(
        {APPROVED, CANCELLED, EXPIRED, FAILED, RECOVERY_REQUIRED, REQUIRES_CONFIRMATION}
    ),
    APPROVED: frozenset(
        {
            WAITING_FOR_EXTENSION,
            WAITING_FOR_BROWSER,
            EXECUTING,
            CANCELLED,
            EXPIRED,
            FAILED,
            RECOVERY_REQUIRED,
        }
    ),
    WAITING_FOR_EXTENSION: frozenset(
        {CLAIMED, CANCELLED, EXPIRED, FAILED, RECOVERY_REQUIRED, WAITING_FOR_BROWSER}
    ),
    WAITING_FOR_BROWSER: frozenset(
        {CLAIMED, CANCELLED, EXPIRED, FAILED, RECOVERY_REQUIRED, WAITING_FOR_EXTENSION}
    ),
    CLAIMED: frozenset(
        {EXECUTING, FAILED, CANCELLED, EXPIRED, RECOVERY_REQUIRED}
    ),
    EXECUTING: frozenset(
        {EXECUTED, FAILED, CANCELLED, RECOVERY_REQUIRED}
    ),
    EXECUTED: frozenset({VERIFYING, VERIFICATION_PENDING, FAILED}),
    VERIFYING: frozenset(
        {SUCCESS, FAILED, UNCLEAR, RECOVERING, RECOVERY_REQUIRED}
    ),
    VERIFICATION_PENDING: frozenset(
        {SUCCESS, FAILED, UNCLEAR, RECOVERING, RECOVERY_REQUIRED, VERIFYING}
    ),
    UNCLEAR: frozenset(
        {RECOVERING, FAILED, SUCCESS, CANCELLED, RECOVERY_REQUIRED}
    ),
    FAILED: frozenset({RECOVERING, CANCELLED, RECOVERY_REQUIRED}),
    RECOVERING: frozenset(
        {
            EXECUTING,
            RESOLVED,
            FAILED,
            CANCELLED,
            SUCCESS,
            REQUIRES_CONFIRMATION,
            RECOVERY_REQUIRED,
        }
    ),
    RECOVERY_REQUIRED: frozenset(
        {
            RECOVERING,
            REQUIRES_CONFIRMATION,
            PENDING_CONFIRMATION,
            CANCELLED,
            FAILED,
            SUCCESS,
            RESOLVED,
            WAITING_FOR_EXTENSION,
            WAITING_FOR_BROWSER,
        }
    ),
    SUCCESS: frozenset(),  # terminal
    CANCELLED: frozenset(),  # terminal
    EXPIRED: frozenset(),  # terminal
    BLOCKED: frozenset(),  # terminal
}


class InvalidTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: str, target: str, message: str | None = None):
        self.current = current
        self.target = target
        super().__init__(
            message or f"Invalid transition: {current} → {target}"
        )


@dataclass
class ActionLifecycle:
    """Tracks one action through the confirmation / execute / verify loop."""

    lifecycle_id: str
    state: str = CREATED
    task: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    meta: Dict = field(default_factory=dict)

    def can_transition(self, target: str) -> bool:
        if target not in ALL_STATES:
            return False
        allowed = ALLOWED_TRANSITIONS.get(self.state, frozenset())
        return target in allowed

    def transition(self, target: str, reason: str = "") -> str:
        if target not in ALL_STATES:
            raise InvalidTransitionError(self.state, target, f"Unknown state: {target}")
        if not self.can_transition(target):
            raise InvalidTransitionError(self.state, target)
        previous = self.state
        self.state = target
        self.history.append(
            {"from": previous, "to": target, "reason": reason or ""}
        )
        return self.state

    def is_terminal(self) -> bool:
        return self.state in (SUCCESS, CANCELLED, EXPIRED, BLOCKED) or (
            self.state == FAILED and not self.can_transition(RECOVERING)
        )

    def to_dict(self) -> Dict:
        return {
            "lifecycle_id": self.lifecycle_id,
            "state": self.state,
            "task": self.task,
            "history": list(self.history),
            "meta": dict(self.meta),
        }


class ActionStateMachine:
    """Factory / validator helpers around ActionLifecycle."""

    ALLOWED = ALLOWED_TRANSITIONS

    @staticmethod
    def create(lifecycle_id: str, task: str = "") -> ActionLifecycle:
        return ActionLifecycle(lifecycle_id=lifecycle_id, task=task, state=CREATED)

    @staticmethod
    def validate_transition(current: str, target: str) -> bool:
        if current not in ALL_STATES or target not in ALL_STATES:
            return False
        return target in ALLOWED_TRANSITIONS.get(current, frozenset())

    @staticmethod
    def assert_transition(current: str, target: str) -> None:
        if not ActionStateMachine.validate_transition(current, target):
            raise InvalidTransitionError(current, target)

    @staticmethod
    def allowed_from(current: str) -> Set[str]:
        return set(ALLOWED_TRANSITIONS.get(current, frozenset()))
