"""Bounded adaptive re-planning for Milestone 4C.

Only pending / future steps may be replaced. Completed steps, verification
history, and confirmation history are immutable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agent.page_change_detector import PageChangeResult
from agent.page_state import PageState
from agent.plan_validity import PlanValidityResult, _safe_dialog_present, _sensitive_requirement
from agent.task_plan import (
    STEP_PENDING,
    STEP_SUCCESS,
    TaskPlan,
    TaskStep,
    new_step_id,
)


MAX_REPLAN_ATTEMPTS = 2

STATUS_REPLANNED = "replanned"
STATUS_NOT_NEEDED = "not_needed"
STATUS_PAUSED = "paused"
STATUS_UNSUPPORTED = "unsupported"
STATUS_FAILED = "failed"
STATUS_MAX_ATTEMPTS = "max_attempts_reached"
STATUS_RE_RESOLVE = "re_resolve"


def _blob(el: Mapping[str, Any]) -> str:
    return " ".join(
        str(el.get(k) or "")
        for k in ("label", "text", "role", "type", "ariaLabel", "name")
    ).lower()


def _elements(page_state: Optional[PageState], perception: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if page_state is not None:
        out.extend(page_state.interactive_elements or [])
    if isinstance(perception, Mapping):
        for el in perception.get("ui_elements") or []:
            if isinstance(el, Mapping):
                out.append(dict(el))
        vmap = perception.get("visual_ui_map") or {}
        for el in (vmap.get("elements") if isinstance(vmap, Mapping) else None) or []:
            if isinstance(el, Mapping):
                out.append(dict(el))
    return out


def _target_still_exists(elements: Sequence[Mapping[str, Any]], hint: Optional[str]) -> bool:
    tokens = [t for t in (hint or "").lower().split() if t and t not in ("the", "a", "an")]
    if not tokens:
        return False
    for el in elements:
        blob = _blob(el)
        if all(t in blob for t in tokens):
            return True
        if any(t in blob for t in tokens if len(t) > 3):
            return True
    return False


def _dismiss_cookie_step(index: int) -> TaskStep:
    return TaskStep(
        step_id=new_step_id(index),
        description="Dismiss cookie / consent dialog",
        action_type="click",
        status=STEP_PENDING,
        target_hint="accept cookies",
        meta={"replan_inserted": True, "strategy": "safe_dialog"},
    )


def _snapshot_steps(steps: Sequence[TaskStep]) -> List[Dict[str, Any]]:
    """Safe revision snapshot — descriptions and statuses only."""
    out = []
    for i, s in enumerate(steps):
        out.append(
            {
                "index": i + 1,
                "step_id": s.step_id,
                "description": (s.description or "")[:200],
                "action_type": s.action_type,
                "status": s.status,
                "target_hint": (s.target_hint or "")[:80] if s.target_hint else None,
            }
        )
    return out


@dataclass
class ReplanResult:
    status: str
    updated_steps: List[TaskStep] = field(default_factory=list)
    reason: str = ""
    replan_attempt: int = 0
    changes: List[str] = field(default_factory=list)
    plan_version: int = 1
    generation_ms: float = 0.0
    intervention_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "replan_attempt": self.replan_attempt,
            "changes": list(self.changes),
            "plan_version": self.plan_version,
            "generation_ms": self.generation_ms,
            "intervention_reason": self.intervention_reason,
            "updated_step_count": len(self.updated_steps),
        }


class AdaptiveReplanner:
    """Deterministic strategies to update only pending/future plan steps."""

    def replan(
        self,
        task_plan: TaskPlan,
        current_page_state: Optional[PageState],
        change_result: Optional[PageChangeResult] = None,
        perception: Optional[Mapping[str, Any]] = None,
        *,
        validity: Optional[PlanValidityResult] = None,
    ) -> ReplanResult:
        t0 = time.perf_counter()
        attempt = int(getattr(task_plan, "replan_count", 0) or 0) + 1

        if attempt > MAX_REPLAN_ATTEMPTS:
            return ReplanResult(
                status=STATUS_MAX_ATTEMPTS,
                reason="max_replan_attempts_reached",
                replan_attempt=attempt,
                plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
            )

        elements = _elements(current_page_state, perception)

        # Sensitive requirements → pause for user intervention (never type credentials)
        sensitive = None
        if validity and validity.recommended_action == "requires_user_intervention":
            sensitive = validity.intervention_reason
        if not sensitive:
            sensitive = _sensitive_requirement(elements, current_page_state)
        if sensitive:
            return ReplanResult(
                status=STATUS_PAUSED,
                reason="requires_user_intervention",
                replan_attempt=attempt,
                changes=["user_intervention_required"],
                plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
                intervention_reason=sensitive,
            )

        step = task_plan.current_step()
        change_level = (change_result.level if change_result else None) or "none"
        reasons = list((change_result.reasons if change_result else None) or [])
        if validity:
            reasons.extend(validity.reasons or [])

        # Strategy A/B — target moved or layout changed but semantic target exists
        if step and step.action_type in ("click", "type") and step.target_hint:
            if _target_still_exists(elements, step.target_hint):
                # Clear any stale resolve metadata; do not rewrite plan
                step.meta.pop("cached_coordinates", None)
                step.meta.pop("active_coordinates", None)
                step.resolve_source = None
                step.resolve_strategy = None
                return ReplanResult(
                    status=STATUS_RE_RESOLVE,
                    reason="target_still_present_re_resolve",
                    replan_attempt=attempt,
                    changes=["re_resolve_target", f"change_level:{change_level}"],
                    plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                    generation_ms=round((time.perf_counter() - t0) * 1000, 3),
                )

        # Strategy C — safe dialog (cookie consent)
        safe_dialog = (validity.safe_dialog if validity else False) or _safe_dialog_present(
            elements, current_page_state
        )
        if safe_dialog and current_page_state and current_page_state.dialog_present:
            return self._insert_safe_dialog_step(
                task_plan, attempt, t0, reason="safe_dialog_appeared"
            )

        # Also detect cookie markers without formal dialog_present
        if safe_dialog and "dialog_appeared" in reasons:
            return self._insert_safe_dialog_step(
                task_plan, attempt, t0, reason="safe_dialog_appeared"
            )

        # Strategy D — navigation: keep remaining semantic steps if possible
        if change_level == "navigation" and step:
            # If current semantic target exists on new page → re-resolve
            if step.target_hint and _target_still_exists(elements, step.target_hint):
                step.meta.pop("cached_coordinates", None)
                return ReplanResult(
                    status=STATUS_RE_RESOLVE,
                    reason="navigation_target_re_resolve",
                    replan_attempt=attempt,
                    changes=["navigation", "re_resolve_target"],
                    plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                    generation_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            # Otherwise try to keep future steps that still make sense
            return self._rewrite_pending_for_environment(
                task_plan,
                elements,
                attempt,
                t0,
                reason="navigation_environment_changed",
                changes=["navigation", "pending_steps_updated"],
            )

        # Strategy E — target disappeared
        if step and (
            (validity and validity.target_present is False)
            or "target_missing" in reasons
        ):
            # Try inserting a wait-for-target if results might load
            if step.action_type == "click" and step.target_hint:
                return self._insert_wait_then_retry(
                    task_plan, step, attempt, t0, reason="target_disappeared"
                )
            return ReplanResult(
                status=STATUS_UNSUPPORTED,
                reason="cannot_replan_missing_target",
                replan_attempt=attempt,
                changes=["target_missing"],
                plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
            )

        # Generic structural change without a clear strategy
        if change_level in ("structural", "moderate"):
            if step and step.target_hint and _target_still_exists(elements, step.target_hint):
                return ReplanResult(
                    status=STATUS_RE_RESOLVE,
                    reason="structural_change_re_resolve",
                    replan_attempt=attempt,
                    changes=["structural", "re_resolve_target"],
                    plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                    generation_ms=round((time.perf_counter() - t0) * 1000, 3),
                )
            return ReplanResult(
                status=STATUS_UNSUPPORTED,
                reason="unsupported_environment_change",
                replan_attempt=attempt,
                changes=reasons[:6] or ["structural_change"],
                plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
            )

        return ReplanResult(
            status=STATUS_NOT_NEEDED,
            reason="no_replan_required",
            replan_attempt=max(0, attempt - 1),
            plan_version=int(getattr(task_plan, "plan_version", 1) or 1),
            generation_ms=round((time.perf_counter() - t0) * 1000, 3),
        )

    def _insert_safe_dialog_step(
        self,
        plan: TaskPlan,
        attempt: int,
        t0: float,
        *,
        reason: str,
    ) -> ReplanResult:
        idx = plan.current_step_index
        completed = list(plan.steps[:idx])
        # Immutability check — completed must stay as-is
        for s in completed:
            if s.status != STEP_SUCCESS and s.status not in (
                "failed",
                "cancelled",
                "skipped",
            ):
                # Still treat as frozen prefix
                pass
        pending = list(plan.steps[idx:])
        # Avoid duplicate dismiss steps
        if pending and pending[0].meta.get("strategy") == "safe_dialog":
            return ReplanResult(
                status=STATUS_RE_RESOLVE,
                reason="safe_dialog_step_already_present",
                replan_attempt=attempt,
                changes=["safe_dialog_existing"],
                plan_version=int(getattr(plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
            )
        insert = _dismiss_cookie_step(idx)
        new_pending = [insert] + pending
        # Re-id pending for clarity (keep completed step_ids)
        for i, s in enumerate(new_pending):
            if s is insert or s.meta.get("replan_inserted"):
                continue
        updated = completed + new_pending
        return ReplanResult(
            status=STATUS_REPLANNED,
            updated_steps=updated,
            reason=reason,
            replan_attempt=attempt,
            changes=["inserted_dismiss_cookie_dialog", "pending_steps_shifted"],
            plan_version=int(getattr(plan, "plan_version", 1) or 1) + 1,
            generation_ms=round((time.perf_counter() - t0) * 1000, 3),
        )

    def _insert_wait_then_retry(
        self,
        plan: TaskPlan,
        step: TaskStep,
        attempt: int,
        t0: float,
        *,
        reason: str,
    ) -> ReplanResult:
        idx = plan.current_step_index
        completed = list(plan.steps[:idx])
        # If already waiting for this target, unsupported
        if step.action_type == "wait":
            return ReplanResult(
                status=STATUS_UNSUPPORTED,
                reason="wait_already_active_target_missing",
                replan_attempt=attempt,
                plan_version=int(getattr(plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
            )
        wait = TaskStep(
            step_id=new_step_id(idx),
            description=f"Wait for {step.target_hint or 'target'} to appear",
            action_type="wait",
            status=STEP_PENDING,
            condition="element_present",
            target_hint=step.target_hint,
            timeout_ms=5000,
            meta={"replan_inserted": True, "strategy": "wait_for_target"},
        )
        # Reset current step to pending for retry after wait
        retry = TaskStep(
            step_id=new_step_id(idx + 1),
            description=step.description,
            action_type=step.action_type,
            status=STEP_PENDING,
            target_hint=step.target_hint,
            value=step.value,
            value_redacted=step.value_redacted,
            meta={"replan_inserted": True, "strategy": "retry_after_wait"},
        )
        rest = list(plan.steps[idx + 1 :])
        updated = completed + [wait, retry] + rest
        return ReplanResult(
            status=STATUS_REPLANNED,
            updated_steps=updated,
            reason=reason,
            replan_attempt=attempt,
            changes=["inserted_wait_for_target", "retry_current_step"],
            plan_version=int(getattr(plan, "plan_version", 1) or 1) + 1,
            generation_ms=round((time.perf_counter() - t0) * 1000, 3),
        )

    def _rewrite_pending_for_environment(
        self,
        plan: TaskPlan,
        elements: Sequence[Mapping[str, Any]],
        attempt: int,
        t0: float,
        *,
        reason: str,
        changes: List[str],
    ) -> ReplanResult:
        idx = plan.current_step_index
        completed = list(plan.steps[:idx])
        pending = list(plan.steps[idx:])
        kept: List[TaskStep] = []
        for s in pending:
            if s.action_type in ("wait", "scroll", "navigate"):
                # Fresh pending copy without stale resolve state
                kept.append(
                    TaskStep(
                        step_id=new_step_id(len(completed) + len(kept)),
                        description=s.description,
                        action_type=s.action_type,
                        status=STEP_PENDING,
                        target_hint=s.target_hint,
                        condition=s.condition,
                        direction=s.direction,
                        amount=s.amount,
                        url=s.url,
                        timeout_ms=s.timeout_ms,
                        meta={"replan_kept": True},
                    )
                )
            elif s.target_hint and _target_still_exists(elements, s.target_hint):
                kept.append(
                    TaskStep(
                        step_id=new_step_id(len(completed) + len(kept)),
                        description=s.description,
                        action_type=s.action_type,
                        status=STEP_PENDING,
                        target_hint=s.target_hint,
                        value=s.value,
                        value_redacted=s.value_redacted,
                        meta={"replan_kept": True},
                    )
                )
            else:
                # Drop steps whose targets are gone; keep semantic goal steps that
                # might appear after wait
                if s.action_type == "click" and s.target_hint:
                    kept.append(
                        TaskStep(
                            step_id=new_step_id(len(completed) + len(kept)),
                            description=f"Wait for {s.target_hint}",
                            action_type="wait",
                            status=STEP_PENDING,
                            condition="element_present",
                            target_hint=s.target_hint,
                            timeout_ms=5000,
                            meta={"replan_inserted": True, "strategy": "wait_missing_after_nav"},
                        )
                    )
                    kept.append(
                        TaskStep(
                            step_id=new_step_id(len(completed) + len(kept)),
                            description=s.description,
                            action_type=s.action_type,
                            status=STEP_PENDING,
                            target_hint=s.target_hint,
                            meta={"replan_kept": True},
                        )
                    )
        if not kept:
            return ReplanResult(
                status=STATUS_UNSUPPORTED,
                reason="no_valid_pending_steps_after_navigation",
                replan_attempt=attempt,
                changes=changes,
                plan_version=int(getattr(plan, "plan_version", 1) or 1),
                generation_ms=round((time.perf_counter() - t0) * 1000, 3),
            )
        updated = completed + kept
        return ReplanResult(
            status=STATUS_REPLANNED,
            updated_steps=updated,
            reason=reason,
            replan_attempt=attempt,
            changes=changes,
            plan_version=int(getattr(plan, "plan_version", 1) or 1) + 1,
            generation_ms=round((time.perf_counter() - t0) * 1000, 3),
        )


def apply_replan_to_plan(plan: TaskPlan, result: ReplanResult) -> TaskPlan:
    """Apply a successful replan result onto the plan (mutates plan)."""
    if result.status not in (STATUS_REPLANNED,):
        return plan

    # Freeze completed prefix — verify immutability
    idx = plan.current_step_index
    old_completed = plan.steps[:idx]
    new_completed = result.updated_steps[:idx]
    for old, new in zip(old_completed, new_completed):
        if old.step_id != new.step_id or old.description != new.description:
            # Force preserve completed steps
            result.updated_steps = list(old_completed) + list(result.updated_steps[idx:])
            break

    # Record revision history (safe)
    if not hasattr(plan, "revision_history") or plan.revision_history is None:
        plan.revision_history = []
    plan.revision_history.append(
        {
            "version": int(getattr(plan, "plan_version", 1) or 1),
            "timestamp": time.time(),
            "steps": _snapshot_steps(plan.steps),
            "status": plan.status,
        }
    )

    plan.steps = list(result.updated_steps)
    plan.plan_version = int(result.plan_version or (plan.plan_version + 1))
    plan.replan_count = int(getattr(plan, "replan_count", 0) or 0) + 1
    plan.last_replan_reason = result.reason
    plan.updated_at = time.time()
    # Current index stays at first pending (same idx — completed prefix unchanged)
    plan.current_step_index = min(idx, max(0, len(plan.steps) - 1)) if plan.steps else 0
    # Reset current step runtime fields
    cur = plan.current_step()
    if cur and cur.status not in (STEP_SUCCESS,):
        if cur.status != STEP_PENDING:
            cur.status = STEP_PENDING
        cur.recovery_attempts = 0
        cur.error = None
        cur.lifecycle_id = None
        cur.confirmation_id = None
        cur.resolve_source = None
        cur.resolve_strategy = None
        cur.meta.pop("cached_coordinates", None)
        cur.meta.pop("active_coordinates", None)

    plan.revision_history.append(
        {
            "version": plan.plan_version,
            "timestamp": time.time(),
            "steps": _snapshot_steps(plan.steps),
            "status": plan.status,
            "replan_reason": result.reason,
            "changes": list(result.changes),
        }
    )
    return plan
