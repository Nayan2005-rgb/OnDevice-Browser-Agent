"""Build safe visual metadata for the agent — never includes raw pixels or faces."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence


FORBIDDEN_KEYS = frozenset(
    {
        "image",
        "data_url",
        "dataUrl",
        "screenshot",
        "raw_image",
        "original_image",
        "face_crop",
        "face_crops",
        "embedding",
        "embeddings",
        "biometric",
        "biometrics",
        "pixels",
        "base64",
    }
)


def build_visual_context(
    *,
    screenshot_available: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
    faces_detected: int = 0,
    faces_redacted: int = 0,
    sensitive_regions: int = 0,
    redactions_applied: int = 0,
    privacy_safe: bool = True,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Create agent-facing visual metadata (safe, no raw image / face data)."""
    ctx: Dict[str, Any] = {
        "screenshot_available": bool(screenshot_available),
        "dimensions": {
            "width": int(width) if width is not None else None,
            "height": int(height) if height is not None else None,
        },
        "faces_detected": int(faces_detected),
        "faces_redacted": int(faces_redacted),
        "sensitive_regions": int(sensitive_regions),
        "redactions_applied": int(redactions_applied),
        "privacy_safe": bool(privacy_safe),
    }
    if extra:
        for key, value in extra.items():
            if key in FORBIDDEN_KEYS or key in ctx:
                continue
            ctx[key] = value
    return ctx


def visual_context_from_privacy_report(
    report: Optional[Mapping[str, Any]],
    *,
    screenshot_available: bool = True,
    privacy_safe: bool = True,
) -> Dict[str, Any]:
    """Derive visual_context from a screenshot privacy report."""
    report = report or {}
    categories = report.get("categories") or {}
    vision = report.get("vision") or {}
    faces_detected = int(
        vision.get("faces_detected", categories.get("face", 0) or 0)
    )
    faces_redacted = int(vision.get("faces_redacted", faces_detected))
    total = int(report.get("total_redactions", 0) or 0)

    return build_visual_context(
        screenshot_available=screenshot_available,
        width=report.get("screenshot_width"),
        height=report.get("screenshot_height"),
        faces_detected=faces_detected,
        faces_redacted=faces_redacted,
        sensitive_regions=total,
        redactions_applied=total,
        privacy_safe=privacy_safe,
    )


def assert_visual_context_is_safe(ctx: Optional[Mapping[str, Any]]) -> bool:
    """Return True if context has no forbidden raw-image / biometric keys."""
    if ctx is None:
        return True
    blob = str(ctx).lower()
    for key in FORBIDDEN_KEYS:
        if key in ctx:
            return False
        # Soft check: embeddings / base64 blobs must not appear
        if key in ("embedding", "embeddings", "biometric", "face_crop") and key in blob:
            return False
    return True


def build_unified_privacy_report(
    *,
    dom_pii: Optional[Mapping[str, int]] = None,
    faces_detected: int = 0,
    faces_redacted: int = 0,
    total_redactions: int = 0,
    categories: Optional[Mapping[str, int]] = None,
    timing: Optional[Mapping[str, Any]] = None,
    screenshot_width: Optional[int] = None,
    screenshot_height: Optional[int] = None,
    redactions: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the Milestone 2B privacy report (counts + timing only)."""
    cats = dict(categories or {})
    report: Dict[str, Any] = {
        "dom_pii": {
            "emails": int((dom_pii or {}).get("emails", cats.get("email", 0))),
            "phones": int((dom_pii or {}).get("phones", cats.get("phone", 0))),
            "passwords": int(
                (dom_pii or {}).get("passwords", cats.get("password", 0))
            ),
            "cards": int((dom_pii or {}).get("cards", cats.get("credit_card", 0))),
            "ssn": int((dom_pii or {}).get("ssn", cats.get("ssn", 0))),
        },
        "vision": {
            "faces_detected": int(faces_detected),
            "faces_redacted": int(faces_redacted),
        },
        "total_redactions": int(total_redactions),
        "categories": cats,
        "timing": dict(timing or {}),
    }
    if screenshot_width is not None:
        report["screenshot_width"] = int(screenshot_width)
    if screenshot_height is not None:
        report["screenshot_height"] = int(screenshot_height)
    if redactions is not None:
        # Geometry + strategy only — never attach image data
        report["redactions"] = [
            {
                "category": r.get("category"),
                "strategy": r.get("strategy"),
                "source": r.get("source"),
                "box": r.get("box"),
            }
            for r in redactions
        ]
    return report
