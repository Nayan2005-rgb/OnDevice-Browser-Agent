"""Plan versioning and revision history tests (Milestone 4C)."""

from agent.adaptive_replanner import AdaptiveReplanner, apply_replan_to_plan
from agent.page_change_detector import PageChangeResult
from agent.page_state import PageState
from agent.task_plan import STEP_PENDING, STEP_SUCCESS, TaskStep, create_empty_plan


def test_public_view_includes_version_fields():
    plan = create_empty_plan("Search for AI courses")
    plan.plan_version = 2
    plan.replan_count = 1
    plan.last_replan_reason = "dialog_appeared"
    plan.last_page_change_level = "structural"
    plan.last_page_change = {
        "level": "structural",
        "score": 0.72,
        "reasons": ["dialog_appeared"],
    }
    view = plan.public_view()
    assert view["version"] == 2
    assert view["replan_count"] == 1
    assert view["last_replan_reason"] == "dialog_appeared"
    assert view["page_change"]["level"] == "structural"
    assert "screenshot" not in str(view).lower() or "[REDACTED]" in str(view)


def test_revision_history_safe():
    plan = create_empty_plan("goal")
    plan.status = "running"
    plan.steps = [
        TaskStep(step_id="a", description="Done", action_type="click", status=STEP_SUCCESS),
        TaskStep(
            step_id="b",
            description="Open result",
            action_type="click",
            status=STEP_PENDING,
            target_hint="first search result",
        ),
    ]
    plan.current_step_index = 1
    state = PageState(
        dialog_present=True,
        dialog_count=1,
        interactive_elements=[
            {"role": "button", "label": "Accept cookies", "type": "button"},
        ],
        element_count=1,
    )
    result = AdaptiveReplanner().replan(
        plan,
        state,
        PageChangeResult(True, "structural", 0.7, ["dialog_appeared"]),
        {},
    )
    apply_replan_to_plan(plan, result)
    assert len(plan.revision_history) >= 1
    blob = str(plan.revision_history).lower()
    assert "password" not in blob
    assert "screenshot" not in blob
    view = plan.public_view()
    assert view["revision_history"]
    assert view["plan_version"] >= 2
