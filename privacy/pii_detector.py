"""Text-based PII detection over extracted page text or OCR output,
using regex patterns and optionally an NER model (e.g. Presidio)."""

import re
from typing import List, Dict

PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"\b(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}


class PiiDetector:
    def detect(self, text: str) -> List[Dict]:
        """Return a list of {"type": str, "match": str, "span": (start, end)}."""
        results = []
        for pii_type, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                results.append({
                    "type": pii_type,
                    "match": match.group(),
                    "span": match.span(),
                })
        return results
