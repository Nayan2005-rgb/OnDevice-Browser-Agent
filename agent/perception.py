"""Perception module: gathers the current state of the browser
(screenshot + sanitized DOM) into a structured observation the
decision engine can reason over."""

from typing import Dict, Optional
import numpy as np

from vision.screenshot_capture import ScreenshotCapture
from vision.ui_element_detection import UIElementDetector
from privacy.sensitive_data_detector import SensitiveDataDetector
from privacy.sanitizer import sanitize_text


class Perception:
    def __init__(self):
        self.capture = ScreenshotCapture()
        self.ui_detector = UIElementDetector()
        self.privacy_detector = SensitiveDataDetector()

    def observe(self, dom_snapshot: Optional[Dict] = None, page_text: str = "") -> Dict:
        """Build a sanitized observation of the current page.

        Returns:
            {
                "screenshot": np.ndarray | None,
                "ui_elements": list,
                "sanitized_text": str,
            }
        """
        findings = self.privacy_detector.analyze(dom_snapshot or {}, page_text)
        sanitized_text = sanitize_text(page_text, findings)

        return {
            "screenshot": None,  # TODO: wire up self.capture.capture()
            "ui_elements": [],   # TODO: wire up self.ui_detector.detect_elements(...)
            "sanitized_text": sanitized_text,
            "privacy_findings": findings,
        }
