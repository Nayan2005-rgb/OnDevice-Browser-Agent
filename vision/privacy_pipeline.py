"""Local privacy pipeline: face detect → unify → redact → safe metadata.

Used by the on-device Flask privacy worker and by tests. The raw image is
processed in-memory and never persisted.
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from vision.face_detection import FaceDetector, create_face_detector
from vision.redaction import DEFAULT_FACE_CONFIG, redact_by_categories
from vision.unified_regions import unify_sensitive_regions
from vision.visual_metadata import (
    build_unified_privacy_report,
    visual_context_from_privacy_report,
)


def decode_image_input(image: Any) -> Optional[np.ndarray]:
    """Decode ndarray / PNG bytes / data-URL into RGB uint8 array."""
    if image is None:
        return None
    if isinstance(image, np.ndarray):
        return image
    if isinstance(image, (bytes, bytearray)):
        return _bytes_to_rgb(bytes(image))
    if isinstance(image, str):
        data = image
        if data.startswith("data:"):
            # data:image/png;base64,....
            parts = data.split(",", 1)
            if len(parts) != 2:
                return None
            data = parts[1]
        try:
            raw = base64.b64decode(data)
        except Exception:
            return None
        return _bytes_to_rgb(raw)
    return None


def _bytes_to_rgb(raw: bytes) -> Optional[np.ndarray]:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.asarray(img)
    except Exception:
        return None


def encode_png_data_url(image: np.ndarray) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(image.astype(np.uint8)).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def process_screenshot_privacy(
    image: Any,
    dom_boxes: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    detector: Optional[FaceDetector] = None,
    face_detection: bool = True,
    min_confidence: float = 0.6,
    face_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run local face detection + unified redaction.

    Returns sanitized image (data URL), privacy report, visual_context, timing.
    Never logs or returns face crops / embeddings / raw PII.
    """
    t0 = time.perf_counter()
    timing: Dict[str, Any] = {}

    arr = decode_image_input(image)
    if arr is None:
        return {
            "ok": False,
            "error": "invalid_image",
            "sanitized_data_url": None,
            "privacy_report": None,
            "visual_context": None,
            "regions": [],
            "timing": timing,
        }

    face_boxes: List[Dict[str, Any]] = []
    t_face = time.perf_counter()
    if face_detection:
        det = detector or create_face_detector(
            backend="haar", min_confidence=min_confidence
        )
        face_boxes = det.detect(arr)
    timing["face_detection_ms"] = int(round((time.perf_counter() - t_face) * 1000))

    t_merge = time.perf_counter()
    unified = unify_sensitive_regions(dom_boxes or [], face_boxes)
    regions = unified["regions"]
    timing["face_box_processing_ms"] = int(
        round((time.perf_counter() - t_merge) * 1000)
    )

    cfg = {**DEFAULT_FACE_CONFIG, **(face_config or {})}
    t_redact = time.perf_counter()
    sanitized, redact_report = redact_by_categories(arr, regions, face_config=cfg)
    timing["image_redaction_ms"] = int(
        round((time.perf_counter() - t_redact) * 1000)
    )
    timing["total_privacy_processing_ms"] = int(
        round((time.perf_counter() - t0) * 1000)
    )

    faces_detected = len(face_boxes)
    faces_redacted = int((redact_report.get("vision") or {}).get("faces_redacted", 0))

    privacy_report = build_unified_privacy_report(
        categories=redact_report.get("categories"),
        faces_detected=faces_detected,
        faces_redacted=faces_redacted,
        total_redactions=redact_report.get("total_redactions", 0),
        timing=timing,
        screenshot_width=redact_report.get("screenshot_width"),
        screenshot_height=redact_report.get("screenshot_height"),
        redactions=redact_report.get("redactions"),
    )
    # Preserve processing_time_ms for Milestone 2A consumers
    privacy_report["processing_time_ms"] = redact_report.get("processing_time_ms")

    visual_context = visual_context_from_privacy_report(
        privacy_report, screenshot_available=True, privacy_safe=True
    )

    return {
        "ok": True,
        "sanitized_data_url": encode_png_data_url(sanitized),
        "privacy_report": privacy_report,
        "visual_context": visual_context,
        "regions": regions,
        "face_boxes": face_boxes,  # geometry only — caller must not log crops
        "timing": timing,
    }
