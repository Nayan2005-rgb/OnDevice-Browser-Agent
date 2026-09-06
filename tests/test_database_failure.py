"""Database failure must fail closed — no silent in-memory continuation for sessions."""

from __future__ import annotations

import pytest

from agent.session_manager import SessionManager
from agent.task_plan_registry import TaskPlanRegistry
from storage.database import StorageUnavailableError


def test_storage_unavailable_blocks_session_ops(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    session = sm.create_session(goal="g")
    # Simulate failure on this manager without poisoning the shared Database forever
    sm._fail_closed(RuntimeError("disk I/O error"))
    with pytest.raises(StorageUnavailableError):
        sm.require_storage()
    with pytest.raises(StorageUnavailableError):
        sm.persist_session(session)
    temp_db._available = True
    temp_db._last_error = None


def test_api_returns_503_when_storage_down(session_client, temp_db):
    # Create one session while healthy
    res = session_client.post("/api/agent/sessions", json={"goal": "g", "tab_id": 1})
    assert res.status_code == 200
    from agent.operator_control import reset_operator_control
    from agent.session_manager import get_session_manager, reset_session_manager

    sm = get_session_manager()
    sm._fail_closed(RuntimeError("simulated failure"))
    reset_operator_control()

    listed = session_client.get("/api/agent/sessions")
    assert listed.status_code == 503
    assert listed.get_json()["status"] == "storage_unavailable"
    # Restore so later tests on same fixture are not poisoned
    temp_db._available = True
    temp_db._last_error = None
    sm._storage_failed = False
    reset_session_manager(temp_db)
    reset_operator_control()


def test_operator_pause_fails_closed(temp_db):
    from agent.operator_control import OperatorControl
    from agent.session import SESSION_RUNNING

    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    session = sm.create_session(goal="g")
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    sm._fail_closed(RuntimeError("down"))
    result = op.pause_session(session.session_id)
    assert result["status"] == "storage_unavailable"
    temp_db._available = True
    temp_db._last_error = None
