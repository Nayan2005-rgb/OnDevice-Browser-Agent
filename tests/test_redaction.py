"""Tests for the sanitizer's redaction of detected PII in text."""

from privacy.sanitizer import sanitize_text


def test_sanitize_replaces_email():
    text = "Reach me at jane.doe@example.com anytime."
    findings = [{"type": "email", "match": "jane.doe@example.com", "span": (12, 33)}]
    result = sanitize_text(text, findings)
    assert "jane.doe@example.com" not in result
    assert "[REDACTED_EMAIL]" in result
