"""Persistence privacy validator tests."""

from __future__ import annotations

import pytest

from privacy.persistence_validator import (
    PersistencePrivacyError,
    find_forbidden_paths,
    sanitize_for_persistence,
    validate_for_persistence,
)


FORBIDDEN_PAYLOADS = [
    {"raw_image": "data:image/png;base64,AAAA"},
    {"original_image": "xxxx"},
    {"screenshot_base64": "iVBORw0KGgo="},
    {"password": "secret"},
    {"otp": "123456"},
    {"credit_card": "4111111111111111"},
    {"ssn": "123-45-6789"},
    {"face_embedding": [0.1, 0.2]},
    {"face_crop": "data:image/jpeg;base64,/9j/"},
]


@pytest.mark.parametrize("payload", FORBIDDEN_PAYLOADS)
def test_forbidden_keys_rejected(payload):
    with pytest.raises(PersistencePrivacyError):
        validate_for_persistence(payload)


def test_nested_forbidden_rejected():
    with pytest.raises(PersistencePrivacyError):
        validate_for_persistence({"meta": {"screenshot_base64": "abc"}})


def test_safe_metadata_allowed():
    safe = {
        "page_signature": "abc",
        "url_signature": "def",
        "role_counts": {"button": 3},
        "element_count": 3,
        "reason": "cookie_dialog_blocking_target",
    }
    validate_for_persistence(safe)
    cleaned = sanitize_for_persistence(safe)
    assert cleaned["page_signature"] == "abc"


def test_sanitize_reject_false_strips_keys():
    raw = {"password": "x", "goal": "search"}
    cleaned = sanitize_for_persistence(raw, reject=False)
    assert "password" not in cleaned
    assert cleaned["goal"] == "search"


def test_find_forbidden_paths():
    paths = find_forbidden_paths({"a": {"otp": "1"}})
    assert any("otp" in p for p in paths)
