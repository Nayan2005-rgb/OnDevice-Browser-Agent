"""Confirmation manager for Milestone 4A / 5B.

Stores pending actions server-side so the frontend cannot modify
selector / coordinates / action type after confirmation.

Default store is in-memory; configure with SqliteConfirmationStore for
durable, restart-safe confirmations (Milestone 5B).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol


DEFAULT_EXPIRES_IN_SECONDS = 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


class ConfirmationStore(Protocol):
    """Interface for confirmation persistence (in-memory now; DB later)."""

    def put(self, confirmation_id: str, record: Dict[str, Any]) -> None: ...

    def get(self, confirmation_id: str) -> Optional[Dict[str, Any]]: ...

    def delete(self, confirmation_id: str) -> None: ...

    def values(self) -> list: ...


class InMemoryConfirmationStore:
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}

    def put(self, confirmation_id: str, record: Dict[str, Any]) -> None:
        self._data[confirmation_id] = record

    def get(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(confirmation_id)

    def delete(self, confirmation_id: str) -> None:
        self._data.pop(confirmation_id, None)

    def values(self) -> list:
        return list(self._data.values())

    def clear(self) -> None:
        self._data.clear()


@dataclass
class PendingConfirmation:
    id: str
    action: Dict[str, Any]
    category: str
    reason: str
    target: Dict[str, Any]
    task: str
    created_at: float
    expires_at: float
    state: str = "pending"  # pending | approved | cancelled | expired | consumed
    consumed: bool = False
    lifecycle_id: Optional[str] = None
    tab_id: Optional[int] = None
    window_id: Optional[int] = None
    execution_id: Optional[str] = None
    performance: Dict[str, float] = field(default_factory=dict)

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def public_view(self, expires_in_seconds: Optional[int] = None) -> Dict[str, Any]:
        """Safe payload for API / UI — never includes secrets or raw PII."""
        remaining = int(max(0, self.expires_at - time.time()))
        if expires_in_seconds is not None:
            remaining = min(remaining, int(expires_in_seconds))
        safe_target = {
            "label": self.target.get("label") or self.target.get("text") or "Unknown",
            "type": self.target.get("type") or "unknown",
            "source": self.target.get("source") or "dom",
        }
        if self.target.get("confidence") is not None:
            safe_target["confidence"] = self.target.get("confidence")
        # Never expose selectors with potential secrets or typed text
        view: Dict[str, Any] = {
            "id": self.id,
            "action": self.action.get("type") if isinstance(self.action, dict) else self.action,
            "category": self.category,
            "target": safe_target,
            "reason": self.reason,
            "expires_in_seconds": remaining,
            "task": self.task,
            "state": self.state,
        }
        # Safe execution context only (no screenshot / PII / DOM values)
        if self.tab_id is not None:
            view["tab_id"] = self.tab_id
        if self.window_id is not None:
            view["window_id"] = self.window_id
        return view

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action": dict(self.action),
            "category": self.category,
            "reason": self.reason,
            "target": dict(self.target),
            "task": self.task,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "state": self.state,
            "consumed": self.consumed,
            "lifecycle_id": self.lifecycle_id,
            "tab_id": self.tab_id,
            "window_id": self.window_id,
            "execution_id": self.execution_id,
            "performance": dict(self.performance),
        }

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "PendingConfirmation":
        tab_id = record.get("tab_id")
        window_id = record.get("window_id")
        return cls(
            id=record["id"],
            action=dict(record.get("action") or {}),
            category=record.get("category") or "unknown",
            reason=record.get("reason") or "",
            target=dict(record.get("target") or {}),
            task=record.get("task") or "",
            created_at=float(record.get("created_at") or time.time()),
            expires_at=float(record.get("expires_at") or time.time()),
            state=record.get("state") or "pending",
            consumed=bool(record.get("consumed")),
            lifecycle_id=record.get("lifecycle_id"),
            tab_id=int(tab_id) if tab_id is not None else None,
            window_id=int(window_id) if window_id is not None else None,
            execution_id=record.get("execution_id"),
            performance=dict(record.get("performance") or {}),
        )


class ConfirmationManager:
    """Create / validate / approve / cancel confirmations."""

    def __init__(
        self,
        store: Optional[InMemoryConfirmationStore] = None,
        default_ttl_seconds: int = DEFAULT_EXPIRES_IN_SECONDS,
    ) -> None:
        self.store = store or InMemoryConfirmationStore()
        self.default_ttl_seconds = int(default_ttl_seconds)
        self._latest_pending_id: Optional[str] = None

    def create(
        self,
        *,
        action: Dict[str, Any],
        category: str,
        reason: str,
        target: Optional[Dict[str, Any]] = None,
        task: str = "",
        expires_in_seconds: Optional[int] = None,
        lifecycle_id: Optional[str] = None,
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a pending confirmation. Returns public confirmation view."""
        t0 = time.perf_counter()
        self.expire_old()

        ttl = int(expires_in_seconds or self.default_ttl_seconds)
        confirmation_id = f"confirm_{secrets.token_hex(8)}"
        now = time.time()

        # Sanitize stored target label (no password values)
        safe_target = dict(target or {})
        label = safe_target.get("label") or safe_target.get("text") or "Unknown"
        if safe_target.get("sensitive") or str(label).upper().find("PASSWORD") >= 0:
            label = "[REDACTED]"
        safe_target["label"] = str(label)[:80]
        safe_target.pop("value", None)
        # Keep action as source of truth (server-side only)
        stored_action = dict(action or {})
        # Strip any accidental secret fields from type actions for storage logs,
        # but keep text for non-sensitive type actions (needed for execution).
        if stored_action.get("type") == "type" and safe_target.get("sensitive"):
            stored_action["text"] = ""

        safe_tab_id = None
        safe_window_id = None
        try:
            if tab_id is not None:
                safe_tab_id = int(tab_id)
        except (TypeError, ValueError):
            safe_tab_id = None
        try:
            if window_id is not None:
                safe_window_id = int(window_id)
        except (TypeError, ValueError):
            safe_window_id = None

        pending = PendingConfirmation(
            id=confirmation_id,
            action=stored_action,
            category=category or "unknown",
            reason=reason or "Confirmation required.",
            target=safe_target,
            task=task or "",
            created_at=now,
            expires_at=now + ttl,
            state="pending",
            consumed=False,
            lifecycle_id=lifecycle_id,
            tab_id=safe_tab_id,
            window_id=safe_window_id,
            performance={"confirmation_creation_ms": _ms(t0)},
        )
        self.store.put(confirmation_id, pending.to_record())
        self._latest_pending_id = confirmation_id

        view = pending.public_view(ttl)
        view["status"] = "requires_confirmation"
        return {
            "status": "requires_confirmation",
            "confirmation": view,
            "performance": pending.performance,
        }

    def get(self, confirmation_id: str) -> Optional[PendingConfirmation]:
        self.expire_old()
        record = self.store.get(confirmation_id)
        if not record:
            return None
        pending = PendingConfirmation.from_record(record)
        if pending.is_expired() and pending.state == "pending":
            pending.state = "expired"
            self.store.put(confirmation_id, pending.to_record())
        return pending

    def get_pending_action(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        """Return the stored action only if confirmation is approved and not consumed."""
        pending = self.get(confirmation_id)
        if not pending:
            return None
        if pending.state != "approved" or pending.consumed:
            return None
        return dict(pending.action)

    def approve(self, confirmation_id: str) -> Dict[str, Any]:
        """Validate and mark confirmation approved (exactly once).

        After approval the stored action is queued for extension claim;
        the confirmation token cannot be approved again.
        """
        self.expire_old()
        record = self.store.get(confirmation_id)
        if not record:
            return {"status": "invalid_confirmation", "confirmation_id": confirmation_id}

        pending = PendingConfirmation.from_record(record)

        if pending.consumed or pending.state in ("consumed", "cancelled"):
            return {"status": "invalid_confirmation", "confirmation_id": confirmation_id}

        if pending.is_expired() or pending.state == "expired":
            pending.state = "expired"
            self.store.put(confirmation_id, pending.to_record())
            return {"status": "expired", "confirmation_id": confirmation_id}

        # Replay: already approved — do not re-approve or re-queue
        if pending.state == "approved":
            return {
                "status": "invalid_confirmation",
                "confirmation_id": confirmation_id,
                "error": "already_approved",
            }

        if pending.state != "pending":
            return {"status": "invalid_confirmation", "confirmation_id": confirmation_id}

        pending.state = "approved"
        self.store.put(confirmation_id, pending.to_record())
        return {
            "status": "approved",
            "confirmation_id": confirmation_id,
            "lifecycle_id": pending.lifecycle_id,
            "tab_id": pending.tab_id,
            "window_id": pending.window_id,
            "action": dict(pending.action),
            "task": pending.task,
            "category": pending.category,
        }

    def consume(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        """Mark approved confirmation as consumed and return stored action.

        Prevents replay: a second consume returns None.
        """
        record = self.store.get(confirmation_id)
        if not record:
            return None
        pending = PendingConfirmation.from_record(record)
        if pending.state != "approved" or pending.consumed:
            return None
        if pending.is_expired():
            pending.state = "expired"
            self.store.put(confirmation_id, pending.to_record())
            return None
        pending.consumed = True
        pending.state = "consumed"
        self.store.put(confirmation_id, pending.to_record())
        return dict(pending.action)

    def cancel(self, confirmation_id: str) -> Dict[str, Any]:
        self.expire_old()
        record = self.store.get(confirmation_id)
        if not record:
            return {"status": "invalid_confirmation", "confirmation_id": confirmation_id}
        pending = PendingConfirmation.from_record(record)
        if pending.consumed or pending.state in ("consumed",):
            return {"status": "invalid_confirmation", "confirmation_id": confirmation_id}
        if pending.is_expired() or pending.state == "expired":
            pending.state = "expired"
            self.store.put(confirmation_id, pending.to_record())
            return {"status": "expired", "confirmation_id": confirmation_id}
        pending.state = "cancelled"
        self.store.put(confirmation_id, pending.to_record())
        if self._latest_pending_id == confirmation_id:
            self._latest_pending_id = None
        return {"status": "cancelled", "confirmation_id": confirmation_id}

    def pending_public(self) -> Optional[Dict[str, Any]]:
        """Return the latest pending (non-expired) confirmation for the UI."""
        self.expire_old()
        # Prefer latest id, else scan
        candidates = []
        if self._latest_pending_id:
            p = self.get(self._latest_pending_id)
            if p and p.state == "pending" and not p.is_expired():
                return p.public_view()
        for record in self.store.values():
            p = PendingConfirmation.from_record(record)
            if p.state == "pending" and not p.is_expired():
                candidates.append(p)
        if not candidates:
            return None
        candidates.sort(key=lambda c: c.created_at, reverse=True)
        return candidates[0].public_view()

    def expire_old(self) -> int:
        """Mark expired pending confirmations. Returns count expired."""
        now = time.time()
        count = 0
        for record in list(self.store.values()):
            p = PendingConfirmation.from_record(record)
            if p.state == "pending" and p.is_expired(now):
                p.state = "expired"
                self.store.put(p.id, p.to_record())
                count += 1
        return count

    def clear(self) -> None:
        if hasattr(self.store, "clear"):
            self.store.clear()
        self._latest_pending_id = None


# Module-level singleton used by routes (tests may replace / clear)
_default_manager: Optional[ConfirmationManager] = None


def get_confirmation_manager() -> ConfirmationManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = ConfirmationManager()
    return _default_manager


def reset_confirmation_manager(
    store: Optional[Any] = None,
) -> ConfirmationManager:
    global _default_manager
    if store is not None:
        _default_manager = ConfirmationManager(store=store)
    else:
        _default_manager = ConfirmationManager()
    return _default_manager


def configure_confirmation_manager(store: Any) -> ConfirmationManager:
    """Wire a durable ConfirmationStore (e.g. SqliteConfirmationStore)."""
    global _default_manager
    _default_manager = ConfirmationManager(store=store)
    return _default_manager
