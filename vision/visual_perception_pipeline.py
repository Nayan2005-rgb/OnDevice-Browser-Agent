"""Orchestrate Visual UI Perception: DOM geometry + vision + layout + fusion.

Runs on SANITIZED screenshots only. Output is a privacy-validated Visual UI Map.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from vision.layout_analyzer import LayoutAnalyzer
from vision.ui_detector import ContourBasedUIDetector, DOMGeometryDetector, UIElementDetector
from vision.visual_element import VisualElement, boxes_overlap, box_from_mapping
from vision.visual_fusion import VisualFusionEngine
from vision.visual_metadata_validator import enforce_privacy_on_map, validate_visual_ui_map
from vision.visual_ui_map import build_visual_ui_map, empty_visual_ui_map


def _ms_since(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 2)


def _apply_sensitive_overlap(
    elements: List[VisualElement],
    sensitive_regions: Sequence[Mapping[str, Any]],
) -> List[VisualElement]:
    """Mark elements that overlap known sensitive / face regions as redacted."""
    from vision.visual_element import REDACTED_TEXT, Box

    regions: List[Box] = []
    face_regions: List[Box] = []
    for r in sensitive_regions or []:
        box = box_from_mapping(r.get("box") or r)
        if box is None:
            continue
        cat = str(r.get("category") or "").lower()
        if cat == "face":
            face_regions.append(box)
        regions.append(box)

    out: List[VisualElement] = []
    for el in elements:
        # Face regions must not become normal image elements
        if el.type == "image" and any(boxes_overlap(el.box, fb) for fb in face_regions):
            continue
        if any(boxes_overlap(el.box, sb) for sb in regions):
            el.sensitive = True
            el.text = REDACTED_TEXT
        out.append(el)
    return out


class VisualPerceptionPipeline:
    """End-to-end local visual UI perception."""

    def __init__(
        self,
        *,
        vision_detector: Optional[UIElementDetector] = None,
        dom_detector: Optional[DOMGeometryDetector] = None,
        layout_analyzer: Optional[LayoutAnalyzer] = None,
        fusion_engine: Optional[VisualFusionEngine] = None,
        iou_threshold: float = 0.5,
    ):
        self.vision_detector = vision_detector or ContourBasedUIDetector()
        self.dom_detector = dom_detector or DOMGeometryDetector()
        self.layout_analyzer = layout_analyzer or LayoutAnalyzer()
        self.fusion_engine = fusion_engine or VisualFusionEngine(
            iou_threshold=iou_threshold
        )

    def run(
        self,
        *,
        image: Any = None,
        dom_elements: Optional[Sequence[Mapping[str, Any]]] = None,
        sensitive_regions: Optional[Sequence[Mapping[str, Any]]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a privacy-safe Visual UI Map from sanitized inputs.

        Args:
            image: Sanitized screenshot (ndarray / data URL). Optional if DOM-only.
            dom_elements: Sanitized DOM elements with geometry boxes.
            sensitive_regions: Known PII/face boxes (geometry + category only).
            width/height: Override dimensions when image is absent.
        """
        timing: Dict[str, float] = {}
        t_total = time.perf_counter()

        # Dimensions from image when available
        img_w, img_h = width, height
        arr = None
        if image is not None:
            from vision.ui_detector import _ensure_bgr

            arr = _ensure_bgr(image)
            if arr is not None:
                img_h, img_w = int(arr.shape[0]), int(arr.shape[1])

        # 1) DOM geometry
        t0 = time.perf_counter()
        dom_els = self.dom_detector.from_dom_elements(dom_elements or [])
        timing["dom_geometry_ms"] = _ms_since(t0)

        # 2) Vision detection (sanitized image only)
        t0 = time.perf_counter()
        vision_els: List[VisualElement] = []
        if arr is not None:
            vision_els = self.vision_detector.detect(arr)
        timing["vision_detection_ms"] = _ms_since(t0)

        # 3) Privacy: redact / drop face-overlapping image regions before fusion
        t0 = time.perf_counter()
        if sensitive_regions:
            dom_els = _apply_sensitive_overlap(dom_els, sensitive_regions)
            vision_els = _apply_sensitive_overlap(vision_els, sensitive_regions)
        # Measured below with validation; track privacy filtering portion
        privacy_filter_ms = _ms_since(t0)

        # 4) Fusion
        t0 = time.perf_counter()
        fused = self.fusion_engine.fuse(dom_els, vision_els)
        timing["fusion_ms"] = _ms_since(t0)

        # 5) Layout
        t0 = time.perf_counter()
        layout = self.layout_analyzer.analyze(fused)
        timing["layout_analysis_ms"] = _ms_since(t0)

        # 6) Build map + validate
        t0 = time.perf_counter()
        ui_map = build_visual_ui_map(
            elements=fused,
            width=img_w,
            height=img_h,
            layout=layout,
            privacy_safe=True,
        )
        ui_map = enforce_privacy_on_map(ui_map) or empty_visual_ui_map(
            width=img_w, height=img_h, privacy_safe=False
        )
        is_safe, reasons = validate_visual_ui_map(ui_map)
        ui_map["privacy_safe"] = bool(is_safe)
        if not is_safe:
            ui_map["validation_errors"] = reasons
        timing["privacy_validation_ms"] = round(
            privacy_filter_ms + _ms_since(t0), 2
        )

        timing["total_visual_perception_ms"] = _ms_since(t_total)
        ui_map["performance"] = timing

        # Split views useful for dashboard toggles (safe metadata only)
        ui_map["views"] = {
            "dom_elements": [e.to_dict() for e in dom_els],
            "vision_elements": [e.to_dict() for e in vision_els],
            "fused_elements": [e.to_dict() for e in fused],
        }
        return ui_map


def build_visual_ui_map_from_inputs(
    image: Any = None,
    dom_elements: Optional[Sequence[Mapping[str, Any]]] = None,
    sensitive_regions: Optional[Sequence[Mapping[str, Any]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience entry point used by the API layer."""
    pipeline = VisualPerceptionPipeline(
        iou_threshold=float(kwargs.pop("iou_threshold", 0.5))
    )
    return pipeline.run(
        image=image,
        dom_elements=dom_elements,
        sensitive_regions=sensitive_regions,
        width=kwargs.get("width"),
        height=kwargs.get("height"),
    )
