"""Cancel control tests."""

from __future__ import annotations

from agent.operator_control import OperatorControl
from agent.session import SESSION_CANCELLED, SESSION_PAUSED, SESSION_RUNNING
from agent.session_manager import SessionManager
from agent.task_plan import STEP_PENDING, TaskStep, create_empty_plan
from agent.task_plan_registry import TaskPlanRegistry


def test_cancel_idempotent(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [TaskStep(step_id="s1", description="x", action_type="click", status=STEP_PENDING)]
    session = sm.create_session(plan=plan)
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)

    r1 = op.cancel_session(session.session_id)
    assert r1["status"] == "cancelled"
    r2 = op.cancel_session(session.session_id)
    assert r2["status"] == "cancelled"
    assert r2.get("idempotent") is True
    s = sm.get_session(session.session_id)
    assert s.status == SESSION_CANCELLED


def test_cancel_prevents_future_execution(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [TaskStep(step_id="s1", description="x", action_type="click", status=STEP_PENDING)]
    session = sm.create_session(plan=plan)
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    op.cancel_session(session.session_id)

    from agent.task_orchestrator import TaskOrchestrator

    orch = TaskOrchestrator(registry=sm.plan_registry)
    result = orch.resolve_current_step(
        plan.plan_id,
        page={"url": "https://x", "elements": [{"label": "x"}]},
    )
    assert result["status"] == "cancelled" or (result.get("plan") or {}).get(
        "status"
    ) == "cancelled"
