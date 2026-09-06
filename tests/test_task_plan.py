"""Unit tests for TaskPlan / TaskStep state machines (Milestone 4B)."""

import pytest

from agent.task_plan import (
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_CREATED,
    PLAN_FAILED,
    PLAN_PLANNING,
    PLAN_RUNNING,
    PLAN_WAITING_FOR_CONFIRMATION,
    STEP_CANCELLED,
    STEP_EXECUTING,
    STEP_FAILED,
    STEP_PENDING,
    STEP_SUCCESS,
    STEP_VERIFYING,
    InvalidPlanTransitionError,
    InvalidStepTransitionError,
    TaskPlan,
    TaskStep,
    create_empty_plan,
    looks_sensitive_value,
    new_plan_id,
    new_step_id,
)


def test_plan_ids_unique():
    ids = {new_plan_id() for _ in range(20)}
    assert len(ids) == 20


def test_step_ids_unique():
    ids = {new_step_id(i) for i in range(10)}
    assert len(ids) == 10


def test_allowed_plan_transitions():
    plan = create_empty_plan("Click Submit")
    assert plan.status == PLAN_CREATED
    plan.transition(PLAN_PLANNING)
    plan.transition(PLAN_RUNNING)
    plan.transition(PLAN_WAITING_FOR_CONFIRMATION)
    plan.transition(PLAN_RUNNING)
    plan.transition(PLAN_COMPLETED)
    assert plan.is_terminal()


def test_invalid_plan_transition_rejected():
    plan = create_empty_plan("x")
    plan.transition(PLAN_PLANNING)
    plan.transition(PLAN_RUNNING)
    plan.transition(PLAN_COMPLETED)
    with pytest.raises(InvalidPlanTransitionError):
        plan.transition(PLAN_RUNNING)


def test_cancelled_cannot_resume():
    plan = create_empty_plan("x")
    plan.transition(PLAN_PLANNING)
    plan.transition(PLAN_RUNNING)
    plan.transition(PLAN_CANCELLED)
    with pytest.raises(InvalidPlanTransitionError):
        plan.transition(PLAN_RUNNING)


def test_failed_cannot_rerun():
    plan = create_empty_plan("x")
    plan.transition(PLAN_PLANNING)
    plan.transition(PLAN_RUNNING)
    plan.transition(PLAN_FAILED)
    with pytest.raises(InvalidPlanTransitionError):
        plan.transition(PLAN_RUNNING)


def test_step_advancement_rules():
    step = TaskStep(
        step_id="s1",
        description="Type query",
        action_type="type",
        status=STEP_PENDING,
    )
    step.transition("resolving")
    step.transition("executing")
    step.transition("verifying")
    step.transition(STEP_SUCCESS)
    assert step.is_terminal()
    with pytest.raises(InvalidStepTransitionError):
        step.transition(STEP_EXECUTING)


def test_public_view_redacts_sensitive_value():
    step = TaskStep(
        step_id="s1",
        description="Type secret",
        action_type="type",
        value="should-not-leak",
        value_redacted=True,
    )
    view = step.public_view(0)
    assert view["value"] == "[REDACTED]"
    assert "password" not in str(view).lower() or view["value"] == "[REDACTED]"


def test_plan_public_view_no_raw_screenshot_keys():
    plan = create_empty_plan("Search for AI courses")
    plan.steps = [
        TaskStep(step_id="s1", description="Enter query", action_type="type", value="AI")
    ]
    view = plan.public_view()
    blob = str(view)
    assert "raw_screenshot" not in blob
    assert "face_crop" not in blob
    assert "embedding" not in blob
    assert view["goal"]


def test_looks_sensitive_value():
    assert looks_sensitive_value("my password is x")
    assert looks_sensitive_value("4111111111111111")
    assert not looks_sensitive_value("AI courses")


def test_advance_after_success():
    plan = create_empty_plan("g")
    plan.transition(PLAN_PLANNING)
    plan.transition(PLAN_RUNNING)
    plan.steps = [
        TaskStep(step_id="a", description="1", action_type="click", status=STEP_SUCCESS),
        TaskStep(step_id="b", description="2", action_type="click", status=STEP_PENDING),
    ]
    assert plan.advance_after_success() is True
    assert plan.current_step_index == 1
    plan.steps[1].status = STEP_SUCCESS
    assert plan.advance_after_success() is False
    assert plan.status == PLAN_COMPLETED
