"""AgentSession model and state machine tests."""

from __future__ import annotations

import pytest

from agent.session import (
    SESSION_CANCELLED,
    SESSION_COMPLETED,
    SESSION_CREATED,
    SESSION_PAUSED,
    SESSION_RUNNING,
    AgentSession,
    InvalidSessionTransitionError,
    new_session_id,
    to_public_session,
)


def test_session_id_format():
    sid = new_session_id()
    assert sid.startswith("sess_")


def test_valid_transitions():
    s = AgentSession(session_id="sess_test", status=SESSION_CREATED)
    s.transition(SESSION_RUNNING, "start")
    assert s.status == SESSION_RUNNING
    s.transition(SESSION_PAUSED, "pause")
    assert s.status == SESSION_PAUSED
    s.transition(SESSION_RUNNING, "resume")
    s.transition(SESSION_COMPLETED, "done")
    assert s.is_terminal()


def test_invalid_transition_raises():
    s = AgentSession(session_id="sess_test", status=SESSION_COMPLETED)
    with pytest.raises(InvalidSessionTransitionError):
        s.transition(SESSION_RUNNING, "nope")


def test_cancelled_is_terminal():
    s = AgentSession(session_id="sess_x", status=SESSION_RUNNING)
    s.transition(SESSION_CANCELLED, "cancel")
    assert s.is_terminal()
    assert not s.is_active()


def test_public_view_has_no_db_path():
    s = AgentSession(session_id="sess_abc", goal="Search laptops", status=SESSION_RUNNING)
    s.performance["database_path"] = "/secret/path.db"
    view = to_public_session(s)
    assert "database_path" not in view.get("performance", {})
    assert view["session_label"].startswith("#")
    assert "password" not in view
