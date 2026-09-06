"""Resume control tests."""

from __future__ import annotations

from agent.operator_control import OperatorControl
from agent.session import SESSION_CANCELLED, SESSION_PAUSED, SESSION_RUNNING
from agent.session_manager import SessionManager
from agent.task_plan import STEP_PENDING, TaskStep, create_empty_plan
from agent.task_plan_registry import TaskPlanRegistry


def test_resume_requires_fresh_perception(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [TaskStep(step_id="s1", description="x", action_type="click", status=STEP_PENDING)]
    session = sm.create_session(plan=plan)
    session.transition(SESSION_RUNNING, "r")
    session.transition(SESSION_PAUSED, "p")
    sm.persist_session(session)

    result = op.resume_session(session.session_id)
    assert result["status"] == "fresh_perception_required"


def test_resume_idempotent_when_already_running_path(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g", tab_id=1)
    plan.steps = [
        TaskStep(
            step_id="s1",
            description="Click search",
            action_type="click",
            status=STEP_PENDING,
            target_hint="search",
        )
    ]
    session = sm.create_session(plan=plan, tab_id=1)
    session.transition(SESSION_RUNNING, "r")
    session.transition(SESSION_PAUSED, "p")
    sm.persist_session(session)
    sm.persist_plan(plan, session_id=session.session_id)

    page = {
        "url": "https://example.com",
        "elements": [{"role": "button", "label": "search", "text": "search"}],
    }
    r1 = op.resume_session(session.session_id, page=page, execute=False)
    assert r1["status"] in ("resumed", "replan_required", "requires_user_intervention")
    r2 = op.resume_session(session.session_id, page=page, execute=False)
    assert r2["status"] != "cancelled"


def test_cancelled_cannot_resume(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    session = sm.create_session(goal="g")
    session.transition(SESSION_RUNNING, "r")
    session.transition(SESSION_CANCELLED, "c")
    sm.persist_session(session)
    result = op.resume_session(
        session.session_id,
        page={"url": "https://x", "elements": []},
    )
    assert result["status"] == "cancelled"
