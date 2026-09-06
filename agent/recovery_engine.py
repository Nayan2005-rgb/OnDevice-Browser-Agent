"""Controlled recovery after failed / unclear action verification."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


MAX_RECOVERY_ATTEMPTS = 2

STRATEGY_RETRY_SELECTOR = "retry_exact_selector"
STRATEGY_RE_RESOLVE = "re_resolve_target"
STRATEGY_FUSED = "try_fused_dom_vision"
STRATEGY_COORDINATE = "coordinate_fallback"
STRATEGY_STOP = "stop"


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


@dataclass
class RecoveryAttempt:
    attempt: int
    strategy: str
    reason: str
    resolution_ms: float = 0.0
    action: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "attempt": self.attempt,
            "strategy": self.strategy,
            "reason": self.reason,
            "resolution_ms": self.resolution_ms,
        }
        if self.action is not None:
            # Safe summary only
            out["action_type"] = self.action.get("type")
            if self.action.get("selector") and not self.action.get("sensitive"):
                out["has_selector"] = True
            if self.action.get("x") is not None:
                out["has_coordinates"] = True
        return out


@dataclass
class RecoveryPlan:
    allowed: bool
    strategy: str
    reason: str
    attempt: int
    action: Optional[Dict[str, Any]] = None
    stop: bool = False
    performance: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "strategy": self.strategy,
            "reason": self.reason,
            "attempt": self.attempt,
            "stop": self.stop,
            "action_type": (self.action or {}).get("type") if self.action else None,
            "performance": dict(self.performance),
        }


class RecoveryEngine:
    """Plan at most MAX_RECOVERY_ATTEMPTS recovery strategies."""

    def __init__(self, max_attempts: int = MAX_RECOVERY_ATTEMPTS) -> None:
        self.max_attempts = int(max_attempts)
        self._attempts: List[RecoveryAttempt] = []

    @property
    def attempts(self) -> List[RecoveryAttempt]:
        return list(self._attempts)

    def reset(self) -> None:
        self._attempts.clear()

    def plan_recovery(
        self,
        *,
        original_action: Optional[Mapping[str, Any]] = None,
        verification_status: str = "failed",
        safety_level: str = "safe",
        confirmation_valid: bool = True,
        confirmation_required: bool = False,
        re_resolved_action: Optional[Mapping[str, Any]] = None,
        fused_action: Optional[Mapping[str, Any]] = None,
        coordinate_action: Optional[Mapping[str, Any]] = None,
        reason: str = "",
    ) -> RecoveryPlan:
        t0 = time.perf_counter()
        attempt_num = len(self._attempts) + 1

        # Never retry blocked actions
        if safety_level == "blocked":
            plan = RecoveryPlan(
                allowed=False,
                strategy=STRATEGY_STOP,
                reason="Blocked actions are never retried.",
                attempt=attempt_num,
                stop=True,
                performance={"recovery_resolution_ms": _ms(t0)},
            )
            self._record(plan, reason=plan.reason, t0=t0)
            return plan

        # Never bypass confirmation for destructive actions
        if confirmation_required and not confirmation_valid:
            plan = RecoveryPlan(
                allowed=False,
                strategy=STRATEGY_STOP,
                reason="Destructive actions cannot be retried without valid confirmation.",
                attempt=attempt_num,
                stop=True,
                performance={"recovery_resolution_ms": _ms(t0)},
            )
            self._record(plan, reason=plan.reason, t0=t0)
            return plan

        if attempt_num > self.max_attempts:
            plan = RecoveryPlan(
                allowed=False,
                strategy=STRATEGY_STOP,
                reason=f"Maximum recovery attempts ({self.max_attempts}) reached.",
                attempt=attempt_num,
                stop=True,
                performance={"recovery_resolution_ms": _ms(t0)},
            )
            self._record(plan, reason=plan.reason, t0=t0)
            return plan

        used = {a.strategy for a in self._attempts}
        original = dict(original_action or {})

        # Strategy order
        strategies = [
            (
                STRATEGY_RETRY_SELECTOR,
                original
                if original.get("type") == "click" and original.get("selector")
                else None,
                reason or "Retry exact selector once",
            ),
            (
                STRATEGY_RE_RESOLVE,
                dict(re_resolved_action) if re_resolved_action else None,
                reason or "Original selector no longer matched",
            ),
            (
                STRATEGY_FUSED,
                dict(fused_action) if fused_action else None,
                reason or "Try fused DOM + vision target",
            ),
            (
                STRATEGY_COORDINATE,
                dict(coordinate_action) if coordinate_action else None,
                reason or "Try safe coordinate fallback",
            ),
        ]

        for strategy, action, strat_reason in strategies:
            if strategy in used:
                continue
            if action is None:
                continue
            # Do not invent confidence bumps
            plan = RecoveryPlan(
                allowed=True,
                strategy=strategy,
                reason=strat_reason,
                attempt=attempt_num,
                action=action,
                stop=False,
                performance={"recovery_resolution_ms": _ms(t0)},
            )
            self._record(plan, reason=strat_reason, t0=t0, action=action)
            return plan

        plan = RecoveryPlan(
            allowed=False,
            strategy=STRATEGY_STOP,
            reason=reason or "No further recovery strategies available.",
            attempt=attempt_num,
            stop=True,
            performance={"recovery_resolution_ms": _ms(t0)},
        )
        self._record(plan, reason=plan.reason, t0=t0)
        return plan

    def record_external(
        self, attempt: int, strategy: str, reason: str, action: Optional[Dict] = None
    ) -> RecoveryAttempt:
        t0 = time.perf_counter()
        rec = RecoveryAttempt(
            attempt=attempt,
            strategy=strategy,
            reason=reason,
            resolution_ms=_ms(t0),
            action=action,
        )
        self._attempts.append(rec)
        return rec

    def _record(
        self,
        plan: RecoveryPlan,
        *,
        reason: str,
        t0: float,
        action: Optional[Dict] = None,
    ) -> None:
        self._attempts.append(
            RecoveryAttempt(
                attempt=plan.attempt,
                strategy=plan.strategy,
                reason=reason,
                resolution_ms=_ms(t0),
                action=action or plan.action,
            )
        )

    def history(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._attempts]
