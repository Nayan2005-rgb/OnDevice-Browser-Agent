"""Operator control layer — pause / resume / cancel / replan preview approval."""

from __future__ import annotations

import secrets
import time
from typing import Any, Dict, List, Optional

from agent.adaptive_replanner import (
    MAX_REPLAN_ATTEMPTS,
    STATUS_REPLANNED,
    AdaptiveReplanner,
    apply_replan_to_plan,
    _snapshot_steps,
)
from agent.approved_action_delivery import get_approved_action_delivery
from agent.confirmation_manager import get_confirmation_manager
from agent.page_change_detector import PageChangeDetector, PageChangeResult
from agent.page_state import build_page_state, page_state_from_dict
from agent.session import (
    EVENT_PAUSED,
    EVENT_REPLAN_APPROVED,
    EVENT_REPLAN_REJECTED,
    EVENT_RESUMED,
    EVENT_SESSION_CANCELLED,
    SESSION_CANCELLED,
    SESSION_PAUSED,
    SESSION_RUNNING,
    SESSION_WAITING_FOR_CONFIRMATION,
    AgentSession,
    InvalidSessionTransitionError,
    to_public_session,
)
from agent.session_manager import SessionManager, get_session_manager
from agent.session_recovery import SessionRecovery
from agent.task_plan import (
    PLAN_CANCELLED,
    PLAN_PAUSED,
    PLAN_RUNNING,
    STEP_CANCELLED,
    STEP_PENDING,
    STEP_SUCCESS,
    InvalidPlanTransitionError,
    TaskPlan,
    TaskStep,
)
from agent.task_orchestrator import TaskOrchestrator, get_task_orchestrator
from storage.database import StorageUnavailableError


class OperatorControl:
    """All operator-facing session mutations go through this layer."""

    def __init__(
        self,
        session_manager: Optional[SessionManager] = None,
        *,
        orchestrator: Optional[TaskOrchestrator] = None,
        recovery: Optional[SessionRecovery] = None,
        adaptive_replanner: Optional[AdaptiveReplanner] = None,
    ) -> None:
        self.sm = session_manager or get_session_manager()
        self.orch = orchestrator
        self.recovery = recovery or SessionRecovery(self.sm)
        self.replanner = adaptive_replanner or AdaptiveReplanner()
        self.confirm_mgr = get_confirmation_manager()
        self.delivery = get_approved_action_delivery()

    def _orch(self) -> TaskOrchestrator:
        return self.orch or get_task_orchestrator()

    # ------------------------------------------------------------------
    # Pause / Resume / Cancel
    # ------------------------------------------------------------------
    def pause_session(
        self, session_id: str, *, reason: str = "operator_pause"
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}
        if session.status == SESSION_CANCELLED or session.cancel_requested:
            return {
                "status": "cancelled",
                "error": "cancelled_session_cannot_pause",
                "session": to_public_session(session),
            }
        if session.is_terminal():
            return {
                "status": session.status,
                "error": "terminal_session",
                "session": to_public_session(session),
            }

        # Idempotent pause
        if session.status == SESSION_PAUSED:
            session.pause_reason = reason or session.pause_reason
            session.performance["pause_operation_ms"] = round(
                (time.perf_counter() - t0) * 1000, 3
            )
            self.sm.persist_session(session)
            return {
                "status": "paused",
                "idempotent": True,
                "session": to_public_session(session),
            }

        try:
            if session.can_transition(SESSION_PAUSED):
                session.transition(SESSION_PAUSED, reason)
            else:
                return {
                    "status": "invalid_transition",
                    "error": f"cannot_pause_from_{session.status}",
                    "session": to_public_session(session),
                }
        except InvalidSessionTransitionError as exc:
            return {
                "status": "invalid_transition",
                "error": str(exc),
                "session": to_public_session(session),
            }

        session.pause_reason = reason
        # Do not cancel pending confirmation unless explicitly requested
        plan = self._load_plan(session)
        if plan and plan.can_transition(PLAN_PAUSED):
            try:
                plan.transition(PLAN_PAUSED, reason)
            except InvalidPlanTransitionError:
                pass
            self.sm.persist_plan(plan, session_id=session.session_id)

        self.sm.append_event(
            session.session_id,
            EVENT_PAUSED,
            plan_version=session.plan_version,
            step_index=session.current_step_index,
            safe_metadata={"reason": (reason or "")[:160]},
        )
        session.performance["pause_operation_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        self.sm.persist_session(session)
        return {"status": "paused", "session": to_public_session(session)}

    def resume_session(
        self,
        session_id: str,
        *,
        page: Optional[Dict[str, Any]] = None,
        page_text: str = "",
        visual_context: Any = None,
        visual_ui_map: Any = None,
        privacy_report: Optional[Dict[str, Any]] = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
        execute: bool = True,
    ) -> Dict[str, Any]:
        """Resume requires fresh sanitized perception. Never uses cached coords."""
        t0 = time.perf_counter()
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}
        if session.cancel_requested or session.status == SESSION_CANCELLED:
            return {
                "status": "cancelled",
                "error": "cancelled_session_cannot_resume",
                "session": to_public_session(session),
            }
        if session.is_terminal():
            return {
                "status": session.status,
                "error": "terminal_session_cannot_resume",
                "session": to_public_session(session),
            }

        # Fresh perception required
        has_fresh = bool(page or safe_page_state or visual_ui_map)
        if not has_fresh:
            return {
                "status": "fresh_perception_required",
                "error": "resume_requires_fresh_sanitized_perception",
                "session": to_public_session(session),
            }

        # Recover / validate against saved page state
        recovery = self.recovery.recover_with_fresh_perception(
            session_id,
            page=page,
            page_text=page_text,
            visual_ui_map=visual_ui_map,
            safe_page_state=safe_page_state,
        )
        session = self.sm.get_session(session_id) or session
        decision = (recovery.get("decision") or {}).get("action")

        if decision == "require_intervention":
            session.performance["resume_operation_ms"] = round(
                (time.perf_counter() - t0) * 1000, 3
            )
            self.sm.persist_session(session)
            return {
                "status": "requires_user_intervention",
                "session": to_public_session(session),
                "recovery": recovery,
                "action": None,
            }

        if decision == "replan":
            # Generate preview (or auto-replan if operator approval disabled)
            preview_result = self.request_replan_preview(
                session_id,
                page=page,
                page_text=page_text,
                visual_ui_map=visual_ui_map,
                safe_page_state=safe_page_state,
            )
            session.performance["resume_operation_ms"] = round(
                (time.perf_counter() - t0) * 1000, 3
            )
            self.sm.persist_session(session)
            return {
                "status": "replan_required",
                "session": to_public_session(session),
                "recovery": recovery,
                "replan_preview": preview_result.get("preview"),
                "action": None,
            }

        # Idempotent: already running with same step — still re-resolve via orch
        if session.status == SESSION_PAUSED or session.status != SESSION_RUNNING:
            if session.can_transition(SESSION_RUNNING):
                try:
                    session.transition(SESSION_RUNNING, "Operator resume")
                except InvalidSessionTransitionError:
                    pass

        self.sm.append_event(
            session.session_id,
            EVENT_RESUMED,
            plan_version=session.plan_version,
            step_index=session.current_step_index,
            safe_metadata={"decision": decision or "resume"},
        )
        session.pause_reason = None
        self.sm.persist_session(session)

        result: Dict[str, Any] = {
            "status": "resumed",
            "session": to_public_session(session),
            "recovery": recovery,
            "action": None,
        }

        if execute and session.plan_id:
            orch_result = self._orch().resume_plan(
                session.plan_id,
                page=page,
                page_text=page_text,
                visual_context=visual_context,
                visual_ui_map=visual_ui_map,
                privacy_report=privacy_report,
                safe_page_state=safe_page_state,
            )
            # Sync session after orchestrator
            plan = self._load_plan(session)
            if plan:
                self.sm.sync_from_plan(plan)
            session = self.sm.get_session(session_id) or session
            result["status"] = orch_result.get("status") or "resumed"
            result["action"] = orch_result.get("action")
            result["plan"] = orch_result.get("plan")
            result["confirmation"] = orch_result.get("confirmation")
            result["session"] = to_public_session(session)

        session.performance["resume_operation_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        try:
            self.sm.persist_session(session)
        except StorageUnavailableError:
            pass
        return result

    def cancel_session(self, session_id: str, *, reason: str = "operator_cancel") -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}

        # Idempotent cancel
        if session.status == SESSION_CANCELLED or session.cancel_requested:
            session.cancel_requested = True
            session.performance["cancel_operation_ms"] = round(
                (time.perf_counter() - t0) * 1000, 3
            )
            self.sm.persist_session(session)
            return {
                "status": "cancelled",
                "idempotent": True,
                "session": to_public_session(session),
            }

        session.cancel_requested = True
        plan = self._load_plan(session)

        # Cancel pending confirmations + approved-but-unclaimed actions
        if plan:
            step = plan.current_step()
            if step and step.confirmation_id:
                try:
                    self.confirm_mgr.cancel(step.confirmation_id)
                except Exception:
                    pass
                try:
                    self.delivery.cancel_for_confirmation(step.confirmation_id)
                except Exception:
                    pass
            # Prefer cancelling via the session-backed plan object (authoritative)
            plan.cancel_requested = True
            if step and not step.is_terminal():
                try:
                    if step.can_transition(STEP_CANCELLED):
                        step.transition(STEP_CANCELLED, reason)
                    else:
                        step.status = STEP_CANCELLED
                except Exception:
                    step.status = STEP_CANCELLED
            try:
                if plan.can_transition(PLAN_CANCELLED):
                    plan.transition(PLAN_CANCELLED, reason)
                else:
                    plan.status = PLAN_CANCELLED
            except InvalidPlanTransitionError:
                plan.status = PLAN_CANCELLED
            # Also notify global orchestrator registry if it has a copy
            try:
                self._orch().cancel_plan(plan.plan_id)
            except Exception:
                pass
            self.sm.persist_plan(plan, session_id=session.session_id)

        try:
            if session.can_transition(SESSION_CANCELLED):
                session.transition(SESSION_CANCELLED, reason)
            else:
                session.status = SESSION_CANCELLED
                session.updated_at = time.time()
        except InvalidSessionTransitionError:
            session.status = SESSION_CANCELLED

        self.sm.clear_replan_preview(session.session_id, status="cleared")
        self.sm.append_event(
            session.session_id,
            EVENT_SESSION_CANCELLED,
            plan_version=session.plan_version,
            safe_metadata={"reason": (reason or "")[:160]},
        )
        session.performance["cancel_operation_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        self.sm.persist_session(session)
        return {"status": "cancelled", "session": to_public_session(session)}

    # ------------------------------------------------------------------
    # Replan preview / approve / reject
    # ------------------------------------------------------------------
    def request_replan_preview(
        self,
        session_id: str,
        *,
        page: Optional[Dict[str, Any]] = None,
        page_text: str = "",
        visual_ui_map: Any = None,
        safe_page_state: Optional[Dict[str, Any]] = None,
        observation: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}
        if session.cancel_requested or session.status == SESSION_CANCELLED:
            return {"status": "cancelled", "error": "cancelled_session"}

        plan = self._load_plan(session)
        if not plan:
            return {"status": "plan_not_found", "session_id": session_id}

        if plan.replan_count >= MAX_REPLAN_ATTEMPTS and not force:
            return {
                "status": "max_replan_attempts",
                "error": "replan_limit_enforced",
                "session": to_public_session(session),
                "plan": plan.public_view(),
            }

        obs = dict(observation or {})
        if visual_ui_map is not None:
            obs["visual_ui_map"] = visual_ui_map
        current_state = build_page_state(
            observation=obs,
            page=page or {},
            safe_page_state=safe_page_state,
            visual_ui_map=obs.get("visual_ui_map"),
            generation=plan.perception_generation,
        )
        previous = page_state_from_dict(plan.last_page_state)
        change = PageChangeDetector().compare(previous, current_state)

        result = self.replanner.replan(
            plan, current_state, change, obs, validity=None
        )
        gen_ms = round((time.perf_counter() - t0) * 1000, 3)
        session.performance["replan_preview_generation_ms"] = gen_ms

        if result.status != STATUS_REPLANNED:
            self.sm.persist_session(session)
            return {
                "status": result.status,
                "reason": result.reason,
                "session": to_public_session(session),
                "plan": plan.public_view(),
                "preview": None,
            }

        # Build preview WITHOUT mutating active pending steps
        completed = [
            s.public_view(i)
            for i, s in enumerate(plan.steps)
            if i < plan.current_step_index or s.status == STEP_SUCCESS
        ]
        old_pending = [
            s.public_view(i)
            for i, s in enumerate(plan.steps)
            if i >= plan.current_step_index and s.status != STEP_SUCCESS
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

        # Diff
        old_desc = [s.get("description") for s in old_pending]
        new_desc = [s.get("description") for s in new_pending]
        added = [d for d in new_desc if d not in old_desc]
        removed = [d for d in old_desc if d not in new_desc]
        changed = [
            {"from": a, "to": b}
            for a, b in zip(old_desc, new_desc)
            if a != b and a in old_desc and b in new_desc
        ]

        preview = {
            "preview_id": f"rpv_{secrets.token_hex(6)}",
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "plan_version": int(plan.plan_version or 1),
            "proposed_version": int(result.plan_version or (plan.plan_version + 1)),
            "reason": result.reason,
            "page_change_level": change.level,
            "changes": list(result.changes),
            "added_steps": added,
            "removed_steps": removed,
            "changed_steps": changed,
            "completed_steps": completed,
            "old_pending_steps": old_pending,
            "new_pending_steps": new_pending,
            "proposed_steps_snapshot": _snapshot_steps(result.updated_steps),
            "status": "pending",
            "created_at": time.time(),
            # Internal: keep serializable step payloads for approval apply
            "_proposed_steps": [
                s.serialize_for_persistence() for s in result.updated_steps
            ],
        }

        # If operator approval not required, apply immediately (4C default)
        if not session.operator_replan_approval:
            apply_replan_to_plan(plan, result)
            plan.last_page_state = current_state.to_dict()
            plan.last_page_signature = current_state.page_signature
            self.sm.persist_plan(plan, session_id=session.session_id)
            self.sm.sync_from_plan(plan)
            self.sm.persist_session(session)
            return {
                "status": "replanned",
                "auto_applied": True,
                "session": to_public_session(session),
                "plan": plan.public_view(),
                "preview": None,
                "replan": result.to_dict(),
            }

        # Operator mode: store preview only — do not mutate pending steps
        public_preview = {k: v for k, v in preview.items() if not k.startswith("_")}
        # Persist with internal steps for later apply (still privacy-validated)
        self.sm.save_replan_preview(session_id, preview)
        if session.can_transition(SESSION_PAUSED):
            try:
                session.transition(SESSION_PAUSED, "Awaiting replan approval")
            except InvalidSessionTransitionError:
                pass
        session.pause_reason = "awaiting_replan_approval"
        self.sm.persist_session(session)

        return {
            "status": "preview_ready",
            "session": to_public_session(session),
            "plan": plan.public_view(),
            "preview": public_preview,
            "performance": {"replan_preview_generation_ms": gen_ms},
        }

    def get_replan_preview(self, session_id: str) -> Dict[str, Any]:
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}
        preview = self.sm.get_replan_preview(session_id)
        if not preview:
            return {"status": "not_found", "preview": None}
        public = {k: v for k, v in preview.items() if not k.startswith("_")}
        return {"status": "ok", "preview": public}

    def approve_replan(self, session_id: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}
        if session.cancel_requested or session.status == SESSION_CANCELLED:
            return {"status": "cancelled", "error": "cancelled_session"}

        preview = self.sm.get_replan_preview(session_id)
        if not preview or preview.get("status") not in (None, "pending"):
            return {"status": "no_pending_preview", "session": to_public_session(session)}

        plan = self._load_plan(session)
        if not plan:
            return {"status": "plan_not_found"}

        if plan.replan_count >= MAX_REPLAN_ATTEMPTS:
            return {
                "status": "max_replan_attempts",
                "error": "replan_limit_enforced",
                "session": to_public_session(session),
            }

        proposed = preview.get("_proposed_steps") or []
        if not proposed:
            return {"status": "invalid_preview", "error": "missing_proposed_steps"}

        from agent.adaptive_replanner import ReplanResult

        updated_steps = [TaskStep.from_persistence(s) for s in proposed]
        # Enforce completed immutability
        idx = plan.current_step_index
        updated_steps = list(plan.steps[:idx]) + list(updated_steps[idx:])

        result = ReplanResult(
            status=STATUS_REPLANNED,
            updated_steps=updated_steps,
            reason=preview.get("reason") or "operator_approved_replan",
            replan_attempt=plan.replan_count + 1,
            changes=list(preview.get("changes") or []),
            plan_version=int(preview.get("proposed_version") or (plan.plan_version + 1)),
        )
        apply_replan_to_plan(plan, result)
        self.sm.persist_plan(plan, session_id=session.session_id)
        self.sm.clear_replan_preview(session_id, status="approved")
        self.sm.append_event(
            session_id,
            EVENT_REPLAN_APPROVED,
            plan_version=plan.plan_version,
            safe_metadata={
                "reason": (result.reason or "")[:120],
                "proposed_version": plan.plan_version,
            },
        )
        session.plan_version = plan.plan_version
        session.replan_count = plan.replan_count
        session.current_step_index = plan.current_step_index
        session.pause_reason = None
        if session.can_transition(SESSION_RUNNING):
            try:
                session.transition(SESSION_RUNNING, "Replan approved")
            except InvalidSessionTransitionError:
                pass
        session.performance["replan_approval_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )
        self.sm.persist_session(session)
        return {
            "status": "approved",
            "session": to_public_session(session),
            "plan": plan.public_view(),
        }

    def reject_replan(
        self, session_id: str, *, reason: str = "replan_rejected_by_operator"
    ) -> Dict[str, Any]:
        try:
            self.sm.require_storage()
        except StorageUnavailableError as exc:
            return {"status": "storage_unavailable", "error": str(exc)}

        session = self.sm.get_session(session_id)
        if not session:
            return {"status": "not_found", "session_id": session_id}

        preview = self.sm.get_replan_preview(session_id)
        # Rejecting clears preview; does not mutate plan
        self.sm.clear_replan_preview(session_id, status="rejected")
        self.sm.append_event(
            session_id,
            EVENT_REPLAN_REJECTED,
            plan_version=session.plan_version,
            safe_metadata={"reason": reason[:160]},
        )

        if session.can_transition(SESSION_PAUSED):
            try:
                session.transition(SESSION_PAUSED, reason)
            except InvalidSessionTransitionError:
                pass
        elif session.status != SESSION_PAUSED and not session.is_terminal():
            session.status = SESSION_PAUSED
            session.updated_at = time.time()
        session.pause_reason = reason

        plan = self._load_plan(session)
        if plan and plan.can_transition(PLAN_PAUSED):
            try:
                plan.transition(PLAN_PAUSED, reason)
            except InvalidPlanTransitionError:
                pass
            self.sm.persist_plan(plan, session_id=session.session_id)

        self.sm.persist_session(session)
        return {
            "status": "rejected",
            "session": to_public_session(session),
            "plan": plan.public_view() if plan else None,
            "had_preview": preview is not None,
        }

    def _load_plan(self, session: AgentSession) -> Optional[TaskPlan]:
        if not session.plan_id:
            return None
        return self.sm.load_plan_into_registry(session.plan_id)


_operator: Optional[OperatorControl] = None


def get_operator_control() -> OperatorControl:
    global _operator
    if _operator is None:
        _operator = OperatorControl()
    return _operator


def reset_operator_control(
    session_manager: Optional[SessionManager] = None,
) -> OperatorControl:
    global _operator
    _operator = OperatorControl(session_manager=session_manager)
    return _operator
