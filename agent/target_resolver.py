"""Target resolution for hybrid DOM + Visual UI Map action targeting.

Converts a user task into a single safe Target using deterministic ranking:

    1. Exact DOM selector
    2. DOM semantic / text match
    3. Fused DOM + Vision element
    4. Visual UI Map interactive element
    5. Safe no_action

Never guesses random coordinates. Low confidence → no_action.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from vision.action_coordinate_mapping import (
    CoordinateMappingError,
    map_action_coordinates,
    screenshot_box_center_to_css,
)

# ---------------------------------------------------------------------------
# Configurable safety thresholds
# ---------------------------------------------------------------------------
MIN_VISUAL_CONFIDENCE = 0.75
MIN_TARGET_WIDTH = 10
MIN_TARGET_HEIGHT = 10
MIN_SAFE_SCORE = 0.55

# Base semantic match scores (deterministic)
SCORE_EXACT_SELECTOR = 1.00
SCORE_DOM_SEMANTIC = 0.90
SCORE_FUSED = 0.90  # mid of 0.85–0.95; refined by confidence
SCORE_VISION_INTERACTIVE = 0.78  # mid of 0.70–0.85
SCORE_UNKNOWN_VISUAL = 0.50

# Destructive task phrases → requires_confirmation (never auto-execute)
DESTRUCTIVE_PATTERNS = (
    r"\bdelete\b",
    r"\bremove\b",
    r"\bdestroy\b",
    r"\bpurchase\b",
    r"\bcheckout\b",
    r"\bpayment\b",
    r"\bpay\b",
    r"\btransfer\s+money\b",
    r"\bsubmit\s+payment\b",
    r"\bdelete\s+account\b",
    r"\bremove\s+account\b",
)

SENSITIVE_TYPES = frozenset(
    {"password", "credit_card", "card", "ssn", "cvv", "payment"}
)


@dataclass
class Target:
    """Structured action target — never mixes selector and coordinate ambiguously."""

    id: str
    type: str
    strategy: str  # "selector" | "coordinates"
    selector: Optional[str] = None
    coordinates: Optional[Dict[str, int]] = None
    confidence: float = 0.0
    source: str = "dom"  # "dom" | "vision" | "dom+vision"
    score: float = 0.0
    box: Optional[Dict[str, int]] = None
    sensitive: bool = False
    interactive: bool = True
    text: Optional[str] = None
    reason: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "strategy": self.strategy,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "score": round(float(self.score), 4),
        }
        if self.strategy == "selector":
            out["selector"] = self.selector
            out["coordinates"] = None
        else:
            out["selector"] = None
            out["coordinates"] = dict(self.coordinates or {})
        if self.box is not None:
            out["box"] = dict(self.box)
        if self.sensitive:
            out["sensitive"] = True
        if self.text is not None:
            # Never expose raw sensitive values
            out["text"] = "[REDACTED]" if self.sensitive else self.text
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class ResolveResult:
    """Outcome of target resolution."""

    status: str  # "ready" | "no_action" | "requires_confirmation"
    target: Optional[Target] = None
    reason: str = ""
    candidates_considered: int = 0
    performance: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target.to_dict() if self.target else None,
            "reason": self.reason,
            "candidates_considered": self.candidates_considered,
            "performance": dict(self.performance),
        }


class TargetResolver:
    """Generate, rank, and validate action targets from observation + task."""

    def __init__(
        self,
        *,
        min_visual_confidence: float = MIN_VISUAL_CONFIDENCE,
        min_target_width: int = MIN_TARGET_WIDTH,
        min_target_height: int = MIN_TARGET_HEIGHT,
        min_safe_score: float = MIN_SAFE_SCORE,
    ):
        self.min_visual_confidence = float(min_visual_confidence)
        self.min_target_width = int(min_target_width)
        self.min_target_height = int(min_target_height)
        self.min_safe_score = float(min_safe_score)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resolve(
        self,
        observation: Dict[str, Any],
        task: str,
        *,
        intent: str = "click",
        prefer_tags: Sequence[str] = (),
        viewport: Optional[Mapping] = None,
    ) -> ResolveResult:
        """Resolve the best safe target for ``task``.

        Args:
            observation: Perception observation (ui_elements + visual_ui_map).
            task: User task / hint string.
            intent: "click" or "type".
            prefer_tags: Preferred HTML tags for DOM matching.
            viewport: Optional CSS viewport {width, height}. When absent,
                dimensions from the visual map / page context are used.
        """
        t0 = time.perf_counter()
        performance: Dict[str, float] = {}

        task_clean = (task or "").strip()
        if not task_clean:
            return ResolveResult(
                status="no_action",
                reason="Empty task",
                performance={"target_resolution_ms": 0.0},
            )

        # Destructive tasks still resolve a target for confirmation storage,
        # but never return status "ready" (Milestone 4A).
        destructive_task = self.is_destructive_task(task_clean)

        hint = self.extract_hint(task_clean, intent=intent)
        if not hint:
            performance["target_resolution_ms"] = _ms(t0)
            if destructive_task:
                return ResolveResult(
                    status="requires_confirmation",
                    reason="Destructive action requires confirmation",
                    performance=performance,
                )
            return ResolveResult(
                status="no_action",
                reason="No target hint extracted from task",
                performance=performance,
            )

        t_gen = time.perf_counter()
        candidates = self.generate_candidates(
            observation, hint, intent=intent, prefer_tags=prefer_tags
        )
        performance["candidate_generation_ms"] = _ms(t_gen)

        t_rank = time.perf_counter()
        ranked = self.rank_candidates(candidates, hint)
        performance["candidate_ranking_ms"] = _ms(t_rank)

        if not ranked:
            performance["target_resolution_ms"] = _ms(t0)
            if destructive_task:
                return ResolveResult(
                    status="requires_confirmation",
                    reason="Destructive action requires confirmation",
                    candidates_considered=len(candidates),
                    performance=performance,
                )
            return ResolveResult(
                status="no_action",
                reason="No safe target reached the minimum confidence threshold",
                candidates_considered=len(candidates),
                performance=performance,
            )

        best = ranked[0]
        if best.score < self.min_safe_score:
            performance["target_resolution_ms"] = _ms(t0)
            if destructive_task:
                return ResolveResult(
                    status="requires_confirmation",
                    reason="Destructive action requires confirmation",
                    candidates_considered=len(candidates),
                    performance=performance,
                )
            return ResolveResult(
                status="no_action",
                reason="No safe target reached the minimum confidence threshold",
                candidates_considered=len(candidates),
                performance=performance,
            )

        # Safety + coordinate mapping for visual targets
        t_val = time.perf_counter()
        # When the task itself is destructive, temporarily ignore label-gate
        # so we can attach the matched target for confirmation.
        validated = self._finalize_target(
            best,
            observation,
            viewport=viewport,
            performance=performance,
            allow_destructive_label=destructive_task,
        )
        performance["execution_validation_ms"] = _ms(t_val)
        performance["target_resolution_ms"] = _ms(t0)

        if validated is None:
            if destructive_task:
                return ResolveResult(
                    status="requires_confirmation",
                    reason="Destructive action requires confirmation",
                    candidates_considered=len(candidates),
                    performance=performance,
                )
            return ResolveResult(
                status="no_action",
                reason="Target failed safety or coordinate validation",
                candidates_considered=len(candidates),
                performance=performance,
            )

        if destructive_task or validated.get("status") == "requires_confirmation":
            return ResolveResult(
                status="requires_confirmation",
                target=validated.get("target"),
                reason=(
                    "Destructive action requires confirmation"
                    if destructive_task
                    else validated.get("reason", "Requires confirmation")
                ),
                candidates_considered=len(candidates),
                performance=performance,
            )

        target = validated["target"]
        return ResolveResult(
            status="ready",
            target=target,
            reason=target.reason or self._default_reason(target),
            candidates_considered=len(candidates),
            performance=performance,
        )

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------
    def generate_candidates(
        self,
        observation: Dict[str, Any],
        hint: str,
        *,
        intent: str = "click",
        prefer_tags: Sequence[str] = (),
    ) -> List[Target]:
        candidates: List[Target] = []
        hint_norm = _normalize(hint)

        # DOM candidates
        for i, el in enumerate(self._dom_elements(observation)):
            match_kind, match_score = self._dom_match_kind(el, hint_norm)
            if match_score <= 0:
                continue
            if self._is_unsafe_dom(el, intent=intent):
                continue

            selector = el.get("selector")
            if not selector:
                continue

            strategy = "selector"
            source = "dom"
            base = (
                SCORE_EXACT_SELECTOR
                if match_kind == "exact_selector"
                else SCORE_DOM_SEMANTIC
            )
            conf = 1.0 if match_kind == "exact_selector" else min(0.98, match_score / 100.0)

            tag = (el.get("tag") or el.get("type") or "unknown").lower()
            el_type = self._map_dom_type(el)
            candidates.append(
                Target(
                    id=el.get("id") or f"dom_{i}",
                    type=el_type,
                    strategy=strategy,
                    selector=selector,
                    coordinates=None,
                    confidence=conf,
                    source=source,
                    score=base,
                    box=el.get("box"),
                    sensitive=bool(el.get("sensitive")),
                    interactive=True,
                    text=el.get("text") or el.get("ariaLabel"),
                    reason=(
                        "Exact DOM selector match"
                        if match_kind == "exact_selector"
                        else "DOM semantic/text match"
                    ),
                    extra={
                        "match_kind": match_kind,
                        "match_score": match_score,
                        "tag": tag,
                        "prefer_bonus": 1.0 if prefer_tags and tag in prefer_tags else 0.0,
                    },
                )
            )

        # Visual / fused candidates from Visual UI Map
        ui_map = observation.get("visual_ui_map") or {}
        for el in ui_map.get("elements") or []:
            if not isinstance(el, dict):
                continue
            source = (el.get("source") or "vision").lower()
            interactive = bool(el.get("interactive"))
            confidence = float(el.get("confidence") or 0.0)
            sensitive = bool(el.get("sensitive"))

            match_kind, match_score = self._visual_match_kind(el, hint_norm)
            if match_score <= 0:
                continue

            # Vision-only: require interactive + confidence threshold
            if source == "vision":
                if not interactive:
                    continue
                if confidence < self.min_visual_confidence:
                    continue
                base = SCORE_VISION_INTERACTIVE
                strategy = "coordinates"
                selector = None
            elif source == "dom+vision":
                base = SCORE_FUSED
                # Prefer selector when available; else coordinates
                if el.get("selector"):
                    strategy = "selector"
                    selector = el.get("selector")
                else:
                    if confidence < self.min_visual_confidence:
                        continue
                    if not interactive:
                        continue
                    strategy = "coordinates"
                    selector = None
            else:
                # DOM-sourced map entry — usually already covered; allow if unmatched
                if el.get("selector"):
                    strategy = "selector"
                    selector = el.get("selector")
                    base = SCORE_DOM_SEMANTIC
                else:
                    continue

            if sensitive:
                continue

            el_type = (el.get("type") or "unknown").lower()
            if el_type in ("unknown",) and source == "vision":
                base = min(base, SCORE_UNKNOWN_VISUAL)

            candidates.append(
                Target(
                    id=str(el.get("id") or "ui_unknown"),
                    type=el_type,
                    strategy=strategy,
                    selector=selector,
                    coordinates=None,  # filled at finalize
                    confidence=confidence if source != "dom" else max(confidence, 0.9),
                    source=source,
                    score=base,
                    box=el.get("box"),
                    sensitive=sensitive,
                    interactive=interactive,
                    text=el.get("text"),
                    reason=(
                        "DOM + Vision fused target"
                        if source == "dom+vision"
                        else "High-confidence visual target"
                    ),
                    extra={
                        "match_kind": match_kind,
                        "match_score": match_score,
                        "role": el.get("role"),
                    },
                )
            )

        return candidates

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------
    def rank_candidates(
        self, candidates: Sequence[Target], hint: str
    ) -> List[Target]:
        """Deterministic scoring. Higher is better."""
        hint_norm = _normalize(hint)
        scored: List[Target] = []

        # Detect ambiguous duplicates (same normalized label, different ids)
        label_counts: Dict[str, int] = {}
        for c in candidates:
            label = _normalize(c.text or c.selector or c.id)
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1

        for c in candidates:
            semantic = float(c.score)
            confidence = max(0.0, min(1.0, float(c.confidence)))
            visibility = self._visibility_factor(c)
            safety = self._safety_factor(c)

            # Prefer-tag / submit bonus from DOM extra
            prefer_bonus = 1.0 + 0.02 * float((c.extra or {}).get("prefer_bonus") or 0)

            # Ambiguity penalty
            label = _normalize(c.text or c.selector or c.id)
            ambiguous = label_counts.get(label, 1) > 1
            ambiguity = 0.85 if ambiguous else 1.0

            # Tiny element penalty already in visibility; boost exact selector
            match_kind = (c.extra or {}).get("match_kind")
            if match_kind == "exact_selector":
                semantic = SCORE_EXACT_SELECTOR

            # Blend match_score (0–100) into confidence for DOM semantic
            match_score = float((c.extra or {}).get("match_score") or 0)
            if c.source == "dom" and match_kind != "exact_selector":
                confidence = max(confidence, min(0.98, match_score / 100.0))

            final = (
                semantic
                * confidence
                * visibility
                * safety
                * prefer_bonus
                * ambiguity
            )
            c.score = round(final, 6)
            if safety <= 0:
                continue
            if c.strategy == "coordinates" and confidence < self.min_visual_confidence:
                continue
            if final < self.min_safe_score and match_kind != "exact_selector":
                # Still keep exact selector even if factors pull score down slightly
                if final < self.min_safe_score * 0.5:
                    continue
            scored.append(c)

        # Stable sort: score desc, then id asc for determinism
        scored.sort(key=lambda t: (-t.score, t.id))
        return scored

    # ------------------------------------------------------------------
    # Safety / finalize
    # ------------------------------------------------------------------
    def _finalize_target(
        self,
        target: Target,
        observation: Dict[str, Any],
        *,
        viewport: Optional[Mapping] = None,
        performance: Optional[Dict[str, float]] = None,
        allow_destructive_label: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if target.sensitive:
            return None

        # Destructive labels on the element itself
        label = _normalize(target.text or "")
        if any(re.search(p, label) for p in DESTRUCTIVE_PATTERNS):
            if allow_destructive_label:
                # Attach target for confirmation; caller forces requires_confirmation
                if target.strategy == "selector" and target.selector:
                    return {"status": "requires_confirmation", "target": target}
                # Fall through to coordinate validation for visual targets
            else:
                return {
                    "status": "requires_confirmation",
                    "target": target,
                    "reason": "Destructive control requires confirmation",
                }

        if target.strategy == "selector":
            if not target.selector:
                return None
            # Password / sensitive type checks for typing handled upstream;
            # still block coordinate-like misuse
            return {"status": "ready", "target": target}

        # Coordinate strategy
        box = target.box or {}
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        if width < self.min_target_width or height < self.min_target_height:
            return None

        if float(target.confidence) < self.min_visual_confidence:
            return None

        if not target.interactive and target.source == "vision":
            return None

        ui_map = observation.get("visual_ui_map") or {}
        dims = ui_map.get("dimensions") or {}
        screenshot = {
            "width": dims.get("width")
            or (observation.get("page_context") or {}).get("screenshot_width"),
            "height": dims.get("height")
            or (observation.get("page_context") or {}).get("screenshot_height"),
        }
        vp = dict(viewport or {})
        if not vp:
            page_ctx = observation.get("page_context") or {}
            page = observation.get("page") or {}
            vp = {
                "width": page_ctx.get("viewport_width")
                or page.get("viewportWidth")
                or dims.get("width"),
                "height": page_ctx.get("viewport_height")
                or page.get("viewportHeight")
                or dims.get("height"),
            }

        # If viewport unknown, assume 1:1 with screenshot dimensions
        if not vp.get("width") or not vp.get("height"):
            if screenshot.get("width") and screenshot.get("height"):
                vp = {
                    "width": screenshot["width"],
                    "height": screenshot["height"],
                }
            else:
                return None

        if not screenshot.get("width") or not screenshot.get("height"):
            screenshot = {"width": vp["width"], "height": vp["height"]}

        t_map = time.perf_counter()
        try:
            css = screenshot_box_center_to_css(box, screenshot, vp, clamp=True)
            # Also verify via full mapper
            map_action_coordinates(
                float(box.get("x", 0)) + width / 2.0,
                float(box.get("y", 0)) + height / 2.0,
                screenshot,
                vp,
            )
        except (CoordinateMappingError, TypeError, ValueError):
            return None
        finally:
            if performance is not None:
                performance["coordinate_mapping_ms"] = _ms(t_map)

        # Off-screen / invalid after clamp check: center must be inside viewport
        vw = float(vp["width"])
        vh = float(vp["height"])
        if css["x"] < 0 or css["y"] < 0 or css["x"] >= vw or css["y"] >= vh:
            return None

        target.coordinates = {"x": css["x"], "y": css["y"]}
        target.strategy = "coordinates"
        target.selector = None
        return {"status": "ready", "target": target}

    def _visibility_factor(self, target: Target) -> float:
        box = target.box
        if not box:
            # Selector-only DOM without box: assume visible
            return 1.0 if target.strategy == "selector" else 0.5
        w = float(box.get("width") or 0)
        h = float(box.get("height") or 0)
        if w < self.min_target_width or h < self.min_target_height:
            return 0.3
        if w < 20 or h < 20:
            return 0.7
        # Off-screen heuristic: negative origin deeply off-screen
        x = float(box.get("x") or 0)
        y = float(box.get("y") or 0)
        if x + w <= 0 or y + h <= 0:
            return 0.0
        return 1.0

    def _safety_factor(self, target: Target) -> float:
        if target.sensitive:
            return 0.0
        role = _normalize(str((target.extra or {}).get("role") or ""))
        el_type = _normalize(target.type)
        if role == "password" or el_type == "password":
            return 0.0
        if el_type in SENSITIVE_TYPES:
            return 0.0
        text = _normalize(target.text or "")
        if "password" in text or "credit card" in text:
            return 0.0
        return 1.0

    def _is_unsafe_dom(self, el: Dict[str, Any], *, intent: str) -> bool:
        el_type = (el.get("type") or "").lower()
        if el_type == "password":
            return True
        if el.get("sensitive") and intent == "type" and el_type == "password":
            return True
        if el_type in SENSITIVE_TYPES:
            return True
        # Never auto-target credit-card-like inputs
        name = _normalize(str(el.get("name") or ""))
        el_id = _normalize(str(el.get("id") or ""))
        placeholder = _normalize(str(el.get("placeholder") or ""))
        combined = f"{name} {el_id} {placeholder}"
        if any(k in combined for k in ("password", "credit", "card-number", "cvv", "ssn")):
            return True
        return False

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------
    def _dom_match_kind(
        self, el: Dict[str, Any], hint_norm: str
    ) -> Tuple[str, int]:
        if not hint_norm:
            return ("none", 0)

        selector = _normalize(str(el.get("selector") or ""))
        el_id = _normalize(str(el.get("id") or ""))
        # Exact selector: hint is #id or full selector
        if hint_norm.startswith("#") and (
            selector == hint_norm or f"#{el_id}" == hint_norm or el_id == hint_norm.lstrip("#")
        ):
            return ("exact_selector", 100)
        if selector and selector == hint_norm:
            return ("exact_selector", 100)
        if el_id and (hint_norm == el_id or hint_norm == f"#{el_id}"):
            return ("exact_selector", 100)

        score = self._score_fields(
            hint_norm,
            [
                el.get("text"),
                el.get("ariaLabel"),
                el.get("aria-label"),
                el.get("id"),
                el.get("name"),
                el.get("placeholder"),
                el.get("selector"),
                el.get("value"),
                el.get("role"),
            ],
        )
        if score <= 0:
            return ("none", 0)
        return ("semantic", score)

    def _visual_match_kind(
        self, el: Dict[str, Any], hint_norm: str
    ) -> Tuple[str, int]:
        if not hint_norm:
            return ("none", 0)
        selector = _normalize(str(el.get("selector") or ""))
        el_id = _normalize(str(el.get("dom_id") or el.get("id") or ""))
        if hint_norm.startswith("#") and (
            selector == hint_norm or el_id == hint_norm.lstrip("#")
        ):
            return ("exact_selector", 100)
        score = self._score_fields(
            hint_norm,
            [
                el.get("text"),
                el.get("role"),
                el.get("selector"),
                el.get("dom_id"),
                el.get("id"),
                el.get("type"),
            ],
        )
        if score <= 0:
            return ("none", 0)
        return ("semantic", score)

    @staticmethod
    def _score_fields(hint_norm: str, fields: Sequence[Any]) -> int:
        best = 0
        for raw in fields:
            if not raw:
                continue
            val = _normalize(str(raw))
            if not val:
                continue
            if val == hint_norm:
                best = max(best, 100)
            elif hint_norm in val:
                best = max(best, 80)
            elif val in hint_norm:
                best = max(best, 60)
            else:
                hint_tokens = set(hint_norm.split())
                val_tokens = set(val.split())
                overlap = hint_tokens & val_tokens
                if overlap:
                    best = max(best, 40 + 10 * len(overlap))
        return best

    @staticmethod
    def _map_dom_type(el: Dict[str, Any]) -> str:
        tag = (el.get("tag") or "").lower()
        if tag in ("button",):
            return "button"
        if tag in ("a",):
            return "link"
        if tag in ("input", "textarea", "select"):
            return "input"
        if tag in ("dialog",):
            return "dialog"
        return tag or "unknown"

    def _dom_elements(self, observation: Dict[str, Any]) -> List[Dict[str, Any]]:
        elements = observation.get("ui_elements") or []
        if elements:
            return list(elements)
        page = observation.get("page") or {}
        return list(page.get("elements") or [])

    @staticmethod
    def is_destructive_task(task: str) -> bool:
        lower = (task or "").lower()
        return any(re.search(p, lower) for p in DESTRUCTIVE_PATTERNS)

    @staticmethod
    def extract_hint(task: str, *, intent: str = "click") -> str:
        """Extract a target hint from a natural-language task."""
        text = (task or "").strip()
        if not text:
            return ""

        if intent == "type":
            patterns = [
                r'(?i)^type\s+["\']?.+?["\']?\s+in(?:to)?\s+(?:the\s+)?(.+)$',
                r'(?i)^enter\s+["\']?.+?["\']?\s+in(?:to)?\s+(?:the\s+)?(.+)$',
                r'(?i)^fill\s+(?:the\s+)?(.+?)\s+with\s+["\']?.+["\']?$',
            ]
            for pattern in patterns:
                m = re.match(pattern, text)
                if m:
                    return m.group(1).strip().rstrip(".")
            return ""

        patterns = [
            r'(?i)^(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+)$',
            r'(?i)^(?:click|press|tap)$',
        ]
        for pattern in patterns:
            m = re.match(pattern, text)
            if m and m.lastindex:
                hint = m.group(1).strip().rstrip(".")
                hint = re.sub(
                    r"(?i)\s+(button|link|input|field|box)$", "", hint
                ).strip()
                return hint

        m = re.search(
            r'(?i)(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+button|\s+link)?$',
            text,
        )
        if m:
            hint = m.group(1).strip().rstrip(".")
            hint = re.sub(
                r"(?i)\s+(button|link|input|field|box)$", "", hint
            ).strip()
            return hint

        lower = text.lower()
        if "click" in lower or "press" in lower or "tap" in lower:
            hint = re.sub(
                r"(?i)^(find and\s+)?(click|press|tap)\s+(the\s+)?", "", text
            )
            hint = re.sub(r"(?i)\s+button$", "", hint).strip()
            return hint

        # Bare selector / id as task
        if text.startswith("#") or text.startswith("."):
            return text
        return text

    @staticmethod
    def _default_reason(target: Target) -> str:
        if target.strategy == "selector" and target.source == "dom":
            if (target.extra or {}).get("match_kind") == "exact_selector":
                return "Exact DOM target match"
            return "DOM semantic/text match"
        if target.source == "dom+vision":
            if target.strategy == "coordinates":
                return (
                    "DOM selector unavailable; high-confidence fused visual target found"
                )
            return "DOM + Vision fused target"
        if target.strategy == "coordinates":
            return "High-confidence visual target"
        return "Target resolved"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)
