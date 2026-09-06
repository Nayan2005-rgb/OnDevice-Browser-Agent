"""Deterministic page-change classification for Milestone 4C."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agent.page_state import PageState


class PageChangeLevel(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    STRUCTURAL = "structural"
    NAVIGATION = "navigation"


# Centralized weights and thresholds
WEIGHTS = {
    "url_change": 0.40,
    "dialog_change": 0.25,
    "interactive_structure": 0.20,
    "visual_ui": 0.10,
    "minor_layout": 0.05,
}

THRESHOLDS = {
    "none_max": 0.10,
    "minor_max": 0.30,
    "moderate_max": 0.60,
    "structural_max": 0.85,
}


@dataclass
class PageChangeResult:
    changed: bool
    level: str
    score: float
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    detection_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changed": self.changed,
            "level": self.level,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "details": dict(self.details),
            "detection_ms": self.detection_ms,
        }


def _level_from_score(score: float, *, navigation: bool) -> str:
    if navigation:
        return PageChangeLevel.NAVIGATION.value
    if score <= THRESHOLDS["none_max"]:
        return PageChangeLevel.NONE.value
    if score <= THRESHOLDS["minor_max"]:
        return PageChangeLevel.MINOR.value
    if score <= THRESHOLDS["moderate_max"]:
        return PageChangeLevel.MODERATE.value
    return PageChangeLevel.STRUCTURAL.value


def _role_dist_delta(a: Dict[str, int], b: Dict[str, int]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    total = 0
    diff = 0
    for k in keys:
        av = int(a.get(k) or 0)
        bv = int(b.get(k) or 0)
        total += max(av, bv)
        diff += abs(av - bv)
    if total == 0:
        return 0.0
    return min(1.0, diff / total)


def _visual_delta(prev: Dict[str, Any], curr: Dict[str, Any]) -> float:
    keys = (
        "total_elements",
        "buttons",
        "inputs",
        "links",
        "dialogs",
        "vision_only_elements",
        "dom_only_elements",
        "fused_elements",
        "layout_group_count",
    )
    scores: List[float] = []
    for k in keys:
        a = prev.get(k)
        b = curr.get(k)
        if a is None and b is None:
            continue
        ai = float(a or 0)
        bi = float(b or 0)
        denom = max(ai, bi, 1.0)
        scores.append(abs(ai - bi) / denom)
    if not scores:
        return 0.0
    return min(1.0, sum(scores) / len(scores))


class PageChangeDetector:
    """Compare two privacy-safe PageState snapshots."""

    def compare(
        self,
        previous_state: Optional[PageState],
        current_state: Optional[PageState],
        *,
        target_missing: bool = False,
    ) -> PageChangeResult:
        t0 = time.perf_counter()
        if previous_state is None or current_state is None:
            return PageChangeResult(
                changed=False,
                level=PageChangeLevel.NONE.value,
                score=0.0,
                reasons=["insufficient_state"],
                detection_ms=round((time.perf_counter() - t0) * 1000, 3),
            )

        reasons: List[str] = []
        details: Dict[str, Any] = {}
        score = 0.0
        navigation = False

        # URL / navigation
        prev_url = previous_state.url_signature or ""
        curr_url = current_state.url_signature or ""
        if prev_url and curr_url and prev_url != curr_url:
            score += WEIGHTS["url_change"]
            navigation = True
            reasons.append("url_changed")
            details["url_changed"] = True
        else:
            details["url_changed"] = False

        # Dialog appearance / disappearance
        dialog_delta = 0.0
        if previous_state.dialog_present != current_state.dialog_present:
            dialog_delta = 1.0
            if current_state.dialog_present and not previous_state.dialog_present:
                reasons.append("dialog_appeared")
            else:
                reasons.append("dialog_disappeared")
        elif previous_state.dialog_count != current_state.dialog_count:
            dialog_delta = min(
                1.0,
                abs(previous_state.dialog_count - current_state.dialog_count)
                / max(previous_state.dialog_count, current_state.dialog_count, 1),
            )
            reasons.append("dialog_count_changed")
        score += WEIGHTS["dialog_change"] * dialog_delta
        details["dialog_delta"] = round(dialog_delta, 4)

        # Interactive structure
        count_prev = previous_state.element_count
        count_curr = current_state.element_count
        count_delta = 0.0
        if count_prev or count_curr:
            count_delta = abs(count_prev - count_curr) / max(count_prev, count_curr, 1)
        role_delta = _role_dist_delta(
            previous_state.role_distribution, current_state.role_distribution
        )
        sig_changed = (
            previous_state.page_signature
            and current_state.page_signature
            and previous_state.page_signature != current_state.page_signature
        )
        structure = max(count_delta, role_delta)
        if sig_changed and structure < 0.35:
            structure = max(structure, 0.35)
        if count_delta >= 0.15:
            reasons.append("interactive_element_count_changed")
        if role_delta >= 0.2:
            reasons.append("role_distribution_changed")
        if sig_changed:
            reasons.append("page_signature_changed")
        score += WEIGHTS["interactive_structure"] * min(1.0, structure)
        details["count_delta"] = round(count_delta, 4)
        details["role_delta"] = round(role_delta, 4)
        details["signature_changed"] = bool(sig_changed)

        # Visual UI summary
        vis = _visual_delta(previous_state.visual_summary, current_state.visual_summary)
        if vis >= 0.15:
            reasons.append("visual_structure_changed")
        score += WEIGHTS["visual_ui"] * vis
        details["visual_delta"] = round(vis, 4)
        details["visual_structure_changed"] = vis >= 0.15
        details["visual_before"] = {
            k: previous_state.visual_summary.get(k)
            for k in (
                "total_elements",
                "dialogs",
                "dom_only_elements",
                "vision_only_elements",
                "fused_elements",
            )
            if k in previous_state.visual_summary
        }
        details["visual_after"] = {
            k: current_state.visual_summary.get(k)
            for k in (
                "total_elements",
                "dialogs",
                "dom_only_elements",
                "vision_only_elements",
                "fused_elements",
            )
            if k in current_state.visual_summary
        }

        # Minor layout
        layout_delta = 0.0
        if previous_state.layout_group_count or current_state.layout_group_count:
            layout_delta = abs(
                previous_state.layout_group_count - current_state.layout_group_count
            ) / max(
                previous_state.layout_group_count,
                current_state.layout_group_count,
                1,
            )
        if layout_delta > 0:
            reasons.append("layout_group_changed")
        score += WEIGHTS["minor_layout"] * min(1.0, layout_delta)
        details["layout_delta"] = round(layout_delta, 4)

        if target_missing:
            score = max(score, 0.55)
            reasons.append("target_missing")
            details["target_missing"] = True

        # Signature + structural deltas should at least register as MINOR
        if sig_changed and (count_delta > 0 or role_delta > 0 or layout_delta > 0):
            score = max(score, THRESHOLDS["none_max"] + 0.02)

        score = min(1.0, round(score, 4))
        # Deduplicate reasons while preserving order
        seen = set()
        uniq_reasons: List[str] = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                uniq_reasons.append(r)

        level = _level_from_score(score, navigation=navigation)
        changed = level != PageChangeLevel.NONE.value

        return PageChangeResult(
            changed=changed,
            level=level,
            score=score,
            reasons=uniq_reasons,
            details=details,
            detection_ms=round((time.perf_counter() - t0) * 1000, 3),
        )
