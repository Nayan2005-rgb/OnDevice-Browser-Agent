"""Approved action delivery queue for Milestone 4A.1 / 5B.

After dashboard confirmation, the stored action waits here until the
originating extension tab claims it exactly once.

When a Database is configured, deliveries are persisted with lease-based
claiming so they survive backend restarts without blind replay.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from storage.database import Database

APPROVED_ACTION_TTL_SECONDS = 60
ACTION_LEASE_SECONDS = 30

# Delivery / claim states
STATUS_APPROVED = "approved"
STATUS_WAITING_FOR_BROWSER = "waiting_for_browser"
STATUS_CLAIMED = "claimed"
STATUS_EXECUTING = "executing"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"
STATUS_RECOVERY_REQUIRED = "recovery_required"

LIVE_STATUSES = frozenset(
    {
        STATUS_APPROVED,
        STATUS_WAITING_FOR_BROWSER,
        STATUS_CLAIMED,
        STATUS_EXECUTING,
    }
)


class ApprovedActionDelivery:
    """Exactly-once delivery of confirmed actions to tabs.

    In-memory by default; optional SQLite write-through via repository.
    """

    def __init__(
        self,
        ttl_seconds: int = APPROVED_ACTION_TTL_SECONDS,
        *,
        lease_seconds: int = ACTION_LEASE_SECONDS,
        db: Optional[Database] = None,
    ) -> None:
        self.ttl_seconds = int(ttl_seconds)
        self.lease_seconds = int(lease_seconds)
        self._lock = threading.Lock()
        self._by_execution: Dict[str, Dict[str, Any]] = {}
        self._by_confirmation: Dict[str, str] = {}
        self._by_tab: Dict[int, str] = {}
        self._db = db
        self._repo = None
        if db is not None:
            from storage.repositories.action_delivery_repository import (
                ActionDeliveryRepository,
            )

            self._repo = ActionDeliveryRepository(db)

    def create(
        self,
        *,
        confirmation_id: str,
        action: Dict[str, Any],
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
        lifecycle_id: Optional[str] = None,
        task: str = "",
        category: str = "",
        session_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register an approved action for claim-by-tab delivery."""
        t0 = time.perf_counter()
        with self._lock:
            self._expire_unlocked()
            # One live delivery per confirmation
            existing_eid = self._by_confirmation.get(confirmation_id)
            if existing_eid and existing_eid in self._by_execution:
                existing = self._by_execution[existing_eid]
                if existing["status"] in LIVE_STATUSES:
                    return {
                        "status": "already_queued",
                        "execution_id": existing_eid,
                        "record": self._public_record(existing),
                    }

            execution_id = f"exec_{secrets.token_hex(8)}"
            now = time.time()
            record: Dict[str, Any] = {
                "execution_id": execution_id,
                "confirmation_id": confirmation_id,
                "lifecycle_id": lifecycle_id,
                "session_id": session_id,
                "plan_id": plan_id,
                "step_id": step_id,
                "tab_id": int(tab_id) if tab_id is not None else None,
                "window_id": int(window_id) if window_id is not None else None,
                "action": dict(action or {}),
                "task": task or "",
                "category": category or "",
                "status": STATUS_WAITING_FOR_BROWSER,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
                "claimed_at": None,
                "lease_until": None,
                "executed_at": None,
                "recovery_reason": None,
                "performance": {
                    "confirmation_approval_ms": round(
                        (time.perf_counter() - t0) * 1000, 3
                    ),
                },
            }
            # Also expose approved for legacy status checks
            record["status"] = STATUS_APPROVED
            self._by_execution[execution_id] = record
            self._by_confirmation[confirmation_id] = execution_id
            if tab_id is not None:
                self._by_tab[int(tab_id)] = execution_id
            self._persist_unlocked(record)
            # Mark waiting_for_browser after persist for durable queue semantics
            record["status"] = STATUS_WAITING_FOR_BROWSER
            self._persist_unlocked(record)
            # Public API still reports approved for existing clients
            return {
                "status": STATUS_APPROVED,
                "execution_id": execution_id,
                "record": self._public_record(
                    {**record, "status": STATUS_APPROVED}
                ),
            }

    def claim_for_tab(self, tab_id: Optional[int]) -> Dict[str, Any]:
        """Atomically claim an approved action for the given tab.

        First successful claim returns the action; subsequent polls return none.
        Sets a lease; if not reported before lease expiry → recovery_required.
        """
        t0 = time.perf_counter()
        if tab_id is None:
            return {"status": "none"}

        try:
            tab_key = int(tab_id)
        except (TypeError, ValueError):
            return {"status": "none"}

        with self._lock:
            self._expire_unlocked()
            self._expire_leases_unlocked()

            # Prefer durable atomic claim when repository is available
            if self._repo is not None:
                claimed = self._repo.claim_atomic(
                    tab_id=tab_key, lease_seconds=self.lease_seconds
                )
                if claimed:
                    # Sync into memory
                    eid = claimed["execution_id"]
                    self._by_execution[eid] = claimed
                    self._by_confirmation[claimed.get("confirmation_id") or ""] = eid
                    self._by_tab.pop(tab_key, None)
                    action = dict(claimed.get("action") or {})
                    return {
                        "status": "approved",
                        "execution_id": eid,
                        "confirmation_id": claimed.get("confirmation_id"),
                        "lifecycle_id": claimed.get("lifecycle_id"),
                        "action": action,
                        "task": claimed.get("task") or "",
                    }

            execution_id = self._by_tab.get(tab_key)
            if not execution_id:
                execution_id = self._find_claimable_for_tab_unlocked(tab_key)
            if not execution_id:
                return {"status": "none"}

            record = self._by_execution.get(execution_id)
            if not record:
                self._by_tab.pop(tab_key, None)
                return {"status": "none"}

            if record.get("tab_id") is not None and int(record["tab_id"]) != tab_key:
                return {"status": "none"}

            if record["status"] not in (
                STATUS_APPROVED,
                STATUS_WAITING_FOR_BROWSER,
            ):
                return {"status": "none"}

            if time.time() >= float(record["expires_at"]):
                record["status"] = STATUS_EXPIRED
                self._persist_unlocked(record)
                return {"status": "none"}

            now = time.time()
            record["status"] = STATUS_CLAIMED
            record["claimed_at"] = now
            record["lease_until"] = now + self.lease_seconds
            claim_ms = round((time.perf_counter() - t0) * 1000, 3)
            record["performance"]["action_claim_ms"] = claim_ms
            if record.get("created_at"):
                record["performance"]["approved_action_wait_ms"] = round(
                    (record["claimed_at"] - float(record["created_at"])) * 1000, 3
                )
            self._by_tab.pop(tab_key, None)
            self._persist_unlocked(record)

            action = dict(record["action"])
            return {
                "status": "approved",
                "execution_id": record["execution_id"],
                "confirmation_id": record["confirmation_id"],
                "lifecycle_id": record.get("lifecycle_id"),
                "action": action,
                "task": record.get("task") or "",
            }

    def mark_executing(self, execution_id: str) -> bool:
        with self._lock:
            record = self._by_execution.get(execution_id)
            if not record or record["status"] != STATUS_CLAIMED:
                # Try hydrate from DB
                if self._repo is not None and not record:
                    loaded = self._repo.get(execution_id)
                    if loaded:
                        self._by_execution[execution_id] = loaded
                        record = loaded
                if not record or record["status"] != STATUS_CLAIMED:
                    return False
            record["status"] = STATUS_EXECUTING
            # Extend lease slightly while executing
            if record.get("lease_until"):
                record["lease_until"] = max(
                    float(record["lease_until"]),
                    time.time() + self.lease_seconds,
                )
            self._persist_unlocked(record)
            return True

    def report_execution(
        self,
        *,
        execution_id: str,
        confirmation_id: str,
        tab_id: Optional[int],
        status: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Correlate and accept an execution report exactly once."""
        with self._lock:
            self._expire_unlocked()
            record = self._by_execution.get(execution_id)
            if not record and self._repo is not None:
                loaded = self._repo.get(execution_id)
                if loaded:
                    self._by_execution[execution_id] = loaded
                    record = loaded
            if not record:
                return {"ok": False, "error": "unknown_execution_id"}

            if record["confirmation_id"] != confirmation_id:
                return {"ok": False, "error": "confirmation_mismatch"}

            expected_tab = record.get("tab_id")
            if expected_tab is not None:
                try:
                    if tab_id is None or int(tab_id) != int(expected_tab):
                        return {"ok": False, "error": "tab_mismatch"}
                except (TypeError, ValueError):
                    return {"ok": False, "error": "tab_mismatch"}

            if record["status"] in (
                STATUS_EXECUTED,
                STATUS_FAILED,
            ):
                return {"ok": False, "error": "duplicate_execution"}

            if record["status"] == STATUS_RECOVERY_REQUIRED:
                return {"ok": False, "error": "recovery_required"}

            if record["status"] not in (STATUS_CLAIMED, STATUS_EXECUTING):
                return {"ok": False, "error": "not_claimed"}

            if record["status"] == STATUS_EXPIRED or time.time() >= float(
                record["expires_at"]
            ):
                if status == "success":
                    record["status"] = STATUS_EXPIRED
                    self._persist_unlocked(record)
                    return {"ok": False, "error": "expired"}

            ok_status = status in ("success", "ok", "executed")
            record["status"] = STATUS_EXECUTED if ok_status else STATUS_FAILED
            record["executed_at"] = time.time()
            record["result"] = self._sanitize_result(result)
            if record.get("claimed_at"):
                record["performance"]["extension_execution_ms"] = round(
                    (record["executed_at"] - float(record["claimed_at"])) * 1000, 3
                )
            self._persist_unlocked(record)
            return {
                "ok": True,
                "execution_id": execution_id,
                "confirmation_id": confirmation_id,
                "status": record["status"],
                "lifecycle_id": record.get("lifecycle_id"),
                "performance": dict(record.get("performance") or {}),
                "record": self._public_record(record),
            }

    def cancel_for_confirmation(self, confirmation_id: str) -> None:
        with self._lock:
            eid = self._by_confirmation.get(confirmation_id)
            if not eid and self._repo is not None:
                loaded = self._repo.get_by_confirmation(confirmation_id)
                if loaded:
                    eid = loaded["execution_id"]
                    self._by_execution[eid] = loaded
                    self._by_confirmation[confirmation_id] = eid
            if not eid:
                return
            record = self._by_execution.get(eid)
            if not record:
                return
            if record["status"] in (
                STATUS_APPROVED,
                STATUS_WAITING_FOR_BROWSER,
                STATUS_CLAIMED,
                STATUS_RECOVERY_REQUIRED,
            ):
                record["status"] = STATUS_CANCELLED
                tab = record.get("tab_id")
                if tab is not None:
                    self._by_tab.pop(int(tab), None)
                self._persist_unlocked(record)

    def mark_recovery_required(
        self, execution_id: str, *, reason: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._by_execution.get(execution_id)
            if not record and self._repo is not None:
                record = self._repo.get(execution_id)
                if record:
                    self._by_execution[execution_id] = record
            if not record:
                return None
            if record["status"] not in LIVE_STATUSES | {STATUS_RECOVERY_REQUIRED}:
                return dict(record)
            record["status"] = STATUS_RECOVERY_REQUIRED
            record["recovery_reason"] = reason
            tab = record.get("tab_id")
            if tab is not None:
                self._by_tab.pop(int(tab), None)
            self._persist_unlocked(record)
            return dict(record)

    def expire_leases(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._expire_leases_unlocked()

    def list_recovery_required(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._expire_leases_unlocked()
            out = [
                self._public_record(r)
                for r in self._by_execution.values()
                if r.get("status") == STATUS_RECOVERY_REQUIRED
            ]
            if self._repo is not None:
                for r in self._repo.list_by_status([STATUS_RECOVERY_REQUIRED]):
                    if r["execution_id"] not in self._by_execution:
                        self._by_execution[r["execution_id"]] = r
                        out.append(self._public_record(r))
            return out

    def list_pending_for_tab(self, tab_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            self._expire_unlocked()
            self._expire_leases_unlocked()
            out = []
            for r in self._by_execution.values():
                if r.get("tab_id") is not None and int(r["tab_id"]) == int(tab_id):
                    if r["status"] in (
                        STATUS_APPROVED,
                        STATUS_WAITING_FOR_BROWSER,
                        STATUS_RECOVERY_REQUIRED,
                    ):
                        out.append(self._public_record(r))
            if self._repo is not None:
                for r in self._repo.list_for_tab(
                    int(tab_id),
                    statuses=(
                        STATUS_APPROVED,
                        STATUS_WAITING_FOR_BROWSER,
                        STATUS_RECOVERY_REQUIRED,
                    ),
                ):
                    self._by_execution[r["execution_id"]] = r
            return out

    def get(self, execution_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._expire_unlocked()
            self._expire_leases_unlocked()
            record = self._by_execution.get(execution_id)
            if not record and self._repo is not None:
                record = self._repo.get(execution_id)
                if record:
                    self._by_execution[execution_id] = record
            return dict(record) if record else None

    def get_by_confirmation(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._expire_unlocked()
            eid = self._by_confirmation.get(confirmation_id)
            if eid:
                record = self._by_execution.get(eid)
                if record:
                    return dict(record)
            if self._repo is not None:
                record = self._repo.get_by_confirmation(confirmation_id)
                if record:
                    self._by_execution[record["execution_id"]] = record
                    self._by_confirmation[confirmation_id] = record["execution_id"]
                    return dict(record)
            return None

    def peek_for_tab(self, tab_id: int) -> Optional[Dict[str, Any]]:
        """Non-claiming lookup (tests / diagnostics)."""
        with self._lock:
            self._expire_unlocked()
            eid = self._by_tab.get(int(tab_id)) or self._find_claimable_for_tab_unlocked(
                int(tab_id)
            )
            if not eid:
                return None
            record = self._by_execution.get(eid)
            return self._public_record(record) if record else None

    def hydrate(self) -> Dict[str, Any]:
        """Load unfinished deliveries from SQLite after restart."""
        t0 = time.perf_counter()
        restored = 0
        recovery_marked = 0
        if self._repo is None:
            return {
                "restored": 0,
                "recovery_marked": 0,
                "crash_recovery_ms": 0.0,
            }
        with self._lock:
            rows = self._repo.list_by_status(
                [
                    STATUS_APPROVED,
                    STATUS_WAITING_FOR_BROWSER,
                    STATUS_CLAIMED,
                    STATUS_EXECUTING,
                    STATUS_RECOVERY_REQUIRED,
                ],
                limit=500,
            )
            now = time.time()
            for row in rows:
                status = row.get("status")
                # Uncertain mid-flight states → recovery_required (never assume not executed)
                if status in (STATUS_CLAIMED, STATUS_EXECUTING):
                    row["status"] = STATUS_RECOVERY_REQUIRED
                    row["recovery_reason"] = (
                        "server_restart_during_execution"
                        if status == STATUS_EXECUTING
                        else "server_restart_during_claim"
                    )
                    self._repo.upsert(row)
                    recovery_marked += 1
                elif status in (STATUS_APPROVED, STATUS_WAITING_FOR_BROWSER):
                    # Approved but not claimed — restore as waiting; do not auto-execute
                    if now >= float(row.get("expires_at") or 0):
                        row["status"] = STATUS_EXPIRED
                        self._repo.upsert(row)
                    else:
                        row["status"] = STATUS_WAITING_FOR_BROWSER
                        self._repo.upsert(row)
                        if row.get("tab_id") is not None:
                            self._by_tab[int(row["tab_id"])] = row["execution_id"]
                eid = row["execution_id"]
                self._by_execution[eid] = row
                if row.get("confirmation_id"):
                    self._by_confirmation[row["confirmation_id"]] = eid
                restored += 1
            # Also expire any remaining leases
            recovery_marked += len(self._expire_leases_unlocked())
        return {
            "restored": restored,
            "recovery_marked": recovery_marked,
            "crash_recovery_ms": round((time.perf_counter() - t0) * 1000, 3),
        }

    def clear(self) -> None:
        with self._lock:
            self._by_execution.clear()
            self._by_confirmation.clear()
            self._by_tab.clear()
            if self._repo is not None:
                try:
                    with self._db.transaction() as conn:  # type: ignore[union-attr]
                        conn.execute("DELETE FROM action_deliveries")
                except Exception:
                    pass

    def _persist_unlocked(self, record: Dict[str, Any]) -> None:
        if self._repo is None:
            return
        try:
            saved = self._repo.upsert(record)
            if saved.get("performance"):
                record.setdefault("performance", {}).update(saved["performance"])
        except Exception:
            # Fail closed for durable mode: mark unavailable via repo/db
            raise

    def _find_claimable_for_tab_unlocked(self, tab_id: int) -> Optional[str]:
        for eid, record in self._by_execution.items():
            if (
                record.get("tab_id") is not None
                and int(record["tab_id"]) == tab_id
                and record["status"]
                in (STATUS_APPROVED, STATUS_WAITING_FOR_BROWSER)
            ):
                return eid
        return None

    def _expire_unlocked(self) -> int:
        now = time.time()
        count = 0
        for record in list(self._by_execution.values()):
            if (
                record["status"] in (STATUS_APPROVED, STATUS_WAITING_FOR_BROWSER)
                and now >= float(record["expires_at"])
            ):
                record["status"] = STATUS_EXPIRED
                tab = record.get("tab_id")
                if tab is not None:
                    self._by_tab.pop(int(tab), None)
                self._persist_unlocked(record)
                count += 1
        return count

    def _expire_leases_unlocked(self) -> List[Dict[str, Any]]:
        now = time.time()
        changed: List[Dict[str, Any]] = []
        if self._repo is not None:
            for rec in self._repo.expire_leases(now=now):
                self._by_execution[rec["execution_id"]] = {
                    **self._by_execution.get(rec["execution_id"], {}),
                    **rec,
                }
                changed.append(rec)
        for record in list(self._by_execution.values()):
            if record["status"] != STATUS_CLAIMED:
                continue
            lease = record.get("lease_until")
            if lease is None:
                continue
            if now >= float(lease):
                t0 = time.perf_counter()
                record["status"] = STATUS_RECOVERY_REQUIRED
                record["recovery_reason"] = "lease_expired"
                record.setdefault("performance", {})["lease_validation_ms"] = round(
                    (time.perf_counter() - t0) * 1000, 3
                )
                tab = record.get("tab_id")
                if tab is not None:
                    self._by_tab.pop(int(tab), None)
                self._persist_unlocked(record)
                changed.append(dict(record))
        return changed

    @staticmethod
    def _sanitize_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(result, dict):
            return None
        return {
            "strategy": result.get("strategy"),
            "target_found": result.get("target_found"),
            "status": result.get("status")
            or ("success" if result.get("success") else None),
            "element_tag": result.get("element_tag")
            or (result.get("target") or {}).get("tag"),
            "execution_time_ms": result.get("execution_time_ms"),
            "reason": result.get("reason") or result.get("error"),
        }

    @staticmethod
    def _public_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Safe metadata — action details excluded from general public views."""
        return {
            "execution_id": record.get("execution_id"),
            "confirmation_id": record.get("confirmation_id"),
            "lifecycle_id": record.get("lifecycle_id"),
            "session_id": record.get("session_id"),
            "plan_id": record.get("plan_id"),
            "step_id": record.get("step_id"),
            "tab_id": record.get("tab_id"),
            "window_id": record.get("window_id"),
            "status": record.get("status"),
            "task": record.get("task"),
            "category": record.get("category"),
            "expires_at": record.get("expires_at"),
            "claimed_at": record.get("claimed_at"),
            "lease_until": record.get("lease_until"),
            "recovery_reason": record.get("recovery_reason"),
            "performance": dict(record.get("performance") or {}),
            "action_type": (record.get("action") or {}).get("type")
            or record.get("action_type"),
        }


_delivery: Optional[ApprovedActionDelivery] = None


def get_approved_action_delivery() -> ApprovedActionDelivery:
    global _delivery
    if _delivery is None:
        _delivery = ApprovedActionDelivery()
    return _delivery


def reset_approved_action_delivery(
    db: Optional[Database] = None,
) -> ApprovedActionDelivery:
    global _delivery
    _delivery = ApprovedActionDelivery(db=db)
    return _delivery


def configure_approved_action_delivery(
    db: Database,
) -> ApprovedActionDelivery:
    global _delivery
    _delivery = ApprovedActionDelivery(db=db)
    return _delivery
