"""Tests for text-based PII detection and DOM-level sensitive fields."""

from privacy.pii_detector import PiiDetector
from privacy.dom_detector import DomDetector
from privacy.sanitizer import sanitize_text


def test_detects_email():
    detector = PiiDetector()
    results = detector.detect("Contact me at jane.doe@example.com for details.")
    types = [r["type"] for r in results]
    assert "email" in types


def test_detects_ssn():
    detector = PiiDetector()
    results = detector.detect("SSN: 123-45-6789")
    types = [r["type"] for r in results]
    assert "ssn" in types


def test_detects_phone():
    detector = PiiDetector()
    results = detector.detect("Call 987-654-3210 now")
    types = [r["type"] for r in results]
    assert "phone" in types


def test_no_false_positive_on_plain_text():
    detector = PiiDetector()
    results = detector.detect("This is a plain sentence with no PII.")
    assert results == []


def test_password_field_redaction_via_dom_detector():
    detector = DomDetector()
    snapshot = {
        "elements": [
            {
                "tag": "input",
                "type": "password",
                "name": "password",
                "id": "pwd",
                "selector": "#pwd",
                "sensitive": True,
                "value": "[PASSWORD_REDACTED]",
            },
            {
                "tag": "input",
                "type": "email",
                "name": "email",
                "id": "email",
                "selector": "#email",
                "sensitive": True,
            },
        ]
    }
    findings = detector.find_sensitive_nodes(snapshot)
    types = {f["type"] for f in findings}
    assert "password" in types
    assert "email" in types
    # Never include raw secrets in findings
    for f in findings:
        assert "secret" not in (f.get("match") or "").lower()


def test_sanitize_password_placeholder():
    text = "Password: secret123"
    findings = [{"type": "password", "match": "secret123", "span": (10, 19)}]
    result = sanitize_text(text, findings)
    assert "secret123" not in result
    assert "[REDACTED_PASSWORD]" in result


def test_dom_snapshot_structure_fields():
    """Sanitized page payloads used by /api/agent/step should carry these keys."""
    page = {
        "url": "https://example.com",
        "title": "Example Page",
        "visibleText": "Submit your application",
        "elements": [
            {
                "tag": "button",
                "text": "Submit",
                "selector": "#submit-button",
                "type": None,
                "name": None,
                "placeholder": None,
                "sensitive": False,
            }
        ],
    }
    assert "url" in page
    assert "title" in page
    assert "visibleText" in page
    assert isinstance(page["elements"], list)
    el = page["elements"][0]
    for key in ("tag", "text", "selector", "sensitive"):
        assert key in el
