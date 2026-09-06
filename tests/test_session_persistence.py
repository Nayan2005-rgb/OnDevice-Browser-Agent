"""Session persistence survives process-equivalent restarts."""

from __future__ import annotations

from agent.session import SESSION_RUNNING, AgentSession
from agent.session_manager import SessionManager
from agent.task_plan import TaskStep, create_empty_plan
from agent.task_plan import STEP_PENDING, STEP_SUCCESS
from agent.task_plan_registry import TaskPlanRegistry


def test_session_survives_restart(temp_db):
    reg = TaskPlanRegistry()
    sm1 = SessionManager(temp_db, plan_registry=reg)
    plan = create_empty_plan("Search laptops", tab_id=7)
    plan.steps = [
        TaskStep(step_id="s1", description="Type query", action_type="type", status=STEP_SUCCESS),
        TaskStep(step_id="s2", description="Click result", action_type="click", status=STEP_PENDING),
    ]
    plan.current_step_index = 1
    plan.status = "running"
    plan.plan_version = 2
    session = sm1.create_session(goal=plan.goal, plan=plan, tab_id=7)
    session.transition(SESSION_RUNNING, "go")
    sm1.persist_session(session)
    sm1.persist_plan(plan, session_id=session.session_id)
    sid = session.session_id
    pid = plan.plan_id
    assert sm1.plans.get(pid) is not None, "plan missing immediately after persist"

    # Simulate restart: new manager, empty registry, same DB
    reg2 = TaskPlanRegistry()
    sm2 = SessionManager(temp_db, plan_registry=reg2)
    restored = sm2.get_session(sid)
    assert restored is not None
    assert restored.plan_id == pid
    assert restored.current_step_index == 1
    assert restored.plan_version == 2

    plan2 = sm2.load_plan_into_registry(pid)
    assert plan2 is not None
    assert plan2.steps[0].status == STEP_SUCCESS
    assert plan2.steps[1].status == STEP_PENDING
    assert plan2.current_step_index == 1


def test_revision_history_survives(temp_db):
    reg = TaskPlanRegistry()
    sm = SessionManager(temp_db, plan_registry=reg)
    plan = create_empty_plan("Goal")
    plan.revision_history = [
        {
            "version": 1,
            "reason": "cookie_dialog_blocking_target",
            "timestamp": 1.0,
            "changes": ["Inserted safe cookie dismiss step"],
        }
    ]
    session = sm.create_session(plan=plan)
    sm.persist_plan(plan, session_id=session.session_id)

    sm2 = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    loaded = sm2.load_plan_into_registry(plan.plan_id)
    assert loaded.revision_history[0]["reason"] == "cookie_dialog_blocking_target"


def test_event_history_survives(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    session = sm.create_session(goal="g")
    sm.append_event(session.session_id, "step_completed", step_index=0, safe_metadata={"ok": True})
    sm2 = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    events = sm2.timeline(session.session_id)
    types = [e["event_type"] for e in events]
    assert "session_created" in types
    assert "step_completed" in types
