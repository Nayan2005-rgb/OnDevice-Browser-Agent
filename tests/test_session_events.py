"""Append-only session event timeline tests."""

from __future__ import annotations

from agent.session_manager import SessionManager
from agent.task_plan_registry import TaskPlanRegistry


def test_events_append_only(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    session = sm.create_session(goal="g")
    sm.append_event(session.session_id, "paused", safe_metadata={"reason": "x"})
    sm.append_event(session.session_id, "resumed", safe_metadata={"reason": "y"})
    events = sm.timeline(session.session_id)
    assert len(events) >= 3
    # timestamps non-decreasing
    ts = [e["timestamp"] for e in events]
    assert ts == sorted(ts)


def test_events_public_safe(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    session = sm.create_session(goal="g")
    sm.append_event(
        session.session_id,
        "step_completed",
        safe_metadata={"description": "Clicked search"},
    )
    ev = sm.timeline(session.session_id)[-1]
    assert "safe_metadata" in ev
    assert "password" not in str(ev).lower()
