"""Helpers for building sensitive-region bounding boxes from structured
element descriptors (mirrors extension/privacyFilter.js rules for tests).

The live browser path uses getBoundingClientRect() in the extension.
This module validates the same category + visibility rules without a DOM.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "pwd",
    "ssn",
    "social security",
    "credit card",
    "card number",
    "cvv",
    "cvc",
    "passport",
    "secret",
)


def field_looks_sensitive(el: Mapping[str, Any]) -> Optional[str]:
    """Classify an element descriptor; return category or None."""
    input_type = str(el.get("type") or "").lower()
    if input_type == "password":
        return "password"

    attrs = " ".join(
        str(el.get(k) or "")
        for k in ("type", "name", "id", "autocomplete", "placeholder", "ariaLabel", "aria-label")
    ).lower()
    # Normalize separators so credit_card / cc-number match card heuristics
    attrs_norm = attrs.replace("_", " ").replace("-", " ")

    for kw in SENSITIVE_KEYWORDS:
        if kw in attrs or kw in attrs_norm:
            if kw in ("password", "passwd", "pwd", "secret") or "password" in kw:
                return "password"
            if "card" in kw or kw in ("cvv", "cvc"):
                return "credit_card"
            if "ssn" in kw or "social" in kw:
                return "ssn"
            return "sensitive"

    if "card" in attrs_norm or "cc number" in attrs_norm or "ccnum" in attrs_norm.replace(" ", ""):
        return "credit_card"

    if input_type == "email" or "email" in attrs:
        return "email"
    if input_type == "tel" or "phone" in attrs or "tel" in attrs:
        return "phone"
    return None


def is_visible_box(box: Mapping[str, float], viewport_w: float = 1e9, viewport_h: float = 1e9) -> bool:
    w = float(box.get("width", 0))
    h = float(box.get("height", 0))
    if w <= 0 or h <= 0:
        return False
    x = float(box.get("x", 0))
    y = float(box.get("y", 0))
    return x < viewport_w and y < viewport_h and x + w > 0 and y + h > 0


def collect_sensitive_boxes(
    elements: List[Mapping[str, Any]],
    viewport: Optional[Mapping[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Build {category,x,y,width,height} for visible sensitive elements.

    Never includes raw field values — only category + geometry.
    Each element may provide rect via `box` or `x/y/width/height`.
    """
    vw = float((viewport or {}).get("width", 1e9))
    vh = float((viewport or {}).get("height", 1e9))
    boxes: List[Dict[str, Any]] = []
    seen = set()

    for el in elements:
        category = field_looks_sensitive(el)
        if not category:
            continue

        if "box" in el and isinstance(el["box"], dict):
            geom = el["box"]
        else:
            geom = {
                "x": el.get("x", 0),
                "y": el.get("y", 0),
                "width": el.get("width", 0),
                "height": el.get("height", 0),
            }

        if not is_visible_box(geom, vw, vh):
            continue

        item = {
            "category": category,
            "x": int(round(float(geom["x"]))),
            "y": int(round(float(geom["y"]))),
            "width": int(round(float(geom["width"]))),
            "height": int(round(float(geom["height"]))),
        }
        key = (item["category"], item["x"], item["y"], item["width"], item["height"])
        if key in seen:
            continue
        seen.add(key)
        boxes.append(item)

    return boxes
