"""Build and serialize the Visual UI Map — central multimodal UI representation."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from vision.visual_element import VisualElement


def build_summary(elements: Sequence[VisualElement]) -> Dict[str, int]:
    counts = {
        "total_elements": len(elements),
        "buttons": 0,
        "inputs": 0,
        "links": 0,
        "images": 0,
        "dialogs": 0,
        "containers": 0,
        "text": 0,
        "unknown": 0,
        "vision_only_elements": 0,
        "dom_only_elements": 0,
        "fused_elements": 0,
    }
    type_key = {
        "button": "buttons",
        "input": "inputs",
        "link": "links",
        "image": "images",
        "dialog": "dialogs",
        "container": "containers",
        "text": "text",
        "unknown": "unknown",
    }
    for el in elements:
        key = type_key.get(el.type, "unknown")
        counts[key] = counts.get(key, 0) + 1
        if el.source == "vision":
            counts["vision_only_elements"] += 1
        elif el.source == "dom":
            counts["dom_only_elements"] += 1
        elif el.source == "dom+vision":
            counts["fused_elements"] += 1
    return counts


def build_visual_ui_map(
    *,
    elements: Sequence[VisualElement],
    width: Optional[int] = None,
    height: Optional[int] = None,
    layout: Optional[Mapping[str, Any]] = None,
    performance: Optional[Mapping[str, Any]] = None,
    privacy_safe: bool = True,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the canonical Visual UI Map dict."""
    els = list(elements or [])
    summary = build_summary(els)
    ui_map: Dict[str, Any] = {
        "dimensions": {
            "width": int(width) if width is not None else None,
            "height": int(height) if height is not None else None,
        },
        "elements": [el.to_dict() for el in els],
        "layout": dict(layout or {"groups": []}),
        "summary": summary,
        "privacy_safe": bool(privacy_safe),
    }
    if performance is not None:
        ui_map["performance"] = dict(performance)
    if extra:
        for k, v in extra.items():
            if k not in ui_map:
                ui_map[k] = v
    return ui_map


def empty_visual_ui_map(
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    privacy_safe: bool = True,
) -> Dict[str, Any]:
    return build_visual_ui_map(
        elements=[],
        width=width,
        height=height,
        layout={"groups": []},
        privacy_safe=privacy_safe,
    )
