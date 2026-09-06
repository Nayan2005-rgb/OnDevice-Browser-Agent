"""Replan approval / rejection tests."""

from __future__ import annotations

from agent.adaptive_replanner import STATUS_REPLANNED, ReplanResult, apply_replan_to_plan
from agent.operator_control import OperatorControl
from agent.session import SESSION_PAUSED, SESSION_RUNNING
from agent.session_manager import SessionManager
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep, create_empty_plan
from agent.task_plan_registry import TaskPlanRegistry


def test_approve_creates_new_plan_version(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [
        TaskStep(step_id="s1", description="Done", action_type="type", status=STEP_SUCCESS),
        TaskStep(step_id="s2", description="Click", action_type="click", status=STEP_PENDING),
    ]
    plan.current_step_index = 1
    plan.plan_version = 1
    session = sm.create_session(plan=plan, operator_replan_approval=True)
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    sm.persist_plan(plan, session_id=session.session_id)

    new_steps = list(plan.steps[:1]) + [
        TaskStep(
            step_id="s_new",
            description="Dismiss cookie dialog",
            action_type="click",
            status=STEP_PENDING,
            target_hint="accept cookies",
        ),
        TaskStep(
            step_id="s2b",
            description="Click",
            action_type="click",
            status=STEP_PENDING,
        ),
    ]
    preview = {
        "preview_id": "rpv_test",
        "session_id": session.session_id,
        "plan_id": plan.plan_id,
        "plan_version": 1,
        "proposed_version": 2,
        "reason": "cookie_dialog_blocking_target",
        "page_change_level": "structural",
        "changes": ["Inserted safe cookie dismiss step"],
        "status": "pending",
        "_proposed_steps": [s.serialize_for_persistence() for s in new_steps],
        "old_pending_steps": [],
        "new_pending_steps": [],
        "completed_steps": [],
    }
    sm.save_replan_preview(session.session_id, preview)

    result = op.approve_replan(session.session_id)
    assert result["status"] == "approved"
    plan2 = sm.load_plan_into_registry(plan.plan_id)
    assert plan2.plan_version == 2
    assert plan2.steps[0].status == STEP_SUCCESS
    assert any("cookie" in s.description.lower() for s in plan2.steps)


def test_reject_pauses_session(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [
        TaskStep(step_id="s1", description="Click", action_type="click", status=STEP_PENDING)
    ]
    session = sm.create_session(plan=plan, operator_replan_approval=True)
    session.transition(SESSION_RUNNING, "r")
    sm.persist_session(session)
    sm.save_replan_preview(
        session.session_id,
        {
            "preview_id": "rpv_x",
            "session_id": session.session_id,
            "plan_id": plan.plan_id,
            "plan_version": 1,
            "proposed_version": 2,
            "reason": "x",
            "status": "pending",
            "_proposed_steps": [plan.steps[0].serialize_for_persistence()],
        },
    )
    result = op.reject_replan(session.session_id)
    assert result["status"] == "rejected"
    s = sm.get_session(session.session_id)
    assert s.status == SESSION_PAUSED
    assert s.pause_reason == "replan_rejected_by_operator"
    # Plan pending steps unchanged
    plan2 = sm.load_plan_into_registry(plan.plan_id)
    assert len(plan2.steps) == 1


def test_replan_limit_still_enforced(temp_db):
    sm = SessionManager(temp_db, plan_registry=TaskPlanRegistry())
    op = OperatorControl(session_manager=sm)
    plan = create_empty_plan("g")
    plan.steps = [
        TaskStep(step_id="s1", description="Click", action_type="click", status=STEP_PENDING)
    ]
    plan.replan_count = 2
    session = sm.create_session(plan=plan, operator_replan_approval=True)
    sm.persist_plan(plan, session_id=session.session_id)
    result = op.request_replan_preview(
        session.session_id, page={"url": "https://x", "elements": []}
    )
    assert result["status"] == "max_replan_attempts"
