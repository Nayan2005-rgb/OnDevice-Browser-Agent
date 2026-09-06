"""Deterministic action risk classification for Milestone 4A.

Classifies proposed actions as safe, confirmation_required, or blocked
using task text, target metadata, and sensitivity flags — no LLM.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional


# ---------------------------------------------------------------------------
# Pattern catalogs (deterministic)
# ---------------------------------------------------------------------------

DELETION_PATTERNS = (
    r"\bdelete\b",
    r"\bremove\b",
    r"\bdestroy\b",
    r"\berase\b",
    r"\bwipe\b",
)

PAYMENT_PATTERNS = (
    r"\bpay\b",
    r"\bpayment\b",
    r"\bpurchase\b",
    r"\bbuy\s+now\b",
    r"\bplace\s+order\b",
)

CHECKOUT_PATTERNS = (
    r"\bcheckout\b",
    r"\bcomplete\s+purchase\b",
)

TRANSFER_PATTERNS = (
    r"\btransfer\s+money\b",
    r"\bwire\s+transfer\b",
    r"\bsend\s+funds\b",
    r"\btransfer\s+funds\b",
)

ACCOUNT_CHANGE_PATTERNS = (
    r"\bchange\s+password\b",
    r"\breset\s+password\b",
    r"\bclose\s+account\b",
    r"\bdeactivate\s+account\b",
    r"\bdelete\s+account\b",
    r"\bremove\s+account\b",
)

AUTH_PATTERNS = (
    r"\blog\s*in\b",
    r"\bsign\s*in\b",
    r"\bauthenticate\b",
    r"\bverify\s+identity\b",
)

FORM_SUBMIT_PATTERNS = (
    r"\bsubmit\b",
    r"\bconfirm\s+order\b",
    r"\bsend\s+form\b",
)

SENSITIVE_INPUT_TYPES = frozenset(
    {"password", "credit_card", "card", "ssn", "cvv", "payment", "tel", "email"}
)

SENSITIVE_TYPE_BLOCK = frozenset({"password", "credit_card", "card", "ssn", "cvv", "payment"})

CATEGORY_REASONS = {
    "deletion": "This action may permanently delete data.",
    "payment": "This action may initiate a payment.",
    "financial_transfer": "This action may transfer funds.",
    "account_change": "This action may change account settings.",
    "authentication": "This action involves authentication.",
    "form_submission": "This action may submit a form with lasting effects.",
    "sensitive_input": "Interacting with sensitive input fields is blocked.",
    "navigation": "Navigation action.",
    "unknown": "Action risk could not be fully determined.",
}


@dataclass
class ActionSafetyResult:
    level: str  # "safe" | "confirmation_required" | "blocked"
    reason: str
    category: str
    classification_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ActionRiskClassifier:
    """Rule-based action risk classifier."""

    def classify(
        self,
        *,
        task: str = "",
        action: Optional[str] = None,
        target: Optional[Mapping[str, Any]] = None,
        url: Optional[str] = None,
        element_type: Optional[str] = None,
        role: Optional[str] = None,
        aria_label: Optional[str] = None,
        target_text: Optional[str] = None,
        sensitive: Optional[bool] = None,
    ) -> ActionSafetyResult:
        t0 = time.perf_counter()
        target = target or {}
        task_l = (task or "").strip().lower()
        action_l = (action or "").strip().lower()

        label = self._safe_label(
            target_text
            or target.get("text")
            or aria_label
            or target.get("ariaLabel")
            or target.get("aria_label")
            or ""
        )
        label_l = label.lower()
        el_type = (
            element_type
            or target.get("type")
            or target.get("input_type")
            or ""
        ).lower()
        el_role = (role or target.get("role") or "").lower()
        is_sensitive = bool(
            sensitive
            if sensitive is not None
            else target.get("sensitive")
        )
        combined = f"{task_l} {label_l} {el_type} {el_role}"

        # --- Blocked: sensitive / password ---
        if el_type in SENSITIVE_TYPE_BLOCK or is_sensitive and el_type == "password":
            return self._result(
                "blocked",
                "sensitive_input",
                "Interacting with password or payment fields is blocked.",
                t0,
            )
        if is_sensitive and action_l in ("type", "click", "coordinate_click"):
            # Password / card / SSN targets
            if el_type in SENSITIVE_INPUT_TYPES or "password" in label_l:
                return self._result(
                    "blocked",
                    "sensitive_input",
                    "Interacting with sensitive input fields is blocked.",
                    t0,
                )
        if "password" in task_l and action_l in ("type", "click", "coordinate_click", ""):
            if "password" in task_l and (
                "click" in task_l or "type" in task_l or "enter" in task_l or "fill" in task_l
            ):
                return self._result(
                    "blocked",
                    "sensitive_input",
                    "Interacting with password fields is blocked.",
                    t0,
                )
        if el_type == "password" or (is_sensitive and "password" in (label_l + el_type)):
            return self._result(
                "blocked",
                "sensitive_input",
                "Interacting with password fields is blocked.",
                t0,
            )

        # --- Confirmation categories (most specific first) ---
        if self._matches(combined, ACCOUNT_CHANGE_PATTERNS) or self._matches(
            label_l, ACCOUNT_CHANGE_PATTERNS
        ):
            return self._result(
                "confirmation_required",
                "account_change",
                CATEGORY_REASONS["account_change"],
                t0,
            )
        if self._matches(combined, TRANSFER_PATTERNS):
            return self._result(
                "confirmation_required",
                "financial_transfer",
                CATEGORY_REASONS["financial_transfer"],
                t0,
            )
        if self._matches(combined, PAYMENT_PATTERNS) or self._matches(
            label_l, PAYMENT_PATTERNS
        ):
            return self._result(
                "confirmation_required",
                "payment",
                CATEGORY_REASONS["payment"],
                t0,
            )
        if self._matches(combined, CHECKOUT_PATTERNS) or self._matches(
            label_l, CHECKOUT_PATTERNS
        ):
            return self._result(
                "confirmation_required",
                "payment",
                "This action may proceed to checkout or payment.",
                t0,
            )
        if self._matches(combined, DELETION_PATTERNS) or self._matches(
            label_l, DELETION_PATTERNS
        ):
            return self._result(
                "confirmation_required",
                "deletion",
                CATEGORY_REASONS["deletion"],
                t0,
            )
        if self._matches(combined, AUTH_PATTERNS):
            return self._result(
                "confirmation_required",
                "authentication",
                CATEGORY_REASONS["authentication"],
                t0,
            )
        # Form submit only when label/task strongly suggests submit (not every click)
        if action_l in ("click", "coordinate_click", "") and self._matches(
            label_l, (r"^submit$", r"\bsubmit\b")
        ):
            # Soft: "Submit" alone on a button is usually OK for demos;
            # require confirmation when task also mentions submit form/order
            if self._matches(task_l, (r"\bsubmit\s+(form|order|payment|application)\b",)):
                return self._result(
                    "confirmation_required",
                    "form_submission",
                    CATEGORY_REASONS["form_submission"],
                    t0,
                )

        # Navigation / scroll / benign clicks
        if action_l == "scroll" or "scroll" in task_l:
            return self._result("safe", "navigation", CATEGORY_REASONS["navigation"], t0)

        if action_l in ("click", "coordinate_click", "type", "scroll") or action_l == "":
            return self._result(
                "safe",
                "navigation" if action_l == "scroll" else "unknown",
                "Action appears low-risk.",
                t0,
            )

        return self._result("safe", "unknown", CATEGORY_REASONS["unknown"], t0)

    def classify_decision(
        self, decision: Mapping[str, Any], task: str = ""
    ) -> ActionSafetyResult:
        """Classify from a decision-engine output dict."""
        resolved = decision.get("resolved_target") or {}
        return self.classify(
            task=task or decision.get("task") or "",
            action=decision.get("action"),
            target=resolved,
            target_text=resolved.get("text"),
            element_type=resolved.get("type") or resolved.get("input_type"),
            role=resolved.get("role"),
            sensitive=resolved.get("sensitive"),
        )

    @staticmethod
    def _matches(text: str, patterns: tuple) -> bool:
        if not text:
            return False
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)

    @staticmethod
    def _safe_label(text: Any) -> str:
        s = str(text or "").strip()
        # Never propagate redaction tokens as classification features beyond presence
        if s in ("[REDACTED]", "[PASSWORD_REDACTED]", "[EMAIL_REDACTED]"):
            return ""
        # Cap length to avoid accidental PII-sized payloads in reasons
        return s[:80]

    def _result(
        self, level: str, category: str, reason: str, t0: float
    ) -> ActionSafetyResult:
        return ActionSafetyResult(
            level=level,
            reason=reason,
            category=category,
            classification_ms=round((time.perf_counter() - t0) * 1000, 3),
        )
