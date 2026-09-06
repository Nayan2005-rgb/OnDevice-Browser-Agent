"""Session recovery after server restart — never blind-replay actions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent.page_change_detector import PageChangeDetector
from agent.page_state import PageState, build_page_state, page_state_from_dict
from agent.plan_validity import PlanValidityChecker
from agent.session import (
    EVENT_RECOVERY_COMPLETED,
    EVENT_USER_INTERVENTION_REQUIRED,
    RECOVERY_FAILED,
    RECOVERY_READY,
    RECOVERY_REPLANNING,
    RECOVERY_REQUIRES_INTERVENTION,
    RECOVERY_RECOVERING,
    RECOVERY_VALIDATING,
    RECOVERY_WAITING_FOR_BROWSER,
    SESSION_PAUSED,
    SESSION_RECOVERING,
    SESSION_REQUIRES_USER_INTERVENTION,
    SESSION_RUNNING,
    SESSION_WAITING_FOR_BROWSER,
    AgentSession,
    InvalidSessionTransitionError,
)
from agent.session_manager import SessionManager
from agent.task_plan import PLAN_PAUSED, TaskPlan
from storage.database import StorageUnavailableError


CHANGE_NONE = "none"
CHANGE_MINOR = "minor"
CHANGE_MODERATE = "moderate"
CHANGE_STRUCTURAL = "structural"
CHANGE_NAVIGATION = "navigation"


@dataclass
class RecoveryDecision:
    action: str  # resume | re_resolve | replan | require_intervention | pause | fail
    reason: str = ""
    page_change_level: Optional[str] = None
    recovery_status: str = RECOVERY_READY
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "page_change_level": self.page_change_level,
            "recovery_status": self.recovery_status,
            "details": dict(self.details),
        }


class SessionRecovery:
    """Validate restored sessions against fresh sanitized page state."""

    def __init__(
        self,
        session_manager: SessionManager,
        *,
        page_change_detector: Optional[PageChangeDetector] = None,
        plan_validity_checker: Optional[PlanValidityChecker] = None,
    ) -> None:
        self.sm = session_manager
        self.detector = page_change_detector or PageChangeDetector()
        self.validity = plan_validity_checker or PlanValidityChecker()

    def mark_waiting_for_browser(self, session: AgentSession) -> AgentSession:
        session.recovery_status = RECOVERY_WAITING_FOR_BROWSER
        if session.status not in (SESSION_WAITING_FOR_BROWSER, SESSION_CANCELLED):
            if session.can_transition(SESSION_WAITING_FOR_BROWSER):
                try:
                    session.transition(
                        SESSION_WAITING_FOR_BROWSER, "Awaiting fresh browser perception"
                    )
                except InvalidSessionTransitionError:
                    pass
        self.sm.persist_session(session)
        return session

    def recover_with_fresh_perception(
        self,
        session_id: str,
        *,
        page: Optional[Dict[str, Any]] = None,
        page_text: str = "",
        visual_ui_map: Any = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
        observation: Optional[Dict[str, Any]] = None,
        plan: Optional[TaskPlan] = None,
    ) -> Dict[str, Any]:
        """Compare fresh page with saved safe state and decide how to resume.

        Never executes actions. Caller must use operator/orchestrator afterward
        with the decision — first action always uses this fresh perception.
        """
        t0 = time.perf_counter()
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {
                "status": "storage_unavailable",
                "error": str(exc),
                "decision": RecoveryDecision(
                    action="fail",
                    reason="storage_unavailable",
                    recovery_status=RECOVERY_FAILED,
                ).to_dict(),
            }

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}
        if session.is_terminal() or session.cancel_requested:
            return {
                "status": session.status,
                "error": "terminal_or_cancelled",
                "session": session.public_view(),
            }

        session.recovery_status = RECOVERY_RECOVERING
        if session.can_transition(SESSION_RECOVERING):
            try:
                session.transition(SESSION_RECOVERING, "Fresh perception received")
            except InvalidSessionTransitionError:
                pass
        self.sm.persist_session(session)

        if plan is None and session.plan_id:
            plan = self.sm.load_plan_into_registry(session.plan_id)
        if plan is None:
            decision = RecoveryDecision(
                action="fail",
                reason="plan_missing",
                recovery_status=RECOVERY_FAILED,
            )
            session.recovery_status = RECOVERY_FAILED
            self.sm.persist_session(session)
            return {
                "status": "failed",
                "decision": decision.to_dict(),
                "session": session.public_view(),
            }

        # Strip any cached coordinates from pending steps — never replay
        for step in plan.steps:
            if step.meta:
                step.meta.pop("cached_coordinates", None)
                step.meta.pop("active_coordinates", None)
                step.meta.pop("coordinates", None)

        obs = dict(observation or {})
        if visual_ui_map is not None and "visual_ui_map" not in obs:
            obs["visual_ui_map"] = visual_ui_map

        t_val = time.perf_counter()
        session.recovery_status = RECOVERY_VALIDATING
        self.sm.persist_session(session)

        current_state = build_page_state(
            observation=obs,
            page=page or {},
            safe_page_state=safe_page_state,
            visual_ui_map=obs.get("visual_ui_map"),
            generation=int(plan.perception_generation or 0) + 1,
        )
        plan.perception_generation = int(plan.perception_generation or 0) + 1
        previous = page_state_from_dict(plan.last_page_state)

        change = self.detector.compare(previous, current_state)
        step = plan.current_step()
        validity = self.validity.check(
            plan,
            step,
            current_state,
            obs,
            page_change_level=change.level,
        )

        # Persist fresh safe page state (not screenshots)
        plan.last_page_state = current_state.to_dict()
        plan.last_page_signature = current_state.page_signature
        plan.last_page_change = change.to_dict()
        plan.last_page_change_level = change.level
        session.last_page_signature = current_state.page_signature
        session.last_url_signature = current_state.url_signature

        decision = self._decide(session, plan, change.level, validity)
        validate_ms = round((time.perf_counter() - t_val) * 1000, 3)
        total_ms = round((time.perf_counter() - t0) * 1000, 3)
        session.performance["page_validation_ms"] = validate_ms
        session.performance["session_recovery_ms"] = total_ms
        session.recovery_status = decision.recovery_status

        if decision.action == "require_intervention":
            plan.requires_user_intervention = True
            plan.intervention_reason = decision.reason
            session.intervention_reason = decision.reason
            if plan.status != PLAN_PAUSED and plan.can_transition(PLAN_PAUSED):
                plan.transition(PLAN_PAUSED, decision.reason)
            if session.can_transition(SESSION_REQUIRES_USER_INTERVENTION):
                try:
                    session.transition(
                        SESSION_REQUIRES_USER_INTERVENTION, decision.reason
                    )
                except InvalidSessionTransitionError:
                    pass
            self.sm.append_event(
                session.session_id,
                EVENT_USER_INTERVENTION_REQUIRED,
                plan_version=session.plan_version,
                step_index=plan.current_step_index,
                safe_metadata={"reason": decision.reason[:160]},
            )
        elif decision.action == "replan":
            session.recovery_status = RECOVERY_REPLANNING
            if session.can_transition(SESSION_PAUSED):
                try:
                    session.transition(SESSION_PAUSED, "Recovery requires replan review")
                except InvalidSessionTransitionError:
                    pass
            if plan.can_transition(PLAN_PAUSED):
                try:
                    plan.transition(PLAN_PAUSED, "Recovery requires replan")
                except Exception:
                    pass
        elif decision.action in ("resume", "re_resolve"):
            session.recovery_status = RECOVERY_READY
            if session.can_transition(SESSION_RUNNING):
                try:
                    session.transition(SESSION_RUNNING, "Recovery validated — ready")
                except InvalidSessionTransitionError:
                    pass
            self.sm.append_event(
                session.session_id,
                EVENT_RECOVERY_COMPLETED,
                plan_version=session.plan_version,
                safe_metadata={
                    "action": decision.action,
                    "page_change_level": decision.page_change_level,
                },
            )
        elif decision.action == "pause":
            if session.can_transition(SESSION_PAUSED):
                try:
                    session.transition(SESSION_PAUSED, decision.reason)
                except InvalidSessionTransitionError:
                    pass
            session.pause_reason = decision.reason

        self.sm.persist_plan(plan, session_id=session.session_id)
        self.sm.persist_session(session)

        return {
            "status": "ok",
            "session": session.public_view(),
            "plan": plan.public_view(),
            "decision": decision.to_dict(),
            "page_change": change.to_dict(),
            "validity": validity.to_dict(),
            "fresh_page_signature": current_state.page_signature,
            "performance": {
                "page_validation_ms": validate_ms,
                "session_recovery_ms": total_ms,
            },
        }

    def _decide(
        self,
        session: AgentSession,
        plan: TaskPlan,
        change_level: str,
        validity,
    ) -> RecoveryDecision:
        # Sensitive page always requires intervention
        if getattr(validity, "intervention_reason", None) or (
            getattr(validity, "recommended_action", None)
            == "requires_user_intervention"
        ):
            return RecoveryDecision(
                action="require_intervention",
                reason=validity.intervention_reason
                or "Sensitive page requires user intervention",
                page_change_level=change_level,
                recovery_status=RECOVERY_REQUIRES_INTERVENTION,
            )

        if getattr(plan, "requires_user_intervention", False):
            return RecoveryDecision(
                action="require_intervention",
                reason=plan.intervention_reason or "User intervention required",
                page_change_level=change_level,
                recovery_status=RECOVERY_REQUIRES_INTERVENTION,
            )

        level = (change_level or CHANGE_NONE).lower()
        if level in (CHANGE_NONE,):
            return RecoveryDecision(
                action="resume",
                reason="page_state_valid",
                page_change_level=level,
                recovery_status=RECOVERY_READY,
            )
        if level == CHANGE_MINOR:
            return RecoveryDecision(
                action="re_resolve",
                reason="minor_change_re_resolve_target",
                page_change_level=level,
                recovery_status=RECOVERY_READY,
            )
        if level == CHANGE_MODERATE:
            return RecoveryDecision(
                action="replan",
                reason="moderate_change_adaptive_replan",
                page_change_level=level,
                recovery_status=RECOVERY_REPLANNING,
            )
        if level == CHANGE_STRUCTURAL:
            return RecoveryDecision(
                action="replan",
                reason="structural_change_requires_replan_or_review",
                page_change_level=level,
                recovery_status=RECOVERY_REPLANNING,
            )
        if level == CHANGE_NAVIGATION:
            return RecoveryDecision(
                action="replan",
                reason="navigation_change_validate_pending_steps",
                page_change_level=level,
                recovery_status=RECOVERY_REPLANNING,
            )
        return RecoveryDecision(
            action="pause",
            reason="uncertain_page_state",
            page_change_level=level,
            recovery_status=RECOVERY_FAILED,
        )
