"""Map Visual UI Map (screenshot pixel) coordinates to CSS viewport
coordinates used by browser actions (elementFromPoint / click).

Screenshot dimensions often differ from CSS viewport size because of
devicePixelRatio / HiDPI capture.

    scaleX = screenshotWidth / viewportWidth
    scaleY = screenshotHeight / viewportHeight
    cssX   = imageX / scaleX
    cssY   = imageY / scaleY
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple, Union

PointDict = Dict[str, Union[int, float]]


class CoordinateMappingError(ValueError):
    """Raised when dimensions or coordinates cannot be mapped safely."""


def compute_inverse_scale(
    screenshot_width: float,
    screenshot_height: float,
    viewport_width: float,
    viewport_height: float,
) -> Tuple[float, float]:
    """Return (scale_x, scale_y) where scale = screenshot / viewport.

    CSS coords = screenshot_coords / scale.
    """
    sw = float(screenshot_width)
    sh = float(screenshot_height)
    vw = float(viewport_width)
    vh = float(viewport_height)
    if sw <= 0 or sh <= 0 or vw <= 0 or vh <= 0:
        raise CoordinateMappingError("invalid_dimensions")
    return sw / vw, sh / vh


def screenshot_point_to_css(
    x: float,
    y: float,
    screenshot: Mapping[str, float],
    viewport: Mapping[str, float],
    *,
    clamp: bool = True,
) -> Dict[str, int]:
    """Convert a screenshot-pixel point to CSS viewport coordinates.

    Args:
        x, y: Point in screenshot pixel space.
        screenshot: {width, height} of the captured image.
        viewport: {width, height} CSS viewport size.
        clamp: If True, clamp result inside [0, viewport-1].

    Returns:
        {"x": int, "y": int} in CSS viewport pixels.
    """
    sw = float(screenshot.get("width", 0))
    sh = float(screenshot.get("height", 0))
    vw = float(viewport.get("width", 0))
    vh = float(viewport.get("height", 0))
    scale_x, scale_y = compute_inverse_scale(sw, sh, vw, vh)

    css_x = float(x) / scale_x
    css_y = float(y) / scale_y

    if clamp:
        css_x = max(0.0, min(css_x, max(0.0, vw - 1.0)))
        css_y = max(0.0, min(css_y, max(0.0, vh - 1.0)))

    return {"x": int(round(css_x)), "y": int(round(css_y))}


def screenshot_box_center_to_css(
    box: Mapping[str, float],
    screenshot: Mapping[str, float],
    viewport: Mapping[str, float],
    *,
    clamp: bool = True,
) -> Dict[str, int]:
    """Map the center of a screenshot-space box to CSS viewport coords."""
    cx = float(box.get("x", 0)) + float(box.get("width", 0)) / 2.0
    cy = float(box.get("y", 0)) + float(box.get("height", 0)) / 2.0
    return screenshot_point_to_css(
        cx, cy, screenshot, viewport, clamp=clamp
    )


def clamp_css_point(
    x: float,
    y: float,
    viewport: Mapping[str, float],
) -> Dict[str, int]:
    """Clamp a CSS point into the viewport."""
    vw = max(1.0, float(viewport.get("width", 1)))
    vh = max(1.0, float(viewport.get("height", 1)))
    return {
        "x": int(round(max(0.0, min(float(x), vw - 1.0)))),
        "y": int(round(max(0.0, min(float(y), vh - 1.0)))),
    }


def validate_dimensions(
    screenshot: Mapping[str, Any],
    viewport: Mapping[str, Any],
) -> bool:
    """Return True when both screenshot and viewport have positive sizes."""
    try:
        sw = float(screenshot.get("width", 0))
        sh = float(screenshot.get("height", 0))
        vw = float(viewport.get("width", 0))
        vh = float(viewport.get("height", 0))
    except (TypeError, ValueError):
        return False
    return sw > 0 and sh > 0 and vw > 0 and vh > 0


def map_action_coordinates(
    image_x: float,
    image_y: float,
    screenshot: Mapping[str, float],
    viewport: Mapping[str, float],
) -> Dict[str, Any]:
    """Full mapping helper used by the action pipeline.

    Returns a structured result with scales and CSS coordinates, or raises
    CoordinateMappingError on invalid input.
    """
    if not validate_dimensions(screenshot, viewport):
        raise CoordinateMappingError("invalid_dimensions")

    scale_x, scale_y = compute_inverse_scale(
        float(screenshot["width"]),
        float(screenshot["height"]),
        float(viewport["width"]),
        float(viewport["height"]),
    )
    css = screenshot_point_to_css(
        image_x, image_y, screenshot, viewport, clamp=True
    )
    return {
        "screenshot": {"x": float(image_x), "y": float(image_y)},
        "css": css,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "viewport": {
            "width": int(viewport["width"]),
            "height": int(viewport["height"]),
        },
        "screenshot_size": {
            "width": int(screenshot["width"]),
            "height": int(screenshot["height"]),
        },
    }
