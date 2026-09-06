"""Map DOM viewport (CSS pixel) boxes onto screenshot pixel coordinates.

Screenshot dimensions often differ from CSS viewport size because of
devicePixelRatio / HiDPI capture. Scale = screenshot_size / viewport_size.
"""

from __future__ import annotations

from typing import Dict, Mapping, MutableMapping, Sequence, Tuple, Union

BoxDict = Dict[str, Union[int, float]]
BoxTuple = Tuple[float, float, float, float]  # x, y, width, height


def compute_scale(
    viewport_width: float,
    viewport_height: float,
    screenshot_width: float,
    screenshot_height: float,
) -> Tuple[float, float]:
    """Return (scale_x, scale_y) from CSS viewport → screenshot pixels."""
    vw = max(float(viewport_width), 1.0)
    vh = max(float(viewport_height), 1.0)
    return float(screenshot_width) / vw, float(screenshot_height) / vh


def map_box_to_screenshot(
    box: Mapping[str, float],
    viewport: Mapping[str, float],
    screenshot: Mapping[str, float],
) -> Dict[str, int]:
    """Map a CSS-viewport box to integer screenshot-pixel box.

    Args:
        box: {x, y, width, height} in CSS viewport pixels.
        viewport: {width, height} CSS viewport size.
        screenshot: {width, height} captured image size.

    Returns:
        {x, y, width, height} in screenshot pixels (ints, width/height >= 1).
    """
    scale_x, scale_y = compute_scale(
        viewport.get("width", 1),
        viewport.get("height", 1),
        screenshot.get("width", 1),
        screenshot.get("height", 1),
    )
    x = int(round(float(box.get("x", 0)) * scale_x))
    y = int(round(float(box.get("y", 0)) * scale_y))
    width = max(1, int(round(float(box.get("width", 0)) * scale_x)))
    height = max(1, int(round(float(box.get("height", 0)) * scale_y)))
    return {"x": x, "y": y, "width": width, "height": height}


def map_boxes_to_screenshot(
    boxes: Sequence[Mapping[str, float]],
    viewport: Mapping[str, float],
    screenshot: Mapping[str, float],
) -> list:
    """Map a list of viewport boxes; preserves non-geometry keys (e.g. category)."""
    mapped = []
    for box in boxes:
        geom = map_box_to_screenshot(box, viewport, screenshot)
        item: MutableMapping[str, Union[str, int, float]] = dict(geom)
        if "category" in box:
            item["category"] = box["category"]  # type: ignore[assignment]
        mapped.append(item)
    return mapped


def box_dict_to_xyxy(box: Mapping[str, float]) -> Tuple[int, int, int, int]:
    """Convert {x,y,width,height} → (x1, y1, x2, y2) for redact_regions."""
    x1 = int(box.get("x", 0))
    y1 = int(box.get("y", 0))
    x2 = x1 + max(1, int(box.get("width", 1)))
    y2 = y1 + max(1, int(box.get("height", 1)))
    return x1, y1, x2, y2
