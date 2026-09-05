"""Higher-level orchestrator combining DOM-based and text-based PII
detection to decide what should be sanitized before leaving the device."""

from typing import Dict, List
from privacy.dom_detector import DomDetector
from privacy.pii_detector import PiiDetector


class SensitiveDataDetector:
    def __init__(self):
        self.dom_detector = DomDetector()
        self.pii_detector = PiiDetector()

    def analyze(self, dom_snapshot: Dict, page_text: str) -> List[Dict]:
        findings = []
        findings.extend(self.dom_detector.find_sensitive_nodes(dom_snapshot))
        findings.extend(self.pii_detector.detect(page_text))
        return findings
