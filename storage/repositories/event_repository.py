"""Append-only session event timeline repository."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Dict, List, Optional

from privacy.persistence_validator import assert_safe_metadata, validate_for_persistence
from storage.database import Database, StorageUnavailableError


def new_event_id() -> str:
    return f"evt_{secrets.token_hex(8)}"


class EventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append(
        self,
        *,
        session_id: str,
        event_type: str,
        plan_version: Optional[int] = None,
        step_index: Optional[int] = None,
        safe_metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        self.db.require_available()
        meta = assert_safe_metadata(safe_metadata or {}, context="session_event")
        validate_for_persistence(
            {"event_type": event_type, "session_id": session_id},
            context="session_event",
        )
        eid = event_id or new_event_id()
        ts = float(timestamp if timestamp is not None else time.time())
        record = {
            "event_id": eid,
            "session_id": session_id,
            "timestamp": ts,
            "event_type": event_type,
            "plan_version": plan_version,
            "step_index": step_index,
            "safe_metadata": meta,
        }
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO session_events (
                        event_id, session_id, timestamp, event_type,
                        plan_version, step_index, safe_metadata_json
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        eid,
                        session_id,
                        ts,
                        event_type,
                        plan_version,
                        step_index,
                        json.dumps(meta, separators=(",", ":")),
                    ),
                )
        except StorageUnavailableError:
            raise
        except Exception as exc:
            self.db.mark_unavailable(str(exc))
            raise StorageUnavailableError(str(exc)) from exc
        return record

    def list_for_session(
        self, session_id: str, *, limit: int = 500
    ) -> List[Dict[str, Any]]:
        self.db.require_available()
        rows = self.db.fetchall(
            "SELECT * FROM session_events WHERE session_id = ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (session_id, int(limit)),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                meta = json.loads(r["safe_metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                meta = {}
            out.append(
                {
                    "event_id": r["event_id"],
                    "session_id": r["session_id"],
                    "timestamp": r["timestamp"],
                    "event_type": r["event_type"],
                    "plan_version": r["plan_version"],
                    "step_index": r["step_index"],
                    "safe_metadata": meta,
                }
            )
        return out
