"""Validate that a Visual UI Map is privacy-safe before transmission.

Checks:
  ✓ no raw image / base64 screenshot
  ✓ no embeddings / face crops
  ✓ no known PII patterns in text fields
  ✓ sensitive text redacted
  ✓ privacy_safe flag consistency
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from vision.visual_metadata import FORBIDDEN_KEYS

# Patterns that must never appear as raw values in the map
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)
_PHONE_RE = re.compile(
    r"\b(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

# Data-URL / base64 image sniff
_DATA_URL_RE = re.compile(r"data:image\/[a-zA-Z0-9.+-]+;base64,", re.I)
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")

ALLOWED_REDACTION_MARKERS = (
    "[REDACTED]",
    "[PASSWORD_REDACTED]",
    "[EMAIL_REDACTED]",
    "[PHONE_REDACTED]",
    "[SSN_REDACTED]",
    "[CREDIT_CARD_REDACTED]",
    "[CARD_REDACTED]",
)


def _walk_keys(obj: Any, path: str = "") -> List[str]:
    keys: List[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            keys.append(str(k))
            keys.extend(_walk_keys(v, p))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            keys.extend(_walk_keys(v, f"{path}[{i}]"))
    return keys


def _collect_strings(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, Mapping):
        for v in obj.values():
            out.extend(_collect_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_collect_strings(v))
    return out


def _has_forbidden_key(obj: Mapping[str, Any]) -> Optional[str]:
    keys = set(_walk_keys(obj))
    for forbidden in FORBIDDEN_KEYS:
        if forbidden in keys:
            return forbidden
        # Nested common aliases
        if forbidden.lower() in {k.lower() for k in keys}:
            return forbidden
    return None


def _text_contains_pii(text: str) -> bool:
    if not text:
        return False
    # Redaction markers are fine
    upper = text.strip().upper()
    if upper.startswith("[") and "REDACTED" in upper:
        return False
    if _EMAIL_RE.search(text):
        return True
    if _SSN_RE.search(text):
        return True
    if _PHONE_RE.search(text):
        return True
    # Card: only flag long digit runs that look like PANs (avoid short ids)
    if _CARD_RE.search(text) and sum(c.isdigit() for c in text) >= 13:
        return True
    return False


def _looks_like_image_payload(text: str) -> bool:
    if not text:
        return False
    if _DATA_URL_RE.search(text):
        return True
    if text.startswith("data:image"):
        return True
    # Large base64 blobs are suspicious in metadata
    if len(text) > 500 and _BASE64_BLOB_RE.search(text):
        return True
    return False


def validate_visual_ui_map(
    ui_map: Optional[Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    """Validate a visual UI map for privacy safety.

    Returns (is_safe, list_of_reasons).
    If validation fails, callers must set privacy_safe=False and not transmit.
    """
    reasons: List[str] = []
    if ui_map is None:
        return True, []

    if not isinstance(ui_map, Mapping):
        return False, ["not_a_mapping"]

    forbidden = _has_forbidden_key(ui_map)
    if forbidden:
        reasons.append(f"forbidden_key:{forbidden}")

    for s in _collect_strings(ui_map):
        if _looks_like_image_payload(s):
            reasons.append("raw_image_or_base64")
            break

    # Element-level checks
    for el in ui_map.get("elements") or []:
        if not isinstance(el, Mapping):
            continue
        text = el.get("text")
        if isinstance(text, str):
            if el.get("sensitive") and text not in ALLOWED_REDACTION_MARKERS:
                # Sensitive must be redacted (allow empty)
                if text and not (
                    text.startswith("[") and "REDACTED" in text.upper()
                ):
                    reasons.append("sensitive_text_not_redacted")
            if _text_contains_pii(text):
                reasons.append("pii_pattern_in_text")
            if _looks_like_image_payload(text):
                reasons.append("image_in_text_field")

        # Password must never appear as raw value
        role = str(el.get("role") or "").lower()
        if role == "password" and text and "REDACTED" not in str(text).upper():
            reasons.append("password_value_exposed")

    # Embedding-like nested structures
    blob = str(ui_map).lower()
    for token in ("embedding", "face_crop", "biometric", "raw_image"):
        if token in blob and token not in str(ui_map.get("summary") or {}).lower():
            # Avoid false positive on the word appearing only in docs — check keys
            if token in set(k.lower() for k in _walk_keys(ui_map)):
                reasons.append(f"forbidden_token:{token}")

    is_safe = len(reasons) == 0

    # privacy_safe flag must not claim safe when validation failed
    claimed = ui_map.get("privacy_safe", True)
    if claimed and not is_safe:
        reasons.append("privacy_safe_flag_inconsistent")
        is_safe = False

    return is_safe, reasons


def enforce_privacy_on_map(
    ui_map: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return a copy marked privacy_safe correctly; None if input is None.

    If validation fails, sets privacy_safe=False. Does not strip elements —
    callers must refuse transmission when privacy_safe is False.
    """
    if ui_map is None:
        return None
    out = dict(ui_map)
    # Redact any lingering PII in element texts defensively
    elements = []
    for el in out.get("elements") or []:
        if not isinstance(el, Mapping):
            continue
        cleaned = dict(el)
        text = cleaned.get("text")
        if isinstance(text, str) and _text_contains_pii(text):
            cleaned["text"] = "[REDACTED]"
            cleaned["sensitive"] = True
        if cleaned.get("sensitive") and cleaned.get("text"):
            t = str(cleaned["text"])
            if not (t.startswith("[") and "REDACTED" in t.upper()):
                cleaned["text"] = "[REDACTED]"
        elements.append(cleaned)
    out["elements"] = elements

    is_safe, reasons = validate_visual_ui_map(out)
    out["privacy_safe"] = bool(is_safe)
    if not is_safe:
        out["validation_errors"] = reasons
    elif "validation_errors" in out:
        del out["validation_errors"]
    return out
