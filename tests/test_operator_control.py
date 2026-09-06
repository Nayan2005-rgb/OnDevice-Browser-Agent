"""Operator control layer tests."""

from __future__ import annotations

from agent.operator_control import OperatorControl
from agent.session import SESSION_PAUSED, SESSION_RUNNING
from agent.session_manager import SessionManager
from agent.task_plan_registry import TaskPlanRegistry


def test_pause_idempotent(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    session = sm.create_session(goal="Search")
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)

    r1 = op.pause_session(session.session_id, reason="manual")
    assert r1["status"] == "paused"
    r2 = op.pause_session(session.session_id, reason="manual")
    assert r2["status"] == "paused"
    assert r2.get("idempotent") is True
    assert sm.get_session(session.session_id).status == SESSION_PAUSED


def test_pause_persists_reason(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    session = sm.create_session(goal="g")
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    op.pause_session(session.session_id, reason="coffee_break")
    assert sm.get_session(session.session_id).pause_reason == "coffee_break"
