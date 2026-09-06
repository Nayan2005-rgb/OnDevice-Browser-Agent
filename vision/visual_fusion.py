"""Fuse DOM geometry elements with vision detections into a single UI map."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from vision.visual_element import (
    REDACTED_TEXT,
    VisualElement,
    center_distance,
    iou,
)

IOU_MATCH_THRESHOLD = 0.5


class VisualFusionEngine:
    """Combine DOM + vision elements without duplicating matched pairs.

    Rules:
      High IoU  → one fused element (source=dom+vision)
      DOM only  → retain DOM element
      Vision only → retain visual element
    """

    def __init__(self, *, iou_threshold: float = IOU_MATCH_THRESHOLD):
        self.iou_threshold = float(iou_threshold)

    def fuse(
        self,
        dom_elements: Sequence[VisualElement],
        vision_elements: Sequence[VisualElement],
    ) -> List[VisualElement]:
        dom = list(dom_elements or [])
        vision = list(vision_elements or [])

        matched_vision = set()
        fused: List[VisualElement] = []
        idx = 0

        for d in dom:
            best_j: Optional[int] = None
            best_score = 0.0
            for j, v in enumerate(vision):
                if j in matched_vision:
                    continue
                score = self._match_score(d, v)
                if score > best_score:
                    best_score = score
                    best_j = j

            if best_j is not None and best_score >= self.iou_threshold:
                matched_vision.add(best_j)
                fused.append(self._merge(d, vision[best_j], idx))
            else:
                fused.append(self._renumber(d, idx, prefer_dom=True))
            idx += 1

        for j, v in enumerate(vision):
            if j in matched_vision:
                continue
            fused.append(self._renumber(v, idx, prefer_dom=False))
            idx += 1

        return fused

    def _match_score(self, dom: VisualElement, vis: VisualElement) -> float:
        """IoU primary; slight boost when centers are very close relative to size."""
        overlap = iou(dom.box, vis.box)
        if overlap <= 0:
            return 0.0
        # Soft distance term (does not override low IoU)
        avg_dim = max(
            (dom.box.width + vis.box.width + dom.box.height + vis.box.height) / 4.0,
            1.0,
        )
        dist = center_distance(dom.box, vis.box)
        proximity = max(0.0, 1.0 - dist / (avg_dim * 2.0))
        return 0.85 * overlap + 0.15 * proximity

    def _merge(
        self, dom: VisualElement, vis: VisualElement, idx: int
    ) -> VisualElement:
        # Prefer DOM semantics; prefer tighter / DOM box when similar
        box = dom.box
        # Average boxes slightly toward vision when IoU is high but not exact
        overlap = iou(dom.box, vis.box)
        if overlap < 0.85:
            box = type(dom.box)(
                x=(dom.box.x + vis.box.x) / 2.0,
                y=(dom.box.y + vis.box.y) / 2.0,
                width=(dom.box.width + vis.box.width) / 2.0,
                height=(dom.box.height + vis.box.height) / 2.0,
            )

        el_type = dom.type if dom.type != "unknown" else vis.type
        interactive = dom.interactive or vis.interactive
        text = dom.text if dom.text is not None else vis.text
        sensitive = dom.sensitive or vis.sensitive
        if sensitive:
            text = REDACTED_TEXT

        # Confidence: high when both agree
        conf = min(
            0.98,
            0.55 * float(dom.confidence)
            + 0.35 * float(vis.confidence)
            + 0.15 * overlap,
        )
        if dom.type != "unknown" and vis.type != "unknown" and dom.type == vis.type:
            conf = min(0.98, conf + 0.05)

        return VisualElement(
            id=f"ui_{idx:03d}",
            type=el_type,
            box=box,
            interactive=interactive,
            source="dom+vision",
            confidence=conf,
            text=text,
            dom_id=dom.dom_id,
            selector=dom.selector,
            role=dom.role or vis.role,
            sensitive=sensitive,
        )

    def _renumber(
        self, el: VisualElement, idx: int, *, prefer_dom: bool
    ) -> VisualElement:
        return VisualElement(
            id=f"ui_{idx:03d}",
            type=el.type,
            box=el.box,
            interactive=el.interactive,
            source=el.source,
            confidence=el.confidence,
            text=REDACTED_TEXT if el.sensitive else el.text,
            dom_id=el.dom_id,
            selector=el.selector,
            role=el.role,
            sensitive=el.sensitive,
        )
