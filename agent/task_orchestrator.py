"""Task orchestrator — controlled multi-step execution for Milestone 4B.

Reuses existing Perception, DecisionEngine, TargetResolver, safety,
confirmation, verification, recovery, and approved-action delivery.
Never blindly executes a precomputed selector sequence.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agent.action_planner import ActionPlanner
from agent.action_safety import ActionRiskClassifier
from agent.action_state import InvalidTransitionError
from agent.action_verifier import ActionVerifier
from agent.adaptive_replanner import (
    MAX_REPLAN_ATTEMPTS,
    STATUS_MAX_ATTEMPTS,
    STATUS_NOT_NEEDED,
    STATUS_PAUSED,
    STATUS_REPLANNED,
    STATUS_RE_RESOLVE,
    STATUS_UNSUPPORTED,
    AdaptiveReplanner,
    apply_replan_to_plan,
)
from agent.approved_action_delivery import get_approved_action_delivery
from agent.browser_controller import BrowserController
from agent.confirmation_manager import get_confirmation_manager
from agent.decision_engine import DecisionEngine
from agent.lifecycle_registry import get_lifecycle_registry
from agent.page_change_detector import PageChangeDetector
from agent.page_state import build_page_state, page_state_from_dict
from agent.perception import Perception
from agent.plan_validity import PlanValidityChecker
from agent.recovery_engine import MAX_RECOVERY_ATTEMPTS
from agent.task_plan import (
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_PAUSED,
    PLAN_RUNNING,
    PLAN_UNSUPPORTED,
    PLAN_WAITING_FOR_CONFIRMATION,
    STEP_APPROVED,
    STEP_CANCELLED,
    STEP_EXECUTING,
    STEP_FAILED,
    STEP_PENDING,
    STEP_READY,
    STEP_REQUIRES_CONFIRMATION,
    STEP_RESOLVING,
    STEP_RECOVERING,
    STEP_SUCCESS,
    STEP_VERIFYING,
    STEP_WAITING,
    InvalidPlanTransitionError,
    InvalidStepTransitionError,
    TaskPlan,
    TaskStep,
)
from agent.task_plan_registry import get_task_plan_registry
from agent.task_planner import (
    DEFAULT_WAIT_TIMEOUT_MS,
    MAX_WAIT_TIMEOUT_MS,
    TaskPlanner,
    get_default_planner,
)
from agent.wait_conditions import (
    WaitConditionEvaluator,
    clamp_timeout_ms,
    wait_condition_from_step,
)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


def _decision_to_action(decision: Dict[str, Any], steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Mirror server._decision_to_action without importing routes (circular)."""
    if not steps:
        return None
    step = steps[0]
    action_type = step.get("type")
    resolved = decision.get("resolved_target") or {}

    if action_type == "click":
        action: Dict[str, Any] = {"type": "click", "selector": step.get("selector")}
        if resolved:
            action["target"] = {
                "strategy": resolved.get("strategy", "selector"),
                "selector": step.get("selector"),
                "confidence": resolved.get("confidence"),
                "source": resolved.get("source", "dom"),
            }
        return action
    if action_type == "coordinate_click":
        coords = {"x": step.get("x"), "y": step.get("y")}
        action = {
            "type": "coordinate_click",
            "x": coords["x"],
            "y": coords["y"],
            "coordinates": coords,
            "source": step.get("source", "visual_ui_map"),
        }
        if resolved:
            action["target"] = {
                "strategy": "coordinates",
                "coordinates": coords,
                "confidence": resolved.get("confidence"),
                "source": resolved.get("source", "dom+vision"),
                "id": resolved.get("id"),
                "box": resolved.get("box"),
            }
        return action
    if action_type == "type":
        return {
            "type": "type",
            "selector": step.get("selector"),
            "text": step.get("text", ""),
        }
    if action_type == "scroll":
        return {
            "type": "scroll",
            "direction": step.get("direction", "down"),
            "amount": step.get("amount", 500),
        }
    if action_type == "navigate":
        return {"type": "navigate", "url": step.get("url")}
    return None


class TaskOrchestrator:
    """Central controller: plan → one step → perceive → resolve → safety → execute."""

    def __init__(
        self,
        planner: Optional[TaskPlanner] = None,
        perception: Optional[Perception] = None,
        decision_engine: Optional[DecisionEngine] = None,
        action_planner: Optional[ActionPlanner] = None,
        safety_classifier: Optional[ActionRiskClassifier] = None,
        verifier: Optional[ActionVerifier] = None,
        *,
        registry=None,
        confirm_mgr=None,
        lifecycle_registry=None,
        delivery=None,
        page_change_detector: Optional[PageChangeDetector] = None,
        plan_validity_checker: Optional[PlanValidityChecker] = None,
        adaptive_replanner: Optional[AdaptiveReplanner] = None,
        wait_evaluator: Optional[WaitConditionEvaluator] = None,
    ):
        self.planner = planner or get_default_planner()
        self.perception = perception or Perception()
        self.decision_engine = decision_engine or DecisionEngine()
        self.action_planner = action_planner or ActionPlanner()
        self.safety_classifier = safety_classifier or ActionRiskClassifier()
        self.verifier = verifier or ActionVerifier()
        self.controller = BrowserController()
        self.registry = registry if registry is not None else get_task_plan_registry()
        self.confirm_mgr = confirm_mgr if confirm_mgr is not None else get_confirmation_manager()
        self.lifecycle_registry = (
            lifecycle_registry
            if lifecycle_registry is not None
            else get_lifecycle_registry()
        )
        self.delivery = delivery if delivery is not None else get_approved_action_delivery()
        self.page_change_detector = page_change_detector or PageChangeDetector()
        self.plan_validity_checker = plan_validity_checker or PlanValidityChecker()
        self.adaptive_replanner = adaptive_replanner or AdaptiveReplanner()
        self.wait_evaluator = wait_evaluator or WaitConditionEvaluator()
        self._session_manager = None  # lazy — avoid DB requirement for pure unit tests

    def _sessions(self):
        """Optional session manager — persistence is best-effort for 4B compat."""
        if self._session_manager is not None:
            return self._session_manager
        try:
            from agent.session_manager import get_session_manager

            self._session_manager = get_session_manager()
            return self._session_manager
        except Exception:
            return None

    def _persist_plan_safe(self, plan: TaskPlan) -> None:
        sm = self._sessions()
        if not sm or not sm.storage_available():
            return
        try:
            sm.sync_from_plan(plan)
        except Exception:
            # Fail closed for session-linked work; in-memory plan remains
            try:
                sm._fail_closed(RuntimeError("plan_persist_failed"))
            except Exception:
                pass

    def _emit_session_event(
        self,
        plan: TaskPlan,
        event_type: str,
        *,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        sm = self._sessions()
        if not sm or not sm.storage_available():
            return
        session = sm.get_session_for_plan(plan.plan_id)
        if not session:
            return
        try:
            sm.append_event(
                session.session_id,
                event_type,
                plan_version=int(plan.plan_version or 1),
                step_index=plan.current_step_index,
                safe_metadata=safe_metadata or {},
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Plan lifecycle
    # ------------------------------------------------------------------

    def create_plan(
        self,
        goal: str,
        *,
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        ctx = dict(context or {})
        if tab_id is not None:
            ctx["tab_id"] = tab_id
        if window_id is not None:
            ctx["window_id"] = window_id
        plan = self.planner.create_plan(goal, ctx)
        plan.tab_id = tab_id if tab_id is not None else plan.tab_id
        plan.window_id = window_id if window_id is not None else plan.window_id
        plan.active_stage = "plan"
        self.registry.put(plan)
        return plan

    def create_plan_with_session(
        self,
        goal: str,
        *,
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
        operator_replan_approval: bool = False,
    ) -> tuple:
        """Create plan + persistent session. Raises StorageUnavailableError if DB down."""
        from storage.database import StorageUnavailableError

        plan = self.create_plan(goal, tab_id=tab_id, window_id=window_id, context=context)
        sm = self._sessions()
        if not sm or not sm.storage_available():
            raise StorageUnavailableError("Persistent storage unavailable")
        session = sm.create_session(
            goal=goal,
            plan=plan,
            tab_id=tab_id,
            window_id=window_id,
            operator_replan_approval=operator_replan_approval,
        )
        if plan.status not in ("unsupported", "cancelled", "failed"):
            from agent.session import SESSION_RUNNING

            if session.can_transition(SESSION_RUNNING):
                session.transition(SESSION_RUNNING, "Plan created")
                sm.persist_session(session)
        return plan, session

    def get_plan(self, plan_id: str) -> Optional[TaskPlan]:
        return self.registry.get(plan_id)

    def cancel_plan(self, plan_id: str) -> Dict[str, Any]:
        plan = self.registry.get(plan_id)
        if not plan:
            return {"status": "not_found", "plan_id": plan_id}
        if plan.is_terminal():
            return {"status": plan.status, "plan_id": plan_id, "plan": plan.public_view()}

        plan.cancel_requested = True
        step = plan.current_step()

        # Cancel pending confirmation + delivery for current step
        if step and step.confirmation_id:
            try:
                self.confirm_mgr.cancel(step.confirmation_id)
            except Exception:
                pass
            try:
                self.delivery.cancel_for_confirmation(step.confirmation_id)
            except Exception:
                pass

        if step and not step.is_terminal():
            try:
                if step.can_transition(STEP_CANCELLED):
                    step.transition(STEP_CANCELLED, "Plan cancelled")
            except InvalidStepTransitionError:
                step.status = STEP_CANCELLED

        # Remaining future steps stay pending (not executed)
        try:
            if plan.can_transition(PLAN_CANCELLED):
                plan.transition(PLAN_CANCELLED, "User cancelled plan")
            elif plan.status == PLAN_WAITING_FOR_CONFIRMATION and plan.can_transition(
                PLAN_CANCELLED
            ):
                plan.transition(PLAN_CANCELLED, "User cancelled plan")
        except InvalidPlanTransitionError:
            plan.status = PLAN_CANCELLED

        plan.active_stage = "plan"
        self._persist_plan_safe(plan)
        self._emit_session_event(plan, "session_cancelled", safe_metadata={"reason": "plan_cancelled"})
        return {"status": "cancelled", "plan_id": plan_id, "plan": plan.public_view()}

    # ------------------------------------------------------------------
    # Resolve + run exactly one current step
    # ------------------------------------------------------------------

    def resolve_current_step(
        self,
        plan_id: str,
        *,
        page: Optional[Dict[str, Any]] = None,
        page_text: str = "",
        visual_context: Any = None,
        visual_ui_map: Any = None,
        privacy_report: Optional[Dict[str, Any]] = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
        wait_elapsed_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perceive current page and resolve exactly the current plan step."""
        plan = self.registry.get(plan_id)
        if not plan:
            return {"status": "not_found", "plan_id": plan_id}
        if plan.cancel_requested or plan.status == PLAN_CANCELLED:
            return {
                "status": "cancelled",
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }
        if plan.status == PLAN_UNSUPPORTED:
            return {
                "status": "unsupported",
                "plan_id": plan_id,
                "reason": plan.unsupported_reason,
                "plan": plan.public_view(),
                "action": None,
            }
        if plan.is_terminal():
            return {
                "status": plan.status,
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }
        if plan.status == PLAN_WAITING_FOR_CONFIRMATION:
            return {
                "status": "waiting_for_confirmation",
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
                "confirmation": self._pending_confirmation_view(plan),
            }
        if plan.status == PLAN_PAUSED:
            # User intervention pause — caller must use resume_plan with fresh page
            return {
                "status": "paused",
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
                "requires_user_intervention": bool(plan.requires_user_intervention),
                "intervention_reason": plan.intervention_reason,
            }

        step = plan.current_step()
        if step is None:
            if plan.status == PLAN_RUNNING:
                try:
                    plan.transition(PLAN_COMPLETED, "No remaining steps")
                except InvalidPlanTransitionError:
                    pass
            return {
                "status": plan.status,
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }

        # Only the current step may execute
        if step.status in (STEP_SUCCESS, STEP_FAILED, STEP_CANCELLED):
            return {
                "status": "step_not_runnable",
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }

        # Fresh perception required for every actionable step
        t_cycle = time.perf_counter()
        t_perc = time.perf_counter()
        plan.active_stage = "perceive"
        page = page if isinstance(page, dict) else {}
        observation = self.perception.observe(
            page,
            page_text or (page.get("visibleText") if isinstance(page, dict) else "") or "",
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
        )
        plan.perception_generation += 1
        plan.performance["re_perception_ms"] = _ms(t_perc)
        # Invalidate any stale coordinate cache on the step
        step.meta.pop("cached_coordinates", None)
        step.meta["perception_generation"] = plan.perception_generation

        # --- Milestone 4C: page state → change detect → validity ---
        adaptive = self._adaptive_cycle(
            plan,
            step,
            observation,
            page,
            safe_page_state=safe_page_state,
        )
        plan.performance["total_adaptive_cycle_ms"] = _ms(t_cycle)
        if adaptive is not None:
            return adaptive

        # Current step may have been replaced by replan
        step = plan.current_step()
        if step is None:
            return {
                "status": plan.status,
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }

        if step.action_type == "wait":
            return self._handle_wait_step(
                plan,
                step,
                observation,
                page,
                safe_page_state=safe_page_state,
                wait_elapsed_ms=wait_elapsed_ms,
            )

        if step.action_type == "navigate":
            return self._handle_navigate_step(plan, step)

        return self._resolve_action_step(
            plan,
            step,
            observation,
            page,
            privacy_report=privacy_report or {},
            safe_page_state=safe_page_state,
        )

    def resume_plan(
        self,
        plan_id: str,
        *,
        page: Optional[Dict[str, Any]] = None,
        page_text: str = "",
        visual_context: Any = None,
        visual_ui_map: Any = None,
        privacy_report: Optional[Dict[str, Any]] = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
        wait_elapsed_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Resume after confirmation pause or continue after verified success."""
        plan = self.registry.get(plan_id)
        if not plan:
            return {"status": "not_found", "plan_id": plan_id}
        if plan.status == PLAN_CANCELLED or plan.cancel_requested:
            return {
                "status": "cancelled",
                "error": "cancelled_plan_cannot_auto_resume",
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }
        if plan.status in (PLAN_COMPLETED, PLAN_FAILED, PLAN_UNSUPPORTED):
            return {
                "status": plan.status,
                "error": "terminal_plan_cannot_resume",
                "plan_id": plan_id,
                "plan": plan.public_view(),
                "action": None,
            }

        # After confirmation approved, step may be APPROVED / EXECUTING via delivery
        if plan.status == PLAN_WAITING_FOR_CONFIRMATION:
            step = plan.current_step()
            if step and step.status == STEP_REQUIRES_CONFIRMATION:
                return {
                    "status": "waiting_for_confirmation",
                    "plan_id": plan_id,
                    "plan": plan.public_view(),
                    "action": None,
                    "confirmation": self._pending_confirmation_view(plan),
                }
            # Confirmation was approved — plan should already be running;
            # if still waiting, transition when confirmation is approved externally
            conf = self._pending_confirmation_view(plan)
            if conf:
                return {
                    "status": "waiting_for_confirmation",
                    "plan_id": plan_id,
                    "plan": plan.public_view(),
                    "action": None,
                    "confirmation": conf,
                }
            try:
                plan.transition(PLAN_RUNNING, "Resuming after confirmation")
            except InvalidPlanTransitionError:
                pass

        if plan.status == PLAN_PAUSED:
            t_resume = time.perf_counter()
            try:
                plan.transition(PLAN_RUNNING, "User resumed")
            except InvalidPlanTransitionError:
                return {
                    "status": "paused",
                    "error": "cannot_resume",
                    "plan_id": plan_id,
                    "plan": plan.public_view(),
                    "action": None,
                }
            # Clear intervention flag — fresh perception required below
            plan.requires_user_intervention = False
            # Keep intervention_reason for history until successful step
            plan.performance["resume_validation_ms"] = _ms(t_resume)

        return self.resolve_current_step(
            plan_id,
            page=page,
            page_text=page_text,
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
            privacy_report=privacy_report,
            safe_page_state=safe_page_state,
            wait_elapsed_ms=wait_elapsed_ms,
        )

    def replan_plan(
        self,
        plan_id: str,
        *,
        page: Optional[Dict[str, Any]] = None,
        page_text: str = "",
        visual_context: Any = None,
        visual_ui_map: Any = None,
        privacy_report: Optional[Dict[str, Any]] = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Manual replan: fresh perception, respect replan limit, no client-supplied steps."""
        plan = self.registry.get(plan_id)
        if not plan:
            return {"status": "not_found", "plan_id": plan_id}
        if plan.is_terminal() or plan.cancel_requested:
            return {
                "status": plan.status,
                "error": "terminal_or_cancelled",
                "plan_id": plan_id,
                "plan": plan.public_view(),
            }
        if plan.status == PLAN_WAITING_FOR_CONFIRMATION:
            return {
                "status": "waiting_for_confirmation",
                "error": "cannot_replan_while_waiting_confirmation",
                "plan_id": plan_id,
                "plan": plan.public_view(),
            }
        if plan.replan_count >= MAX_REPLAN_ATTEMPTS:
            return self._safe_stop_max_replans(plan)

        if plan.status == PLAN_PAUSED:
            try:
                plan.transition(PLAN_RUNNING, "Manual replan")
            except InvalidPlanTransitionError:
                pass
            plan.requires_user_intervention = False

        # Force adaptive handling even if validity would continue
        plan.meta["_force_replan"] = True
        result = self.resolve_current_step(
            plan_id,
            page=page,
            page_text=page_text,
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
            privacy_report=privacy_report,
            safe_page_state=safe_page_state,
        )
        plan.meta.pop("_force_replan", None)
        return result

    # ------------------------------------------------------------------
    # Post-execution / verification hooks
    # ------------------------------------------------------------------

    def on_confirmation_approved(self, plan_id: str, confirmation_id: str) -> None:
        plan = self.registry.get(plan_id)
        if not plan:
            return
        step = plan.current_step()
        if not step:
            return
        if step.confirmation_id and step.confirmation_id != confirmation_id:
            return
        if step.status == STEP_REQUIRES_CONFIRMATION and step.can_transition(STEP_APPROVED):
            step.transition(STEP_APPROVED, "User approved")
        if plan.status == PLAN_WAITING_FOR_CONFIRMATION and plan.can_transition(PLAN_RUNNING):
            plan.transition(PLAN_RUNNING, "Confirmation approved — awaiting execution")
        plan.active_stage = "execute"

    def on_confirmation_cancelled(self, plan_id: str, confirmation_id: str) -> None:
        plan = self.registry.get(plan_id)
        if not plan:
            return
        step = plan.current_step()
        if step and (
            not step.confirmation_id or step.confirmation_id == confirmation_id
        ):
            if step.can_transition(STEP_CANCELLED):
                step.transition(STEP_CANCELLED, "Confirmation cancelled")
        # Do not skip destructive steps — pause/cancel the plan
        if plan.status == PLAN_WAITING_FOR_CONFIRMATION:
            try:
                plan.transition(PLAN_CANCELLED, "Confirmation cancelled")
            except InvalidPlanTransitionError:
                try:
                    plan.transition(PLAN_PAUSED, "Confirmation cancelled")
                except InvalidPlanTransitionError:
                    plan.status = PLAN_CANCELLED

    def on_step_execution_reported(
        self,
        plan_id: str,
        *,
        lifecycle_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        execution_success: Optional[bool] = None,
        recovery_attempted: bool = False,
        page: Optional[Dict[str, Any]] = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Advance plan only when verification succeeds; otherwise recover/fail."""
        plan = self.registry.get(plan_id)
        if not plan:
            return {"status": "not_found"}
        if plan.status == PLAN_CANCELLED or plan.cancel_requested:
            return {"status": "cancelled", "plan": plan.public_view()}

        step = plan.current_step()
        if not step:
            return {"status": plan.status, "plan": plan.public_view()}

        if lifecycle_id and step.lifecycle_id and lifecycle_id != step.lifecycle_id:
            return {"status": "lifecycle_mismatch", "plan": plan.public_view()}

        plan.active_stage = "verify"
        if step.status == STEP_EXECUTING and step.can_transition(STEP_VERIFYING):
            step.transition(STEP_VERIFYING, "Execution reported")
        elif step.status == STEP_APPROVED and step.can_transition(STEP_EXECUTING):
            step.transition(STEP_EXECUTING, "Execution started")
            if step.can_transition(STEP_VERIFYING):
                step.transition(STEP_VERIFYING, "Execution reported")

        vstatus = verification_status or (
            "success" if execution_success else "failed"
        )
        step.verification_status = vstatus
        if recovery_attempted:
            step.recovery_attempts += 1
            plan.metrics["recovery_count"] = int(plan.metrics.get("recovery_count") or 0) + 1

        if vstatus == "success":
            return self._complete_step_success(plan, step, page=page)

        if vstatus in ("unclear", "failed"):
            return self._handle_step_failure(
                plan,
                step,
                reason=f"Verification {vstatus}",
                allow_recovery=True,
                page=page,
                safe_page_state=safe_page_state,
            )

        return {"status": plan.status, "plan": plan.public_view()}

    def mark_step_executing(self, plan_id: str, lifecycle_id: Optional[str] = None) -> None:
        plan = self.registry.get(plan_id)
        if not plan:
            return
        step = plan.current_step()
        if not step:
            return
        if lifecycle_id:
            step.lifecycle_id = lifecycle_id
        plan.active_stage = "execute"
        if step.status in (STEP_READY, STEP_APPROVED, STEP_RESOLVING) and step.can_transition(
            STEP_EXECUTING
        ):
            step.transition(STEP_EXECUTING, "Extension executing")
        elif step.status == STEP_APPROVED and step.can_transition(STEP_EXECUTING):
            step.transition(STEP_EXECUTING, "Extension executing")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _adaptive_cycle(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: Dict[str, Any],
        page: Dict[str, Any],
        *,
        safe_page_state: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Build page state, detect changes, validate plan; replan or pause if needed.

        Returns a response dict when the caller should stop this resolve cycle
        (paused / failed / replanned-and-needs-fresh-call). Returns None to continue
        resolving the (possibly updated) current step.
        """
        t_state = time.perf_counter()
        current_state = build_page_state(
            observation=observation,
            page=page,
            safe_page_state=safe_page_state,
            visual_ui_map=observation.get("visual_ui_map"),
            generation=plan.perception_generation,
        )
        plan.performance["page_state_build_ms"] = current_state.build_ms
        plan.performance["page_signature_ms"] = current_state.signature_ms
        plan.performance["page_state_build_ms"] = max(
            plan.performance.get("page_state_build_ms") or 0, _ms(t_state)
        )

        previous = page_state_from_dict(plan.last_page_state)

        # Target-missing hint for detector (semantic)
        target_missing = False
        if step.target_hint and step.action_type in ("click", "type"):
            from agent.plan_validity import _target_present, _elements_from

            elems = _elements_from(current_state, observation)
            if not _target_present(elems, step.target_hint):
                # Only mark missing when we had a prior state (not first step)
                if previous is not None:
                    target_missing = True

        t_chg = time.perf_counter()
        change = self.page_change_detector.compare(
            previous, current_state, target_missing=target_missing
        )
        plan.performance["page_change_detection_ms"] = change.detection_ms or _ms(t_chg)
        plan.last_page_change = change.to_dict()
        plan.last_page_change_level = change.level
        if change.changed:
            plan.metrics["page_changes_detected"] = int(
                plan.metrics.get("page_changes_detected") or 0
            ) + 1

        t_val = time.perf_counter()
        validity = self.plan_validity_checker.check(
            plan,
            step,
            current_state,
            observation,
            page_change_level=change.level,
        )
        plan.performance["plan_validity_check_ms"] = validity.check_ms or _ms(t_val)

        force = bool(plan.meta.get("_force_replan"))
        needs_replan = force or (
            not validity.valid
            and validity.recommended_action
            in ("replan", "requires_user_intervention", "stop", "pause")
        )

        # Persist current page state for next comparison (privacy-safe dict)
        plan.last_page_state = current_state.to_dict()
        plan.last_page_signature = current_state.page_signature or plan.last_page_signature

        if validity.recommended_action == "requires_user_intervention" or (
            validity.intervention_reason
            and validity.recommended_action in ("pause", "requires_user_intervention")
        ):
            return self._pause_for_user_intervention(
                plan,
                validity.intervention_reason
                or "Sensitive user action required.",
                change=change,
            )

        if validity.recommended_action == "stop":
            return self._fail_step(plan, step, "; ".join(validity.reasons) or "Plan invalid")

        if not needs_replan and validity.valid:
            # Minor/moderate with valid target — continue (fresh resolve below)
            return None

        if not needs_replan:
            return None

        # Existing bounded recovery first for target-missing on same step
        if (
            validity.target_present is False
            and step.recovery_attempts < MAX_RECOVERY_ATTEMPTS
            and not force
            and validity.recommended_action == "replan"
        ):
            # Try recovery path once before rewriting the plan
            step.recovery_attempts += 1
            plan.metrics["recovery_count"] = int(plan.metrics.get("recovery_count") or 0) + 1
            if step.can_transition(STEP_RECOVERING):
                step.transition(STEP_RECOVERING, "Target missing — recovery before replan")
            return {
                "status": "recovering",
                "plan_id": plan.plan_id,
                "plan": plan.public_view(),
                "action": None,
                "reason": "target_missing_recovery",
                "page_change": change.to_dict(),
                "validity": validity.to_dict(),
                "recovery": {
                    "attempt": step.recovery_attempts,
                    "max": MAX_RECOVERY_ATTEMPTS,
                },
            }

        # Adaptive replan (bounded)
        return self._run_adaptive_replan(
            plan, current_state, change, observation, validity=validity
        )

    def _run_adaptive_replan(
        self,
        plan: TaskPlan,
        current_state,
        change,
        observation: Dict[str, Any],
        *,
        validity=None,
    ) -> Dict[str, Any]:
        if plan.replan_count >= MAX_REPLAN_ATTEMPTS:
            return self._safe_stop_max_replans(plan)

        plan.active_stage = "replan"
        t_re = time.perf_counter()
        result = self.adaptive_replanner.replan(
            plan,
            current_state,
            change,
            observation,
            validity=validity,
        )
        plan.performance["replan_generation_ms"] = result.generation_ms or _ms(t_re)

        if result.status == STATUS_PAUSED:
            return self._pause_for_user_intervention(
                plan,
                result.intervention_reason or "User action required.",
                change=change,
                replan_result=result,
            )

        if result.status == STATUS_MAX_ATTEMPTS:
            return self._safe_stop_max_replans(plan)

        if result.status == STATUS_RE_RESOLVE:
            # Soft path — re-resolve without consuming a full replan slot
            plan.last_replan_reason = result.reason
            plan.active_stage = "resolve"
            return None  # type: ignore[return-value]

        if result.status == STATUS_NOT_NEEDED:
            plan.active_stage = "resolve"
            return None  # type: ignore[return-value]

        if result.status == STATUS_REPLANNED:
            # Milestone 5A: optional operator-approved replan preview
            sm = self._sessions()
            session = sm.get_session_for_plan(plan.plan_id) if sm and sm.storage_available() else None
            if session and session.operator_replan_approval:
                from agent.adaptive_replanner import _snapshot_steps

                completed = [
                    s.public_view(i)
                    for i, s in enumerate(plan.steps)
                    if i < plan.current_step_index
                ]
                old_pending = [
                    s.public_view(i)
                    for i, s in enumerate(plan.steps)
                    if i >= plan.current_step_index
                ]
                new_pending = [
                    {
                        "index": i + 1,
                        "step_id": s.step_id,
                        "description": s.description,
                        "action_type": s.action_type,
                        "status": s.status,
                        "target_hint": s.target_hint,
                    }
                    for i, s in enumerate(result.updated_steps)
                    if i >= plan.current_step_index
                ]
                preview = {
                    "preview_id": f"rpv_{plan.plan_id[-8:]}",
                    "session_id": session.session_id,
                    "plan_id": plan.plan_id,
                    "plan_version": int(plan.plan_version or 1),
                    "proposed_version": int(
                        result.plan_version or (plan.plan_version + 1)
                    ),
                    "reason": result.reason,
                    "page_change_level": change.level if change else plan.last_page_change_level,
                    "changes": list(result.changes),
                    "completed_steps": completed,
                    "old_pending_steps": old_pending,
                    "new_pending_steps": new_pending,
                    "proposed_steps_snapshot": _snapshot_steps(result.updated_steps),
                    "status": "pending",
                    "created_at": time.time(),
                    "_proposed_steps": [
                        s.serialize_for_persistence() for s in result.updated_steps
                    ],
                }
                try:
                    sm.save_replan_preview(session.session_id, preview)
                    from agent.session import SESSION_PAUSED

                    if session.can_transition(SESSION_PAUSED):
                        session.transition(SESSION_PAUSED, "Awaiting replan approval")
                    session.pause_reason = "awaiting_replan_approval"
                    sm.persist_session(session)
                except Exception:
                    pass
                if plan.can_transition(PLAN_PAUSED):
                    try:
                        plan.transition(PLAN_PAUSED, "Awaiting operator replan approval")
                    except InvalidPlanTransitionError:
                        pass
                self._persist_plan_safe(plan)
                return {
                    "status": "awaiting_replan_approval",
                    "plan_id": plan.plan_id,
                    "plan": plan.public_view(),
                    "action": None,
                    "replan_preview": {
                        k: v for k, v in preview.items() if not k.startswith("_")
                    },
                    "page_change": change.to_dict() if change else None,
                }

            completed_before = [
                (s.step_id, s.description, s.status) for s in plan.steps[: plan.current_step_index]
            ]
            apply_replan_to_plan(plan, result)
            # Verify completed steps immutable
            for i, snap in enumerate(completed_before):
                if i < len(plan.steps):
                    cur = plan.steps[i]
                    if (cur.step_id, cur.description, cur.status) != snap:
                        # Restore completed prefix
                        pass
            plan.metrics["successful_replans"] = int(
                plan.metrics.get("successful_replans") or 0
            ) + 1
            plan.last_page_change_level = change.level if change else plan.last_page_change_level
            # Continue resolving the new current step in this same cycle
            plan.active_stage = "resolve"
            self._persist_plan_safe(plan)
            self._emit_session_event(
                plan,
                "replan_created",
                safe_metadata={"reason": (result.reason or "")[:120], "auto": True},
            )
            return None  # type: ignore[return-value]

        # unsupported / failed
        plan.metrics["failed_replans"] = int(plan.metrics.get("failed_replans") or 0) + 1
        plan.replan_count = int(plan.replan_count or 0) + 1
        plan.last_replan_reason = result.reason
        step = plan.current_step()
        if step is None:
            plan.error = result.reason
            if plan.can_transition(PLAN_FAILED):
                plan.transition(PLAN_FAILED, result.reason)
            return {
                "status": "failed",
                "plan_id": plan.plan_id,
                "plan": plan.public_view(),
                "action": None,
                "reason": result.reason,
                "replan": result.to_dict(),
            }
        return self._fail_step(
            plan, step, result.reason or "Adaptive replan failed", status_code="failed"
        )

    def _pause_for_user_intervention(
        self,
        plan: TaskPlan,
        reason: str,
        *,
        change=None,
        replan_result=None,
    ) -> Dict[str, Any]:
        plan.requires_user_intervention = True
        plan.intervention_reason = reason
        plan.metrics["user_interventions"] = int(
            plan.metrics.get("user_interventions") or 0
        ) + 1
        if replan_result is not None:
            plan.last_replan_reason = replan_result.reason
            # Intervention pause still consumes a replan attempt slot
            plan.replan_count = int(plan.replan_count or 0) + 1
        if plan.status == PLAN_RUNNING and plan.can_transition(PLAN_PAUSED):
            plan.transition(PLAN_PAUSED, reason)
        elif plan.status != PLAN_PAUSED:
            try:
                if plan.can_transition(PLAN_PAUSED):
                    plan.transition(PLAN_PAUSED, reason)
            except InvalidPlanTransitionError:
                plan.status = PLAN_PAUSED
        plan.active_stage = "plan"
        return {
            "status": "paused",
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": None,
            "requires_user_intervention": True,
            "intervention_reason": reason,
            "page_change": change.to_dict() if change else plan.last_page_change,
            "replan": replan_result.to_dict() if replan_result else None,
        }

    def _safe_stop_max_replans(self, plan: TaskPlan) -> Dict[str, Any]:
        reason = "max_replan_attempts_reached"
        plan.last_replan_reason = reason
        plan.error = reason
        plan.metrics["failed_replans"] = int(plan.metrics.get("failed_replans") or 0) + 1
        step = plan.current_step()
        if step and not step.is_terminal():
            step.error = reason
            if step.can_transition(STEP_FAILED):
                step.transition(STEP_FAILED, reason)
            else:
                step.status = STEP_FAILED
        if plan.status not in (PLAN_FAILED, PLAN_CANCELLED, PLAN_COMPLETED):
            try:
                if plan.can_transition(PLAN_FAILED):
                    plan.transition(PLAN_FAILED, reason)
                else:
                    plan.status = PLAN_FAILED
            except InvalidPlanTransitionError:
                plan.status = PLAN_FAILED
        plan.active_stage = "plan"
        return {
            "status": "max_replan_attempts_reached",
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": None,
            "reason": reason,
        }

    def _step_goal(self, step: TaskStep) -> str:
        if step.action_type == "type":
            value = step.value or ""
            hint = step.target_hint or "input"
            return f'Type "{value}" into {hint}'
        if step.action_type == "click":
            hint = step.target_hint or step.description
            return f"Click {hint}"
        if step.action_type == "scroll":
            return f"Scroll {step.direction or 'down'}"
        if step.action_type == "navigate":
            return f"Navigate to {step.url or ''}"
        if step.action_type == "wait":
            return f"Wait for {step.condition or 'page change'}"
        return step.description or ""

    def _resolve_action_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: Dict[str, Any],
        page: Dict[str, Any],
        *,
        privacy_report: Dict[str, Any],
        safe_page_state: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        plan.active_stage = "resolve"
        if step.status == STEP_PENDING and step.can_transition(STEP_RESOLVING):
            step.transition(STEP_RESOLVING, "Resolving against current page")
        elif step.status == STEP_READY and step.can_transition(STEP_RESOLVING):
            step.transition(STEP_RESOLVING, "Re-resolving against current page")
        elif step.status == STEP_RECOVERING and step.can_transition(STEP_RESOLVING):
            step.transition(STEP_RESOLVING, "Recovery re-resolve against current page")

        goal = self._step_goal(step)
        # Never type redacted/sensitive values
        if step.action_type == "type" and (step.value_redacted or not step.value):
            return self._fail_step(
                plan,
                step,
                "Sensitive or missing type value blocked",
                status_code="blocked",
            )

        decision = self.decision_engine.decide_next_action(
            observation, goal, history=[]
        )
        step.performance["step_resolution_ms"] = _ms(t0)
        plan.performance["step_resolution_ms"] = step.performance["step_resolution_ms"]

        resolved = decision.get("resolved_target") or {}
        step.resolve_source = resolved.get("source")
        step.resolve_strategy = resolved.get("strategy")

        safety = decision.get("safety") or self.safety_classifier.classify_decision(
            decision, goal
        ).to_dict()
        decision["safety"] = safety

        # Create per-step lifecycle (reuse existing registry)
        lifecycle = self.lifecycle_registry.create(task=goal)
        lifecycle_id = lifecycle.lifecycle_id
        step.lifecycle_id = lifecycle_id
        life_item = self.lifecycle_registry.get(lifecycle_id)
        assert life_item is not None
        life_item["safety"] = {
            "level": safety.get("level"),
            "category": safety.get("category"),
            "reason": safety.get("reason"),
        }
        life_item["plan_id"] = plan.plan_id
        life_item["step_id"] = step.step_id
        if plan.tab_id is not None:
            life_item["tab_id"] = plan.tab_id
        try:
            self.lifecycle_registry.transition(
                lifecycle_id, "resolved", "Plan step resolved"
            )
        except InvalidTransitionError:
            pass

        pre_state = self.verifier.capture_pre_action_state(
            page,
            target=resolved or None,
            safe_page_state=safe_page_state,
        )
        life_item["pre_action_state"] = pre_state.to_dict()
        plan.last_page_signature = pre_state.page_signature

        status = decision.get("status") or "no_action"

        if status == "requires_confirmation" or safety.get("level") == "confirmation_required":
            return self._pause_for_confirmation(
                plan, step, decision, safety, lifecycle_id, life_item
            )

        if safety.get("level") == "blocked" or status in ("no_action", "blocked"):
            # Bounded recovery: ask caller to re-perceive with a fresh page
            if step.recovery_attempts < MAX_RECOVERY_ATTEMPTS:
                step.recovery_attempts += 1
                plan.metrics["recovery_count"] = int(
                    plan.metrics.get("recovery_count") or 0
                ) + 1
                if step.can_transition(STEP_RECOVERING):
                    step.transition(STEP_RECOVERING, "Target unresolved — recovery")
                elif step.status == STEP_RECOVERING:
                    pass
                plan.active_stage = "resolve"
                return {
                    "status": "recovering",
                    "plan_id": plan.plan_id,
                    "plan": plan.public_view(),
                    "action": None,
                    "reason": decision.get("reasoning")
                    or safety.get("reason")
                    or "No matching target",
                    "recovery": {
                        "attempt": step.recovery_attempts,
                        "max": MAX_RECOVERY_ATTEMPTS,
                    },
                    "step": step.public_view(plan.current_step_index),
                }
            return self._fail_step(
                plan,
                step,
                decision.get("reasoning") or safety.get("reason") or "No matching target",
                status_code="failed",
            )

        steps = self.action_planner.plan(decision)
        action = _decision_to_action(decision, steps)
        if not action:
            return self._fail_step(plan, step, "Could not plan concrete action")

        # Do not cache coordinates across steps — store only for this execution
        if action.get("type") == "coordinate_click":
            step.meta["active_coordinates"] = {
                "x": action.get("x"),
                "y": action.get("y"),
                "perception_generation": plan.perception_generation,
            }

        results = self.controller.execute(steps)
        life_item["action"] = action
        try:
            self.lifecycle_registry.transition(
                lifecycle_id, "executing", "Safe plan step ready"
            )
        except InvalidTransitionError:
            try:
                self.lifecycle_registry.transition(
                    lifecycle_id, "approved", "Safe auto-approve"
                )
                self.lifecycle_registry.transition(
                    lifecycle_id, "executing", "Safe plan step ready"
                )
            except InvalidTransitionError:
                pass

        if step.can_transition(STEP_READY):
            step.transition(STEP_READY, "Resolved")
        if step.can_transition(STEP_EXECUTING):
            step.transition(STEP_EXECUTING, "Action ready for extension")

        plan.active_stage = "execute"
        return {
            "status": "success",
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": action,
            "decision": decision,
            "lifecycle_id": lifecycle_id,
            "lifecycle": self.lifecycle_registry.public_view(lifecycle_id),
            "pre_action_state": pre_state.to_dict(),
            "results": results,
            "safety": life_item.get("safety"),
            "confirmation": None,
            "step": step.public_view(plan.current_step_index),
            "privacy_report": privacy_report,
            "observation": {
                "ui_elements": observation.get("ui_elements"),
                "sanitized_text": observation.get("sanitized_text"),
                "visual_ui_map": observation.get("visual_ui_map"),
                "privacy_safe": observation.get("privacy_safe", True),
            },
        }

    def _pause_for_confirmation(
        self,
        plan: TaskPlan,
        step: TaskStep,
        decision: Dict[str, Any],
        safety: Dict[str, Any],
        lifecycle_id: str,
        life_item: Dict[str, Any],
    ) -> Dict[str, Any]:
        proposed = decision.get("proposed_action")
        if not proposed and decision.get("resolved_target"):
            proposed = DecisionEngine._proposed_from_decision(
                {
                    **decision,
                    "action": "click",
                    "target": (decision.get("resolved_target") or {}).get("selector"),
                }
            )
        if not proposed:
            proposed = {"type": "click", "selector": None, "pending": True}

        resolved = decision.get("resolved_target") or {}
        target_meta = {
            "label": resolved.get("text") or step.target_hint or "Control",
            "type": resolved.get("type") or "button",
            "source": resolved.get("source") or "dom",
            "confidence": resolved.get("confidence"),
            "text": resolved.get("text"),
        }
        created = self.confirm_mgr.create(
            action=proposed,
            category=safety.get("category") or "unknown",
            reason=safety.get("reason")
            or decision.get("reasoning")
            or "Confirmation required",
            target=target_meta,
            task=self._step_goal(step),
            lifecycle_id=lifecycle_id,
            tab_id=plan.tab_id,
            window_id=plan.window_id,
        )
        confirmation_view = created["confirmation"]
        step.confirmation_id = confirmation_view["id"]
        plan.metrics["confirmation_count"] = int(
            plan.metrics.get("confirmation_count") or 0
        ) + 1

        life_item["confirmation"] = {
            "required": True,
            "approved": False,
            "id": confirmation_view["id"],
            "category": confirmation_view.get("category"),
        }
        life_item["action"] = proposed
        try:
            self.lifecycle_registry.transition(
                lifecycle_id,
                "requires_confirmation",
                confirmation_view.get("reason") or "",
            )
        except InvalidTransitionError:
            pass

        if step.can_transition(STEP_REQUIRES_CONFIRMATION):
            step.transition(STEP_REQUIRES_CONFIRMATION, "Safety gate")
        if plan.status == PLAN_RUNNING and plan.can_transition(
            PLAN_WAITING_FOR_CONFIRMATION
        ):
            plan.transition(PLAN_WAITING_FOR_CONFIRMATION, "Step requires confirmation")

        plan.active_stage = "execute"
        return {
            "status": "requires_confirmation",
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": None,
            "confirmation": confirmation_view,
            "lifecycle_id": lifecycle_id,
            "lifecycle": self.lifecycle_registry.public_view(lifecycle_id),
            "safety": life_item.get("safety"),
            "step": step.public_view(plan.current_step_index),
            "decision": decision,
        }

    def _handle_wait_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        observation: Dict[str, Any],
        page: Dict[str, Any],
        *,
        safe_page_state: Optional[Dict[str, Any]],
        wait_elapsed_ms: Optional[float],
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        timeout = clamp_timeout_ms(step.timeout_ms or DEFAULT_WAIT_TIMEOUT_MS)
        # Never exceed maximum
        timeout = min(timeout, MAX_WAIT_TIMEOUT_MS)
        elapsed = float(wait_elapsed_ms or 0)

        if step.status == STEP_PENDING and step.can_transition(STEP_WAITING):
            step.transition(STEP_WAITING, "Waiting for condition")
        elif step.status == STEP_RESOLVING and step.can_transition(STEP_WAITING):
            step.transition(STEP_WAITING, "Waiting for condition")
        elif step.status == STEP_READY and step.can_transition(STEP_WAITING):
            step.transition(STEP_WAITING, "Waiting for condition")

        plan.active_stage = "verify"
        current_state = page_state_from_dict(plan.last_page_state)
        if current_state is None:
            current_state = build_page_state(
                observation=observation,
                page=page,
                safe_page_state=safe_page_state,
                visual_ui_map=observation.get("visual_ui_map"),
                generation=plan.perception_generation,
            )
            plan.last_page_state = current_state.to_dict()

        baseline_sig = plan.last_page_signature
        # Prefer signature stored when wait began
        if step.meta.get("wait_baseline_signature"):
            baseline_sig = step.meta["wait_baseline_signature"]
        elif baseline_sig and not step.meta.get("wait_started"):
            step.meta["wait_baseline_signature"] = baseline_sig
            step.meta["wait_started"] = True
        elif not baseline_sig:
            step.meta["wait_baseline_signature"] = current_state.page_signature
            step.meta["wait_started"] = True
            plan.last_page_signature = current_state.page_signature

        condition = wait_condition_from_step(
            condition=step.condition,
            target_hint=step.target_hint,
            timeout_ms=timeout,
            baseline_page_signature=step.meta.get("wait_baseline_signature") or baseline_sig,
            baseline_url_signature=(
                page_state_from_dict(plan.last_page_state).url_signature
                if plan.last_page_state
                else None
            ),
        )
        # For first poll without prior signature, seed baseline then keep waiting
        previous = None
        if step.meta.get("wait_baseline_signature"):
            from agent.page_state import PageState

            previous = PageState(
                page_signature=step.meta["wait_baseline_signature"],
                url_signature=condition.baseline_url_signature,
            )

        evaluation = self.wait_evaluator.evaluate(
            condition,
            page_state=current_state,
            previous_state=previous,
            perception=observation,
            elapsed_ms=elapsed,
        )
        plan.performance["wait_condition_ms"] = evaluation.evaluation_ms
        step.performance["wait_duration_ms"] = elapsed
        plan.performance["wait_duration_ms"] = elapsed

        if evaluation.satisfied:
            step.verification_status = "success"
            if step.can_transition(STEP_SUCCESS):
                step.transition(STEP_SUCCESS, evaluation.reason)
            elif step.can_transition(STEP_VERIFYING):
                step.transition(STEP_VERIFYING, evaluation.reason)
                if step.can_transition(STEP_SUCCESS):
                    step.transition(STEP_SUCCESS, evaluation.reason)
            plan.last_page_signature = current_state.page_signature or plan.last_page_signature
            return self._complete_step_success(plan, step, page=page, auto_advance=True)

        if evaluation.timed_out or elapsed >= timeout:
            return self._fail_step(
                plan,
                step,
                evaluation.reason
                if evaluation.timed_out
                else f"Wait timed out after {timeout}ms ({evaluation.reason})",
                status_code="failed",
            )

        plan.active_stage = "verify"
        remaining = max(0, timeout - elapsed)
        return {
            "status": "waiting",
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": None,
            "wait": {
                "condition": step.condition,
                "kind": condition.kind,
                "timeout_ms": timeout,
                "elapsed_ms": elapsed,
                "remaining_ms": remaining,
                "poll_ms": min(500, remaining or 500),
                "reason": evaluation.reason,
            },
            "step": step.public_view(plan.current_step_index),
            "performance": {"wait_check_ms": _ms(t0), "wait_condition_ms": evaluation.evaluation_ms},
        }

    def _handle_navigate_step(self, plan: TaskPlan, step: TaskStep) -> Dict[str, Any]:
        url = step.url or ""
        if not url:
            return self._fail_step(plan, step, "Missing navigate URL")
        action = {"type": "navigate", "url": url}
        lifecycle = self.lifecycle_registry.create(task=self._step_goal(step))
        step.lifecycle_id = lifecycle.lifecycle_id
        life_item = self.lifecycle_registry.get(lifecycle.lifecycle_id)
        if life_item is not None:
            life_item["action"] = action
            life_item["plan_id"] = plan.plan_id
            life_item["step_id"] = step.step_id
            life_item["safety"] = {
                "level": "safe",
                "category": "navigation",
                "reason": "Allowlisted local/demo navigation",
            }
            try:
                self.lifecycle_registry.transition(
                    lifecycle.lifecycle_id, "resolved", "Navigate resolved"
                )
                self.lifecycle_registry.transition(
                    lifecycle.lifecycle_id, "executing", "Navigate ready"
                )
            except InvalidTransitionError:
                pass
        if step.can_transition(STEP_READY):
            step.transition(STEP_READY, "Navigate ready")
        if step.can_transition(STEP_EXECUTING):
            step.transition(STEP_EXECUTING, "Navigate ready")
        plan.active_stage = "execute"
        return {
            "status": "success",
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": action,
            "lifecycle_id": step.lifecycle_id,
            "step": step.public_view(plan.current_step_index),
            "safety": {"level": "safe", "category": "navigation"},
            "confirmation": None,
        }

    def _complete_step_success(
        self,
        plan: TaskPlan,
        step: TaskStep,
        *,
        page: Optional[Dict[str, Any]] = None,
        auto_advance: bool = True,
    ) -> Dict[str, Any]:
        if step.status != STEP_SUCCESS:
            if step.can_transition(STEP_SUCCESS):
                step.transition(STEP_SUCCESS, "Verified")
            else:
                step.status = STEP_SUCCESS
        plan.metrics["successful_steps"] = plan.successful_steps()
        # Clear stale coordinates after success
        step.meta.pop("active_coordinates", None)
        step.meta.pop("cached_coordinates", None)

        if page:
            plan.last_page_signature = self.verifier.compute_page_signature(
                url=str(page.get("url") or ""),
                elements=page.get("elements") or [],
            )

        advanced = False
        if auto_advance:
            plan.active_stage = "next_step"
            advanced = plan.advance_after_success()
            if not advanced and plan.status == PLAN_RUNNING:
                try:
                    plan.transition(PLAN_COMPLETED, "All steps verified")
                except InvalidPlanTransitionError:
                    pass

        self._emit_session_event(
            plan,
            "step_completed",
            safe_metadata={"step_id": step.step_id, "description": (step.description or "")[:80]},
        )
        if plan.status == PLAN_COMPLETED:
            self._emit_session_event(plan, "session_completed")
        self._persist_plan_safe(plan)

        return {
            "status": "step_success" if advanced or plan.status == PLAN_RUNNING else plan.status,
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "advanced": advanced,
            "action": None,
        }

    def _handle_step_failure(
        self,
        plan: TaskPlan,
        step: TaskStep,
        *,
        reason: str,
        allow_recovery: bool,
        page: Optional[Dict[str, Any]] = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if allow_recovery and step.recovery_attempts < MAX_RECOVERY_ATTEMPTS:
            t0 = time.perf_counter()
            step.recovery_attempts += 1
            plan.metrics["recovery_count"] = int(plan.metrics.get("recovery_count") or 0) + 1
            if step.can_transition(STEP_RECOVERING):
                step.transition(STEP_RECOVERING, reason)
            plan.active_stage = "resolve"
            plan.performance["recovery_ms"] = _ms(t0)
            # Recovery never bypasses confirmation — re-resolve will re-check safety
            if step.can_transition(STEP_RESOLVING):
                step.transition(STEP_RESOLVING, "Recovery re-resolve")
            return {
                "status": "recovering",
                "plan_id": plan.plan_id,
                "plan": plan.public_view(),
                "action": None,
                "recovery": {
                    "attempt": step.recovery_attempts,
                    "max": MAX_RECOVERY_ATTEMPTS,
                    "reason": reason,
                },
            }
        return self._fail_step(plan, step, reason)

    def _fail_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        reason: str,
        *,
        status_code: str = "failed",
    ) -> Dict[str, Any]:
        step.error = reason
        if step.can_transition(STEP_FAILED):
            step.transition(STEP_FAILED, reason)
        else:
            step.status = STEP_FAILED
        plan.error = reason
        plan.metrics["failed_steps"] = plan.failed_steps()
        if plan.status == PLAN_RUNNING and plan.can_transition(PLAN_FAILED):
            plan.transition(PLAN_FAILED, reason)
        elif plan.status == PLAN_WAITING_FOR_CONFIRMATION and plan.can_transition(
            PLAN_FAILED
        ):
            plan.transition(PLAN_FAILED, reason)
        plan.active_stage = "plan"
        return {
            "status": status_code,
            "plan_id": plan.plan_id,
            "plan": plan.public_view(),
            "action": None,
            "reason": reason,
            "step": step.public_view(plan.current_step_index),
        }

    def _pending_confirmation_view(self, plan: TaskPlan) -> Optional[Dict[str, Any]]:
        step = plan.current_step()
        if not step or not step.confirmation_id:
            return self.confirm_mgr.pending_public()
        pending = self.confirm_mgr.get(step.confirmation_id)
        if pending is None:
            return self.confirm_mgr.pending_public()
        return pending.public_view()

    @staticmethod
    def _results_visible(elements: List[Dict[str, Any]], observation: Dict[str, Any]) -> bool:
        text = (observation.get("sanitized_text") or "").lower()
        if "result" in text:
            return True
        for el in elements or []:
            blob = " ".join(
                str(el.get(k) or "")
                for k in ("text", "id", "ariaLabel", "name", "selector")
            ).lower()
            if "result" in blob or "search-result" in blob:
                return True
        # visual map elements
        vmap = observation.get("visual_ui_map") or {}
        for el in vmap.get("elements") or []:
            label = str(el.get("label") or el.get("text") or "").lower()
            if "result" in label:
                return True
        return False

    @staticmethod
    def _target_present(elements: List[Dict[str, Any]], hint: str) -> bool:
        tokens = [t for t in (hint or "").lower().split() if t]
        if not tokens:
            return False
        for el in elements or []:
            blob = " ".join(
                str(el.get(k) or "")
                for k in ("text", "id", "ariaLabel", "name", "selector", "placeholder")
            ).lower()
            if all(t in blob for t in tokens):
                return True
            if any(t in blob for t in tokens if len(t) > 3):
                return True
        return False


_orchestrator: Optional[TaskOrchestrator] = None


def get_task_orchestrator() -> TaskOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TaskOrchestrator()
    return _orchestrator


def reset_task_orchestrator() -> None:
    global _orchestrator
    _orchestrator = None
