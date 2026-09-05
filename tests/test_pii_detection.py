"""Tests for text-based PII detection."""

from privacy.pii_detector import PiiDetector


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


def test_no_false_positive_on_plain_text():
    detector = PiiDetector()
    results = detector.detect("This is a plain sentence with no PII.")
    assert results == []
