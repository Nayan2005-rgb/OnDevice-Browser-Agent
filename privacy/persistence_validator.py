"""Persistence privacy validator — blocks unsafe data from entering SQLite.

Every object destined for durable storage must pass through validate_for_persistence
or sanitize_for_persistence before write.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Set, Tuple


FORBIDDEN_KEY_PATTERNS: Tuple[str, ...] = (
    "raw_image",
    "original_image",
    "screenshot_base64",
    "screenshot",
    "raw_screenshot",
    "image_data",
    "base64",
    "password",
    "passwd",
    "otp",
    "one_time",
    "credit_card",
    "card_number",
    "cardnumber",
    "cvv",
    "cvc",
    "ssn",
    "social_security",
    "face_embedding",
    "face_embeddings",
    "face_crop",
    "embedding",
    "biometric",
    "innerHTML",
    "outerHTML",
    "raw_dom",
    "dom_snapshot",
    "html",
)

# Exact keys always forbidden (case-insensitive)
FORBIDDEN_KEYS: Set[str] = {k.lower() for k in FORBIDDEN_KEY_PATTERNS}

# Nested path segments that indicate image payloads
_IMAGE_VALUE_PREFIXES = ("data:image", "iVBOR", "/9j/")


class PersistencePrivacyError(ValueError):
    """Raised when unsafe data would be persisted."""

    def __init__(self, message: str, *, paths: Optional[List[str]] = None):
        self.paths = paths or []
        super().__init__(message)


def _key_forbidden(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in FORBIDDEN_KEYS:
        return True
    for pat in FORBIDDEN_KEY_PATTERNS:
        if pat in lowered:
            return True
    return False


def _looks_like_image_blob(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) < 32:
        return False
    head = value[:64].strip()
    return any(head.startswith(p) or p in head for p in _IMAGE_VALUE_PREFIXES)


def _looks_like_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) >= 12:
        return True
    return False


def find_forbidden_paths(
    obj: Any, *, path: str = "$"
) -> List[str]:
    """Return dotted paths of forbidden keys / unsafe blobs."""
    found: List[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key = str(k)
            child = f"{path}.{key}"
            if _key_forbidden(key):
                found.append(child)
            elif _looks_like_image_blob(v):
                found.append(child)
            else:
                found.extend(find_forbidden_paths(v, path=child))
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            found.extend(find_forbidden_paths(item, path=f"{path}[{i}]"))
    return found


def validate_for_persistence(obj: Any, *, context: str = "payload") -> None:
    """Reject objects containing forbidden keys or raw image/PII blobs.

    Raises PersistencePrivacyError on violation.
    """
    paths = find_forbidden_paths(obj)
    if paths:
        raise PersistencePrivacyError(
            f"Refusing to persist unsafe data in {context}: {', '.join(paths[:8])}",
            paths=paths,
        )


def sanitize_for_persistence(
    obj: Any,
    *,
    reject: bool = True,
    context: str = "payload",
) -> Any:
    """Deep-copy and strip forbidden keys.

    If reject=True (default), raises when forbidden keys were present rather
    than silently writing a scrubbed version of raw secrets/images.
    Sensitive-looking string values under otherwise-safe keys are redacted.
    """
    paths = find_forbidden_paths(obj)
    if paths and reject:
        raise PersistencePrivacyError(
            f"Refusing to persist unsafe data in {context}: {', '.join(paths[:8])}",
            paths=paths,
        )

    def _walk(node: Any) -> Any:
        if isinstance(node, Mapping):
            out: Dict[str, Any] = {}
            for k, v in node.items():
                key = str(k)
                if _key_forbidden(key):
                    continue  # drop (only reached when reject=False)
                if _looks_like_image_blob(v):
                    continue
                cleaned = _walk(v)
                if isinstance(cleaned, str) and _looks_like_secret(cleaned):
                    out[key] = "[REDACTED]"
                else:
                    out[key] = cleaned
            return out
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, tuple):
            return [_walk(x) for x in node]
        if isinstance(node, str) and _looks_like_image_blob(node):
            return None
        return node

    return _walk(copy.deepcopy(obj))


def assert_safe_metadata(metadata: Optional[Mapping[str, Any]], *, context: str = "metadata") -> Dict[str, Any]:
    """Validate and return a plain dict safe for JSON persistence."""
    data = dict(metadata or {})
    validate_for_persistence(data, context=context)
    return sanitize_for_persistence(data, reject=True, context=context)
