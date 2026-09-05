"""Visual PII detection: finds regions in a screenshot likely to contain
personally identifiable information (text fields with SSNs, card numbers,
emails rendered as text, etc.) so they can be redacted before leaving device."""

from typing import List, Dict
import numpy as np


class VisualPIIDetector:
    def __init__(self, model_path: str = "models/pii_detection/pii_model"):
        self.model_path = model_path

    def detect(self, image: np.ndarray) -> List[Dict]:
        """Return bounding boxes of regions suspected to contain PII.

        TODO: combine OCR (e.g. pytesseract) with the text-based
        privacy/pii_detector.py regex/NER pipeline.
        """
        return []
