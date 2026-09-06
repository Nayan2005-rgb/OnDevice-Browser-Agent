"""Session state machine edge cases."""

from __future__ import annotations

import pytest

from agent.session import (
    ALLOWED_SESSION_TRANSITIONS,
    SESSION_CANCELLED,
    SESSION_CREATED,
    SESSION_PAUSED,
    SESSION_RECOVERING,
    SESSION_RUNNING,
    SESSION_WAITING_FOR_BROWSER,
    AgentSession,
    InvalidSessionTransitionError,
)


def test_all_statuses_have_transition_map():
    from agent.session import ALL_SESSION_STATUSES

    for st in ALL_SESSION_STATUSES:
        assert st in ALLOWED_SESSION_TRANSITIONS


def test_pause_from_waiting_confirmation():
    from agent.session import SESSION_WAITING_FOR_CONFIRMATION

    s = AgentSession(session_id="s1", status=SESSION_WAITING_FOR_CONFIRMATION)
    s.transition(SESSION_PAUSED, "operator")
    assert s.status == SESSION_PAUSED


def test_waiting_for_browser_after_running():
    s = AgentSession(session_id="s1", status=SESSION_RUNNING)
    s.transition(SESSION_WAITING_FOR_BROWSER, "restart")
    s.transition(SESSION_RECOVERING, "perception")
    s.transition(SESSION_RUNNING, "ready")


def test_cannot_resume_cancelled_via_transition():
    s = AgentSession(session_id="s1", status=SESSION_CANCELLED)
    with pytest.raises(InvalidSessionTransitionError):
        s.transition(SESSION_RUNNING)
