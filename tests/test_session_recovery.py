"""Session recovery after simulated restart."""

from __future__ import annotations

from agent.session import SESSION_RUNNING, SESSION_WAITING_FOR_BROWSER
from agent.session_manager import SessionManager
from agent.session_recovery import SessionRecovery
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep, create_empty_plan
from agent.task_plan_registry import TaskPlanRegistry


def _page(elements, url="https://shop.example/search"):
    return {
        "url": url,
        "title": "Search",
        "elements": elements,
    }


def test_hydrate_marks_waiting_for_browser(temp_db):
    reg = TaskPlanRegistry()
    sm = SessionManager(temp_db, plan_registry=reg)
    plan = create_empty_plan("Search", tab_id=3)
    plan.steps = [
        TaskStep(step_id="s1", description="A", action_type="click", status=STEP_SUCCESS),
        TaskStep(step_id="s2", description="B", action_type="click", status=STEP_PENDING),
    ]
    plan.current_step_index = 1
    plan.status = "running"
    session = sm.create_session(plan=plan, tab_id=3)
    session.transition(SESSION_RUNNING, "run")
    sm.persist_session(session)
    sm.persist_plan(plan, session_id=session.session_id)

    sm2 = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    restored = sm2.hydrate_active_sessions()
    assert restored
    s = sm2.get_session(session.session_id)
    assert s.status == SESSION_WAITING_FOR_BROWSER
    assert s.recovery_status == "waiting_for_browser"


def test_recovery_requires_fresh_page_and_strips_coords(temp_db):
    reg = TaskPlanRegistry()
    sm = SessionManager(temp_db, plan_registry=reg)
    plan = create_empty_plan("Search", tab_id=1)
    plan.steps = [
        TaskStep(
            step_id="s1",
            description="Click",
            action_type="click",
            status=STEP_PENDING,
            target_hint="laptop",
            meta={"cached_coordinates": {"x": 10, "y": 20}},
        )
    ]
    plan.last_page_state = {
        "url_signature": "abc",
        "page_signature": "sig1",
        "interactive_elements": [{"role": "button", "label": "laptop"}],
        "role_counts": {"button": 1},
        "element_count": 1,
    }
    session = sm.create_session(plan=plan, tab_id=1)
    session.transition(SESSION_RUNNING, "run")
    sm.persist_session(session)
    sm.persist_plan(plan, session_id=session.session_id)

    recovery = SessionRecovery(sm)
    result = recovery.recover_with_fresh_perception(
        session.session_id,
        page=_page([{"role": "button", "label": "laptop", "text": "laptop"}]),
    )
    assert result["status"] == "ok"
    plan2 = sm.load_plan_into_registry(plan.plan_id)
    assert "cached_coordinates" not in (plan2.steps[0].meta or {})
    assert result["decision"]["action"] in ("resume", "re_resolve", "replan", "require_intervention", "pause")


def test_completed_steps_not_replayed_index(temp_db):
    assert temp_db.available
    reg = TaskPlanRegistry()
    sm = SessionManager(temp_db, plan_registry=reg)
    plan = create_empty_plan("Search")
    plan.steps = [
        TaskStep(step_id="s1", description="Done", action_type="type", status=STEP_SUCCESS),
        TaskStep(step_id="s2", description="Next", action_type="click", status=STEP_PENDING),
    ]
    plan.current_step_index = 1
    session = sm.create_session(plan=plan)
    sm.persist_plan(plan, session_id=session.session_id)
    assert sm.plans.get(plan.plan_id) is not None

    sm2 = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    loaded = sm2.load_plan_into_registry(plan.plan_id)
    assert loaded is not None
    assert loaded.current_step_index == 1
    assert loaded.steps[0].status == STEP_SUCCESS
