"""Concurrency / exactly-once safety around sessions."""

from __future__ import annotations

from agent.operator_control import OperatorControl
from agent.session import SESSION_RUNNING
from agent.session_manager import SessionManager
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep, create_empty_plan
from agent.task_plan_registry import TaskPlanRegistry


def test_duplicate_resume_does_not_rewind_completed(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g", tab_id=1)
    plan.steps = [
        TaskStep(step_id="s1", description="Done", action_type="type", status=STEP_SUCCESS),
        TaskStep(
            step_id="s2",
            description="Next",
            action_type="click",
            status=STEP_PENDING,
            target_hint="go",
        ),
    ]
    plan.current_step_index = 1
    session = sm.create_session(plan=plan, tab_id=1)
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    sm.persist_plan(plan, session_id=session.session_id)

    page = {"url": "https://x", "elements": [{"label": "go", "role": "button"}]}
    op.resume_session(session.session_id, page=page, execute=False)
    op.resume_session(session.session_id, page=page, execute=False)
    loaded = sm.load_plan_into_registry(plan.plan_id)
    assert loaded.steps[0].status == STEP_SUCCESS
    assert loaded.current_step_index == 1


def test_cancel_wins_over_resume(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [
        TaskStep(step_id="s1", description="x", action_type="click", status=STEP_PENDING)
    ]
    session = sm.create_session(plan=plan)
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    op.cancel_session(session.session_id)
    result = op.resume_session(
        session.session_id, page={"url": "https://x", "elements": []}
    )
    assert result["status"] == "cancelled"
