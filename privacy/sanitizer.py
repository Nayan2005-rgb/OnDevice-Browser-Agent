"""Applies sanitization/redaction to text or DOM content based on
findings from SensitiveDataDetector, before data is sent to the LLM
or stored in action history."""

from typing import Dict, List


def sanitize_text(text: str, findings: List[Dict]) -> str:
    """Replace each PII match with a typed placeholder, e.g. [REDACTED_EMAIL]."""
    sanitized = text
    # Replace longest matches first to avoid partial-overlap issues.
    for finding in sorted(findings, key=lambda f: len(f.get("match", "")), reverse=True):
        match = finding.get("match")
        pii_type = finding.get("type", "PII").upper()
        if match:
            sanitized = sanitized.replace(match, f"[REDACTED_{pii_type}]")
    return sanitized
