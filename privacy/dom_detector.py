"""Detects sensitive elements directly from DOM structure/attributes
(e.g. input[type=password], autocomplete=email, aria-labels containing
'ssn') as a complement to visual PII detection."""

from typing import List, Dict

SENSITIVE_INPUT_TYPES = {"password", "email", "tel"}
SENSITIVE_KEYWORDS = {
    "ssn",
    "social security",
    "credit card",
    "cvv",
    "cvc",
    "passport",
    "password",
    "passwd",
    "pwd",
    "secret",
}


class DomDetector:
    def find_sensitive_nodes(self, dom_snapshot: Dict) -> List[Dict]:
        """
        Args:
            dom_snapshot: a parsed representation of the page DOM
                          (e.g. from the extension's content script).

        Returns:
            List of nodes flagged as sensitive, with a reason.
            Match strings are placeholders only — never raw secrets.
        """
        findings: List[Dict] = []
        elements = []

        if isinstance(dom_snapshot, dict):
            if isinstance(dom_snapshot.get("elements"), list):
                elements = dom_snapshot["elements"]
            elif isinstance(dom_snapshot.get("page"), dict):
                elements = dom_snapshot["page"].get("elements") or []

        for el in elements:
            tag = (el.get("tag") or "").lower()
            input_type = (el.get("type") or "").lower()
            attrs_blob = " ".join(
                str(el.get(k) or "")
                for k in ("name", "id", "placeholder", "ariaLabel", "autocomplete", "text")
            ).lower()

            reason = None
            pii_type = "sensitive"

            if input_type == "password" or "password" in attrs_blob or "passwd" in attrs_blob:
                reason = "password_field"
                pii_type = "password"
            elif input_type in SENSITIVE_INPUT_TYPES:
                reason = f"input_type_{input_type}"
                pii_type = input_type if input_type != "tel" else "phone"
            else:
                for kw in SENSITIVE_KEYWORDS:
                    if kw in attrs_blob:
                        reason = f"keyword:{kw}"
                        if "card" in kw or kw in ("cvv", "cvc"):
                            pii_type = "credit_card"
                        elif "ssn" in kw or "social" in kw:
                            pii_type = "ssn"
                        elif "pass" in kw or kw in ("pwd", "passwd", "secret"):
                            pii_type = "password"
                        break

            if el.get("sensitive") and not reason:
                reason = "flagged_sensitive"
                pii_type = "sensitive"

            if not reason:
                continue

            # Do not echo raw field values into findings
            findings.append(
                {
                    "type": pii_type,
                    "match": f"[{pii_type.upper()}_REDACTED]",
                    "span": (0, 0),
                    "selector": el.get("selector"),
                    "reason": reason,
                    "tag": tag,
                }
            )

        return findings
