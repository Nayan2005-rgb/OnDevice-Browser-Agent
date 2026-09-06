"""Standard Visual UI Element schema for the multimodal UI map.

Required: id, type, box, interactive, source, confidence
Optional: text, dom_id, selector, role, sensitive
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Supported semantic types (heuristic detectors may emit "unknown")
ELEMENT_TYPES = frozenset(
    {
        "button",
        "input",
        "text",
        "image",
        "link",
        "dialog",
        "container",
        "unknown",
    }
)

VALID_SOURCES = frozenset({"dom", "vision", "dom+vision"})

REDACTED_TEXT = "[REDACTED]"


@dataclass
class Box:
    """Axis-aligned bounding box in screenshot (or viewport) pixels."""

    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> Dict[str, int]:
        return {
            "x": int(round(self.x)),
            "y": int(round(self.y)),
            "width": max(1, int(round(self.width))),
            "height": max(1, int(round(self.height))),
        }

    @property
    def area(self) -> float:
        return max(0.0, float(self.width)) * max(0.0, float(self.height))

    @property
    def center(self) -> tuple:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def as_xyxy(self) -> tuple:
        return (
            float(self.x),
            float(self.y),
            float(self.x) + float(self.width),
            float(self.y) + float(self.height),
        )

    def is_valid(self) -> bool:
        return (
            self.width > 0
            and self.height > 0
            and self.x == self.x  # not NaN
            and self.y == self.y
        )


def box_from_mapping(data: Mapping[str, Any]) -> Optional[Box]:
    """Parse a box dict; return None if invalid."""
    try:
        box = Box(
            x=float(data.get("x", 0)),
            y=float(data.get("y", 0)),
            width=float(data.get("width", 0)),
            height=float(data.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None
    return box if box.is_valid() else None


def iou(a: Box, b: Box) -> float:
    """Intersection over Union of two boxes."""
    ax1, ay1, ax2, ay2 = a.as_xyxy()
    bx1, by1, bx2, by2 = b.as_xyxy()
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.area + b.area - inter
    if union <= 0:
        return 0.0
    return inter / union


def center_distance(a: Box, b: Box) -> float:
    cx1, cy1 = a.center
    cx2, cy2 = b.center
    return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5


def boxes_overlap(a: Box, b: Box) -> bool:
    return iou(a, b) > 0.0


def contains(outer: Box, inner: Box, *, margin: float = 2.0) -> bool:
    """True if outer fully contains inner (with optional margin)."""
    ox1, oy1, ox2, oy2 = outer.as_xyxy()
    ix1, iy1, ix2, iy2 = inner.as_xyxy()
    return (
        ix1 >= ox1 - margin
        and iy1 >= oy1 - margin
        and ix2 <= ox2 + margin
        and iy2 <= oy2 + margin
    )


@dataclass
class VisualElement:
    """Canonical visual UI element used throughout the perception stack."""

    id: str
    type: str
    box: Box
    interactive: bool
    source: str
    confidence: float
    text: Optional[str] = None
    dom_id: Optional[str] = None
    selector: Optional[str] = None
    role: Optional[str] = None
    sensitive: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in ELEMENT_TYPES:
            self.type = "unknown"
        if self.source not in VALID_SOURCES:
            # Allow unknown sources to degrade gracefully
            if self.source not in ("dom", "vision", "dom+vision"):
                self.source = "vision"
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if self.sensitive and self.text and self.text != REDACTED_TEXT:
            # Never keep raw sensitive text on the element
            if not str(self.text).startswith("["):
                self.text = REDACTED_TEXT

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "box": self.box.to_dict(),
            "interactive": bool(self.interactive),
            "source": self.source,
            "confidence": round(float(self.confidence), 4),
        }
        if self.text is not None:
            out["text"] = self.text
        if self.dom_id is not None:
            out["dom_id"] = self.dom_id
        if self.selector is not None:
            out["selector"] = self.selector
        if self.role is not None:
            out["role"] = self.role
        if self.sensitive:
            out["sensitive"] = True
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, default_id: str = "ui_000") -> Optional["VisualElement"]:
        box = box_from_mapping(data.get("box") or data)
        if box is None:
            return None
        return cls(
            id=str(data.get("id") or default_id),
            type=str(data.get("type") or "unknown"),
            box=box,
            interactive=bool(data.get("interactive", False)),
            source=str(data.get("source") or "vision"),
            confidence=float(data.get("confidence", 0.5)),
            text=data.get("text"),
            dom_id=data.get("dom_id"),
            selector=data.get("selector"),
            role=data.get("role"),
            sensitive=bool(data.get("sensitive", False)),
        )


def normalize_elements(elements: Sequence[Mapping[str, Any] | VisualElement]) -> List[VisualElement]:
    """Convert mixed dict / VisualElement inputs into validated VisualElements."""
    out: List[VisualElement] = []
    for i, item in enumerate(elements or []):
        if isinstance(item, VisualElement):
            if item.box.is_valid():
                out.append(item)
            continue
        el = VisualElement.from_dict(item, default_id=f"ui_{i:03d}")
        if el is not None:
            out.append(el)
    return out
