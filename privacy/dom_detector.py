"""Detects sensitive elements directly from DOM structure/attributes
(e.g. input[type=password], autocomplete=email, aria-labels containing
'ssn') as a complement to visual PII detection."""

from typing import List, Dict

SENSITIVE_INPUT_TYPES = {"password", "email", "tel"}
SENSITIVE_KEYWORDS = {"ssn", "social security", "credit card", "cvv", "passport"}


class DomDetector:
    def find_sensitive_nodes(self, dom_snapshot: Dict) -> List[Dict]:
        """
        Args:
            dom_snapshot: a parsed representation of the page DOM
                          (e.g. from the extension's content script).

        Returns:
            List of nodes flagged as sensitive, with a reason.
        """
        # TODO: walk dom_snapshot (tag, attributes, text) and flag matches
        # against SENSITIVE_INPUT_TYPES / SENSITIVE_KEYWORDS.
        return []
