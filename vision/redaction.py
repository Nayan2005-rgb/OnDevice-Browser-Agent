"""Applies redaction (blur/mask) to regions flagged by PII or face
detectors before a screenshot is persisted or transmitted.

Note: For browser tabs, primary redaction happens in the Chrome extension
(OffscreenCanvas) BEFORE any network transmission. This Python module is
used for tests, local privacy processing, desktop capture fallbacks, and
shared strategy semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

# Categories that must be fully obscured (not merely blurred).
HIGH_RISK_CATEGORIES = frozenset({"password", "credit_card", "ssn", "sensitive"})

Box = Tuple[int, int, int, int]  # x1, y1, x2, y2

DEFAULT_FACE_CONFIG: Dict[str, Any] = {
    "padding_percent": 10,
    "blur_strength": "adaptive",  # or an int radius
    "min_blur_radius": 3,
    "max_blur_radius": 12,
}


def select_redaction_strategy(category: str) -> str:
    """Return 'black_box' for high-risk data, 'blur' for PII/faces."""
    cat = (category or "").lower().strip()
    if cat in HIGH_RISK_CATEGORIES:
        return "black_box"
    return "blur"


def expand_box_with_padding(
    box: Mapping[str, float],
    padding_percent: float,
    image_width: int,
    image_height: int,
) -> Dict[str, int]:
    """Expand a box by padding_percent on each side, clamped to image bounds."""
    x = float(box.get("x", 0))
    y = float(box.get("y", 0))
    w = float(box.get("width", 1))
    h = float(box.get("height", 1))
    pad_x = w * (float(padding_percent) / 100.0)
    pad_y = h * (float(padding_percent) / 100.0)
    x1 = max(0, int(round(x - pad_x)))
    y1 = max(0, int(round(y - pad_y)))
    x2 = min(image_width, int(round(x + w + pad_x)))
    y2 = min(image_height, int(round(y + h + pad_y)))
    return {
        "x": x1,
        "y": y1,
        "width": max(1, x2 - x1),
        "height": max(1, y2 - y1),
    }


def adaptive_face_blur_radius(
    width: int,
    height: int,
    *,
    min_radius: int = 3,
    max_radius: int = 12,
) -> int:
    """Scale blur kernel with face size — larger faces get stronger blur."""
    size = max(int(width), int(height))
    # Map ~30px faces → min, ~200px+ → max
    t = (size - 30) / 170.0
    t = max(0.0, min(1.0, t))
    radius = int(round(min_radius + t * (max_radius - min_radius)))
    return max(min_radius, min(max_radius, radius))


def _box_blur_region(region: np.ndarray, radius: int = 4) -> np.ndarray:
    """Lightweight repeated 3x3 mean filter (no OpenCV dependency required)."""
    if region.size == 0:
        return region
    h, w = region.shape[:2]
    if h < 2 or w < 2:
        return np.zeros_like(region)

    out = region.astype(np.float64)
    passes = max(2, min(int(radius), 16))
    for _ in range(passes):
        padded = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        out = (
            padded[:-2, :-2]
            + padded[:-2, 1:-1]
            + padded[:-2, 2:]
            + padded[1:-1, :-2]
            + padded[1:-1, 1:-1]
            + padded[1:-1, 2:]
            + padded[2:, :-2]
            + padded[2:, 1:-1]
            + padded[2:, 2:]
        ) / 9.0
    return np.clip(out, 0, 255).astype(region.dtype)


def redact_regions(
    image: np.ndarray,
    boxes: List[Box],
    method: str = "blur",
    blur_radius: int = 4,
) -> np.ndarray:
    """Redact the given bounding boxes in the image.

    Args:
        image: RGB image array (H, W, 3).
        boxes: list of (x1, y1, x2, y2) regions to redact.
        method: "blur" or "black_box".
        blur_radius: mean-filter passes for blur method.

    Returns:
        The redacted image (copy, does not mutate input).
    """
    output = image.copy()
    h, w = output.shape[:2]
    for (x1, y1, x2, y2) in boxes:
        xa, xb = max(0, min(w, int(x1))), max(0, min(w, int(x2)))
        ya, yb = max(0, min(h, int(y1))), max(0, min(h, int(y2)))
        if xb <= xa or yb <= ya:
            continue
        if method == "black_box":
            output[ya:yb, xa:xb] = 0
        else:
            output[ya:yb, xa:xb] = _box_blur_region(
                output[ya:yb, xa:xb], radius=blur_radius
            )
    return output


def _parse_region_box(region: Mapping[str, Any]) -> Box:
    if "box" in region and isinstance(region["box"], dict):
        b = region["box"]
        x1 = int(b.get("x", 0))
        y1 = int(b.get("y", 0))
        x2 = x1 + max(1, int(b.get("width", 1)))
        y2 = y1 + max(1, int(b.get("height", 1)))
    elif "width" in region:
        x1 = int(region.get("x", 0))
        y1 = int(region.get("y", 0))
        x2 = x1 + max(1, int(region.get("width", 1)))
        y2 = y1 + max(1, int(region.get("height", 1)))
    else:
        x1 = int(region.get("x1", 0))
        y1 = int(region.get("y1", 0))
        x2 = int(region.get("x2", x1 + 1))
        y2 = int(region.get("y2", y1 + 1))
    return x1, y1, x2, y2


def redact_by_categories(
    image: np.ndarray,
    regions: Sequence[Dict],
    face_config: Optional[Mapping[str, Any]] = None,
) -> Tuple[np.ndarray, Dict]:
    """Redact regions using per-category strategy selection.

    Each region dict: {category, x, y, width, height} OR
    {category, box: {x,y,width,height}} OR {category, x1,y1,x2,y2}.

    Face regions receive padding + adaptive blur by default.

    Returns:
        (redacted_image, privacy_report) — report never includes raw text/faces.
    """
    import time

    cfg = {**DEFAULT_FACE_CONFIG, **(face_config or {})}
    t0 = time.perf_counter()
    output = image.copy()
    h, w = output.shape[:2]
    redactions = []
    faces_redacted = 0

    for region in regions:
        category = str(region.get("category") or "sensitive")
        strategy = select_redaction_strategy(category)
        x1, y1, x2, y2 = _parse_region_box(region)

        blur_radius = 4
        if category == "face":
            box = {"x": x1, "y": y1, "width": max(1, x2 - x1), "height": max(1, y2 - y1)}
            padded = expand_box_with_padding(
                box,
                cfg.get("padding_percent", 10),
                w,
                h,
            )
            x1 = padded["x"]
            y1 = padded["y"]
            x2 = padded["x"] + padded["width"]
            y2 = padded["y"] + padded["height"]
            strength = cfg.get("blur_strength", "adaptive")
            if strength == "adaptive" or strength is None:
                blur_radius = adaptive_face_blur_radius(
                    padded["width"],
                    padded["height"],
                    min_radius=int(cfg.get("min_blur_radius", 3)),
                    max_radius=int(cfg.get("max_blur_radius", 12)),
                )
            else:
                blur_radius = int(strength)
            faces_redacted += 1

        box_xyxy: Box = (x1, y1, x2, y2)
        output = redact_regions(
            output, [box_xyxy], method=strategy, blur_radius=blur_radius
        )

        xa, xb = max(0, min(w, x1)), max(0, min(w, x2))
        ya, yb = max(0, min(h, y1)), max(0, min(h, y2))
        entry = {
            "category": category,
            "strategy": strategy,
            "source": region.get("source"),
            "box": {
                "x": xa,
                "y": ya,
                "width": max(1, xb - xa),
                "height": max(1, yb - ya),
            },
        }
        if category == "face" and "confidence" in region:
            entry["confidence"] = float(region["confidence"])
        redactions.append(entry)

    elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
    categories: Dict[str, int] = {}
    for item in redactions:
        categories[item["category"]] = categories.get(item["category"], 0) + 1

    report = {
        "screenshot_width": w,
        "screenshot_height": h,
        "redactions": redactions,
        "total_redactions": len(redactions),
        "processing_time_ms": elapsed_ms,
        "categories": categories,
        "vision": {
            "faces_detected": categories.get("face", 0),
            "faces_redacted": faces_redacted,
        },
    }
    return output, report
