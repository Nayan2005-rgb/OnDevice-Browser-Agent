"""Privacy-safe page state snapshots and deterministic signatures for Milestone 4C.

Never stores raw screenshots, DOM/HTML, passwords, PII values, face crops,
or embeddings — only sanitized structure already past the privacy boundary.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


# Keys that must never appear in stored page state
_FORBIDDEN_KEYS = frozenset(
    {
        "screenshot",
        "raw_screenshot",
        "original_image",
        "raw_image",
        "base64",
        "image_data",
        "html",
        "innerHTML",
        "outerHTML",
        "password",
        "otp",
        "ssn",
        "credit_card",
        "card_number",
        "cvv",
        "face_crop",
        "embedding",
        "face_embeddings",
        "email_value",
        "phone_value",
    }
)

_SENSITIVE_LABEL_MARKERS = (
    "[PASSWORD",
    "[EMAIL",
    "[PHONE",
    "[SSN",
    "[CARD",
    "[REDACTED]",
)


def _safe_str(value: Any, max_len: int = 60) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    upper = s.upper()
    if any(tok in upper for tok in _SENSITIVE_LABEL_MARKERS):
        return "[REDACTED]"
    return s[:max_len]


def _url_signature(url: str) -> Optional[str]:
    """Stable, privacy-safe URL fingerprint (scheme + host + path only)."""
    text = _safe_str(url, 300)
    if not text:
        return None
    # Drop query/fragment which may contain tokens
    for sep in ("?", "#"):
        if sep in text:
            text = text.split(sep, 1)[0]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _sanitize_interactive(elements: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for el in elements or []:
        if not isinstance(el, Mapping):
            continue
        sensitive = bool(el.get("sensitive"))
        role = _safe_str(el.get("role") or el.get("tag") or el.get("type"), 40).lower()
        label = _safe_str(
            el.get("label") or el.get("text") or el.get("ariaLabel") or el.get("name"),
            40,
        )
        if sensitive:
            label = "[REDACTED]"
        el_type = _safe_str(el.get("type") or el.get("tag"), 30).lower()
        interactive = el.get("interactive")
        if interactive is None:
            interactive = role in (
                "button",
                "link",
                "textbox",
                "searchbox",
                "checkbox",
                "radio",
                "combobox",
                "menuitem",
                "tab",
                "switch",
            ) or el_type in ("button", "a", "input", "select", "textarea")
        if not interactive and role not in ("dialog", "alertdialog"):
            # Keep dialogs even if not marked interactive
            if el_type not in ("button", "a", "input", "select", "textarea", "dialog"):
                continue
        item = {
            "role": role or el_type or "unknown",
            "label": label,
            "type": el_type or "unknown",
            "sensitive": sensitive,
            "interactive": bool(interactive),
        }
        out.append(item)
        if len(out) >= 80:
            break
    return out


def _role_distribution(elements: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for el in elements:
        role = str(el.get("role") or el.get("type") or "unknown").lower() or "unknown"
        dist[role] = dist.get(role, 0) + 1
    return dict(sorted(dist.items()))


def _detect_dialogs(elements: Sequence[Mapping[str, Any]], visual_summary: Mapping[str, Any]) -> tuple:
    count = 0
    for el in elements:
        role = str(el.get("role") or "").lower()
        el_type = str(el.get("type") or "").lower()
        if role in ("dialog", "alertdialog") or el_type in ("dialog", "modal"):
            count += 1
    summary_dialogs = int(visual_summary.get("dialogs") or 0)
    count = max(count, summary_dialogs)
    return bool(count > 0), count


def _visual_summary_from_map(visual_ui_map: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(visual_ui_map, Mapping):
        return {}
    summary = visual_ui_map.get("summary")
    if not isinstance(summary, Mapping):
        # Derive minimal counts from elements
        elements = visual_ui_map.get("elements") or []
        derived = {
            "total_elements": len(elements),
            "buttons": 0,
            "inputs": 0,
            "links": 0,
            "dialogs": 0,
            "containers": 0,
            "vision_only_elements": 0,
            "dom_only_elements": 0,
            "fused_elements": 0,
        }
        for el in elements:
            if not isinstance(el, Mapping):
                continue
            t = str(el.get("type") or "").lower()
            src = str(el.get("source") or "").lower()
            if t == "button":
                derived["buttons"] += 1
            elif t in ("input", "textbox", "searchbox"):
                derived["inputs"] += 1
            elif t == "link":
                derived["links"] += 1
            elif t in ("dialog", "modal"):
                derived["dialogs"] += 1
            elif t == "container":
                derived["containers"] += 1
            if src == "vision":
                derived["vision_only_elements"] += 1
            elif src == "dom":
                derived["dom_only_elements"] += 1
            elif "dom" in src and "vision" in src:
                derived["fused_elements"] += 1
        summary = derived
    # Only keep numeric / safe summary fields
    safe: Dict[str, Any] = {}
    for key, val in summary.items():
        if isinstance(val, (int, float, bool)):
            safe[str(key)] = val
        elif isinstance(val, str) and len(val) < 40:
            safe[str(key)] = val
    layout = visual_ui_map.get("layout")
    if isinstance(layout, Mapping):
        groups = layout.get("groups") or []
        safe["layout_group_count"] = len(groups) if isinstance(groups, list) else 0
    return safe


def _strip_forbidden(obj: Any) -> Any:
    """Recursively drop forbidden keys from nested dicts."""
    if isinstance(obj, Mapping):
        return {
            k: _strip_forbidden(v)
            for k, v in obj.items()
            if str(k).lower() not in _FORBIDDEN_KEYS
            and not str(k).lower().endswith("_base64")
        }
    if isinstance(obj, list):
        return [_strip_forbidden(x) for x in obj[:80]]
    return obj


@dataclass
class PageState:
    """Privacy-safe structural snapshot of the current page."""

    generation: int = 0
    url_signature: Optional[str] = None
    page_signature: str = ""
    interactive_elements: List[Dict[str, Any]] = field(default_factory=list)
    element_count: int = 0
    dialog_present: bool = False
    dialog_count: int = 0
    visual_summary: Dict[str, Any] = field(default_factory=dict)
    role_distribution: Dict[str, int] = field(default_factory=dict)
    layout_group_count: int = 0
    timestamp: float = field(default_factory=time.time)
    build_ms: float = 0.0
    signature_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return _strip_forbidden(data)

    def public_view(self) -> Dict[str, Any]:
        """Dashboard-safe projection (no element labels that might leak)."""
        return {
            "generation": self.generation,
            "url_signature": self.url_signature,
            "page_signature": self.page_signature,
            "element_count": self.element_count,
            "dialog_present": self.dialog_present,
            "dialog_count": self.dialog_count,
            "visual_summary": dict(self.visual_summary),
            "role_distribution": dict(self.role_distribution),
            "layout_group_count": self.layout_group_count,
            "timestamp": self.timestamp,
            "build_ms": self.build_ms,
            "signature_ms": self.signature_ms,
        }


def compute_page_signature(
    *,
    url_signature: Optional[str] = None,
    interactive_elements: Sequence[Mapping[str, Any]] = (),
    dialog_present: bool = False,
    dialog_count: int = 0,
    visual_summary: Optional[Mapping[str, Any]] = None,
    role_distribution: Optional[Mapping[str, int]] = None,
) -> str:
    """Deterministic SHA-256 signature over normalized safe structure."""
    roles = [
        str(el.get("role") or "unknown").lower()
        for el in interactive_elements
    ]
    labels = [
        _safe_str(el.get("label"), 40)
        for el in interactive_elements
        if not el.get("sensitive")
    ]
    labels = [l for l in labels if l and l != "[REDACTED]"]
    labels.sort()
    roles_sorted = sorted(roles)
    payload = {
        "url_sig": url_signature or "",
        "roles": roles_sorted,
        "labels": labels[:40],
        "count": len(interactive_elements),
        "dialog": bool(dialog_present),
        "dialog_count": int(dialog_count),
        "role_dist": dict(sorted((role_distribution or {}).items())),
        "visual": {
            k: visual_summary[k]
            for k in sorted((visual_summary or {}).keys())
            if isinstance((visual_summary or {}).get(k), (int, float, bool))
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_page_state(
    *,
    observation: Optional[Mapping[str, Any]] = None,
    page: Optional[Mapping[str, Any]] = None,
    safe_page_state: Optional[Mapping[str, Any]] = None,
    visual_ui_map: Optional[Mapping[str, Any]] = None,
    generation: int = 0,
) -> PageState:
    """Build a PageState from sanitized perception / page payloads."""
    t0 = time.perf_counter()
    observation = observation or {}
    page = page or {}
    safe = safe_page_state or {}

    url = str(
        safe.get("url")
        or page.get("url")
        or (observation.get("page") or {}).get("url")
        or ""
    )
    url_sig = _url_signature(url)

    vmap = visual_ui_map
    if vmap is None:
        vmap = observation.get("visual_ui_map")
    visual_summary = _visual_summary_from_map(vmap if isinstance(vmap, Mapping) else None)

    raw_elements = (
        safe.get("visible_elements")
        or safe.get("elements")
        or observation.get("ui_elements")
        or page.get("elements")
        or []
    )
    interactive = _sanitize_interactive(raw_elements)
    # Also fold visual map interactive elements (structure only)
    if isinstance(vmap, Mapping):
        for el in vmap.get("elements") or []:
            if not isinstance(el, Mapping):
                continue
            if not el.get("interactive") and str(el.get("type") or "").lower() not in (
                "dialog",
                "button",
                "link",
                "input",
            ):
                continue
            interactive.append(
                {
                    "role": _safe_str(el.get("role") or el.get("type"), 40).lower()
                    or "unknown",
                    "label": "[REDACTED]"
                    if el.get("sensitive")
                    else _safe_str(el.get("text") or el.get("label"), 40),
                    "type": _safe_str(el.get("type"), 30).lower() or "unknown",
                    "sensitive": bool(el.get("sensitive")),
                    "interactive": True,
                }
            )
            if len(interactive) >= 80:
                break

    role_dist = _role_distribution(interactive)
    dialog_present_hint = safe.get("dialog_present")
    dialog_present, dialog_count = _detect_dialogs(interactive, visual_summary)
    if dialog_present_hint is not None:
        dialog_present = bool(dialog_present_hint) or dialog_present
        if dialog_present and dialog_count == 0:
            dialog_count = 1

    layout_group_count = int(visual_summary.get("layout_group_count") or 0)

    t_sig = time.perf_counter()
    signature = compute_page_signature(
        url_signature=url_sig,
        interactive_elements=interactive,
        dialog_present=dialog_present,
        dialog_count=dialog_count,
        visual_summary=visual_summary,
        role_distribution=role_dist,
    )
    signature_ms = round((time.perf_counter() - t_sig) * 1000, 3)
    build_ms = round((time.perf_counter() - t0) * 1000, 3)

    state = PageState(
        generation=int(generation or 0),
        url_signature=url_sig,
        page_signature=signature,
        interactive_elements=interactive,
        element_count=len(interactive),
        dialog_present=dialog_present,
        dialog_count=dialog_count,
        visual_summary=visual_summary,
        role_distribution=role_dist,
        layout_group_count=layout_group_count,
        timestamp=time.time(),
        build_ms=build_ms,
        signature_ms=signature_ms,
    )
    # Final privacy scrub
    scrubbed = _strip_forbidden(state.to_dict())
    return PageState(
        generation=scrubbed["generation"],
        url_signature=scrubbed.get("url_signature"),
        page_signature=scrubbed.get("page_signature") or "",
        interactive_elements=list(scrubbed.get("interactive_elements") or []),
        element_count=int(scrubbed.get("element_count") or 0),
        dialog_present=bool(scrubbed.get("dialog_present")),
        dialog_count=int(scrubbed.get("dialog_count") or 0),
        visual_summary=dict(scrubbed.get("visual_summary") or {}),
        role_distribution=dict(scrubbed.get("role_distribution") or {}),
        layout_group_count=int(scrubbed.get("layout_group_count") or 0),
        timestamp=float(scrubbed.get("timestamp") or time.time()),
        build_ms=float(scrubbed.get("build_ms") or 0),
        signature_ms=float(scrubbed.get("signature_ms") or 0),
    )


def page_state_from_dict(data: Optional[Mapping[str, Any]]) -> Optional[PageState]:
    if not isinstance(data, Mapping) or not data:
        return None
    return PageState(
        generation=int(data.get("generation") or 0),
        url_signature=data.get("url_signature"),
        page_signature=str(data.get("page_signature") or ""),
        interactive_elements=list(data.get("interactive_elements") or []),
        element_count=int(data.get("element_count") or 0),
        dialog_present=bool(data.get("dialog_present")),
        dialog_count=int(data.get("dialog_count") or 0),
        visual_summary=dict(data.get("visual_summary") or {}),
        role_distribution=dict(data.get("role_distribution") or {}),
        layout_group_count=int(data.get("layout_group_count") or 0),
        timestamp=float(data.get("timestamp") or time.time()),
        build_ms=float(data.get("build_ms") or 0),
        signature_ms=float(data.get("signature_ms") or 0),
    )
