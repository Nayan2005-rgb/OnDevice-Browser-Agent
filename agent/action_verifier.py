"""Safe pre/post action verification using sanitized page state only."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


DEFAULT_VERIFICATION_DELAY_MS = 300
MAX_VERIFICATION_DELAY_MS = 2000

# Fields never allowed in verification snapshots
_FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "password",
        "value",
        "email",
        "phone",
        "ssn",
        "card",
        "credit_card",
        "cvv",
        "raw_screenshot",
        "screenshot",
        "face_crop",
        "embedding",
    }
)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


def _safe_str(value: Any, max_len: int = 60) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    upper = s.upper()
    if any(
        tok in upper
        for tok in (
            "[PASSWORD",
            "[EMAIL",
            "[PHONE",
            "[SSN",
            "[CARD",
            "[REDACTED]",
        )
    ):
        return "[REDACTED]"
    return s[:max_len]


@dataclass
class PreActionState:
    url: str = ""
    visible_elements: List[Dict[str, Any]] = field(default_factory=list)
    page_signature: str = ""
    target_signature: str = ""
    element_count: int = 0
    dialog_present: bool = False
    scroll_y: Optional[int] = None
    target_present: Optional[bool] = None
    target_enabled: Optional[bool] = None
    capture_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    status: str  # "success" | "unclear" | "failed"
    signals: Dict[str, Any]
    reason: str
    analysis_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "signals": dict(self.signals),
            "reason": self.reason,
            "analysis_ms": self.analysis_ms,
        }


class ActionVerifier:
    """Capture sanitized pre-action state and compare post-action snapshots."""

    def capture_pre_action_state(
        self,
        page: Optional[Mapping[str, Any]] = None,
        *,
        target: Optional[Mapping[str, Any]] = None,
        safe_page_state: Optional[Mapping[str, Any]] = None,
    ) -> PreActionState:
        t0 = time.perf_counter()
        page = page or {}
        safe = safe_page_state or {}

        url = _safe_str(safe.get("url") or page.get("url") or "", 200)
        elements = self._sanitize_elements(
            safe.get("visible_elements")
            or safe.get("elements")
            or page.get("elements")
            or []
        )
        dialog_present = bool(
            safe.get("dialog_present")
            if "dialog_present" in safe
            else self._detect_dialog(elements)
        )
        scroll_y = safe.get("scroll_y")
        if scroll_y is not None:
            try:
                scroll_y = int(scroll_y)
            except (TypeError, ValueError):
                scroll_y = None

        page_sig = self.compute_page_signature(
            url=url,
            elements=elements,
            dialog_present=dialog_present,
            layout=safe.get("layout_structure"),
        )
        target_sig = self.compute_target_signature(target or safe.get("target") or {})

        target_present = safe.get("target_present")
        target_enabled = safe.get("target_enabled")
        if target_present is None and target:
            sel = target.get("selector")
            if sel:
                target_present = any(e.get("selector") == sel for e in elements)

        state = PreActionState(
            url=url,
            visible_elements=elements,
            page_signature=page_sig,
            target_signature=target_sig,
            element_count=len(elements),
            dialog_present=dialog_present,
            scroll_y=scroll_y,
            target_present=target_present,
            target_enabled=target_enabled,
            capture_ms=_ms(t0),
        )
        return state

    def verify(
        self,
        before: PreActionState | Mapping[str, Any],
        after: PreActionState | Mapping[str, Any],
        *,
        action_type: str = "click",
        execution_success: Optional[bool] = None,
    ) -> VerificationResult:
        t0 = time.perf_counter()
        before_d = before.to_dict() if isinstance(before, PreActionState) else dict(before)
        after_d = after.to_dict() if isinstance(after, PreActionState) else dict(after)

        # Privacy guard: reject snapshots that smuggle forbidden keys at top level
        for snap in (before_d, after_d):
            for key in list(snap.keys()):
                if key.lower() in _FORBIDDEN_VALUE_KEYS and key not in (
                    "visible_elements",
                    "page_signature",
                    "target_signature",
                ):
                    snap.pop(key, None)

        url_changed = (before_d.get("url") or "") != (after_d.get("url") or "")
        page_changed = (before_d.get("page_signature") or "") != (
            after_d.get("page_signature") or ""
        )
        target_changed = (before_d.get("target_signature") or "") != (
            after_d.get("target_signature") or ""
        )
        before_count = int(before_d.get("element_count") or 0)
        after_count = int(after_d.get("element_count") or 0)
        new_elements = max(0, after_count - before_count)
        dialog_appeared = (not before_d.get("dialog_present")) and bool(
            after_d.get("dialog_present")
        )
        dialog_changed = bool(before_d.get("dialog_present")) != bool(
            after_d.get("dialog_present")
        )
        scroll_changed = False
        if before_d.get("scroll_y") is not None and after_d.get("scroll_y") is not None:
            scroll_changed = int(before_d["scroll_y"]) != int(after_d["scroll_y"])

        target_disappeared = (
            before_d.get("target_present") is True
            and after_d.get("target_present") is False
        )
        target_enabled_changed = (
            before_d.get("target_enabled") is not None
            and after_d.get("target_enabled") is not None
            and before_d.get("target_enabled") != after_d.get("target_enabled")
        )

        signals = {
            "url_changed": url_changed,
            "page_changed": page_changed,
            "target_changed": target_changed or target_disappeared or target_enabled_changed,
            "new_elements": new_elements,
            "dialog_appeared": dialog_appeared,
            "dialog_changed": dialog_changed,
            "scroll_changed": scroll_changed,
            "target_disappeared": bool(target_disappeared),
            "execution_reported_success": execution_success,
        }

        action_l = (action_type or "click").lower()
        status, reason = self._interpret(
            action_l=action_l,
            signals=signals,
            execution_success=execution_success,
        )
        return VerificationResult(
            status=status,
            signals=signals,
            reason=reason,
            analysis_ms=_ms(t0),
        )

    def _interpret(
        self,
        *,
        action_l: str,
        signals: Dict[str, Any],
        execution_success: Optional[bool],
    ) -> tuple:
        positive = (
            signals["url_changed"]
            or signals["page_changed"]
            or signals["target_changed"]
            or signals["dialog_appeared"]
            or signals["new_elements"] > 0
            or signals["scroll_changed"]
        )

        if action_l in ("click", "coordinate_click"):
            if signals["url_changed"]:
                return "success", "URL changed after action."
            if signals["dialog_appeared"]:
                return "success", "New dialog appeared after action."
            if signals["target_disappeared"] or signals["target_changed"]:
                return "success", "Target state changed after action."
            if signals["page_changed"] or signals["new_elements"] > 0:
                return "success", "Page structure changed after action."
            if execution_success is False:
                return "failed", "Execution reported failure and page did not change."
            if execution_success and not positive:
                return "unclear", "Execution reported success but no page change detected."
            if not positive:
                return "failed", "No observable page change after action."
            return "success", "Page structure changed after action."

        if action_l == "type":
            # Never verify raw typed content — only field/page interaction state
            if signals["target_changed"] or signals["page_changed"]:
                return "success", "Field or page interaction state changed."
            if execution_success:
                return "unclear", "Type executed; typed content not verified for privacy."
            return "failed", "Type action produced no observable state change."

        if action_l == "scroll":
            if signals["scroll_changed"] or signals["page_changed"]:
                return "success", "Scroll position or viewport content changed."
            if execution_success:
                return "unclear", "Scroll reported success but position unchanged."
            return "failed", "Scroll produced no observable change."

        if positive:
            return "success", "Observable page change detected."
        if execution_success:
            return "unclear", "Execution succeeded without clear verification signals."
        return "failed", "No verification signals matched."

    def compute_page_signature(
        self,
        *,
        url: str = "",
        elements: Sequence[Mapping[str, Any]] = (),
        dialog_present: bool = False,
        layout: Any = None,
    ) -> str:
        """Hash a normalized, sanitized representation of page structure."""
        normalized = []
        for el in elements:
            normalized.append(
                {
                    "role": _safe_str(el.get("role") or el.get("tag"), 40),
                    "label": _safe_str(el.get("label") or el.get("text"), 40),
                    "type": _safe_str(el.get("type"), 20),
                    "sensitive": bool(el.get("sensitive")),
                }
            )
        normalized.sort(
            key=lambda e: (e["role"], e["label"], e["type"], str(e["sensitive"]))
        )
        payload = {
            "url": _safe_str(url, 200),
            "count": len(normalized),
            "elements": normalized,
            "dialog": bool(dialog_present),
            "layout": layout if isinstance(layout, (str, int, float, bool)) else None,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def compute_target_signature(self, target: Mapping[str, Any]) -> str:
        if not target:
            return ""
        payload = {
            "id": _safe_str(target.get("id"), 40),
            "type": _safe_str(target.get("type"), 20),
            "role": _safe_str(target.get("role"), 20),
            "label": _safe_str(target.get("label") or target.get("text"), 40),
            "selector": _safe_str(target.get("selector"), 80)
            if not target.get("sensitive")
            else "",
            "enabled": target.get("enabled"),
            "present": target.get("present"),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _sanitize_elements(
        self, elements: Sequence[Mapping[str, Any]]
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for el in elements or []:
            if not isinstance(el, Mapping):
                continue
            sensitive = bool(el.get("sensitive"))
            label = _safe_str(el.get("label") or el.get("text") or el.get("ariaLabel"), 40)
            if sensitive:
                label = "[REDACTED]"
            item = {
                "tag": _safe_str(el.get("tag"), 20),
                "role": _safe_str(el.get("role"), 20),
                "type": _safe_str(el.get("type"), 20),
                "label": label,
                "sensitive": sensitive,
            }
            sel = el.get("selector")
            if sel and not sensitive:
                item["selector"] = _safe_str(sel, 80)
            out.append(item)
            if len(out) >= 80:
                break
        return out

    @staticmethod
    def _detect_dialog(elements: Sequence[Mapping[str, Any]]) -> bool:
        for el in elements:
            role = str(el.get("role") or "").lower()
            tag = str(el.get("tag") or "").lower()
            if role in ("dialog", "alertdialog") or tag in ("dialog",):
                return True
        return False

    @staticmethod
    def clamp_delay_ms(delay_ms: Optional[int] = None) -> int:
        if delay_ms is None:
            return DEFAULT_VERIFICATION_DELAY_MS
        try:
            val = int(delay_ms)
        except (TypeError, ValueError):
            return DEFAULT_VERIFICATION_DELAY_MS
        return max(0, min(val, MAX_VERIFICATION_DELAY_MS))
