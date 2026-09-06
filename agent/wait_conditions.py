"""Bounded wait predicates over sanitized page state (Milestone 4C)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agent.page_state import PageState


DEFAULT_WAIT_TIMEOUT_MS = 5000
MAX_WAIT_TIMEOUT_MS = 10000

WAIT_KINDS = frozenset(
    {
        "element_present",
        "element_disappeared",
        "url_changed",
        "page_signature_changed",
        "dialog_present",
        "dialog_disappeared",
        "navigation_complete",
        "results_present",
        "page_changed",
        "page_changed_or_results_visible",
        "target_appears",
        "results_visible",
        "dialog",
    }
)


def clamp_timeout_ms(timeout_ms: Optional[int] = None) -> int:
    if timeout_ms is None:
        return DEFAULT_WAIT_TIMEOUT_MS
    try:
        val = int(timeout_ms)
    except (TypeError, ValueError):
        return DEFAULT_WAIT_TIMEOUT_MS
    return max(0, min(val, MAX_WAIT_TIMEOUT_MS))


@dataclass
class WaitCondition:
    kind: str
    target_hint: Optional[str] = None
    timeout_ms: int = DEFAULT_WAIT_TIMEOUT_MS
    baseline_url_signature: Optional[str] = None
    baseline_page_signature: Optional[str] = None

    def __post_init__(self) -> None:
        self.kind = (self.kind or "page_changed").lower().strip()
        self.timeout_ms = clamp_timeout_ms(self.timeout_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "target_hint": self.target_hint,
            "timeout_ms": self.timeout_ms,
            "baseline_url_signature": self.baseline_url_signature,
            "baseline_page_signature": self.baseline_page_signature,
        }


@dataclass
class WaitEvaluation:
    satisfied: bool
    reason: str
    timed_out: bool = False
    elapsed_ms: float = 0.0
    remaining_ms: float = 0.0
    evaluation_ms: float = 0.0
    kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "reason": self.reason,
            "timed_out": self.timed_out,
            "elapsed_ms": self.elapsed_ms,
            "remaining_ms": self.remaining_ms,
            "evaluation_ms": self.evaluation_ms,
            "kind": self.kind,
        }


def _blob(el: Mapping[str, Any]) -> str:
    return " ".join(
        str(el.get(k) or "")
        for k in ("label", "text", "role", "type", "ariaLabel", "name", "placeholder", "id")
    ).lower()


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


def _results_present(
    elements: Sequence[Mapping[str, Any]],
    perception: Optional[Mapping[str, Any]] = None,
) -> bool:
    text = ""
    if isinstance(perception, Mapping):
        text = str(perception.get("sanitized_text") or "").lower()
    if "result" in text:
        return True
    for el in elements:
        blob = _blob(el)
        if "result" in blob or "search-result" in blob:
            return True
    return False


def _collect_elements(
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
    return out


class WaitConditionEvaluator:
    """Evaluate bounded wait predicates without inspecting sensitive values."""

    def evaluate(
        self,
        condition: WaitCondition,
        *,
        page_state: Optional[PageState] = None,
        previous_state: Optional[PageState] = None,
        perception: Optional[Mapping[str, Any]] = None,
        elapsed_ms: float = 0.0,
    ) -> WaitEvaluation:
        t0 = time.perf_counter()
        timeout = clamp_timeout_ms(condition.timeout_ms)
        elapsed = max(0.0, float(elapsed_ms or 0))
        remaining = max(0.0, timeout - elapsed)
        kind = (condition.kind or "page_changed").lower()
        elements = _collect_elements(page_state, perception)

        satisfied = False
        reason = ""

        baseline_url = condition.baseline_url_signature
        baseline_sig = condition.baseline_page_signature
        if previous_state is not None:
            baseline_url = baseline_url or previous_state.url_signature
            baseline_sig = baseline_sig or previous_state.page_signature

        curr_url = page_state.url_signature if page_state else None
        curr_sig = page_state.page_signature if page_state else None
        dialog = bool(page_state.dialog_present) if page_state else False

        if kind in ("element_present", "target_appears"):
            hint = condition.target_hint or "result"
            satisfied = _target_present(elements, hint) or (
                "result" in hint.lower() and _results_present(elements, perception)
            )
            reason = "Element present" if satisfied else "Element not yet visible"

        elif kind == "element_disappeared":
            hint = condition.target_hint or ""
            present = _target_present(elements, hint) if hint else False
            satisfied = not present
            reason = "Element disappeared" if satisfied else "Element still present"

        elif kind in ("url_changed", "navigation_complete", "navigation"):
            if baseline_url and curr_url and baseline_url != curr_url:
                satisfied = True
                reason = "URL signature changed"
            elif baseline_sig and curr_sig and baseline_sig != curr_sig:
                satisfied = True
                reason = "Page signature changed (navigation)"
            else:
                reason = "URL unchanged"

        elif kind in ("page_signature_changed", "page_changed"):
            if baseline_sig and curr_sig and baseline_sig != curr_sig:
                satisfied = True
                reason = "Page signature changed"
            else:
                reason = "Page signature unchanged"

        elif kind == "dialog_present" or kind == "dialog":
            satisfied = dialog
            reason = "Dialog present" if satisfied else "Dialog not present"

        elif kind == "dialog_disappeared":
            satisfied = not dialog
            reason = "Dialog disappeared" if satisfied else "Dialog still present"

        elif kind in ("results_present", "results_visible", "page_changed_or_results_visible"):
            if _results_present(elements, perception):
                satisfied = True
                reason = "Results visible"
            elif baseline_sig and curr_sig and baseline_sig != curr_sig:
                satisfied = True
                reason = "Page signature changed"
            else:
                reason = "Results not yet visible"

        else:
            # Unknown — treat as page change or results
            if _results_present(elements, perception):
                satisfied = True
                reason = "Generic wait satisfied via results"
            elif baseline_sig and curr_sig and baseline_sig != curr_sig:
                satisfied = True
                reason = "Page signature changed"
            else:
                reason = f"Unknown wait condition: {kind}"

        timed_out = (not satisfied) and elapsed >= timeout
        return WaitEvaluation(
            satisfied=satisfied,
            reason=reason if not timed_out else f"Wait timed out after {timeout}ms ({reason})",
            timed_out=timed_out,
            elapsed_ms=elapsed,
            remaining_ms=0.0 if timed_out else remaining,
            evaluation_ms=round((time.perf_counter() - t0) * 1000, 3),
            kind=kind,
        )


def wait_condition_from_step(
    *,
    condition: Optional[str],
    target_hint: Optional[str] = None,
    timeout_ms: Optional[int] = None,
    baseline_url_signature: Optional[str] = None,
    baseline_page_signature: Optional[str] = None,
) -> WaitCondition:
    kind = (condition or "page_changed").lower().strip()
    return WaitCondition(
        kind=kind,
        target_hint=target_hint,
        timeout_ms=clamp_timeout_ms(timeout_ms),
        baseline_url_signature=baseline_url_signature,
        baseline_page_signature=baseline_page_signature,
    )
