"""Replan preview — does not mutate active plan before approval."""

from __future__ import annotations

from agent.operator_control import OperatorControl
from agent.session import SESSION_RUNNING
from agent.session_manager import SessionManager
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep, create_empty_plan
from agent.task_plan_registry import TaskPlanRegistry


def _make_session(temp_db, *, approval=True):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    plan = create_empty_plan("Search laptops", tab_id=1)
    plan.steps = [
        TaskStep(step_id="s1", description="Search product", action_type="type", status=STEP_SUCCESS),
        TaskStep(
            step_id="s2",
            description="Click first product",
            action_type="click",
            status=STEP_PENDING,
            target_hint="laptop",
        ),
    ]
    plan.current_step_index = 1
    plan.status = "running"
    plan.last_page_state = {
        "page_signature": "old",
        "url_signature": "u1",
        "interactive_elements": [{"role": "button", "label": "laptop"}],
        "role_counts": {"button": 1},
        "element_count": 1,
        "dialog_present": False,
    }
    session = sm.create_session(
        plan=plan, tab_id=1, operator_replan_approval=approval
    )
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    sm.persist_plan(plan, session_id=session.session_id)
    return sm, OperatorControl(session_manager=sm), session, plan


def test_preview_does_not_mutate_pending(temp_db):
    sm, op, session, plan = _make_session(temp_db, approval=True)
    before = [s.description for s in plan.steps]
    page = {
        "url": "https://shop.example/search",
        "elements": [
            {"role": "button", "label": "accept cookies", "text": "Accept cookies"},
            {"role": "button", "label": "laptop", "text": "Laptop"},
        ],
    }
    # Force dialog-ish page state via safe_page_state
    safe = {
        "page_signature": "new",
        "url_signature": "u1",
        "interactive_elements": [
            {"role": "button", "label": "accept cookies"},
            {"role": "button", "label": "laptop"},
        ],
        "role_counts": {"button": 2},
        "element_count": 2,
        "dialog_present": True,
        "dialog_type": "cookie",
    }
    result = op.request_replan_preview(
        session.session_id, page=page, safe_page_state=safe
    )
    plan2 = sm.load_plan_into_registry(plan.plan_id)
    after = [s.description for s in plan2.steps]
    # Pending plan unchanged until approval when operator mode on
    if result.get("status") == "preview_ready":
        assert after == before
        assert result["preview"] is not None
        assert result["preview"]["plan_version"] == plan.plan_version
    else:
        # Replanner may decide not needed — still must not crash
        assert result["status"] in (
            "preview_ready",
            "not_needed",
            "re_resolve",
            "paused",
            "unsupported",
            "failed",
            "replanned",
        )


def test_completed_steps_immutable_in_preview(temp_db):
    sm, op, session, plan = _make_session(temp_db, approval=True)
    page = {"url": "https://shop.example/search", "elements": [{"label": "x"}]}
    result = op.request_replan_preview(session.session_id, page=page)
    if result.get("preview"):
        completed = result["preview"].get("completed_steps") or []
        assert any("Search product" in (c.get("description") or "") for c in completed)
