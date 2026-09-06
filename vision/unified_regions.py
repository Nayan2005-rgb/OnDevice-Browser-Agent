"""Merge DOM PII boxes and vision face boxes into unified sensitive regions.

Output regions contain only category, source, geometry, and optional confidence.
Never includes raw PII text, face crops, embeddings, or identity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


def _geom_key(region: Mapping[str, Any]) -> tuple:
    return (
        str(region.get("category") or ""),
        int(region.get("x", 0)),
        int(region.get("y", 0)),
        int(region.get("width", 0)),
        int(region.get("height", 0)),
    )


def _normalize_dom_box(box: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    category = str(box.get("category") or "sensitive")
    try:
        x = int(round(float(box.get("x", 0))))
        y = int(round(float(box.get("y", 0))))
        width = max(1, int(round(float(box.get("width", 0)))))
        height = max(1, int(round(float(box.get("height", 0)))))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "category": category,
        "source": str(box.get("source") or "dom"),
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _normalize_face_box(box: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        x = int(round(float(box.get("x", 0))))
        y = int(round(float(box.get("y", 0))))
        width = max(1, int(round(float(box.get("width", 0)))))
        height = max(1, int(round(float(box.get("height", 0)))))
        confidence = float(box.get("confidence", 1.0))
    except (TypeError, ValueError):
        return None
    return {
        "category": "face",
        "source": "vision",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "confidence": confidence,
    }


def boxes_overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Axis-aligned overlap test (inclusive of touching edges as non-overlap)."""
    ax2 = int(a.get("x", 0)) + int(a.get("width", 0))
    ay2 = int(a.get("y", 0)) + int(a.get("height", 0))
    bx2 = int(b.get("x", 0)) + int(b.get("width", 0))
    by2 = int(b.get("y", 0)) + int(b.get("height", 0))
    return not (
        ax2 <= int(b.get("x", 0))
        or bx2 <= int(a.get("x", 0))
        or ay2 <= int(b.get("y", 0))
        or by2 <= int(a.get("y", 0))
    )


def merge_overlapping_same_category(regions: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Merge overlapping regions that share the same category (union box).

    Different categories that overlap are both kept — redaction applies both.
    """
    merged: List[Dict[str, Any]] = []
    for region in regions:
        item = dict(region)
        consumed = False
        for existing in merged:
            if existing.get("category") != item.get("category"):
                continue
            if not boxes_overlap(existing, item):
                continue
            x1 = min(int(existing["x"]), int(item["x"]))
            y1 = min(int(existing["y"]), int(item["y"]))
            x2 = max(
                int(existing["x"]) + int(existing["width"]),
                int(item["x"]) + int(item["width"]),
            )
            y2 = max(
                int(existing["y"]) + int(existing["height"]),
                int(item["y"]) + int(item["height"]),
            )
            existing["x"] = x1
            existing["y"] = y1
            existing["width"] = max(1, x2 - x1)
            existing["height"] = max(1, y2 - y1)
            if "confidence" in existing or "confidence" in item:
                existing["confidence"] = max(
                    float(existing.get("confidence", 0)),
                    float(item.get("confidence", 0)),
                )
            consumed = True
            break
        if not consumed:
            merged.append(item)
    return merged


def unify_sensitive_regions(
    dom_boxes: Optional[Sequence[Mapping[str, Any]]] = None,
    face_boxes: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    merge_overlaps: bool = True,
) -> Dict[str, Any]:
    """Build unified sensitive regions from DOM PII + vision face boxes."""
    regions: List[Dict[str, Any]] = []
    seen = set()

    for box in dom_boxes or []:
        item = _normalize_dom_box(box)
        if not item:
            continue
        key = _geom_key(item)
        if key in seen:
            continue
        seen.add(key)
        regions.append(item)

    for box in face_boxes or []:
        item = _normalize_face_box(box)
        if not item:
            continue
        key = _geom_key(item)
        if key in seen:
            continue
        seen.add(key)
        regions.append(item)

    if merge_overlaps:
        regions = merge_overlapping_same_category(regions)

    faces = [r for r in regions if r.get("category") == "face"]
    return {
        "regions": regions,
        "counts": {
            "dom": len([r for r in regions if r.get("source") == "dom"]),
            "vision_faces": len(faces),
            "total": len(regions),
        },
    }
