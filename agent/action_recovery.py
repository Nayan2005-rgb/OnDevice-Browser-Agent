"""Action recovery engine for interrupted durable actions (Milestone 5B).

Fail-closed recovery after server/extension crashes. Never blindly replays
actions. Destructive / payment / credential / CAPTCHA paths require
confirmation or user intervention.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from agent.action_safety import ActionRiskClassifier
from agent.page_change_detector import PageChangeDetector
from agent.page_state import PageState, build_page_state
from agent.target_resolver import TargetResolver


DECISION_VERIFIED_COMPLETE = "verified_complete"
DECISION_SAFE_TO_RESUME = "safe_to_resume"
DECISION_REQUIRES_CONFIRMATION = "requires_confirmation"
DECISION_REQUIRES_USER_INTERVENTION = "requires_user_intervention"
DECISION_REPLAN = "replan"
DECISION_CANCEL = "cancel"
DECISION_RECOVERY_FAILED = "recovery_failed"

DESTRUCTIVE_CATEGORIES = frozenset(
    {
        "deletion",
        "payment",
        "financial_transfer",
        "account_change",
        "authentication",
        "form_submission",
    }
)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


def strip_trusted_coordinates(action: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Old coordinates must not be trusted after restart / reconnect."""
    out = dict(action or {})
    out.pop("x", None)
    out.pop("y", None)
    out.pop("coordinates", None)
    out["coordinates_trusted"] = False
    return out


@dataclass
class ActionRecoveryDecision:
    decision: str
    reason: str
    requires_fresh_perception: bool = True
    action: Optional[Dict[str, Any]] = None
    safety: Optional[Dict[str, Any]] = None
    page_change_level: Optional[str] = None
    performance: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "requires_fresh_perception": self.requires_fresh_perception,
            "action_type": (self.action or {}).get("type") if self.action else None,
            "has_selector": bool((self.action or {}).get("selector"))
            if self.action
            else False,
            "coordinates_trusted": False,
            "safety": self.safety,
            "page_change_level": self.page_change_level,
            "performance": dict(self.performance),
        }


class ActionRecoveryEngine:
    """Decide how to continue after an interrupted durable action."""

    def __init__(
        self,
        *,
        risk_classifier: Optional[ActionRiskClassifier] = None,
        target_resolver: Optional[TargetResolver] = None,
        page_change_detector: Optional[PageChangeDetector] = None,
    ) -> None:
        self.risk = risk_classifier or ActionRiskClassifier()
        self.resolver = target_resolver or TargetResolver()
        self.change_detector = page_change_detector or PageChangeDetector()

    def evaluate(
        self,
        *,
        interrupted_status: str,
        category: str = "",
        risk_level: str = "",
        task: str = "",
        stored_action: Optional[Mapping[str, Any]] = None,
        fresh_perception: Optional[Mapping[str, Any]] = None,
        previous_page_state: Optional[PageState] = None,
        verification_status: Optional[str] = None,
        captcha_detected: bool = False,
        credentials_required: bool = False,
        tab_id: Optional[int] = None,
    ) -> ActionRecoveryDecision:
        t0 = time.perf_counter()
        perf: Dict[str, float] = {}

        if tab_id is None:
            return ActionRecoveryDecision(
                decision=DECISION_RECOVERY_FAILED,
                reason="Missing tab context — fail closed.",
                performance={"recovery_resolution_ms": _ms(t0)},
            )

        if captcha_detected:
            return ActionRecoveryDecision(
                decision=DECISION_REQUIRES_USER_INTERVENTION,
                reason="CAPTCHA / security challenge requires user intervention.",
                performance={"recovery_resolution_ms": _ms(t0)},
            )

        if credentials_required:
            return ActionRecoveryDecision(
                decision=DECISION_REQUIRES_USER_INTERVENTION,
                reason="Credentials must never be auto-filled after recovery.",
                performance={"recovery_resolution_ms": _ms(t0)},
            )

        if verification_status in ("success", "verified", "already_complete"):
            return ActionRecoveryDecision(
                decision=DECISION_VERIFIED_COMPLETE,
                reason="Action already verified complete — do not re-execute.",
                requires_fresh_perception=False,
                performance={"recovery_resolution_ms": _ms(t0)},
            )

        if fresh_perception is None:
            return ActionRecoveryDecision(
                decision=DECISION_REQUIRES_USER_INTERVENTION,
                reason="Fresh sanitized perception required before recovery.",
                requires_fresh_perception=True,
                performance={"recovery_resolution_ms": _ms(t0)},
            )

        t_perc = time.perf_counter()
        page_payload = fresh_perception.get("page") if isinstance(
            fresh_perception.get("page"), Mapping
        ) else fresh_perception
        try:
            fresh_state = build_page_state(
                observation=fresh_perception
                if "ui_elements" in (fresh_perception or {})
                else None,
                page=page_payload if isinstance(page_payload, Mapping) else None,
            )
        except Exception:
            fresh_state = None
        perf["fresh_perception_validation_ms"] = _ms(t_perc)

        if fresh_state is None:
            return ActionRecoveryDecision(
                decision=DECISION_RECOVERY_FAILED,
                reason="Unable to rebuild page state from fresh perception.",
                performance={**perf, "recovery_resolution_ms": _ms(t0)},
            )

        page_change_level = "none"
        if previous_page_state is not None:
            try:
                change = self.change_detector.compare(previous_page_state, fresh_state)
                page_change_level = getattr(change, "level", None) or "unknown"
            except Exception:
                page_change_level = "unknown"

        action_for_risk = strip_trusted_coordinates(stored_action)
        action_type = str(action_for_risk.get("type") or "click")
        safety = self.risk.classify(
            task=task or "",
            action=action_type,
            target=action_for_risk,
        )
        safety_dict = safety.to_dict()
        level = (risk_level or safety_dict.get("level") or "confirmation_required").lower()
        cat = (category or safety_dict.get("category") or "").lower()

        if cat == "payment" or level == "blocked":
            decision = (
                DECISION_REQUIRES_USER_INTERVENTION
                if level == "blocked"
                else DECISION_REQUIRES_CONFIRMATION
            )
            return ActionRecoveryDecision(
                decision=decision,
                reason=safety_dict.get("reason")
                or "Payment / blocked actions cannot auto-resume after interruption.",
                safety=safety_dict,
                page_change_level=page_change_level,
                performance={**perf, "recovery_resolution_ms": _ms(t0)},
            )

        if interrupted_status in ("claimed", "executing", "recovery_required"):
            if (
                cat in DESTRUCTIVE_CATEGORIES
                or level == "confirmation_required"
                or "delete" in (task or "").lower()
            ):
                return ActionRecoveryDecision(
                    decision=DECISION_REQUIRES_CONFIRMATION,
                    reason=(
                        "Destructive or confirmation-required action was interrupted — "
                        "explicit confirmation required again (no blind replay)."
                    ),
                    safety=safety_dict,
                    page_change_level=page_change_level,
                    performance={**perf, "recovery_resolution_ms": _ms(t0)},
                )

        if page_change_level in ("structural", "navigation", "major"):
            return ActionRecoveryDecision(
                decision=DECISION_REPLAN,
                reason=f"Page change level '{page_change_level}' requires replanning.",
                safety=safety_dict,
                page_change_level=page_change_level,
                performance={**perf, "recovery_resolution_ms": _ms(t0)},
            )

        re_resolved: Optional[Dict[str, Any]] = None
        try:
            observation = {
                "ui_elements": list(
                    (page_payload or {}).get("elements")
                    or fresh_perception.get("ui_elements")
                    or []
                ),
                "page": page_payload or {},
            }
            if fresh_perception.get("visual_ui_map"):
                observation["visual_ui_map"] = fresh_perception["visual_ui_map"]
            resolved = self.resolver.resolve(observation, task or action_type)
            if getattr(resolved, "action", None):
                re_resolved = strip_trusted_coordinates(resolved.action)
            elif isinstance(getattr(resolved, "to_dict", None), object) and hasattr(
                resolved, "status"
            ):
                data = resolved.to_dict() if callable(resolved.to_dict) else {}
                if data.get("action"):
                    re_resolved = strip_trusted_coordinates(data["action"])
        except Exception:
            re_resolved = None

        if re_resolved and level == "safe":
            return ActionRecoveryDecision(
                decision=DECISION_SAFE_TO_RESUME,
                reason="Safe action re-resolved from fresh perception.",
                action=re_resolved,
                safety=safety_dict,
                page_change_level=page_change_level,
                performance={**perf, "recovery_resolution_ms": _ms(t0)},
            )

        if level == "safe" and action_for_risk.get("selector"):
            resume = strip_trusted_coordinates(action_for_risk)
            return ActionRecoveryDecision(
                decision=DECISION_SAFE_TO_RESUME,
                reason="Safe action may resume with selector after fresh perception.",
                action=resume,
                safety=safety_dict,
                page_change_level=page_change_level,
                performance={**perf, "recovery_resolution_ms": _ms(t0)},
            )

        return ActionRecoveryDecision(
            decision=DECISION_REQUIRES_CONFIRMATION,
            reason="Recovery could not safely resume — confirmation required.",
            safety=safety_dict,
            page_change_level=page_change_level,
            performance={**perf, "recovery_resolution_ms": _ms(t0)},
        )


def detect_interrupted_actions(
    *,
    delivery_records: list,
    lifecycle_records: list,
) -> list:
    """Return public summaries of actions needing recovery attention."""
    out = []
    for r in delivery_records or []:
        status = r.get("status")
        if status in ("claimed", "executing", "recovery_required"):
            out.append(
                {
                    "execution_id": r.get("execution_id"),
                    "lifecycle_id": r.get("lifecycle_id"),
                    "confirmation_id": r.get("confirmation_id"),
                    "session_id": r.get("session_id"),
                    "plan_id": r.get("plan_id"),
                    "tab_id": r.get("tab_id"),
                    "status": status,
                    "recovery_reason": r.get("recovery_reason"),
                    "action_type": r.get("action_type"),
                    "task": r.get("task"),
                    "category": r.get("category"),
                    "lease_until": r.get("lease_until"),
                }
            )
    seen_life = {x.get("lifecycle_id") for x in out}
    for r in lifecycle_records or []:
        state = r.get("state")
        if state == "recovery_required" and r.get("lifecycle_id") not in seen_life:
            out.append(
                {
                    "lifecycle_id": r.get("lifecycle_id"),
                    "execution_id": r.get("execution_id"),
                    "session_id": r.get("session_id"),
                    "plan_id": r.get("plan_id"),
                    "tab_id": r.get("tab_id"),
                    "status": state,
                    "recovery_reason": r.get("recovery_reason"),
                    "action_type": r.get("action_type"),
                    "task": r.get("task"),
                    "risk_level": r.get("risk_level"),
                }
            )
    return out
