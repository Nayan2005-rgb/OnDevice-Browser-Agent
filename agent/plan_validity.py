"""Plan validity checking against the latest sanitized page state (Milestone 4C)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agent.page_state import PageState
from agent.task_plan import TaskPlan, TaskStep


# Labels / roles that indicate sensitive user intervention is required
_SENSITIVE_DIALOG_MARKERS = (
    "password",
    "login",
    "sign in",
    "signin",
    "log in",
    "otp",
    "one-time",
    "verification code",
    "2fa",
    "mfa",
    "captcha",
    "i'm not a robot",
    "recaptcha",
    "credit card",
    "card number",
    "cvv",
    "payment",
    "billing",
    "ssn",
    "social security",
    "identity verification",
    "verify your identity",
)

_SAFE_DIALOG_MARKERS = (
    "cookie",
    "cookies",
    "accept all",
    "reject all",
    "privacy policy",
    "consent",
    "we use cookies",
    "got it",
    "dismiss",
    "close",
)


def _blob(el: Mapping[str, Any]) -> str:
    return " ".join(
        str(el.get(k) or "")
        for k in ("label", "text", "role", "type", "ariaLabel", "name", "placeholder")
    ).lower()


def _elements_from(
    page_state: Optional[PageState],
    perception: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
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


def _target_present(elements: Sequence[Mapping[str, Any]], hint: Optional[str]) -> bool:
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


def _sensitive_requirement(elements: Sequence[Mapping[str, Any]], page_state: Optional[PageState]) -> Optional[str]:
    """Return a safe reason string if credentials / CAPTCHA / payment are needed."""
    dialogish = []
    for el in elements:
        blob = _blob(el)
        role = str(el.get("role") or el.get("type") or "").lower()
        el_type = str(el.get("type") or "").lower()
        if (
            role in ("dialog", "alertdialog")
            or el_type in ("dialog", "modal", "password")
            or el.get("sensitive")
            or any(m in blob for m in _SENSITIVE_DIALOG_MARKERS)
        ):
            dialogish.append(blob)

    if page_state and page_state.dialog_present and not dialogish:
        # Dialog present but labels unknown — check role dist for password inputs
        if (page_state.role_distribution or {}).get("password"):
            return "Login requires credentials."

    joined = " | ".join(dialogish)
    if not joined and page_state and page_state.dialog_present:
        # Inspect interactive labels on dialog pages
        joined = " | ".join(_blob(el) for el in (page_state.interactive_elements or []))

    if any(m in joined for m in ("password", "login", "sign in", "signin", "log in")):
        return "Login requires credentials."
    if any(m in joined for m in ("otp", "one-time", "verification code", "2fa", "mfa")):
        return "OTP / verification code required."
    if any(m in joined for m in ("captcha", "i'm not a robot", "recaptcha")):
        return "CAPTCHA / security challenge encountered."
    if any(m in joined for m in ("credit card", "card number", "cvv", "payment", "billing")):
        return "Payment information required."
    if any(m in joined for m in ("identity verification", "verify your identity", "ssn")):
        return "Identity verification required."
    if any(el.get("type") == "password" or el.get("sensitive") for el in elements):
        # Password field visible
        for el in elements:
            if str(el.get("type") or "").lower() == "password" or (
                el.get("sensitive") and "password" in _blob(el)
            ):
                return "Login requires credentials."
    return None


def _safe_dialog_present(elements: Sequence[Mapping[str, Any]], page_state: Optional[PageState]) -> bool:
    if page_state and not page_state.dialog_present:
        # Still allow detecting cookie banners without formal dialog role
        pass
    for el in elements:
        blob = _blob(el)
        if any(m in blob for m in _SAFE_DIALOG_MARKERS):
            if not any(m in blob for m in _SENSITIVE_DIALOG_MARKERS):
                return True
    return False


@dataclass
class PlanValidityResult:
    valid: bool
    confidence: float
    reasons: List[str] = field(default_factory=list)
    recommended_action: str = "continue"
    intervention_reason: Optional[str] = None
    check_ms: float = 0.0
    target_present: Optional[bool] = None
    safe_dialog: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "confidence": round(self.confidence, 4),
            "reasons": list(self.reasons),
            "recommended_action": self.recommended_action,
            "intervention_reason": self.intervention_reason,
            "check_ms": self.check_ms,
            "target_present": self.target_present,
            "safe_dialog": self.safe_dialog,
        }


class PlanValidityChecker:
    """Can the current pending step still be safely executed on this page?"""

    def check(
        self,
        plan: TaskPlan,
        current_step: Optional[TaskStep],
        page_state: Optional[PageState],
        perception: Optional[Mapping[str, Any]] = None,
        *,
        page_change_level: Optional[str] = None,
    ) -> PlanValidityResult:
        t0 = time.perf_counter()
        if current_step is None:
            return PlanValidityResult(
                valid=False,
                confidence=1.0,
                reasons=["no_current_step"],
                recommended_action="stop",
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
            )

        elements = _elements_from(page_state, perception)
        sensitive = _sensitive_requirement(elements, page_state)
        if sensitive:
            return PlanValidityResult(
                valid=False,
                confidence=0.95,
                reasons=["sensitive_requirement", "user_intervention_required"],
                recommended_action="requires_user_intervention",
                intervention_reason=sensitive,
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
                safe_dialog=False,
            )

        safe_dialog = _safe_dialog_present(elements, page_state)
        action = (current_step.action_type or "").lower()

        # Wait / navigate steps are generally valid; wait may still proceed
        if action == "wait":
            return PlanValidityResult(
                valid=True,
                confidence=0.9,
                reasons=["wait_step"],
                recommended_action="continue",
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
                safe_dialog=safe_dialog,
            )
        if action == "navigate":
            return PlanValidityResult(
                valid=True,
                confidence=0.9,
                reasons=["navigate_step"],
                recommended_action="continue",
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
            )
        if action == "scroll":
            return PlanValidityResult(
                valid=True,
                confidence=0.85,
                reasons=["scroll_step"],
                recommended_action="continue",
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
                safe_dialog=safe_dialog,
            )

        hint = current_step.target_hint or ""
        present = _target_present(elements, hint) if hint else None

        # Safe cookie/consent dialog blocking the flow → replan to dismiss
        if safe_dialog and page_state and page_state.dialog_present:
            if present is False or (page_change_level in ("structural", "moderate", "navigation")):
                return PlanValidityResult(
                    valid=False,
                    confidence=0.8,
                    reasons=["safe_dialog_blocking", "dialog_appeared"],
                    recommended_action="replan",
                    check_ms=round((time.perf_counter() - t0) * 1000, 3),
                    target_present=present,
                    safe_dialog=True,
                )

        if present is True:
            # Target moved / layout change — still valid; re-resolve
            action_rec = "continue"
            reasons = ["target_present"]
            if page_change_level in ("minor", "moderate", "structural", "navigation"):
                reasons.append("page_changed_but_target_resolvable")
            return PlanValidityResult(
                valid=True,
                confidence=0.9,
                reasons=reasons,
                recommended_action=action_rec,
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
                target_present=True,
                safe_dialog=safe_dialog,
            )

        if present is False:
            # Target gone
            if page_change_level == "navigation":
                return PlanValidityResult(
                    valid=False,
                    confidence=0.85,
                    reasons=["target_missing_after_navigation"],
                    recommended_action="replan",
                    check_ms=round((time.perf_counter() - t0) * 1000, 3),
                    target_present=False,
                )
            return PlanValidityResult(
                valid=False,
                confidence=0.85,
                reasons=["target_missing"],
                recommended_action="replan",
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
                target_present=False,
            )

        # No hint / unknown — if structural change, prefer replan; else continue
        if page_change_level in ("structural", "navigation"):
            return PlanValidityResult(
                valid=False,
                confidence=0.6,
                reasons=["structural_change_uncertain_target"],
                recommended_action="replan",
                check_ms=round((time.perf_counter() - t0) * 1000, 3),
                safe_dialog=safe_dialog,
            )

        return PlanValidityResult(
            valid=True,
            confidence=0.55,
            reasons=["no_blocking_change"],
            recommended_action="continue",
            check_ms=round((time.perf_counter() - t0) * 1000, 3),
            target_present=present,
            safe_dialog=safe_dialog,
        )
