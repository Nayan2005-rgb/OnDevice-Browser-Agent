"""Perception module: gathers the current state of the browser
(sanitized DOM snapshot + optional safe visual metadata + visual UI map)
into a structured observation the decision engine can reason over.

PRIVACY BOUNDARY: Inputs to observe() are assumed to already be
sanitized by the extension. This module may run a secondary check
but must not introduce raw PII into logs. visual_context / visual_ui_map
must never contain raw screenshots, face crops, or embeddings.
"""

from typing import Any, Dict, List, Optional

from privacy.sensitive_data_detector import SensitiveDataDetector
from privacy.sanitizer import sanitize_text
from vision.visual_metadata import assert_visual_context_is_safe
from vision.visual_metadata_validator import (
    enforce_privacy_on_map,
    validate_visual_ui_map,
)


class Perception:
    def __init__(self):
        self.privacy_detector = SensitiveDataDetector()

    def observe(
        self,
        dom_snapshot: Optional[Dict] = None,
        page_text: str = "",
        visual_context: Optional[Dict[str, Any]] = None,
        visual_ui_map: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """Build a sanitized observation of the current page.

        Accepts either the structured extension payload::

            {"url", "title", "visibleText", "elements": [...]}

        or a legacy dict. Optional ``visual_context`` is safe metadata only
        (dimensions, face counts, redaction counts) — never raw pixels.

        Optional ``visual_ui_map`` is the multimodal UI structure (Milestone 3A).
        If unavailable, ``visual_ui_map`` is None and the DOM-only flow continues.
        """
        snapshot = dom_snapshot or {}

        # Support both {page: {...}} wrappers and flat page objects
        page = snapshot.get("page") if isinstance(snapshot.get("page"), dict) else snapshot

        # Allow visual_context / visual_ui_map nested inside a combined payload
        if visual_context is None and isinstance(snapshot.get("visual_context"), dict):
            visual_context = snapshot.get("visual_context")
        if visual_ui_map is None and isinstance(snapshot.get("visual_ui_map"), dict):
            visual_ui_map = snapshot.get("visual_ui_map")

        elements: List[Dict] = list(page.get("elements") or [])
        visible_text = page.get("visibleText") or page_text or ""
        url = page.get("url") or ""
        title = page.get("title") or ""

        # Secondary privacy pass (defense in depth). Findings should already
        # be redacted by the extension; this catches anything that slipped through.
        findings = self.privacy_detector.analyze(page, visible_text)
        sanitized_text = sanitize_text(visible_text, findings)

        # Also sanitize element text fields defensively (placeholders only)
        sanitized_elements = []
        for el in elements:
            cleaned = dict(el)
            for key in ("text", "placeholder", "ariaLabel", "value", "name"):
                if cleaned.get(key):
                    cleaned[key] = sanitize_text(str(cleaned[key]), findings)
            sanitized_elements.append(cleaned)

        safe_visual = None
        if visual_context is not None:
            if assert_visual_context_is_safe(visual_context):
                safe_visual = dict(visual_context)
            else:
                # Drop unsafe payloads rather than forwarding raw image data
                safe_visual = {
                    "screenshot_available": False,
                    "privacy_safe": False,
                    "faces_detected": 0,
                    "sensitive_regions": 0,
                    "redactions_applied": 0,
                }

        safe_ui_map = None
        if visual_ui_map is not None:
            enforced = enforce_privacy_on_map(visual_ui_map)
            is_safe, _reasons = validate_visual_ui_map(enforced)
            if is_safe and enforced and enforced.get("privacy_safe", True):
                safe_ui_map = enforced
            else:
                # Unsafe maps are dropped (not transmitted onward)
                safe_ui_map = None

        privacy_safe = True
        if safe_visual is not None:
            privacy_safe = bool(safe_visual.get("privacy_safe", True))
        if safe_ui_map is not None:
            privacy_safe = privacy_safe and bool(safe_ui_map.get("privacy_safe", True))

        return {
            "screenshot": None,  # Raw screenshots never enter the decision engine
            "ui_elements": sanitized_elements,
            "sanitized_text": sanitized_text,
            "page": {
                "url": url,
                "title": title,
                "visibleText": sanitized_text,
                "elements": sanitized_elements,
            },
            "page_context": {
                "url": url,
                "title": title,
                "visibleText": sanitized_text,
                "elements": sanitized_elements,
            },
            "visual_context": safe_visual,
            "visual_ui_map": safe_ui_map,
            "privacy_safe": privacy_safe,
            "privacy_findings": findings,
        }
