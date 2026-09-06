"""Adaptive replanner tests (Milestone 4C)."""

from agent.adaptive_replanner import (
    MAX_REPLAN_ATTEMPTS,
    STATUS_MAX_ATTEMPTS,
    STATUS_PAUSED,
    STATUS_REPLANNED,
    STATUS_RE_RESOLVE,
    AdaptiveReplanner,
    apply_replan_to_plan,
)
from agent.page_change_detector import PageChangeResult
from agent.page_state import PageState
from agent.plan_validity import PlanValidityResult
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep, create_empty_plan


def _plan():
    plan = create_empty_plan("Search for AI courses and open the first result")
    plan.status = "running"
    plan.steps = [
        TaskStep(
            step_id="s1",
            description="Enter search query",
            action_type="type",
            status=STEP_SUCCESS,
            target_hint="search box",
            value="AI courses",
        ),
        TaskStep(
            step_id="s2",
            description="Click Search",
            action_type="click",
            status=STEP_SUCCESS,
            target_hint="search button",
        ),
        TaskStep(
            step_id="s3",
            description="Open first result",
            action_type="click",
            status=STEP_PENDING,
            target_hint="first search result",
        ),
    ]
    plan.current_step_index = 2
    plan.plan_version = 1
    plan.replan_count = 0
    return plan


def _change(level="structural", reasons=None):
    return PageChangeResult(
        changed=True,
        level=level,
        score=0.7,
        reasons=reasons or ["dialog_appeared"],
    )


def test_completed_steps_never_modified():
    plan = _plan()
    completed = [(s.step_id, s.description, s.status) for s in plan.steps[:2]]
    state = PageState(
        dialog_present=True,
        dialog_count=1,
        interactive_elements=[
            {"role": "dialog", "label": "We use cookies", "type": "dialog"},
            {"role": "button", "label": "Accept cookies", "type": "button"},
        ],
        element_count=2,
    )
    result = AdaptiveReplanner().replan(plan, state, _change(), {})
    assert result.status == STATUS_REPLANNED
    apply_replan_to_plan(plan, result)
    after = [(s.step_id, s.description, s.status) for s in plan.steps[:2]]
    assert after == completed


def test_only_pending_steps_updated():
    plan = _plan()
    state = PageState(
        dialog_present=True,
        dialog_count=1,
        interactive_elements=[
            {"role": "button", "label": "Accept cookies", "type": "button"},
            {"role": "button", "label": "Reject all", "type": "button"},
        ],
        element_count=2,
    )
    result = AdaptiveReplanner().replan(plan, state, _change(), {})
    apply_replan_to_plan(plan, result)
    assert plan.steps[0].status == STEP_SUCCESS
    assert plan.steps[1].status == STEP_SUCCESS
    assert plan.steps[2].action_type == "click"
    assert "cookie" in (plan.steps[2].description or "").lower() or plan.steps[
        2
    ].meta.get("strategy") == "safe_dialog"


def test_plan_version_increments():
    plan = _plan()
    state = PageState(
        dialog_present=True,
        dialog_count=1,
        interactive_elements=[
            {"role": "button", "label": "Accept cookies", "type": "button"},
        ],
        element_count=1,
    )
    result = AdaptiveReplanner().replan(plan, state, _change(), {})
    apply_replan_to_plan(plan, result)
    assert plan.plan_version == 2
    assert plan.replan_count == 1
    assert plan.last_replan_reason


def test_re_resolve_when_target_moved():
    plan = _plan()
    state = PageState(
        interactive_elements=[
            {
                "role": "link",
                "label": "First search result: AI courses",
                "type": "a",
                "sensitive": False,
            }
        ],
        element_count=1,
    )
    result = AdaptiveReplanner().replan(
        plan, state, _change("moderate", ["layout_group_changed"]), {}
    )
    assert result.status == STATUS_RE_RESOLVE


def test_sensitive_pauses():
    plan = _plan()
    state = PageState(
        dialog_present=True,
        dialog_count=1,
        interactive_elements=[
            {"role": "dialog", "label": "Sign in", "type": "dialog"},
            {"role": "textbox", "label": "Password", "type": "password", "sensitive": True},
        ],
        element_count=2,
    )
    validity = PlanValidityResult(
        valid=False,
        confidence=0.9,
        recommended_action="requires_user_intervention",
        intervention_reason="Login requires credentials.",
    )
    result = AdaptiveReplanner().replan(plan, state, _change(), {}, validity=validity)
    assert result.status == STATUS_PAUSED


def test_max_attempts_blocked():
    plan = _plan()
    plan.replan_count = MAX_REPLAN_ATTEMPTS
    result = AdaptiveReplanner().replan(
        plan,
        PageState(element_count=0),
        _change(),
        {},
    )
    assert result.status == STATUS_MAX_ATTEMPTS


def test_max_replan_constant():
    assert MAX_REPLAN_ATTEMPTS == 2
