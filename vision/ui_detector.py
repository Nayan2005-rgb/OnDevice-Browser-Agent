"""Pluggable UI element detectors for the visual perception stack.

Architecture:
  UIElementDetector (ABC)
    ├── DOMGeometryDetector      — DOM snapshot → VisualElements
    ├── ContourBasedUIDetector   — OpenCV contours / heuristics
    └── (future) OnnxUIDetector / WebGPUUIDetector / TransformerUIDetector

Detectors never claim high semantic certainty for heuristic vision output.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from vision.visual_element import (
    REDACTED_TEXT,
    Box,
    VisualElement,
    box_from_mapping,
)

# Interactive tags / roles for DOM extraction
_DOM_INTERACTIVE_TAGS = frozenset(
    {"button", "input", "textarea", "select", "a", "img"}
)
_ROLE_TO_TYPE = {
    "button": "button",
    "link": "link",
    "textbox": "input",
    "searchbox": "input",
    "dialog": "dialog",
    "img": "image",
    "image": "image",
}


class UIElementDetector(ABC):
    """Abstract detector interface — swap implementations without rewriting fusion."""

    @abstractmethod
    def detect(self, image: Any = None, **kwargs) -> List[VisualElement]:
        raise NotImplementedError

    # Backward-compatible alias used by the old stub
    def detect_elements(self, image: Any = None, **kwargs) -> List[Dict[str, Any]]:
        return [el.to_dict() for el in self.detect(image, **kwargs)]


def _map_dom_type(el: Mapping[str, Any]) -> str:
    tag = str(el.get("tag") or "").lower()
    role = str(el.get("role") or el.get("ariaRole") or "").lower()
    input_type = str(el.get("type") or "").lower()

    if role in _ROLE_TO_TYPE:
        return _ROLE_TO_TYPE[role]
    if tag == "button" or input_type in ("submit", "button", "reset"):
        return "button"
    if tag in ("input", "textarea", "select") or el.get("contenteditable"):
        return "input"
    if tag == "a":
        return "link"
    if tag == "img":
        return "image"
    if tag in ("dialog",) or role == "dialog":
        return "dialog"
    if tag in ("div", "section", "form", "article", "main"):
        return "container"
    return "unknown"


def _safe_dom_text(el: Mapping[str, Any]) -> Optional[str]:
    """Return display text that is safe to include (never raw passwords / PII values)."""
    if el.get("sensitive"):
        return REDACTED_TEXT
    input_type = str(el.get("type") or "").lower()
    if input_type == "password":
        return REDACTED_TEXT

    # Prefer visible label text over values
    for key in ("text", "ariaLabel", "placeholder", "name"):
        val = el.get(key)
        if val and str(val).strip():
            text = str(val).strip()[:80]
            # Already-redacted markers from the extension are fine
            if any(
                marker in text.upper()
                for marker in ("PASSWORD", "REDACTED", "EMAIL_REDACTED", "PHONE")
            ):
                if "PASSWORD" in text.upper() or text.startswith("["):
                    return text if text.startswith("[") else REDACTED_TEXT
            # Never forward raw email-like / phone-like values
            if "@" in text and "." in text:
                return REDACTED_TEXT
            return text

    # Never include input values (may contain secrets)
    return None


class DOMGeometryDetector(UIElementDetector):
    """Convert sanitized DOM elements (with bounding boxes) into VisualElements."""

    def detect(self, image: Any = None, **kwargs) -> List[VisualElement]:
        elements = kwargs.get("dom_elements") or kwargs.get("elements") or []
        return self.from_dom_elements(elements)

    def from_dom_elements(
        self, elements: Sequence[Mapping[str, Any]]
    ) -> List[VisualElement]:
        out: List[VisualElement] = []
        for i, el in enumerate(elements or []):
            box_data = el.get("box")
            if not box_data:
                # Also accept flat x/y/width/height
                if all(k in el for k in ("x", "y", "width", "height")):
                    box_data = el
                else:
                    continue
            box = box_from_mapping(box_data)
            if box is None:
                continue

            el_type = _map_dom_type(el)
            interactive = el_type in ("button", "input", "link", "select") or bool(
                el.get("interactive", True)
            )
            # selects are inputs in our schema
            if str(el.get("tag") or "").lower() == "select":
                el_type = "input"
                interactive = True

            sensitive = bool(el.get("sensitive")) or str(el.get("type") or "").lower() == "password"
            text = _safe_dom_text(el)
            if sensitive:
                text = REDACTED_TEXT

            out.append(
                VisualElement(
                    id=f"dom_{i:03d}",
                    type=el_type,
                    box=box,
                    interactive=interactive,
                    source="dom",
                    confidence=1.0,
                    text=text,
                    dom_id=el.get("id") or el.get("dom_id"),
                    selector=el.get("selector"),
                    role=el.get("role") or el.get("type"),
                    sensitive=sensitive,
                )
            )
        return out


class ContourBasedUIDetector(UIElementDetector):
    """Lightweight OpenCV contour detector for likely UI regions.

    Uses grayscale → edges → contours → rectangle / aspect-ratio heuristics.
    Confidence scores are intentionally conservative — this is not semantic CV.
    """

    def __init__(
        self,
        *,
        min_area: int = 400,
        max_area_ratio: float = 0.85,
        min_aspect: float = 0.08,
        max_aspect: float = 12.0,
        canny_low: int = 50,
        canny_high: int = 150,
    ):
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio
        self.min_aspect = min_aspect
        self.max_aspect = max_aspect
        self.canny_low = canny_low
        self.canny_high = canny_high

    def detect(self, image: Any = None, **kwargs) -> List[VisualElement]:
        if image is None:
            return []
        arr = _ensure_bgr(image)
        if arr is None:
            return []

        import cv2

        h, w = arr.shape[:2]
        max_area = float(h * w) * self.max_area_ratio
        # Pipeline images are RGB (see privacy_pipeline.decode_image_input)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, self.canny_low, self.canny_high)
        # Dilate to close gaps in control borders
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)

        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        candidates: List[VisualElement] = []
        for i, cnt in enumerate(contours):
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = float(bw * bh)
            if area < self.min_area or area > max_area:
                continue
            if bw < 8 or bh < 8:
                continue
            aspect = bw / float(bh)
            if aspect < self.min_aspect or aspect > self.max_aspect:
                continue

            # Approximate polygon — prefer near-rectangular regions
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
            rectangular = 4 <= len(approx) <= 8

            el_type, interactive, conf = self._classify_region(
                bw, bh, aspect, rectangular, area, h, w
            )
            candidates.append(
                VisualElement(
                    id=f"vision_{i:03d}",
                    type=el_type,
                    box=Box(x=float(x), y=float(y), width=float(bw), height=float(bh)),
                    interactive=interactive,
                    source="vision",
                    confidence=conf,
                )
            )

        # Nested-region analysis: large containers that enclose smaller ones
        candidates = self._promote_containers(candidates, h, w)
        # Deduplicate heavy overlaps among vision-only detections
        return _nms_elements(candidates, iou_threshold=0.7)

    def _classify_region(
        self,
        bw: int,
        bh: int,
        aspect: float,
        rectangular: bool,
        area: float,
        img_h: int,
        img_w: int,
    ) -> tuple:
        """Heuristic type guess with conservative confidence."""
        area_ratio = area / float(max(img_h * img_w, 1))

        # Dialog / modal: large centered-ish block
        if rectangular and area_ratio > 0.15 and 0.5 <= aspect <= 2.0:
            return "dialog", False, 0.55

        # Large image-like region
        if area_ratio > 0.08 and 0.6 <= aspect <= 1.8 and bh > 80 and bw > 80:
            return "image", False, 0.5

        # Button-like: moderate width, short height
        if rectangular and 1.5 <= aspect <= 6.0 and 24 <= bh <= 80 and 40 <= bw <= 400:
            return "button", True, 0.62

        # Input-like: wide and short
        if rectangular and aspect >= 3.0 and 18 <= bh <= 60 and bw >= 100:
            return "input", True, 0.6

        # Container: large region
        if area_ratio > 0.12:
            return "container", False, 0.5

        # Text-like: wide short strip
        if aspect >= 4.0 and bh <= 40:
            return "text", False, 0.45

        return "unknown", False, 0.4 if rectangular else 0.35

    def _promote_containers(
        self, elements: List[VisualElement], img_h: int, img_w: int
    ) -> List[VisualElement]:
        from vision.visual_element import contains

        for el in elements:
            if el.type not in ("unknown", "container", "dialog"):
                continue
            children = [
                o
                for o in elements
                if o is not el and contains(el.box, o.box) and o.area < el.box.area * 0.9
            ]
            if len(children) >= 2 and el.type == "unknown":
                el.type = "container"
                el.confidence = min(0.58, el.confidence + 0.08)
            # Very large region with many children → dialog candidate
            if (
                len(children) >= 3
                and el.box.area / float(max(img_h * img_w, 1)) > 0.2
            ):
                el.type = "dialog"
                el.confidence = min(0.6, el.confidence + 0.1)
        return elements


def _ensure_bgr(image: Any) -> Optional[np.ndarray]:
    """Accept ndarray / PIL / data-URL; return RGB uint8 (OpenCV ops use RGB2GRAY)."""
    if image is None:
        return None
    if isinstance(image, np.ndarray):
        if image.size == 0:
            return None
        if image.ndim == 2:
            return np.stack([image, image, image], axis=-1).astype(np.uint8)
        if image.ndim == 3 and image.shape[2] == 4:
            return image[:, :, :3].copy()
        return image
    try:
        from PIL import Image

        if isinstance(image, Image.Image):
            return np.array(image.convert("RGB"))
        if isinstance(image, (bytes, bytearray, str)):
            from vision.privacy_pipeline import decode_image_input

            return decode_image_input(image)
    except Exception:
        return None
    return None


def _nms_elements(
    elements: List[VisualElement], *, iou_threshold: float = 0.7
) -> List[VisualElement]:
    """Greedy non-max suppression by confidence."""
    from vision.visual_element import iou

    ordered = sorted(elements, key=lambda e: e.confidence, reverse=True)
    kept: List[VisualElement] = []
    for el in ordered:
        if any(iou(el.box, k.box) >= iou_threshold for k in kept):
            continue
        kept.append(el)
    # Re-id sequentially
    for i, el in enumerate(kept):
        el.id = f"vision_{i:03d}"
    return kept


def create_ui_detector(backend: str = "contour", **kwargs) -> UIElementDetector:
    """Factory for UI detectors. Future backends: onnx, webgpu, transformer."""
    backend = (backend or "contour").lower()
    if backend in ("contour", "opencv", "heuristic"):
        return ContourBasedUIDetector(**kwargs)
    if backend in ("dom", "geometry"):
        return DOMGeometryDetector()
    if backend in ("onnx", "webgpu", "transformer"):
        # Placeholders — return contour until implemented
        return ContourBasedUIDetector(**kwargs)
    raise ValueError(f"Unknown UI detector backend: {backend}")
