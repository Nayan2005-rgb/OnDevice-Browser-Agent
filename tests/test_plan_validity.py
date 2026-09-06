"""Plan validity checker tests (Milestone 4C)."""

from agent.page_state import PageState
from agent.plan_validity import PlanValidityChecker
from agent.task_plan import TaskPlan, TaskStep, create_empty_plan


def _plan_with_step(hint="search button", action="click"):
    plan = create_empty_plan("Search for AI courses and open the first result")
    plan.status = "running"
    plan.steps = [
        TaskStep(
            step_id="step_1_a",
            description="Click search",
            action_type=action,
            target_hint=hint,
            status="pending",
        )
    ]
    return plan


def _state(elements, dialog=False):
    return PageState(
        generation=1,
        page_signature="x",
        interactive_elements=elements,
        element_count=len(elements),
        dialog_present=dialog,
        dialog_count=1 if dialog else 0,
        role_distribution={},
    )


def test_existing_target_valid():
    plan = _plan_with_step("search button")
    state = _state(
        [
            {"role": "button", "label": "Search", "type": "button", "sensitive": False},
        ]
    )
    # hint tokens search + button — "Search" button matches "search"
    r = PlanValidityChecker().check(plan, plan.current_step(), state, {})
    assert r.valid is True
    assert r.recommended_action == "continue"


def test_target_disappeared_replan():
    plan = _plan_with_step("first search result")
    state = _state(
        [
            {"role": "button", "label": "Login", "type": "button", "sensitive": False},
        ],
        dialog=True,
    )
    # Add password field to trigger intervention instead — use empty results without password
    state = _state(
        [{"role": "button", "label": "Home", "type": "button", "sensitive": False}],
        dialog=False,
    )
    r = PlanValidityChecker().check(
        plan, plan.current_step(), state, {}, page_change_level="structural"
    )
    assert r.valid is False
    assert r.recommended_action == "replan"
    assert r.target_present is False


def test_navigation_with_valid_target_continue():
    plan = _plan_with_step("first search result")
    state = _state(
        [
            {
                "role": "link",
                "label": "First search result: AI courses",
                "type": "a",
                "sensitive": False,
            }
        ]
    )
    r = PlanValidityChecker().check(
        plan, plan.current_step(), state, {}, page_change_level="navigation"
    )
    assert r.valid is True
    assert r.recommended_action == "continue"


def test_sensitive_login_requires_intervention():
    plan = _plan_with_step("first search result")
    state = _state(
        [
            {"role": "dialog", "label": "Sign in", "type": "dialog", "sensitive": False},
            {"role": "textbox", "label": "Email", "type": "email", "sensitive": False},
            {"role": "textbox", "label": "Password", "type": "password", "sensitive": True},
        ],
        dialog=True,
    )
    r = PlanValidityChecker().check(plan, plan.current_step(), state, {})
    assert r.valid is False
    assert r.recommended_action == "requires_user_intervention"
    assert r.intervention_reason
    assert "password" in r.intervention_reason.lower() or "credential" in r.intervention_reason.lower()
